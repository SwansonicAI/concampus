import csv
import re
from decimal import Decimal
from collections import Counter, defaultdict
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Max, Prefetch, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.contrib.auth.decorators import login_not_required
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from lxml.etree import DocumentInvalid

from core import dates, texts
from core.access import CODENAMES, require
from core.dossier import dossier
from core.forms import ComposeForm
from core.history import tables
from core.apps import plural
from core.models import Consent, Export, Institution, Office, Person, Term
from core.text import fold, handle, matching
from core.views import SHOWN, preview, without_term
from courses.models import Participation
from courses.views import untimed
from curriculum.models import Module, Program, Subject
from curriculum.views import plan
from exams.views import exhausted
from instruments.models import Loan
from . import payments, smv, xhochschule
from .disposal import assess, due, offer
from .models import EXITS, FIRST_TERM_LEAVES, LEAVE_TERMS, LEAVES, OFFICIAL, Disposal, Document, DocumentType, InsuranceReport, Study, StudyTerm

class ChangeForm(forms.Form):
    program = forms.ModelChoiceField(label='Neuer Studiengang', queryset=Program.objects.all(), empty_label='Bitte wählen')
    subject = forms.ModelChoiceField(label='Hauptfach', queryset=Subject.objects.all(), empty_label='Bitte wählen')

    def __init__(self, *args, study=None, **kwargs):
        """Shown for a study: its Hauptfach kept, the Studiengänge one can start with it first; once one is picked, only
        its Hauptfächer."""
        super().__init__(*args, **kwargs)
        self.study = study
        if study:
            self.fields['subject'].initial = study.subject_id
            current = Program.current()
            self.fields['program'].choices = [('', 'Bitte wählen')] + [
                (label, [(p.pk, str(p)) for p in current if (study.subject_id in p.offered) == offered])
                for label, offered in [(f'Mit Hauptfach {study.subject}', True), ('Weitere', False)]
            ]
            self.fields['program'].widget.attrs['data-leads'] = 'subject'
            self.fields['subject'].choices = [('', 'Bitte wählen')] + [(str(p), [(s.pk, str(s)) for s in p.subjects.all()]) for p in current]

    def clean(self):
        data = super().clean()
        if data.get('program') and data.get('subject') and not data['program'].subjects.filter(pk=data['subject'].pk).exists():
            raise forms.ValidationError(f'{data["program"]} bietet das Hauptfach {data["subject"]} nicht an.')
        if self.study and (data.get('program'), data.get('subject')) == (self.study.program, self.study.subject):
            raise forms.ValidationError('Das ist das bisherige Studium.')
        return data


class PaymentForm(forms.Form):
    """A payment booked by hand: when it arrived, how much, and the Beleg to find it again."""

    paid = dates.ReceivedField(label='Eingegangen am')
    amount = forms.DecimalField(label='Betrag', min_value=0, decimal_places=2, localize=True)
    reference = forms.CharField(label='Beleg oder Verwendungszweck', max_length=140)


def own(person):
    """The person's own study to open: the one under way, else the latest they finished or left."""
    return (
        person.studies.filter(status=Study.Status.ENROLLED).first()
        or person.studies.filter(status__in=Study.FORMER, terms__isnull=False).order_by('-since').first()
    )


@require('teacher', 'student', *CODENAMES)
def suggest(request):
    """Enrolled students matching a name as it is typed, for adding someone to a course or a booking: from three letters,
    a few at a time, one's own class, courses and ensembles first. Never the whole list (docs/privacy.md)."""
    person, q = request.user, request.GET.get('q', '').strip()
    if len(q) < 3:
        return JsonResponse({'found': []})
    studies = Study.objects.filter(status=Study.Status.ENROLLED).exclude(person=person).select_related('person', 'subject', 'program')
    near = Q(teacher=person) | Q(participations__course__staff=person, participations__course__term=Term.current()) | Q(ensembles__members__person=person)
    found = matching(studies, q, 'person__first_name', 'person__last_name', 'person__birth_name', starts=True).annotate(near=Count('pk', filter=near))
    shown = {}
    for s in found.order_by('-near', 'person__last_name', 'person__first_name')[:24]:
        shown.setdefault(s.person_id, s)
    return JsonResponse({'found': [
        {'id': s.person_id, 'text': f'{s.person.name} · {s.subject} {s.program.degree}', 'value': f'{s.person.first_name} {s.person.last_name}, {s.subject} {s.program.degree}'}
        for s in list(shown.values())[:8]
    ]})


def index(request):
    roles = request.user.roles
    if 'view_students' in roles:
        return redirect('students:list')
    study = own(request.user)
    if study is None:
        raise PermissionDenied
    return redirect(study.get_absolute_url())


COLUMNS = [  # heading, sort key, field; the server sorts, since the list may be longer than shown
    ('Matrikelnr.', 'matrikel', 'person__matriculation'), ('Name', 'name', 'person__last_name'), ('Studiengang', 'studiengang', 'program__name'),
    ('Hauptfach', 'hauptfach', 'subject__name'), ('Klasse', 'klasse', 'teacher__last_name'), ('FS', 'fs', 'fs'), ('Status', 'status', 'status'),
]


CASES = [
    ('unterricht', 'Ohne Unterrichtszeit'), ('pruefung', 'Prüfung laut Plan überfällig'), ('regelstudienzeit', 'Über der Regelstudienzeit'),
    ('schwerpunkt', 'Schwerpunkt nicht gewählt'), ('beurlaubt', 'Beurlaubt in diesem Semester'),
]
STATES = [('', 'Alle'), *((s.value, s.label) for s in [Study.Status.ENROLLED, Study.Status.GUEST, Study.Status.GRADUATED, Study.Status.LEFT])]  # who studies or studied here


def semesters(studies):
    """The studies with their Fachsemester as `fs`, counted in the database."""
    return studies.annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=timezone.localdate())))


def cases(studies, case):
    """The studies in one of the situations an office keeps an eye on; `studies` carries its Fachsemester as `fs`."""
    if case == 'unterricht':
        return studies.filter(pk__in=untimed(Participation.objects.all()).values('study'))
    if case == 'beurlaubt':
        return studies.filter(terms__term=Term.current(), terms__leave=True)
    if case == 'regelstudienzeit':
        return studies.filter(fs__gt=F('program__semesters'))
    if case == 'schwerpunkt':
        due = Q(pk__in=[])
        for program in Program.objects.filter(pk__in=studies.values('program')):
            if choices := program.choices():
                due |= Q(program=program, fs__gte=min(c.first or 99 for c in choices))
        return studies.filter(due, focus=None)
    if case == 'pruefung':  # an exam of a module under way that the plan recommends for an earlier semester
        rows = list(studies.values_list('pk', 'program', 'subject', 'fs'))
        exams = defaultdict(list)
        for m in Module.objects.filter(exam=True, semester__isnull=False, program__in={r[1] for r in rows}).prefetch_related('waived'):
            exams[m.program_id].append(m)
        seen = defaultdict(set)  # each study's modules passed or taken now, and ('under', parent) for the modules under way
        for study, module, parent, passed in Participation.objects.filter(study__in=[r[0] for r in rows]).exclude(passed=False).values_list('study', 'module', 'module__parent', 'passed'):
            seen[study] |= {module, ('under', parent)} if passed else {module}
        late = [
            pk for pk, program, subject, fs in rows for m in exams[program]
            if m.semester < fs and m.pk not in seen[pk] and ('under', m.parent_id) in seen[pk] and subject not in {s.pk for s in m.waived.all()}
        ]
        return studies.filter(pk__in=late)
    return studies


def narrowed(choices, request, program, subject=None):
    """A filter's choices as far as the Studiengang and Hauptfach already chosen allow: Hauptfächer of the Studiengang,
    teachers of its students in the Hauptfach."""
    if (chosen := request.GET.get('studiengang', '')).isdigit():
        choices = choices.filter(**{program: chosen})
    if subject and (chosen := request.GET.get('fach', '')).isdigit():
        choices = choices.filter(**{subject: chosen})
    return choices.distinct()


def listed(request):
    """(the studies the list shows as filtered, all of them and not only the rows on the page; its status; the studies
    the person may list at that status)."""
    status = request.GET.get('status', Study.Status.ENROLLED)
    status = status if status in dict(STATES) else Study.Status.ENROLLED
    shown = Study.objects.filter(status=status) if status else Study.objects.exclude(status__in=Study.APPLICATION)
    scoped = request.user.within(shown, 'view_students')
    studies = semesters(scoped.select_related('person', 'program', 'subject', 'start_term', 'teacher')).order_by('person__last_name', 'person__first_name')
    for key, field in [('studiengang', 'program'), ('fach', 'subject'), ('klasse', 'teacher'), ('fs', 'fs'), ('beginn', 'start_term')]:
        if (value := request.GET.get(key, '')).isdigit():
            studies = studies.filter(**{field: value})
    studies = cases(studies, request.GET.get('stand', ''))
    studies = matching(studies, request.GET.get('q', '').strip(), 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email', 'person__matriculation', 'teacher__last_name')
    return studies, status, scoped


@require('view_students')
def roster(request):
    studies, status, scoped = listed(request)
    q, sort = request.GET.get('q', '').strip(), request.GET.get('sortierung', '')
    if field := next((f for _, key, f in COLUMNS if key == sort.removeprefix('-')), None):
        studies = studies.order_by(F(field).desc(nulls_last=True) if sort.startswith('-') else F(field).asc(nulls_last=True), 'person__last_name', 'person__first_name')
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="studierende.csv"'
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        personal = 'manage_students' in request.user.roles  # gender and nationality for the student office only (docs/privacy.md)
        writer.writerow(['Matrikelnummer', 'Nachname', 'Vorname', *(['Geschlecht', 'Staatsangehörigkeit'] if personal else []), 'E-Mail', 'Studiengang', 'Hauptfach', 'Fachsemester', 'Status'])
        Export.log(request.user, f'Studierende ({request.GET.urlencode()})', studies.count())
        for s in studies:
            writer.writerow([
                s.person.matriculation, s.person.last_name, s.person.first_name, *([s.person.get_gender_display(), s.person.get_nationality_display()] if personal else []),
                s.person.email, s.program, s.subject, s.fs, s.state,
            ])
        return response
    shown = list(studies[:SHOWN])
    return render(request, 'students/roster.html', {
        'studies': shown, 'count': len(shown) if len(shown) < SHOWN else studies.count(), 'q': q, 'status': status, 'statuses': STATES,
        'sort': sort, 'columns': [(title, key, 'ascending' if sort == key else 'descending' if sort == f'-{key}' else '') for title, key, _ in COLUMNS],
        'programs': request.user.within(Program.objects.ordered(), 'view_students'), 'subjects': narrowed(request.user.within(Subject.objects.all(), 'view_students'), request, 'programs'),
        'terms': Term.objects.filter(intake__isnull=False).distinct().order_by('-start'), 'semesters': range(1, 13),
        'classes': Person.objects.filter(class_studies__in=narrowed(scoped, request, 'program', 'subject')).distinct().order_by('last_name', 'first_name'),
        'cases': CASES,
        **{key: request.GET.get(key, '') for key in ['studiengang', 'fach', 'klasse', 'fs', 'beginn', 'stand']},
    })


@require('manage_students')
def letters(request):
    """The latest Bescheid of each chosen study, ready to print."""
    studies = request.user.within(Study.objects.filter(pk__in=request.GET.getlist('study')), 'manage_students').select_related('person')
    issued = Document.objects.filter(study__in=studies, message__isnull=False).select_related('message__person').order_by('created')
    latest = {d.study_id: d.message for d in issued}
    return render(request, 'core/letter.html', {'letters': latest.values(), 'missing': [s.person.name for s in studies if s.pk not in latest]})


@require('message_students')
def compose(request):
    """One message to the chosen people, e.g. everyone in their first semester: the ticked, or with ?alle every study the
    list's filter finds, beyond the rows it shows."""
    chosen = listed(request)[0].values('pk') if request.GET.get('alle') else request.POST.getlist('study') or request.GET.getlist('study')
    studies = request.user.within(Study.objects.filter(pk__in=chosen), 'message_students').select_related('person')
    people = sorted({s.person for s in studies}, key=lambda p: p.name)
    back = request.POST.get('next') or request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = ''
    form = ComposeForm(request.POST or None)
    sender = ', '.join(Office.objects.filter(user=request.user, permissions__codename='message_students').values_list('name', flat=True)) or str(request.user)
    if people and form.is_valid():
        form.send(people, sender)
        messages.success(request, f'Nachricht an {plural(len(people), "Person", "Personen")} gesendet.')
        return redirect(back or 'students:list')
    return render(request, 'students/compose.html', {'form': form, 'people': people, 'studies': studies, 'back': back, 'sender': sender})


def study(request, pk):
    person = request.user
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term', 'teacher', 'focus', 'advised'), pk=pk)
    if study.pk not in Study.reachable(person, Study.objects.filter(pk=study.pk)):
        raise PermissionDenied
    own, office, files = study.person == person, person.may('manage_students', study), person.may('view_files', study)
    personal = own or office  # health insurance, nationality, Hochschulzugang, fees, grounds of leave, Sperre: the student office only (docs/privacy.md)
    identity = personal or person.may('decide_results', study)  # birth, address, phone, documents: also the office deciding results, for Zeugnisse and Bescheide
    tree, average = study.progress()
    upcoming, late_fee, today = Term.upcoming(), Institution.get().late_fee, timezone.localdate()
    fs, late = study.semester, set()
    others = set(study.program.foci()) - {study.focus_id} if study.focus_id else set()

    def overdue(node):  # exams of modules under way that the plan recommends for an earlier semester; Schwerpunkte not chosen fold away
        node.nodes = [n for n in node.nodes if n.pk not in others]
        leaves = [n for n in node.nodes if not n.nodes]
        if node.earned and any(n.credits for n in leaves):
            late.update(n.pk for n in leaves if n.exam and not n.done and n.semester and n.semester < fs)
        for n in node.nodes:
            overdue(n)

    for root in tree:
        overdue(root)
    terms = list(study.terms.select_related('term').order_by('-term__start'))
    for t in terms:  # the Säumnisgebühr is for a late Rückmeldung, never for the semester one enrols in
        t.due = t.term.fee if t.term_id == study.start_term_id else t.term.due(t.paid or today, late_fee)
    reachable = Study.reachable(person, study.person.studies.exclude(pk=study.pk))
    options = study.options() if study.status == Study.Status.ENROLLED else []
    lost = exhausted(study) if study.status == Study.Status.ENROLLED else []  # the Prüfungsanspruch: no Rückmeldung then
    return render(request, 'students/study.html', {
        'study': study, 'today': today,
        'office': office,
        'own': own,
        'personal': personal, 'identity': identity,
        'files': own or files,  # Leistungsübersicht
        'grades': own or files or study.teacher_id == person.pk,  # the student office sees outcomes: Prüfungsanspruch, Abschluss
        'certify': own or office,  # Bescheinigungen
        'unpaying': 'manage_reregistration' in person.roles,
        'enrolling': study.missing() if office and study.status == Study.Status.ADMITTED else [],
        'rows': plan(tree, study.program.semesters),
        'semesters': range(1, study.program.semesters + 1),
        'taking': set(study.participations.filter(course__term=Term.current()).values_list('module_id', flat=True)),
        'late': late,
        'average': average,
        'earned': sum(m.earned for m in tree),
        'total': sum(m.credits for m in tree),
        'terms': terms,
        'upcoming': upcoming, 'late_fee': late_fee, 'leave_terms': LEAVE_TERMS,
        'bank': Institution.get(), 'owed': upcoming and upcoming.due(today, late_fee),
        'next': study.terms.filter(term=upcoming).first(),
        'paid': StudyTerm.objects.filter(study__person=study.person, term=upcoming, paid__isnull=False).exists(),
        'missing': remedies(study, study.person.missing()) if personal else [],
        'documents': study.documents.all() if identity else [],
        'others': [o for o in study.person.studies.exclude(pk=study.pk).exclude(status=Study.Status.REJECTED).select_related('program', 'subject') if personal or o.pk in reachable],
        'reachable': reachable,
        'change': ChangeForm(study=study) if office else None,
        'exits': EXITS, 'leaves': LEAVES,
        'exhausted': lost, 'final': lost and max(m.appeal for m in lost) < today,
        'leaving': leaving(study) if office and study.status == Study.Status.ENROLLED else [],
        'leaving_day': leaving_day(study, lost) if office and study.status == Study.Status.ENROLLED else None,
        'options': options if own else [], 'fs': fs,
        'choose_by': own and options and not study.focus_id and Study.choose_by(fs, options[0].first),
        'advising': options if study.teacher_id == person.pk and not study.focus_id else [],
        'recording': study.program.choices() if not own and study.status == Study.Status.ENROLLED and decides(person, study) else [],
        'changes': Office.holding('decide_results', study.program, linked=True),
        'leaves_to': Office.holding('manage_students', linked=True), 'attest_kind': DocumentType.system('attest'),
        'window': upcoming and upcoming.reregistration_start <= today <= upcoming.closes,  # when students ask for an Urlaubssemester
        'leave_rules': [  # what an office entry goes beyond, said before it is made
            *([f'schon {LEAVE_TERMS} Urlaubssemester, in der Regel das Höchste.'] if study.terms.filter(leave=True).count() >= LEAVE_TERMS else []),
            *(['im ersten Fachsemester nur bei Erkrankung oder Mutterschutz und Elternzeit.'] if not study.semester else []),
        ] if office else [],
        'recognise': study.status == Study.Status.ENROLLED and person.may('recognise', study),
        'compensate': study.status == Study.Status.ENROLLED and person.may('grant_compensation', study),
        'thesis': study.status == Study.Status.ENROLLED and person.may('manage_exams', study),
        'loans': study.person.loans.filter(returned=None).select_related('instrument') if personal else [],
        'history': bool(tables(person.roles)),
    })


def decides(person, study):
    """Whether the person's office records a study's Schwerpunkt: the office deciding results, which keeps the grades it counts for."""
    return person.may('decide_results', study)


@require_POST
def focus(request, pk):
    """The Schwerpunkt: the student's choice, theirs to change until it begins; the office records or changes it any time,
    with its ground, by Bescheid. First a confirmation naming it and the courses under way that count for another."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status=Study.Status.ENROLLED)
    office = decides(request.user, study)
    if not (office or study.person == request.user):
        raise PermissionDenied
    chosen = next((o for o in (study.program.choices() if office else study.options()) if str(o.pk) == request.POST.get('focus')), None)
    reason = request.POST.get('reason', '').strip()[:200]
    if chosen is None:
        messages.error(request, 'Die Wahl lässt sich hier nicht mehr ändern.' if study.focus_id else 'Bitte eine der Möglichkeiten wählen.')
    elif office and not reason:
        messages.error(request, 'Bitte den Grund angeben, etwa die Erklärung oder den Antrag, nach dem eingetragen wird.')
    elif not request.POST.get('bestaetigt'):
        chosen_by = study.program.chosen_by()
        aside = [  # course work under way that counts for another Schwerpunkt
            f'{p.course.title} zählt für {chosen_by[p.module_id].title}, nicht für {chosen.title}.'
            for p in study.participations.filter(course__term=Term.current()).select_related('course') if p.module_id in chosen_by and chosen_by[p.module_id].pk != chosen.pk
        ]
        advice = [] if office or study.advised_id == chosen.pk else [  # the Studienfachberatung the plan provides for first
            f'Die Lehrenden empfehlen {study.advised.title}.' if study.advised else 'Eine Empfehlung aus der Studienfachberatung ist noch nicht eingetragen'
            + (f'; die Beratung vereinbaren Sie mit {study.teacher} ({study.teacher.email}).' if study.teacher else '.')
        ]
        later = [] if office else [f'Ändern lässt sich die Wahl {changes(study, chosen)}.']
        title = f'{chosen.choice.title}: {chosen.title}'
        return preview(request, title, [], 'Eintragen' if office else 'Verbindlich wählen', study.get_absolute_url(), [contents(study, chosen), *aside, *advice, *later])
    else:
        study.focus = chosen
        study.save(update_fields=['focus'])
        if office:
            study.notify('schwerpunkt_eingetragen', study.get_absolute_url(), schwerpunkt=chosen.title, grund=reason)
        else:
            texts.send(
                'schwerpunkt', study.person, study.get_absolute_url(), studiengang=study.program.title, schwerpunkt=chosen.title,
                inhalt=contents(study, chosen), aendern=changes(study, chosen),
            )
        messages.success(request, f'Eingetragen: {chosen.title}. Bescheid versandt.' if office else f'Gewählt: {chosen.title}. Die Bestätigung liegt in Ihrem Postfach.')
    return redirect(study)


def contents(study, chosen):
    """What a Schwerpunkt holds: its credits and share of the Gesamtnote, then each module with its semesters and
    whether it is part of the Abschlussprüfung."""
    share = study.program.shares(chosen)

    def part(m):
        return m.pk in share and f'{share[m.pk]:.0f} % der Gesamtnote'

    lines = [f'{chosen.title}: ' + ', '.join(filter(None, [f'{chosen.credits} ECTS', part(chosen)]))]
    for m in chosen.nodes:
        semesters = m.first and (f'{m.first}. Semester' if m.first == m.last else f'{m.first}.–{m.last}. Semester')
        lines.append('– ' + ', '.join(filter(None, [f'{m.title}: {m.credits} ECTS', semesters, m.final and 'Teil der Abschlussprüfung', part(m)])))
    return '\n'.join(lines)


def changes(study, chosen):
    """Until when the student changes a Schwerpunkt themselves, and through whom after that."""
    office = Office.holding('decide_results', study.program) or 'die Hochschule'
    if chosen.first and study.semester < chosen.first:
        return f'bis zum Beginn des {chosen.first}. Fachsemesters selbst unter Ihrem Studium, danach nur über {office}'
    return f'nur über {office}'


@require_POST
def advise(request, pk):
    """The teachers' recommendation of a Schwerpunkt, from the Studienfachberatung; the choice stays with the student."""
    study = get_object_or_404(Study, pk=pk, status=Study.Status.ENROLLED, focus=None)
    if not (study.teacher_id == request.user.pk or decides(request.user, study)):
        raise PermissionDenied
    study.advised = next((o for o in study.program.choices() if str(o.pk) == request.POST.get('advised')), None)
    study.save(update_fields=['advised'])
    messages.success(request, f'Empfohlen: {study.advised.title}.' if study.advised else 'Keine Empfehlung.')
    return redirect(study)


def next_matriculation():
    """The next free Matrikelnummer, counting on from the first the Hochschule set."""
    numbers = [int(n) for n in Person.objects.exclude(matriculation=None).values_list('matriculation', flat=True) if n.isdigit()]
    return max(numbers, default=Institution.get().matriculation_start - 1) + 1


def matriculate(study):
    """Immatrikulation of an admitted applicant with nothing missing, the fee paid among it: Matrikelnummer, first
    registered term, Bescheid."""
    person, domain = study.person, Institution.get().student_mail
    with transaction.atomic():
        if not person.matriculation:
            person.matriculation = str(next_matriculation())
        if domain and not person.email.endswith(f'@{domain}'):  # a Hochschul address; the private one is kept for later
            person.private_email, name, n = person.private_email or person.email, handle(person.first_name, person.last_name), 1
            while Person.objects.filter(email=f'{name}{n if n > 1 else ""}@{domain}').exists():
                n += 1
            person.email = f'{name}{n if n > 1 else ""}@{domain}'
        person.save(update_fields=['matriculation', 'email', 'private_email'])
        StudyTerm.objects.get_or_create(study=study, term=study.start_term)
        study.set_status(Study.Status.ENROLLED)
    study.notify('immatrikulation', study.get_absolute_url())


@require('manage_students')
@require_POST
def enrol(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'start_term'), 'manage_students'), pk=pk, status=Study.Status.ADMITTED)
    if missing := study.missing():
        messages.error(request, f'Noch nicht immatrikuliert: {", ".join(missing)}.')
        return redirect(study.get_absolute_url())
    if not request.POST.get('bestaetigt'):
        wording = study.wording('immatrikulation', matrikelnummer=study.person.matriculation or next_matriculation())
        return preview(request, 'Immatrikulation', [('Immatrikulation', [study.person], wording)], 'Immatrikulieren und Bescheid versenden', study.get_absolute_url())
    matriculate(study)
    messages.success(request, f'{study.person} immatrikuliert, Matrikelnummer {study.person.matriculation}. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def enrol_many(request):
    """Immatrikulation of the ticked applicants whose places are taken up and complete."""
    studies = request.user.within(Study.objects.filter(pk__in=request.POST.getlist('study'), status=Study.Status.ADMITTED), 'manage_students')
    ready = [s for s in studies.select_related('person', 'program', 'subject', 'start_term').order_by('person__last_name') if not s.missing()]
    back = request.POST.get('next', '')
    back = back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else reverse('admissions:board')
    if not ready:
        messages.error(request, 'Niemand angekreuzt, für den alles vorliegt.')
    elif not request.POST.get('bestaetigt'):
        people = [f'{s.person.name} · {s.subject} · {s.program.degree}' for s in ready]
        wording = ready[0].wording('immatrikulation', matrikelnummer=ready[0].person.matriculation or next_matriculation())
        return preview(request, 'Immatrikulation', [('Immatrikulation', people, wording)], 'Immatrikulieren und Bescheide versenden', back)
    else:
        for study in ready:
            matriculate(study)
        messages.success(request, f'{plural(len(ready), "Person", "Personen")} immatrikuliert. Bescheide versandt.')
    return redirect(back)


@require('manage_students')
@require_POST
def fee_paid(request, pk):
    """The Semesterbeitrag of an admitted applicant has arrived: booked by hand with day, amount and Beleg, as they have no
    Matrikelnummer yet to find it by in the bank statement."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('start_term'), 'manage_students'), pk=pk, status=Study.Status.ADMITTED)
    form, fee = PaymentForm(request.POST), study.start_term.fee
    if not form.is_valid():
        messages.error(request, ' '.join(f'{form.fields[name].label}: {" ".join(errors)}' for name, errors in form.errors.items()))
    elif form.cleaned_data['amount'] < fee:
        messages.error(request, f'Zu wenig: der Semesterbeitrag beträgt {number_format(fee, 2)} €.')
    else:
        StudyTerm.objects.filter(study=study, term=study.start_term, paid=None).update(**form.cleaned_data)
        messages.success(request, f'Semesterbeitrag verbucht: {number_format(form.cleaned_data["amount"], 2)} € am {date_format(form.cleaned_data["paid"], "j.n.Y")}.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else study.get_absolute_url())


@require('manage_students')
def insurers(request):
    """Studierendenmeldeverfahren (students/smv.py): the insurers' reports ConCampus found nobody for, to book by hand or drop;
    then every report in and out, newest first. An office for the whole Hochschule only: unbooked reports may be about
    anyone."""
    if not request.user.everywhere('manage_students'):
        raise PermissionDenied
    reports, State = InsuranceReport.objects.select_related('person', 'term'), InsuranceReport.State
    unbooked = reports.filter(state=State.OPEN, reason__in=InsuranceReport.INCOMING)
    if request.method == 'POST':
        report = get_object_or_404(reports, pk=request.POST.get('meldung'))
        open_ = report.state == State.OPEN
        if report.incoming and open_ and (person := next((p for p in smv.candidates(report) if str(p.pk) == request.POST.get('person')), None)):
            smv.book(report, person, request.user)
            messages.success(request, f'{report.get_reason_display()} verbucht für {person}.')
        elif report.incoming and open_ and 'verwerfen' in request.POST:
            report.state, report.by = State.DROPPED, request.user
            report.save(update_fields=['state', 'by'])
            messages.success(request, f'{report.get_reason_display()} der {report.insurer} verworfen.')
        elif not report.incoming and 'stornieren' in request.POST and report.state in (State.OPEN, State.DONE) and not report.cancels_id and not report.cancellations.exists():
            done = smv.cancel(report, request.user)
            messages.success(request, 'Meldung zurückgezogen, bevor sie verschickt wurde.' if done is report else 'Storno erstellt; es geht mit dem nächsten Versand.')
        elif not report.incoming and 'erneut' in request.POST and report.state == State.FAILED:
            smv.again(report, request.user)
            messages.success(request, f'{report.get_reason_display()} neu erstellt, mit der Krankenkasse aus den Stammdaten.')
        return redirect(request.get_full_path())
    q, reason, state = request.GET.get('q', '').strip(), request.GET.get('art', ''), request.GET.get('stand', '')
    shown = reports.exclude(pk__in=unbooked).prefetch_related('cancellations')
    if reason in InsuranceReport.Reason.values:
        shown = shown.filter(reason=reason)
    if state in State.values:
        shown = shown.filter(state=state)
    if (who := request.GET.get('person', '')).isdigit():
        shown = shown.filter(person=who)
    shown = matching(shown, q, 'person__first_name', 'person__last_name', 'person__matriculation', 'last_name', 'insurer', starts=True)
    rows = list(shown[:SHOWN])
    files = {}
    for s in Study.objects.filter(person__in={r.person_id for r in rows}).order_by('start_term__start'):
        files[s.person_id] = s  # each person's latest study, for the link to their file
    return render(request, 'students/insurers.html', {
        'unbooked': [(r, smv.candidates(r)) for r in unbooked], 'reports': [(r, files.get(r.person_id)) for r in rows], 'count': shown.count(),
        'q': q, 'reason': reason, 'state': state, 'reasons': InsuranceReport.Reason.choices, 'states': State.choices, 'today': timezone.localdate(),
        'waiting': reports.filter(state=State.OPEN).exclude(reason__in=InsuranceReport.INCOMING).count(),
    })


@require('manage_students')
@require_POST
def checked(request, pk):
    """A Hochschulzugangsberechtigung from abroad is assessed and opens the study."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.entrance_checked = timezone.localdate()
    study.person.save(update_fields=['entrance_checked'])
    messages.success(request, 'Hochschulzugang als geprüft verbucht.')
    return redirect(study.get_absolute_url())


@require_POST
def leave(request, pk):
    """Urlaubssemester for the upcoming term. Students ask within the Rückmeldung, never afterwards, for at most two
    semesters, and in the first Fachsemester only for illness or Mutterschutz; the office decides beyond that."""
    study = get_object_or_404(Study, pk=pk, status=Study.Status.ENROLLED)
    office = request.user.may('manage_students', study)
    if not (study.person == request.user or office):
        raise PermissionDenied
    term = Term.upcoming()
    registration = StudyTerm.objects.filter(study=study, term=term).first()
    leave = not (registration and registration.leave)
    reason = request.POST.get('reason', '') if leave else ''
    rule = leave and (  # what a student may ask for by themselves; the office decides beyond it, reminded
        f'Urlaubssemester für das {term} lassen sich vom {date_format(term.reregistration_start, "j.n.")} bis zum {date_format(term.closes, "j.n.Y")} beantragen.' if not office and not term.reregistration_start <= timezone.localdate() <= term.closes
        else f'In der Regel sind höchstens {LEAVE_TERMS} Urlaubssemester möglich; ein weiteres nur auf schriftlichen Antrag.' if study.terms.filter(leave=True).count() >= LEAVE_TERMS
        else 'Im ersten Fachsemester ist eine Beurlaubung nur bei Erkrankung oder Mutterschutz und Elternzeit möglich.' if not study.semester and reason not in FIRST_TERM_LEAVES
        else None
    )
    if leave and reason not in dict(LEAVES) or rule and not office:
        messages.error(request, rule if reason in dict(LEAVES) else 'Bitte den Grund der Beurlaubung wählen.')
        return redirect(study.get_absolute_url())
    if leave and (file := request.FILES.get('file')):  # an attest, say: for the student office only
        if refusal := DocumentType.system('attest').refuses(file) or (not request.POST.get('consent') and 'Ohne Einwilligung lässt sich der Nachweis nicht hochladen.'):
            messages.error(request, refusal)
            return redirect(study.get_absolute_url())
        Document.hand_in(study, DocumentType.system('attest'), file, Consent.give(study.person, 'zum Urlaubssemester', request.user))
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    registration.leave, registration.leave_reason = leave, reason
    registration.save(update_fields=['leave', 'leave_reason'])
    if leave:
        study.notify('beurlaubung', study.get_absolute_url(), rueckmeldesemester=term, grund=dict(LEAVES)[reason])
    else:
        study.notify('beurlaubung_aufgehoben', study.get_absolute_url(), rueckmeldesemester=term)
    messages.success(request, f'{term}: {"Urlaubssemester eingetragen" if leave else "Urlaubssemester zurückgenommen"}, Bescheid versandt.')
    if rule:
        messages.warning(request, f'Als Ausnahme eingetragen: {rule} Antrag und Nachweis gehören zur Akte.')
    return redirect(study.get_absolute_url())


@require('manage_reregistration')
@require_POST
def unpay(request, pk):
    """Take back a Semesterbeitrag booked by mistake, for a semester not over yet; the person hears that it stands open again."""
    registration = get_object_or_404(StudyTerm.objects.select_related('study__person', 'term'), pk=pk, paid__isnull=False, term__end__gte=timezone.localdate())
    paid_on = registration.paid
    registration.paid, registration.amount, registration.reference = None, None, ''
    registration.save(update_fields=['paid', 'amount', 'reference'])
    texts.send('zahlung_zurueckgenommen', registration.study.person, registration.study.get_absolute_url(), rueckmeldesemester=registration.term, datum=date_format(paid_on, 'j.n.Y'))
    messages.success(request, f'Zahlung vom {date_format(paid_on, "j.n.Y")} für das {registration.term} zurückgenommen; {registration.study.person} ist benachrichtigt.')
    return redirect(registration.study.get_absolute_url())


def remedies(study, missing):
    """Each thing that holds the Rückmeldung back, with what to do about it and whom to ask: [(what, what to do)]."""
    found = []
    for reason in missing:
        if reason.startswith('Sperre'):
            found.append((reason, format_html('aufzuheben über {}', Office.holding('manage_students', study.program, linked=True))))
        elif reason.startswith('Versicherungsmeldung'):
            number = Institution.get().sender_number
            found.append((reason, 'bitte Ihre Krankenkasse, Ihren Versicherungsstatus elektronisch an die Hochschule zu melden' + (f', mit der Absendernummer {number}' if number else '')))
        elif reason.startswith('Beitragsrückstand'):
            found.append((reason, 'bitte bei der Krankenkasse begleichen; sie meldet die Zahlung der Hochschule'))
        elif reason.startswith('Leihinstrument'):
            found.append((reason, format_html('zurückzugeben bei {}', Office.holding('manage_instruments', linked=True))))
        else:
            found.append((reason, ''))
    return found


def leaving(study):
    """The Laufzettel: what is still open before someone leaves, each with the offices it is for."""
    from exams.views import achievements

    found = []
    if study.issued is None and achievements(study)['finished']:
        found.append(('Zeugnis noch nicht ausgestellt', Office.holding('decide_results', study.program)))
    for loan in study.person.loans.filter(returned=None).select_related('instrument'):
        found.append((f'Leihinstrument zurückgeben: {loan.instrument.name}', Office.holding('manage_instruments')))
    if unpaid := study.terms.filter(paid=None, term__start__lte=timezone.localdate()).exclude(leave=True).select_related('term').first():
        found.append((f'Semesterbeitrag {unpaid.term} offen', Office.holding('manage_reregistration')))
    if study.person.hold:
        found.append((f'Sperre: {study.person.hold}', Office.holding('manage_students')))
    return found


def leaving_day(study, lost):
    """The last day enrolled, unless asked otherwise: after a final fail the day the Bescheid became final, or the day
    before this semester if that was earlier; after the Abschluss the end of the semester; otherwise today."""
    term = Term.current()
    if lost:
        return max(max(m.appeal for m in lost) + timedelta(days=1), term.start - timedelta(days=1))
    return term.end if study.issued else timezone.localdate()


@require('manage_students')
@require_POST
def exmatriculate(request, pk):
    """Exmatrikulation with the last day enrolled: on request the day asked for, up to the end of the semester; after the
    Abschluss the end of the semester; after a final fail once that is final; otherwise today; the day before this
    semester for someone who should not be in it. Semesters after that day are dropped, a fee paid for them is refunded
    by the Studierendenwerk. The Bescheid first as a preview, with what is still open (the Laufzettel)."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    reason, term, asked = request.POST.get('reason', ''), Term.current(), request.POST.get('zum', '').strip()
    on, earliest = dates.day(asked) if asked else term.end if reason == '1' else timezone.localdate(), term.start - timedelta(days=1)
    lost = exhausted(study) if reason == '8' else []
    if reason not in dict(EXITS):
        messages.error(request, 'Bitte den Grund der Exmatrikulation wählen.')
    elif reason == '8' and (not lost or max(m.appeal for m in lost) >= timezone.localdate()):  # only on the evidence, and once it is final
        messages.error(request, 'Kein endgültig nicht bestandenes Modul.' if not lost else f'Erst nach der Widerspruchsfrist, ab dem {date_format(max(m.appeal for m in lost) + timedelta(days=1), "j.n.Y")}.')
    elif not on or not earliest <= on <= term.end:
        messages.error(request, f'Bitte einen Tag vom {date_format(earliest, "j.n.Y")} bis zum {date_format(term.end, "j.n.Y")} angeben.')
    else:
        still, later = leaving(study), study.terms.filter(term__start__gt=on)
        paid = [r.term for r in later.select_related('term') if r.paid]
        after = ['erstattung'] * bool(paid) + ['auslaenderbehoerde'] * study.person.permit + ['rechtsbehelf'] * (reason in OFFICIAL)
        insurer = study.person.insurer and f'Die {study.person.insurer} erhält die Meldung der Exmatrikulation im Studierendenmeldeverfahren.'
        values = {'grund': dict(EXITS)[reason], 'zum': date_format(on, 'j. F Y'), 'rueckmeldesemester': ', '.join(map(str, paid))}
        if not request.POST.get('bestaetigt'):
            notes = [f'Noch offen: {what}{f" ({who})" if who else ""}' for what, who in still] + [insurer] * bool(insurer)
            return preview(request, f'Exmatrikulation, letzter Tag {date_format(on, "j.n.Y")}', [('Exmatrikulation', [study.person], study.wording('exmatrikulation', *after, **values))], 'Exmatrikulieren und Bescheid versenden', study.get_absolute_url(), notes)
        with transaction.atomic():
            later.filter(paid=None).delete()
            later.update(withdrawn=True)  # what was paid stays on record, for its refund
            study.set_status(Study.Status.GRADUATED if reason == '1' else Study.Status.LEFT, reason, on)
        study.notify('exmatrikulation', after=after, **values)
        messages.success(request, f'{study.person} mit Ablauf des {date_format(on, "j.n.Y")} exmatrikuliert. Bescheid versandt.' + (f' {insurer}' if insurer else '') + (f' Noch offen: {"; ".join(f"{what} ({who})" if who else what for what, who in still)}.' if still else ''))
    return redirect(study.get_absolute_url())


DELIVERY = {'post': 'per Post, noch nicht gedruckt', 'gedruckt': 'per Post, gedruckt', 'elektronisch': 'elektronisch'}


@require('manage_students')
def notices(request):
    """Versandte Bescheide: every Bescheid the office's studies received, newest first, how each reached its person; by
    the text it came from and how it is delivered. Those going by post print all at once, each a letter, and are marked
    printed afterwards."""
    q, kind, delivery = request.GET.get('q', '').strip(), request.GET.get('art', ''), request.GET.get('zustellung', '')
    sent = (
        Document.objects.filter(kind='notice', study__in=request.user.within(Study.objects.all(), 'manage_students'))
        .select_related('person', 'study__program', 'study__subject', 'message').order_by('-created')
    )
    kinds = sorted(((k, texts.TEXTS[k].title) for k in set(sent.exclude(template='').values_list('template', flat=True)) if k in texts.TEXTS), key=lambda k: k[1])
    if kind:
        sent = sent.filter(template=kind)
    by_post = Q(person__electronic=None)
    sent = sent.filter({'post': by_post & Q(printed=None), 'gedruckt': by_post & Q(printed__isnull=False), 'elektronisch': ~by_post}.get(delivery, Q()))
    sent = matching(sent, q, 'person__first_name', 'person__last_name', 'person__matriculation', starts=True)
    if 'drucken' in request.GET:
        shown = list(sent[:SHOWN])
        return render(request, 'core/letter.html', {
            'letters': [d.message for d in shown if d.message], 'printing': [d.pk for d in shown if not (d.person.electronic or d.printed)],
        })
    return render(request, 'students/notices.html', {
        'sent': sent[:SHOWN], 'count': sent.count(), 'kinds': kinds, 'kind': kind, 'q': q, 'deliveries': DELIVERY, 'delivery': delivery,
    })


@require('manage_students')
@require_POST
def printed(request):
    """Bescheide by post marked printed, after printing them all."""
    done = Document.objects.filter(
        pk__in=request.POST.getlist('bescheid'), kind='notice', printed=None, study__in=request.user.within(Study.objects.all(), 'manage_students'),
    ).update(printed=timezone.now())
    messages.success(request, f'{plural(done, "Bescheid", "Bescheide")} als gedruckt markiert.')
    return redirect(f"{reverse('students:notices')}?zustellung=post")


@require('manage_students')
@require_POST
def change(request, pk):
    """Fachwechsel: end this study, start the new one now with the semesters so far credited, by Bescheid; first as a
    preview of what ends and what begins."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject'), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    form = ChangeForm(request.POST, study=study)
    if not form.is_valid():
        messages.error(request, ' '.join(form.non_field_errors()) or 'Bitte Studiengang und Hauptfach wählen.')
        return redirect(study.get_absolute_url())
    term = Term.current()
    new = Study(
        person=study.person, program=form.cleaned_data['program'], subject=form.cleaned_data['subject'],
        start_term=term, status=Study.Status.ENROLLED, teacher=study.teacher, prior_terms=study.semester - 1,
    )
    values = {'fachsemester': study.semester, 'bisher': f'{study.program.title}, Hauptfach {study.subject}'}
    if not request.POST.get('bestaetigt'):
        notes = [
            f'Endet heute: {study.program.title} · {study.subject}. Beginnt: {new.program.title} · {new.subject} im {study.semester}. Fachsemester, die bisherigen Semester angerechnet.',
            'Leistungen rechnet der Prüfungsausschuss auf Antrag an (Prüfungen › Anerkennung).',
        ]
        return preview(request, 'Fachwechsel', [('Fachwechsel', [study.person], new.wording('fachwechsel', **values))], 'Fachwechsel eintragen und Bescheid versenden', study.get_absolute_url(), notes)
    with transaction.atomic():
        new.save()
        StudyTerm.objects.create(study=new, term=term, paid=timezone.localdate())
        study.set_status(Study.Status.LEFT)
    new.notify('fachwechsel', **values)
    messages.success(request, 'Fachwechsel eingetragen, Bescheid versandt.')
    return redirect(new.get_absolute_url())


@require('manage_students')
@require_POST
def hold(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.hold = request.POST.get('hold', '').strip()
    study.person.save(update_fields=['hold'])
    messages.success(request, 'Sperre gesetzt.' if study.person.hold else 'Sperre aufgehoben.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def restrict(request, pk):
    """Restriction of processing on the person's request (Art. 18 DSGVO): set with its ground, or lifted once they have been told."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    person, ground = study.person, request.POST.get('restriction', '')
    person.restriction = ground if ground in Person.Restriction.values else ''
    person.restricted = (person.restricted or timezone.localdate()) if person.restriction else None
    person.save(update_fields=['restriction', 'restricted'])
    messages.success(request, 'Verarbeitung eingeschränkt.' if person.restriction else 'Einschränkung aufgehoben.')
    return redirect(study.get_absolute_url())


def book(term, enrolled, paid):
    """Book the Semesterbeitrag {person: (day, amount, Beleg)} for all their studies and tell each where their Rückmeldung
    stands."""
    with transaction.atomic():
        for s in enrolled.filter(person__in=paid):
            day, amount, reference = paid[s.person]
            StudyTerm.objects.update_or_create(study=s, term=term, defaults={'paid': day, 'amount': amount, 'reference': reference[:140]})
    for p in paid:
        if missing := p.missing():
            texts.send('rueckmeldung_offen', p, rueckmeldesemester=term, fehlt=', '.join(missing))
        else:
            texts.send('rueckmeldung', p, rueckmeldesemester=term)


@require('manage_reregistration')
def reregistration(request):
    """Rückmeldung for the upcoming term: book payments by hand or from the bank statement; after the Nachfrist,
    exmatriculate whoever has not paid, at the end of the semester under way."""
    term, today = Term.upcoming(), timezone.localdate()
    if term is None:
        return without_term(request, 'das nächste Semester, für das sich Studierende zurückmelden')
    enrolled = (
        Study.objects.filter(status=Study.Status.ENROLLED).select_related('person', 'program', 'subject').order_by('person__last_name')
        .prefetch_related(Prefetch('person__loans', Loan.objects.filter(returned=None).select_related('instrument')))
    )
    registrations = {r.study_id: r for r in StudyTerm.objects.filter(term=term)}
    paid = {r.study.person_id for r in StudyTerm.objects.filter(term=term, paid__isnull=False).select_related('study')}
    late_fee = Institution.get().late_fee
    if request.method == 'POST':
        if 'pay' in request.POST:
            try:
                day, reference = dates.received(request.POST.get('paid', '')), request.POST.get('reference', '').strip()
            except forms.ValidationError as error:
                messages.error(request, f'Eingegangen am: {error.messages[0]}')
                return redirect('students:reregistration')
            if not reference:
                messages.error(request, 'Bitte den Beleg oder Verwendungszweck angeben, mit dem die Zahlung einging.')
                return redirect('students:reregistration')
            people = {s.person: (day, term.due(day, late_fee), reference) for s in enrolled.filter(person__in=request.POST.getlist('person'))}
            book(term, enrolled, people)
            messages.success(request, f'Zahlung für {plural(len(people), "Person", "Personen")} verbucht, je {number_format(term.due(day, late_fee), 2)} € am {date_format(day, "j.n.Y")}.')
        elif statement := request.FILES.get('statement'):  # read first, booked once checked
            unpaid = {s.person.matriculation: s.person for s in enrolled.exclude(person__in=paid)}
            request.session['kontoauszug'] = [
                {'day': day.isoformat(), 'amount': str(amount), 'text': text[:300], 'person': person and person.pk, 'short': bool(person and amount < term.due(day, late_fee))}
                for day, amount, text in payments.credits(statement.read())
                for person in [next((unpaid[n] for n in payments.numbers(text) if n in unpaid), None)]
            ]
            return redirect(f"{reverse('students:reregistration')}?auszug=1")
        elif 'buchen' in request.POST:
            lines, picked = request.session.pop('kontoauszug', []), set(request.POST.getlist('zeile'))
            people = Person.objects.in_bulk([line['person'] for line in lines if line['person']])
            found = {
                people[line['person']]: (dates.day(line['day']), Decimal(line['amount']), line['text'])
                for i, line in enumerate(lines) if str(i) in picked and line['person'] in people and not line['short'] and line['person'] not in paid
            }
            book(term, enrolled, found)
            messages.success(request, f'{plural(len(found), "Zahlung", "Zahlungen")} verbucht.')
        elif 'exmatriculate' in request.POST and today > term.closes:  # a finished study or a lost Prüfungsanspruch leaves on its own ground
            unpaid = list(enrolled.exclude(person__in=paid))
            finished = [s for s in unpaid if s.issued]
            lost = [s for s in unpaid if not s.issued and exhausted(s)]
            rest = [s for s in unpaid if s not in finished and s not in lost]
            ends = term.start - timedelta(days=1)  # with the semester under way
            values = {'rueckmeldesemester': term, 'zum': date_format(ends, 'j. F Y')}
            if not request.POST.get('bestaetigt'):
                notes = [
                    *([f'Nicht dabei, mit ausgestelltem Zeugnis: {", ".join(str(s.person) for s in finished)}. Sie werden einzeln mit dem Grund „Abschluss des Studiums“ exmatrikuliert.'] if finished else []),
                    *([f'Nicht dabei, mit erloschenem Prüfungsanspruch: {", ".join(str(s.person) for s in lost)}. Sie werden einzeln mit dem Grund „Prüfung endgültig nicht bestanden“ exmatrikuliert.'] if lost else []),
                ]
                if not rest:
                    messages.info(request, ' '.join(['Niemand, der wegen fehlender Rückmeldung zu exmatrikulieren ist.', *notes]))
                    return redirect('students:reregistration')
                return preview(
                    request, f'Exmatrikulation ohne Rückmeldung, letzter Tag {date_format(ends, "j.n.Y")}',
                    [('Keine Rückmeldung', [s.person for s in rest], rest[0].wording('exmatrikulation_rueckmeldung', **values))],
                    f'{plural(len(rest), "Studium", "Studien")} beenden und Bescheide versenden', reverse('students:reregistration'), notes,
                )
            for s in rest:
                s.set_status(Study.Status.LEFT, '7', ends)
                s.notify('exmatrikulation_rueckmeldung', **values)
            messages.success(request, f'{plural(len(rest), "Studium", "Studien")} wegen fehlender Rückmeldung zum {date_format(ends, "j.n.Y")} beendet. Bescheide versandt.')
        return redirect('students:reregistration')
    statement = request.session.get('kontoauszug', []) if request.GET.get('auszug') else []
    people = Person.objects.in_bulk([line['person'] for line in statement if line['person']])
    statement = [line | {'who': people.get(line['person']), 'day': dates.day(line['day']), 'amount': Decimal(line['amount'])} for line in statement]
    rows = [(s, registrations.get(s.pk), s.person_id in paid, s.person.missing()) for s in enrolled]
    states = {'offen': ('offen', lambda row: not row[2]), 'bezahlt': ('bezahlt', lambda row: row[2]), 'gesperrt': ('gesperrt', lambda row: row[3])}  # a Sperre or a missing Nachweis holds the Rückmeldung back
    stand, q = request.GET.get('stand', ''), request.GET.get('q', '').strip()
    words = fold(q).split()
    shown = [
        row for row in rows if (stand not in states or states[stand][1](row))
        and all(w in fold(f'{row[0].person.name} {row[0].person.matriculation}') for w in words)
    ]
    counts = {key: len({row[0].person_id for row in rows if test(row)}) for key, (_, test) in states.items()}  # people: the fee is theirs
    return render(request, 'students/reregistration.html', {
        'term': term, 'rows': shown, 'total': len({row[0].person_id for row in rows}), 'open': counts['offen'], 'blocked': counts['gesperrt'], 'overdue': today > term.closes,
        'late_fee': late_fee, 'statement': statement,
        'q': q, 'stand': stand, 'states': [(key, label, counts[key]) for key, (label, _) in states.items()],
    })


def periods(request, pk):
    """Studienzeitbescheinigung: every semester the person was registered here, on leave or not, and how each study ended;
    asked for by pension insurance long after."""
    study = get_object_or_404(Study.objects.select_related('person'), pk=pk)
    person = study.person
    if not (person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    today = timezone.localdate()
    studies = list(person.studies.filter(terms__isnull=False).distinct().select_related('program', 'subject').order_by('start_term__start'))
    rows = []
    for s in studies:
        semester = s.prior_terms
        for registration in s.terms.filter(term__start__lte=today, withdrawn=False).select_related('term'):
            semester += not registration.leave
            rows.append((registration.term, s, registration, semester))
    rows.sort(key=lambda row: row[0].start)
    if 'xml' in request.GET:
        return data(request, lambda: xhochschule.periods(study, rows, first_enrolment(person)), f'studienzeitbescheinigung-{person.matriculation}')
    return render(request, 'students/periods.html', {
        'study': study, 'person': person, 'studies': studies, 'rows': rows, 'count': len({row[0] for row in rows}),
        'leave': len({term for term, _, registration, _ in rows if registration.leave}), 'today': today, 'sender': texts.block('absender_bescheinigungen'),
    })


def exmatriculation(request, pk):
    """Exmatrikulationsbescheinigung: when someone studied here and why it ended, as health insurers and job centres ask."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status__in=Study.FORMER)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    if 'xml' in request.GET:
        return data(request, lambda: xhochschule.exmatriculation(study, first_enrolment(study.person)), f'exmatrikulationsbescheinigung-{study.person.matriculation}')
    return render(request, 'students/exmatriculation.html', {
        'study': study, 'today': timezone.localdate(), 'sender': texts.block('absender_bescheinigungen'),
        'reason': study.exit_reason == '1' or request.GET.get('grund'),  # often handed on: the ground only where asked for
    })


def certificate(request, pk):
    """Immatrikulationsbescheinigung for the current term; with ?bafoeg the version of § 9 BAföG, which stands in for
    Formblatt 2: what its part C asks of a Hochschule besides."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status=Study.Status.ENROLLED)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    term, coming = Term.current(), Term.upcoming()  # the coming semester too, once its Rückmeldung is paid: for a BAföG Folgeantrag
    ahead = coming and request.GET.get('semester') == str(coming.pk) and study.terms.filter(term=coming, paid__isnull=False).first()
    term, first = coming if ahead else term, first_enrolment(study.person)
    registration, fs, code = study.terms.filter(term=term).first(), study.semester + (bool(ahead) and not ahead.leave), f'{study.pk}-{term.pk}-{signature(study.pk, term.pk)}'
    check = request.build_absolute_uri(reverse('students:verify'))
    if 'xml' in request.GET:
        return data(
            request, lambda: xhochschule.enrolment(study, term, registration, fs, first, (code, f'{check}?code={code}'), 'bafoeg' in request.GET),
            f'immatrikulationsbescheinigung-{study.person.matriculation}-{slugify(term.name)}',
        )
    return render(request, 'students/certificate.html', {
        'study': study, 'term': term, 'registration': registration, 'today': timezone.localdate(), 'fs': fs, 'hs': study.university_semester + bool(ahead),
        'bafoeg': 'bafoeg' in request.GET, 'first': first, 'sender': texts.block('absender_bescheinigungen'), 'code': code, 'check': check,
    })


def first_enrolment(person):
    """The semester of the first enrolment anywhere: as the record names one elsewhere, else the first registered here."""
    first = StudyTerm.objects.filter(study__person=person, withdrawn=False).order_by('term__start').select_related('term').first()
    return person.first_term or (first and first.term)


def data(request, build, name):
    """A Bescheinigung as an XHochschule file to download; where the record lacks what the standard asks, back to the page
    with what is missing."""
    try:
        content = build()
    except DocumentInvalid as error:
        messages.error(request, f'Als XHochschule-Datei noch nicht möglich, in der Akte fehlt etwas: {error}')
        return redirect(request.path)
    response = HttpResponse(content, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{name}.xml"'
    return response


def signature(study, term):
    """The Prüfcode of an Immatrikulationsbescheinigung: nobody makes one up for another study or semester."""
    return salted_hmac('immatrikulationsbescheinigung', f'{study}-{term}').hexdigest()[:10].upper()


@login_not_required
def verify(request):
    """Whether an Immatrikulationsbescheinigung is genuine, for whoever it was handed to: its Prüfcode names the study
    and the semester. It shows what the Bescheinigung says, nothing more."""
    code, study, term = request.GET.get('code', '').strip().upper(), None, None
    if found := re.fullmatch(r'(\d+)-(\d+)-([0-9A-F]{10})', code):
        if constant_time_compare(found[3], signature(found[1], found[2])):
            study = Study.objects.select_related('person', 'program', 'subject').filter(pk=found[1]).first()
            term = Term.objects.filter(pk=found[2]).first()
    return render(request, 'students/verify.html', {
        'code': code, 'study': study, 'term': term, 'registered': bool(study and term and study.terms.filter(term=term, study__status__in=[Study.Status.ENROLLED, *Study.FORMER]).exists()),
    })


@require('dispose_files', 'assess_files')
def disposal(request):
    """Aussonderung (students/disposal.py): the student office offers the files kept long enough to the archive; the
    archive takes over a copy of those worth keeping and assesses the rest as not; the night after, each file assessed
    is cut down to its Grunddaten."""
    roles = request.user.roles
    if request.method == 'POST':
        if request.POST.get('anbieten') and 'dispose_files' in roles:
            messages.success(request, f'{plural(offer(due(), request.user), "Akte", "Akten")} dem Archiv angeboten.')
        elif request.POST.get('zuruecknehmen') and 'assess_files' in roles:  # until the night cuts them down
            n = Disposal.objects.filter(assessed__isnull=False, reduced=None).update(assessed=None, archival=None, assessed_by=None)
            messages.success(request, f'{plural(n, "Bewertung", "Bewertungen")} zurückgenommen; die Akten sind wieder angeboten.')
        elif (archival := request.POST.get('archivwuerdig')) and 'assess_files' in roles:
            n = assess(request.POST.getlist('person'), archival == 'ja', request.user)
            messages.success(request, f'{plural(n, "Akte", "Akten")} bewertet; heute Nacht werden sie bis auf die Grunddaten gelöscht.')
        return redirect('students:disposal')
    last = {p.pk: p.last for p in due().annotate(last=Max('studies__since'))}
    done = Disposal.objects.exclude(assessed=None).values('assessed__year').annotate(n=Count('pk'), kept=Count('pk', filter=Q(archival=True))).order_by('-assessed__year')
    offered = Person.objects.filter(disposal__isnull=False, disposal__assessed=None).select_related('disposal').prefetch_related(
        Prefetch('studies', Study.objects.filter(status__in=Study.FORMER).select_related('program', 'subject', 'start_term')),
    ).order_by('last_name', 'first_name')
    return render(request, 'students/disposal.html', {
        'due': sorted(Counter(day.year for day in last.values()).items()), 'due_total': len(last), 'offered': offered[:SHOWN], 'offered_total': offered.count(),
        'done': done, 'institution': Institution.get(), 'assessing': 'assess_files' in roles, 'offering': 'dispose_files' in roles,
        'pending': Disposal.objects.filter(assessed__isnull=False, reduced=None).select_related('person'),
    })


@require('assess_files')
def handover(request, pk):
    """A copy of an offered file for the archive to take over."""
    person = get_object_or_404(Person, pk=pk, disposal__reduced=None)
    Export.log(request.user, f'Akte {person} für das Archiv', 1)
    return FileResponse(dossier(person), as_attachment=True, filename=f'akte-{person.matriculation or person.pk}.zip')
