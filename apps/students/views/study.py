"""A study's Akte: the page, Schwerpunkt, advice, Fachwechsel, holds and restrictions."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from core import texts
from core.access import require
from core.history import tables
from core.models import Institution, Office, Person, Term
from core.shortcuts import preview
from courses.models import Course, Format
from curriculum.models import Program, Subject, coverage
from curriculum.plans import columns, plan
from exams.results import exhausted
from ..leaving import leaving
from ..models import EXITS, LEAVES, DocumentType, Study, StudyTerm
from ..trail import above
from .enrolment import leaving_day


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


def findable(study, tree, choices, taking):
    """The modules of a student's plan that lead to their courses this term, in the Vorlesungsverzeichnis narrowed to
    them: those with something open for this semester or the next, and free electives (a Wahlkatalog); never a
    Schwerpunkt or its choice, nor what lies under a Schwerpunkt before one is chosen. Only where a course this term is
    there to take."""
    offered = set(Course.modules.through.objects.filter(course__term=Term.current(), course__kind=Course.Kind.COURSE, course__cancelled=None).values_list('module', flat=True))
    options, areas = {o.pk for o in choices}, {o.choice.pk for o in choices}
    soon, found = {study.semester, study.semester + 1}, set()

    def leaves(node):
        return [node] if not node.nodes else [leaf for child in node.nodes for leaf in leaves(child)]

    def takeable(nodes):
        return any(n.pk in offered and n.pk not in taking and not n.done for n in nodes)

    def visit(node, hidden):
        hidden = hidden or node.pk in options and not study.focus_id
        if node.done or not node.nodes:
            return
        if not hidden and node.pk not in options | areas:
            due = [n for n in node.nodes if not n.nodes and not n.done and n.semester in soon]
            if takeable(due) or node.offered > node.credits and takeable(leaves(node)):
                found.add(node.pk)
        for child in node.nodes:
            visit(child, hidden)

    for root in tree:
        visit(root, False)
    return found


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
    taking = set(study.participations.filter(course__term=Term.current()).values_list('module_id', flat=True))
    choices = study.program.choices()
    rows = plan(tree, study.program.semesters, choices, taking, late)
    covered, outstanding = coverage(tree, set(study.program.foci()) - {study.focus_id} if study.focus_id else ())
    grades = own or files or study.teacher_id == person.pk  # the student office sees outcomes: Prüfungsanspruch, Abschluss
    options = study.options() if study.status == Study.Status.ENROLLED else []
    lost = exhausted(study) if study.status == Study.Status.ENROLLED else []  # the Prüfungsanspruch: no Rückmeldung then
    return render(request, 'students/study.html', {
        'study': study, 'today': today, 'trail': above(person, study)[:-1],
        'office': office,
        'own': own,
        'personal': personal, 'identity': identity,
        'files': own or files,  # Leistungsübersicht
        'grades': grades,
        'certify': own or office,  # Bescheinigungen
        'unpaying': 'manage_reregistration' in person.roles,
        'enrolling': study.missing() if office and study.status == Study.Status.ADMITTED else [],
        'rows': rows, 'formats': Format.objects.exclude(short=''), **columns(rows, study.program, grades), 'findable': findable(study, tree, choices, taking) if own and study.status == Study.Status.ENROLLED else set(),
        'semesters': range(1, study.program.semesters + 1),
        'taking': taking,
        'late': late,
        'average': average, 'covered': covered, 'outstanding': outstanding[:3],
        'earned': sum(m.earned for m in tree),
        'total': sum(m.credits for m in tree),
        'terms': terms,
        'upcoming': upcoming, 'late_fee': late_fee,
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
            *([f'schon {Institution.get().leave_terms} Urlaubssemester, in der Regel das Höchste.'] if study.terms.filter(leave=True).count() >= Institution.get().leave_terms else []),
            *(['im ersten Fachsemester nur bei Erkrankung oder Mutterschutz und Elternzeit.'] if Institution.get().first_term_leave and not study.semester else []),
        ] if office else [],
        'recognise': study.status == Study.Status.ENROLLED and person.may('recognise', study),
        'compensate': study.status == Study.Status.ENROLLED and person.may('grant_compensation', study),
        'thesis': study.status == Study.Status.ENROLLED and person.may('manage_exams', study),
        'loans': study.person.loans.filter(returned=None).select_related('instrument') if personal else [],
        'mobilities': study.mobilities.select_related('partner') if own or person.everywhere('manage_mobility') or person.may('recognise', study) else [],  # as mobility.views.mobility lets in
        'focus_term': choices[0].choice.title if choices else 'Schwerpunkt',  # the programme's own word, as in its plan: Vertiefung, Profil
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
