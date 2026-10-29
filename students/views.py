import csv
import re
from collections import defaultdict
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.contrib.auth.decorators import login_not_required
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import dates, texts
from core.access import CODENAMES, require
from core.history import tables
from core.apps import plural
from core.models import Export, Institution, Message, Office, Person, Term
from core.text import fold, handle, matching
from core.views import SHOWN, preview, without_term
from courses.models import Participation
from courses.views import untimed
from curriculum.models import Module, Program, Subject
from curriculum.views import plan
from exams.views import exhausted
from instruments.models import Loan
from . import payments
from .models import EXITS, FIRST_TERM_LEAVES, LEAVE_TERMS, LEAVES, OFFICIAL, Document, Study, StudyTerm

class ChangeForm(forms.Form):
    program = forms.ModelChoiceField(label='Neuer Studiengang', queryset=Program.objects.all(), empty_label='Bitte wählen')
    subject = forms.ModelChoiceField(label='Hauptfach', queryset=Subject.objects.all(), empty_label='Bitte wählen')

    def __init__(self, *args, study=None, **kwargs):
        """Shown for a study: its Hauptfach kept, the Studiengänge one can start with it first; once one is picked, only
        its Hauptfächer."""
        super().__init__(*args, **kwargs)
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
        return data


class PaymentForm(forms.Form):
    """A payment booked by hand: when it arrived, how much, and the Beleg to find it again."""

    paid = dates.DayField(label='Eingegangen am')
    amount = forms.DecimalField(label='Betrag', min_value=0, decimal_places=2, localize=True)
    reference = forms.CharField(label='Beleg oder Verwendungszweck', max_length=140)


class ComposeForm(forms.Form):
    subject = forms.CharField(label='Betreff', max_length=200)
    body = forms.CharField(label='Text', widget=forms.Textarea, help_text='{vorname} und {nachname} werden für jede Person eingesetzt.')


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


@require('view_students')
def roster(request):
    today = timezone.localdate()
    status = request.GET.get('status', Study.Status.ENROLLED)
    status = status if status in dict(STATES) else Study.Status.ENROLLED
    shown = Study.objects.filter(status=status) if status else Study.objects.exclude(status__in=Study.APPLICATION)
    scoped = request.user.within(shown, 'view_students')
    studies = semesters(scoped.select_related('person', 'program', 'subject', 'start_term', 'teacher')).order_by('person__last_name', 'person__first_name')
    for key, field in [('studiengang', 'program'), ('fach', 'subject'), ('klasse', 'teacher'), ('fs', 'fs'), ('beginn', 'start_term')]:
        if (value := request.GET.get(key, '')).isdigit():
            studies = studies.filter(**{field: value})
    studies = cases(studies, request.GET.get('stand', ''))
    q, sort = request.GET.get('q', '').strip(), request.GET.get('sortierung', '')
    studies = matching(studies, q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email', 'person__matriculation', 'teacher__last_name')
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
        'programs': request.user.within(Program.objects.ordered(), 'view_students'), 'subjects': request.user.within(Subject.objects.all(), 'view_students'),
        'terms': Term.objects.filter(intake__isnull=False).distinct().order_by('-start'), 'semesters': range(1, 13),
        'classes': Person.objects.filter(class_studies__in=scoped).distinct().order_by('last_name', 'first_name'),
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
    """One message to the chosen people, e.g. everyone in their first semester."""
    studies = request.user.within(Study.objects.filter(pk__in=request.POST.getlist('study') or request.GET.getlist('study')), 'message_students').select_related('person')
    people = sorted({s.person for s in studies}, key=lambda p: p.name)
    back = request.POST.get('next') or request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = ''
    form = ComposeForm(request.POST or None)
    sender = ', '.join(Office.objects.filter(user=request.user, permissions__codename='message_students').values_list('name', flat=True)) or str(request.user)
    if people and form.is_valid():
        for person in people:
            named = {'{vorname}': person.first_name, '{nachname}': person.last_name}
            subject, body = form.cleaned_data['subject'], form.cleaned_data['body']
            for placeholder, value in named.items():
                subject, body = subject.replace(placeholder, value), body.replace(placeholder, value)
            Message.send(person, subject, body, sender=sender)
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
    for t in terms:
        t.after = study.status in Study.FORMER and t.term.start > study.since  # paid for, then left before it began
    return render(request, 'students/study.html', {
        'study': study, 'today': today,
        'office': office,
        'own': own,
        'personal': personal, 'identity': identity,
        'files': own or files,  # Leistungsübersicht
        'grades': own or files or study.teacher_id == person.pk,  # the student office sees outcomes: Prüfungsanspruch, Abschluss
        'certify': own or office,  # Bescheinigungen
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
        'missing': study.person.missing() if personal else [],
        'documents': study.documents.all() if identity else [],
        'others': [o for o in study.person.studies.exclude(pk=study.pk).exclude(status=Study.Status.REJECTED).select_related('program', 'subject') if personal or o.pk in reachable],
        'reachable': reachable,
        'change': ChangeForm(study=study) if office else None,
        'exits': EXITS, 'leaves': LEAVES,
        'exhausted': lost, 'final': lost and max(m.appeal for m in lost) < today,
        'leaving': leaving(study) if office and study.status == Study.Status.ENROLLED else [],
        'leaving_day': leaving_day(study, lost) if office and study.status == Study.Status.ENROLLED else None,
        'options': options if own else [], 'fs': fs,
        'advising': options if study.teacher_id == person.pk and not study.focus_id else [],
        'recording': study.program.choices() if not own and study.status == Study.Status.ENROLLED and decides(person, study) else [],
        'changes': Office.holding('decide_results', study.program),
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
        later = 'Ändern lässt sie sich danach nur über ' + (Office.holding('decide_results', study.program) or 'die Hochschule') + '.'
        return preview(request, f'{chosen.choice.title}: {chosen.title}', [], 'Eintragen' if office else 'Verbindlich wählen', study.get_absolute_url(), aside + [later] * (not office))
    else:
        study.focus = chosen
        study.save(update_fields=['focus'])
        if office:
            study.notify('schwerpunkt_eingetragen', study.get_absolute_url(), schwerpunkt=chosen.title, grund=reason)
        else:
            texts.send('schwerpunkt', study.person, study.get_absolute_url(), studiengang=study.program.title, schwerpunkt=chosen.title)
        messages.success(request, f'Eingetragen: {chosen.title}. Bescheid versandt.' if office else f'Gewählt: {chosen.title}. Die Bestätigung liegt in Ihrem Postfach.')
    return redirect(study)


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
        messages.error(request, 'Bitte Eingangstag, Betrag und Beleg angeben.')
    elif form.cleaned_data['amount'] < fee:
        messages.error(request, f'Zu wenig: der Semesterbeitrag beträgt {number_format(fee, 2)} €.')
    else:
        StudyTerm.objects.filter(study=study, term=study.start_term, paid=None).update(**form.cleaned_data)
        messages.success(request, f'Semesterbeitrag verbucht: {number_format(form.cleaned_data["amount"], 2)} € am {date_format(form.cleaned_data["paid"], "j.n.Y")}.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else study.get_absolute_url())


@require('manage_students')
@require_POST
def insured(request, pk):
    """The health insurer's electronic notice (Studierendenmeldeverfahren) has arrived."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.insurance_confirmed = timezone.localdate()
    study.person.save(update_fields=['insurance_confirmed'])
    messages.success(request, 'Versicherungsmeldung verbucht.')
    return redirect(study.get_absolute_url())


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
        f'Urlaubssemester für das {term} lassen sich nur bis zum {date_format(term.closes, "j.n.Y")} beantragen.' if not office and not term.reregistration_start <= timezone.localdate() <= term.closes
        else f'In der Regel sind höchstens {LEAVE_TERMS} Urlaubssemester möglich; ein weiteres nur auf schriftlichen Antrag.' if study.terms.filter(leave=True).count() >= LEAVE_TERMS
        else 'Im ersten Fachsemester ist eine Beurlaubung nur bei Erkrankung oder Mutterschutz und Elternzeit möglich.' if not study.semester and reason not in FIRST_TERM_LEAVES
        else None
    )
    if leave and reason not in dict(LEAVES) or rule and not office:
        messages.error(request, rule if reason in dict(LEAVES) else 'Bitte den Grund der Beurlaubung wählen.')
        return redirect(study.get_absolute_url())
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    registration.leave, registration.leave_reason = leave, reason
    registration.save(update_fields=['leave', 'leave_reason'])
    if leave:
        study.notify('beurlaubung', study.get_absolute_url(), rueckmeldesemester=term, grund=dict(LEAVES)[reason])
    messages.success(request, f'{term}: {"Urlaubssemester eingetragen, Bescheid versandt" if leave else "Urlaubssemester zurückgenommen"}.')
    if rule:
        messages.warning(request, f'Als Ausnahme eingetragen: {rule} Antrag und Nachweis gehören zur Akte.')
    return redirect(study.get_absolute_url())


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
            later.delete()
            study.set_status(Study.Status.GRADUATED if reason == '1' else Study.Status.LEFT, reason, on)
        study.notify('exmatrikulation', after=after, **values)
        messages.success(request, f'{study.person} mit Ablauf des {date_format(on, "j.n.Y")} exmatrikuliert. Bescheid versandt.' + (f' {insurer}' if insurer else '') + (f' Noch offen: {"; ".join(f"{what} ({who})" if who else what for what, who in still)}.' if still else ''))
    return redirect(study.get_absolute_url())


@require('manage_students')
def notices(request):
    """Versandte Bescheide: every Bescheid the office's studies received, newest first, how each reached its person."""
    q, kind = request.GET.get('q', '').strip(), request.GET.get('art', '')
    sent = (
        Document.objects.filter(kind='notice', study__in=request.user.within(Study.objects.all(), 'manage_students'))
        .select_related('person', 'study__program', 'study__subject', 'message').order_by('-created')
    )
    kinds = sorted(set(sent.values_list('title', flat=True)))
    if kind:
        sent = sent.filter(title=kind)
    sent = matching(sent, q, 'person__first_name', 'person__last_name', 'person__matriculation', starts=True)
    return render(request, 'students/notices.html', {'sent': sent[:SHOWN], 'count': sent.count(), 'kinds': kinds, 'kind': kind, 'q': q})


@require('manage_students')
@require_POST
def change(request, pk):
    """Fachwechsel: end this study, start the new one now with the semesters so far credited."""
    study = get_object_or_404(request.user.within(Study.objects.all(), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    form = ChangeForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(form.non_field_errors()) or 'Bitte Studiengang und Hauptfach wählen.')
        return redirect(study.get_absolute_url())
    term = Term.current()
    with transaction.atomic():
        new = Study.objects.create(
            person=study.person, program=form.cleaned_data['program'], subject=form.cleaned_data['subject'],
            start_term=term, status=Study.Status.ENROLLED, teacher=study.teacher, prior_terms=study.semester - 1,
        )
        StudyTerm.objects.create(study=new, term=term, paid=timezone.localdate())
        study.set_status(Study.Status.LEFT)
    messages.success(request, 'Fachwechsel eingetragen. Bereits erbrachte Leistungen werden über eine Anerkennung angerechnet.')
    return redirect(new.get_absolute_url())


@require('manage_students')
@require_POST
def hold(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.hold = request.POST.get('hold', '').strip()
    study.person.save(update_fields=['hold'])
    messages.success(request, 'Sperre gesetzt.' if study.person.hold else 'Sperre aufgehoben.')
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
            day, reference = dates.day(request.POST.get('paid', '')) or today, request.POST.get('reference', '').strip()
            if not reference:
                messages.error(request, 'Bitte den Beleg oder Verwendungszweck angeben, mit dem die Zahlung einging.')
                return redirect('students:reregistration')
            people = {s.person: (day, term.due(day, late_fee), reference) for s in enrolled.filter(person__in=request.POST.getlist('person'))}
            book(term, enrolled, people)
            messages.success(request, f'Zahlung für {plural(len(people), "Person", "Personen")} verbucht, je {number_format(term.due(day, late_fee), 2)} € am {date_format(day, "j.n.Y")}.')
        elif statement := request.FILES.get('statement'):
            unpaid = {s.person.matriculation: s.person for s in enrolled.exclude(person__in=paid)}
            found, short, unknown = {}, [], 0
            for day, amount, text in payments.credits(statement.read()):
                person = next((unpaid[n] for n in payments.numbers(text) if n in unpaid), None)
                if person is None:
                    unknown += 1
                elif amount < term.due(day, late_fee):
                    short.append(f'{person.name} ({number_format(amount, 2)} €)')
                else:
                    found[person] = (day, amount, text)
            book(term, enrolled, found)
            messages.success(request, ' '.join(filter(None, [
                f'{plural(len(found), "Zahlung", "Zahlungen")} verbucht.', short and f'Zu wenig überwiesen: {", ".join(short)}.',
                unknown and f'{plural(unknown, "Buchung", "Buchungen")} ohne offene Matrikelnummer im Verwendungszweck.',
            ])))
        elif 'exmatriculate' in request.POST and today > term.closes:
            unpaid = list(enrolled.exclude(person__in=paid))
            ends = term.start - timedelta(days=1)  # with the semester under way
            for s in unpaid:
                s.set_status(Study.Status.LEFT, '7', ends)
                s.notify('exmatrikulation_rueckmeldung', rueckmeldesemester=term, zum=date_format(ends, 'j. F Y'))
            messages.success(request, f'{len(unpaid)} Studien wegen fehlender Rückmeldung zum {date_format(ends, "j.n.Y")} beendet. Bescheide versandt.')
        return redirect('students:reregistration')
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
        'late_fee': late_fee,
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
        for registration in s.terms.filter(term__start__lte=today).select_related('term'):
            semester += not registration.leave
            rows.append((registration.term, s, registration.leave, semester))
    rows.sort(key=lambda row: row[0].start)
    return render(request, 'students/periods.html', {
        'study': study, 'person': person, 'studies': studies, 'rows': rows, 'count': len({row[0] for row in rows}),
        'leave': len({term for term, _, leave, _ in rows if leave}), 'today': today, 'sender': texts.block('absender_bescheinigungen'),
    })


def exmatriculation(request, pk):
    """Exmatrikulationsbescheinigung: when someone studied here and why it ended, as health insurers and job centres ask."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk, status__in=Study.FORMER)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    return render(request, 'students/exmatriculation.html', {
        'study': study, 'today': timezone.localdate(), 'sender': texts.block('absender_bescheinigungen'),
        'reason': study.exit_reason == '1' or request.GET.get('grund'),  # often handed on: the ground only where asked for
    })


def certificate(request, pk):
    """Immatrikulationsbescheinigung for the current term."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk, status=Study.Status.ENROLLED)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    term = Term.current()
    return render(request, 'students/certificate.html', {
        'study': study, 'term': term, 'registration': study.terms.filter(term=term).first(), 'today': timezone.localdate(),
        'sender': texts.block('absender_bescheinigungen'), 'code': f'{study.pk}-{term.pk}-{signature(study.pk, term.pk)}',
        'check': request.build_absolute_uri(reverse('students:verify')),
    })


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
