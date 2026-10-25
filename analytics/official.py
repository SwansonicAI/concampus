"""Amtliche Statistik: the records of one Berichtssemester for the Statistik der Studierenden and der Prüfungen, one per
Matrikelnummer, and those of the Gasthörer and of the Promovierenden, as the Statistisches Landesamt takes them through
eSTATISTIK.core (core.destatis), and what is still missing for them."""
import csv
import io
import re
import unicodedata
from collections import defaultdict
from datetime import date

from django.db.models import Prefetch, Q
from django.urls import reverse
from django.utils.choices import flatten_choices

from core import destatis
from core.models import Institution, Person, Term
from students.models import Study, StudyTerm

COUNTED = [Study.Status.ENROLLED, Study.Status.GRADUATED, Study.Status.LEFT]
GENDER = {'m': '1', 'w': '2', 'd': '3', 'x': '4'}
SECOND = 37  # the second Prüfung's fields follow the first's at this distance
ENTRANCE = dict(flatten_choices(destatis.ENTRANCE))
COUNTRIES = dict(destatis.COUNTRIES)


def semester(term):
    """(Berichtssemester, year) of a term: 1 summer, 2 winter."""
    return ('2' if term.start.month > 6 else '1'), term.start.year


def named(name):
    """(Berichtssemester, year) of a term written as Person.first_term has it."""
    match = re.match(r'(WiSe|SoSe) (\d{4})', name)
    return ('2' if match.group(1) == 'WiSe' else '1'), int(match.group(2))


def given(person):
    """The first given name as the record has it: four letters, umlauts written out, other signs as spaces."""
    name = next(iter((person.given_names or person.first_name or person.last_name).split()), '')
    for umlaut, plain in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('Ä', 'Ae'), ('Ö', 'Oe'), ('Ü', 'Ue'), ('ß', 'ss')):
        name = name.replace(umlaut, plain)
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub('[^A-Za-z]', ' ', name)[:4].ljust(4)


def fit(value, length):
    return str(value).zfill(length) if isinstance(value, int) else value


def table(layout, records):
    """A file for eSTATISTIK.core: the column names, then the records, each value as long as its field."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';', quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(name for _, name, _ in layout)
    writer.writerows([fit(values.get(key, ''), length) for key, _, length in layout] for values in records)
    return out.getvalue()


def result(study):
    """(credits earned, Gesamtnote) of a finished study."""
    tree, average = study.progress()
    honours = None not in (average, study.program.honours) and average <= study.program.honours
    return int(sum(m.earned for m in tree)), destatis.grade(average, honours)


class Report:
    """The statistics of a Berichtssemester: who counts in them, their records, and what is missing for them. The
    Promovierenden are counted once a year, on 1 December: in the winter term's report."""

    def __init__(self, term):
        self.term, self.institution = term, Institution.get()
        self.before = Term.objects.filter(start__lt=term.start).order_by('-start').first()
        here = set(StudyTerm.objects.filter(term=term, study__status__in=COUNTED).values_list('study__person', flat=True))
        gone = set(  # exmatriculated after the term before: counted once more, now
            Study.objects.filter(terms__term=self.before, status__in=Study.FORMER).exclude(person__in=here).values_list('person', flat=True)
        ) if self.before else set()
        finished = Study.objects.filter(Q(status=Study.Status.GRADUATED) | Q(status=Study.Status.LEFT, exit_reason='8'), since__range=(term.start, term.end))
        self.finished = set(finished.values_list('pk', flat=True))
        examined = set(finished.values_list('person', flat=True))
        self.year = term.start.year if term.start.month > 6 else term.start.year - 1
        doctoral = set(  # under way on 1 December, or ended since the one before
            Study.objects.filter(program__kind='5', status__in=COUNTED, terms__isnull=False, start_term__start__lte=date(self.year, 12, 1))
            .exclude(status__in=Study.FORMER, since__lte=date(self.year - 1, 12, 1)).values_list('pk', flat=True)
        )
        counted = here | gone
        people = (
            Person.objects.filter(Q(pk__in=counted | examined) | Q(studies__in=doctoral)).distinct().order_by('matriculation', 'last_name')
            .prefetch_related(Prefetch(
                'studies', Study.objects.filter(status__in=COUNTED).select_related('program', 'subject', 'start_term')
                .prefetch_related(Prefetch('terms', StudyTerm.objects.select_related('term'))),
            ))
        )
        self.students = [p for p in people if p.pk in counted]
        self.examined = [p for p in people if p.pk in examined]
        self.doctoral = [s for p in people for s in p.studies.all() if s.pk in doctoral]
        self.guests = list(
            Study.objects.filter(status=Study.Status.GUEST, participations__course__term=term).distinct().order_by('person__last_name')
            .select_related('person', 'subject').prefetch_related('participations__course__subject')
        )
        self.missing, self.setup = defaultdict(list), {}
        if not (self.institution.university and self.institution.region and self.institution.report_id):
            self.setup['Hochschulnummer, Kreis und BerichtseinheitID der Hochschule'] = reverse('setup:edit', args=['hochschule', self.institution.pk])
        self.records = {p.pk: self.record(p) for p in people}
        self.files = {
            'studierende': (destatis.RECORD, [{ef: v for ef, v in self.records[p.pk].items() if not ef.isdigit() or int(ef) < 126} for p in self.students]),
            'pruefungen': (destatis.RECORD, [self.records[p.pk] for p in self.examined]),
            'gasthoerer': (destatis.GUESTS, [self.guest(s, n) for n, s in enumerate(self.guests, 1)]),
            'promovierende': (destatis.DOCTORAL, [self.doctorate(s, n) for n, s in enumerate(self.doctoral, 1)]),
        }

    def csv(self, key):
        layout, records = self.files[key]
        return table(layout, [{'': self.institution.report_id} | values for values in records])

    def need(self, person, value, what):
        """`value`, or '' with `what` noted as missing for the person."""
        if not value:
            self.missing[person].append(what)
        return value or ''

    def needs(self, what, editor, record):
        self.setup[what] = reverse('setup:edit', args=[editor, record.pk])

    def field(self, study):
        """1. Studienfach: the Studiengang's, as in the Lehramt, else the Hauptfach's."""
        if not (code := study.program.field or study.subject.field):
            self.needs(f'Studienfach des Hauptfachs {study.subject}', 'hauptfaecher', study.subject)
        return code

    def exam(self, study, person):
        """Abschlussprüfung with the Studienart before it; an Erststudium after a degree is a Zweitstudium."""
        program = study.program
        if not program.exam:
            self.needs(f'Abschlussprüfung des Studiengangs {program.degree} {program.name}', 'studiengaenge', program)
        degree = person.degree_exam or any(s.status == Study.Status.GRADUATED and s.since < study.start_term.start for s in person.studies.all())
        return ('2' if program.kind == '1' and degree else program.kind) + program.exam

    def record(self, person):
        """EF → value: the person in the Berichtssemester (EF1–125) and the Prüfungen they finished in it (EF126–199)."""
        i, t, v = self.institution, self.term, {}
        studies = list(person.studies.all())
        now = [s for s in studies if any(r.term_id == t.pk for r in s.terms.all())]
        reported = now or [s for s in studies if self.before and any(r.term_id == self.before.pk for r in s.terms.all())]
        until = t if now else self.before
        reported.sort(key=lambda s: (s.status != Study.Status.ENROLLED, s.start_term.start))
        regs = [r for s in studies for r in s.terms.all() if until and r.term.start <= until.start]
        v['1'], v['2'], v['3'], v['4'] = i.region[:2], *semester(t), i.university
        if number := self.need(person, person.matriculation, 'Matrikelnummer'):
            v['6'] = number.zfill(12)
        v['7'] = self.need(person, GENDER.get(person.gender), 'Geschlecht')
        if self.need(person, person.birth_date, 'Geburtsdatum'):
            v['8U1'], v['8U2'], v['8U3'] = person.birth_date.day, person.birth_date.month, person.birth_date.year
        v['9'] = given(person)
        nations = [n for n in (person.nationality, person.second_nationality) if n in COUNTRIES]
        v['10'], v['11'] = (sorted(nations, key=lambda n: n != '000') + ['', ''])[:2]  # German first
        self.need(person, v['10'], 'Staatsangehörigkeit')
        if home := self.need(person, person.home, 'Heimatwohnsitz'):
            v['12U1'], v['12U2'] = (person.residence or home)[:2], (person.residence or home)[2:]
            v['13U1'], v['13U2'] = home[:2], home[2:]
        v['14'], v['23'], v['24'] = '1', 0, 0
        if person.first_university:
            v['17'], v['18'] = person.first_university[:4], person.first_university[4:]
            if self.need(person, person.first_term, 'Semester der Ersteinschreibung'):
                v['19'], v['20'] = named(person.first_term)
        elif person.external_terms:
            self.need(person, None, 'Hochschule der Ersteinschreibung')
        elif regs:
            v['17'] = i.university
            v['19'], v['20'] = semester(min((r.term for r in regs), key=lambda term: term.start))
        v['21'] = len({r.term_id for r in regs}) + person.external_terms
        v['22'] = len({r.term_id for r in regs if r.leave})
        before = {r.term_id for r in regs if r.term.start < t.start}
        for s, n in zip(reported, (28, 44)):
            own = [r for r in s.terms.all() if r.term.start <= until.start]
            this = next((r for r in own if r.term_id == t.pk), None)
            if not now:
                art, reason = '5', self.need(person, s.exit_reason, f'Grund der Exmatrikulation ({s.program.degree} {s.subject})')
            elif this.leave:
                art, reason = '4', self.need(person, this.leave_reason, f'Grund der Beurlaubung ({s.program.degree} {s.subject})')
            elif len(own) > 1 or self.before and self.before.pk in before:
                art, reason = '3', ''
            else:  # first enrolment anywhere, or new here after studying elsewhere or before
                art, reason = ('2' if before or person.first_university or person.external_terms or person.degree_university else '1'), ''
            v[str(n)], v[str(n + 1)], v[str(n + 2)], v[str(n + 3)] = art, reason, '1', '1'
            v[str(n + 4)] = s.prior_terms + sum(1 for r in own if not r.leave)
            v[str(n + 5)] = self.exam(s, person)
            v[f'{n + 6}U1'], v[f'{n + 6}U2'] = i.region[:2], i.region[2:]
            v[str(n + 7)], v[str(n + 8)] = s.program.semesters, self.field(s)
        self.previous(person, studies, reported, v)
        if person.degree_exam:
            self.need(person, all([person.degree_university, person.degree_date, person.degree_field, person.degree_grade]), 'Angaben zum letzten Abschluss')
        if last := self.degree(person, studies, t.start):
            v['89'], v['90'], v['91'], v['92'], v['95'], v['96'], v['98'] = last
            v['97'] = '1'
        if self.need(person, person.entrance_date, 'Jahr der Hochschulzugangsberechtigung'):
            v['117'] = person.entrance_date.year
        v['118'] = self.need(person, person.entrance if person.entrance in ENTRANCE else '', 'Art der Hochschulzugangsberechtigung')
        if region := self.need(person, person.entrance_region, 'Ort der Hochschulzugangsberechtigung'):
            v['119U1'], v['119U2'] = region[:2], region[2:]
        v['122'], v['123'] = '1' if person.vocational else '', '1' if person.internship else ''
        for s, n in zip(sorted((s for s in studies if s.pk in self.finished), key=lambda s: s.since), (0, SECOND)):
            self.passed(person, s, n, v)
        return v

    def guest(self, study, n):
        """A Gasthörer:in of the term, with the Fachrichtungen of the courses attended."""
        person, i = study.person, self.institution
        fields = dict.fromkeys(
            destatis.guest(p.course.subject.field if p.course.subject else study.subject.field) for p in study.participations.all() if p.course.term_id == self.term.pk
        )
        v = {'kind': '1', 'university': i.university, 'number': n, 'matriculation': (person.matriculation or '').zfill(12) if person.matriculation else ''}
        v['gender'] = self.need(person, GENDER.get(person.gender), 'Geschlecht')
        if self.need(person, person.birth_date, 'Geburtsdatum'):
            v['month'], v['year'] = person.birth_date.month, person.birth_date.year
        v['nationality'] = self.need(person, person.nationality if person.nationality in COUNTRIES else '', 'Staatsangehörigkeit')
        return v | {f'field{k}': field for k, field in enumerate(list(fields)[:3], 1)}

    def doctorate(self, study, n):
        """A Promotion as of 1 December: its person as the student record has them, how it stands, where it began and ended."""
        person, i, v, day = study.person, self.institution, self.records[study.person_id], date(self.year, 12, 1)
        if study.status in Study.FORMER and study.since <= day:
            registration = '5' if study.status == Study.Status.GRADUATED or study.exit_reason == '8' else '6'
        elif any(r.leave and r.term.start <= day <= r.term.end for r in study.terms.all()):
            registration = '4'
        elif study.start_term.start > date(self.year - 1, 12, 1):  # new this year: the first, or after an earlier one
            registration = '2' if any(o.program.kind == '5' and o.status in Study.FORMER for o in person.studies.all() if o != study) else '1'
        else:
            registration = '3'
        last = self.degree(person, list(person.studies.all()), study.start_term.start)
        self.need(person, last, 'Letzter Abschluss vor der Promotion')
        d = {
            '1': i.region[:2], '2': self.year, '3': i.university, '4': n, '10': '01', '11': self.field(study), '12': registration,
            '13U1': study.start_term.start.month, '13U2': study.start_term.start.year, '15': '1',
            '16': '1' if study.structured else '0', '17': '1' if study.employed else '0', '18': '2' if study.cumulative else '1', '23': '1' if last else '0',
        }
        if last:
            d['24'], d['25'], d['26'], d['27'], d['28'], d['29'], d['30'] = last
        if registration in ('5', '6'):
            d['14U1'], d['14U2'] = study.since.month, study.since.year
        same = {  # what the student record has already
            '5': '7', '6U1': '8U1', '6U2': '8U2', '6U3': '8U3', '7': '9', '8': '10', '9': '11', '19': '17', '20': '18', '21': '19', '22': '20',
            '31': '117', '32': '118', '33U1': '119U1', '33U2': '119U2',
        }
        return d | {key: v[ef] for key, ef in same.items() if ef in v}

    def previous(self, person, studies, reported, v):
        """EF69–79: where the person studied in the term before."""
        if not self.before:
            return
        then = [s for s in studies if any(r.term_id == self.before.pk for r in s.terms.all())]
        degree = person.degree_university and person.degree_date and self.before.start <= person.degree_date <= self.before.end
        if then:
            v['69'] = '1'
            if same := [n for n, s in enumerate(reported[:2], 1) if s in then]:
                v['70'] = '3' if len(same) == 2 else str(same[0])
            else:
                v['71'], v['73'], v['74'] = self.institution.university, self.exam(then[0], person), self.field(then[0])
        elif degree:
            where = person.degree_university
            v['69'], v['71'], v['72'] = '3' if where.startswith(destatis.ABROAD) else '2', where[:4], where[4:]
            v['73'], v['74'] = destatis.kind(person.degree_exam) + person.degree_exam, person.degree_field

    def degree(self, person, studies, before):
        """The last passed Abschlussprüfung before the day `before`, here or elsewhere: (Hochschule, state abroad, Prüfung,
        Studienfach, month, year, Gesamtnote), or None."""
        here = max((s for s in studies if s.status == Study.Status.GRADUATED and s.since < before), key=lambda s: s.since, default=None)
        elsewhere = person.degree_exam and person.degree_date and person.degree_date < before
        if here and not (elsewhere and person.degree_date > here.since):
            return self.institution.university, '', self.exam(here, person), self.field(here), here.since.month, here.since.year, result(here)[1]
        if elsewhere:
            where, exam, day = person.degree_university, person.degree_exam, person.degree_date
            return where[:4], where[4:], destatis.kind(exam) + exam, person.degree_field, day.month, day.year, person.degree_grade
        return None

    def passed(self, person, study, n, v):
        """EF126–162, or 163–199 for a second: an Abschlussprüfung finished in the Berichtssemester."""
        (earned, grade), program, done = result(study), study.program, study.status == Study.Status.GRADUATED

        def put(ef, value):
            v[str(ef + n)] = value

        put(126, program.exam_office or f'1{self.institution.university}01')
        put(127, study.prior_terms + sum(1 for r in study.terms.all() if not r.leave))
        put(128, study.prior_terms)
        put(132, earned)
        put(147, self.exam(study, person))
        put(148, '01' if program.exam in ('06', '07', '91', '92') else '')
        put(149, program.semesters)
        put(150, self.field(study))
        put(155, study.since.month)
        put(156, study.since.year)
        put(157, '1' if done else '2')
        put(158, grade if done else '9')
