import csv

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import texts
from core.access import require
from core.history import tables
from core.apps import plural
from core.models import Institution, Message, Person, Term
from core.text import fold, matching
from core.views import SHOWN, without_term
from curriculum.models import Program, Subject
from curriculum.views import plan
from instruments.models import Loan
from .models import Document, Study, StudyTerm

class ChangeForm(forms.Form):
    program = forms.ModelChoiceField(label='Neuer Studiengang', queryset=Program.objects.all(), empty_label='Bitte wählen')
    subject = forms.ModelChoiceField(label='Hauptfach', queryset=Subject.objects.all(), empty_label='Bitte wählen')


class ComposeForm(forms.Form):
    subject = forms.CharField(label='Betreff', max_length=200)
    body = forms.CharField(label='Text', widget=forms.Textarea)


def own(person):
    """The person's own study to open: the one under way, else the latest they finished or left."""
    return (
        person.studies.filter(status=Study.Status.ENROLLED).first()
        or person.studies.filter(status__in=Study.FORMER, terms__isnull=False).order_by('-since').first()
    )


def index(request):
    roles = request.user.roles
    if 'view_students' in roles:
        return redirect('students:list')
    study = own(request.user)
    if study is None:
        raise PermissionDenied
    return redirect(study.get_absolute_url())


COLUMNS = [  # heading, sort key, field; the server sorts, since the list may be longer than shown
    ('Matrikel', 'matrikel', 'person__matriculation'), ('Name', 'name', 'person__last_name'), ('Studiengang', 'studiengang', 'program__name'),
    ('Hauptfach', 'hauptfach', 'subject__name'), ('Klasse', 'klasse', 'teacher__last_name'), ('FS', 'fs', 'fs'), ('Status', 'status', 'status'),
]


@require('view_students')
def roster(request):
    today = timezone.localdate()
    status = request.GET.get('status', Study.Status.ENROLLED)
    studies = (
        request.user.within(Study.objects.filter(status=status), 'view_students').select_related('person', 'program', 'subject', 'start_term', 'teacher')
        .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=today)))
        .order_by('person__last_name', 'person__first_name')
    )
    for key, field in [('studiengang', 'program'), ('fach', 'subject'), ('fs', 'fs'), ('beginn', 'start_term')]:
        if (value := request.GET.get(key, '')).isdigit():
            studies = studies.filter(**{field: value})
    q, sort = request.GET.get('q', '').strip(), request.GET.get('sortierung', '')
    studies = matching(studies, q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email', 'person__matriculation', 'teacher__last_name')
    if field := next((f for _, key, f in COLUMNS if key == sort.removeprefix('-')), None):
        studies = studies.order_by(F(field).desc(nulls_last=True) if sort.startswith('-') else F(field).asc(nulls_last=True), 'person__last_name', 'person__first_name')
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="studierende.csv"'
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Matrikel', 'Nachname', 'Vorname', 'Geschlecht', 'Staatsangehörigkeit', 'E-Mail', 'Studiengang', 'Hauptfach', 'Fachsemester', 'Status'])
        for s in studies:
            writer.writerow([
                s.person.matriculation, s.person.last_name, s.person.first_name, s.person.get_gender_display(), s.person.nationality,
                s.person.email, s.program, s.subject, s.fs, s.get_status_display(),
            ])
        return response
    shown = list(studies[:SHOWN])
    return render(request, 'students/roster.html', {
        'studies': shown, 'count': len(shown) if len(shown) < SHOWN else studies.count(), 'q': q, 'status': status, 'statuses': Study.Status.choices,
        'sort': sort, 'columns': [(title, key, 'ascending' if sort == key else 'descending' if sort == f'-{key}' else '') for title, key, _ in COLUMNS],
        'programs': Program.objects.all(), 'subjects': Subject.objects.all(),
        'terms': Term.objects.filter(intake__isnull=False).distinct().order_by('-start'), 'semesters': range(1, 13),
        **{key: request.GET.get(key, '') for key in ['studiengang', 'fach', 'fs', 'beginn']},
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
    if people and form.is_valid():
        for person in people:
            Message.send(person, form.cleaned_data['subject'], form.cleaned_data['body'])
        messages.success(request, f'Nachricht an {plural(len(people), "Person", "Personen")} gesendet.')
        return redirect(back or 'students:list')
    return render(request, 'students/compose.html', {'form': form, 'people': people, 'studies': studies, 'back': back})


def study(request, pk):
    person = request.user
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term', 'teacher', 'focus'), pk=pk)
    office = person.may('manage_students', study)
    if not (study.person == person or study.teacher == person or person.may('view_files', study)):
        raise PermissionDenied
    tree, average = study.progress()
    upcoming = Term.upcoming()
    return render(request, 'students/study.html', {
        'study': study,
        'office': office,
        'own': study.person == person,
        'rows': plan(tree, study.program.semesters),
        'semesters': range(1, study.program.semesters + 1),
        'average': average,
        'earned': sum(m.earned for m in tree),
        'total': sum(m.credits for m in tree),
        'terms': study.terms.select_related('term').order_by('-term__start'),
        'upcoming': upcoming,
        'next': study.terms.filter(term=upcoming).first(),
        'paid': StudyTerm.objects.filter(study__person=study.person, term=upcoming, paid__isnull=False).exists(),
        'missing': study.person.missing(),
        'documents': study.documents.all(),
        'others': study.person.studies.exclude(pk=study.pk).select_related('program', 'subject'),
        'change': ChangeForm() if office else None,
        'thesis': study.status == Study.Status.ENROLLED and person.may('manage_exams', study),
        'loans': study.person.loans.filter(returned=None).select_related('instrument'),
        'history': bool(tables(person.roles)),
    })


def next_matriculation():
    """The next free Matrikelnummer, counting on from the first the Hochschule set."""
    numbers = [int(n) for n in Person.objects.exclude(matriculation=None).values_list('matriculation', flat=True) if n.isdigit()]
    return max(numbers, default=Institution.get().matriculation_start - 1) + 1


@require('manage_students')
@require_POST
def enrol(request, pk):
    """Immatrikulation of an admitted applicant: matriculation number and first registered term."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'start_term'), 'manage_students'), pk=pk, status=Study.Status.ADMITTED)
    person = study.person
    if missing := study.missing():
        messages.error(request, f'Noch nicht immatrikuliert: {", ".join(missing)}.')
        return redirect(study.get_absolute_url())
    with transaction.atomic():
        if not person.matriculation:
            person.matriculation = str(next_matriculation())
            person.save(update_fields=['matriculation'])
        registration, _ = StudyTerm.objects.get_or_create(study=study, term=study.start_term)
        registration.paid = registration.paid or timezone.localdate()
        registration.save(update_fields=['paid'])
        study.set_status(Study.Status.ENROLLED)
    study.notify('immatrikulation', study.get_absolute_url())
    messages.success(request, f'{person} immatrikuliert, Matrikelnummer {person.matriculation}. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def insured(request, pk):
    """The health insurer's electronic notice (Studierendenmeldeverfahren) has arrived."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk)
    study.person.insurance_confirmed = timezone.localdate()
    study.person.save(update_fields=['insurance_confirmed'])
    messages.success(request, 'Versicherungsmeldung verbucht.')
    return redirect(study.get_absolute_url())


@require_POST
def leave(request, pk):
    """Urlaubssemester for the upcoming term, requested by the student or recorded by the office."""
    study = get_object_or_404(Study, pk=pk, status=Study.Status.ENROLLED)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    term = Term.upcoming()
    registration, _ = StudyTerm.objects.get_or_create(study=study, term=term)
    registration.leave = not registration.leave
    registration.save(update_fields=['leave'])
    messages.success(request, f'{term}: {"Urlaubssemester eingetragen" if registration.leave else "Urlaubssemester zurückgenommen"}.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def exmatriculate(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    status = Study.Status.GRADUATED if request.POST.get('reason') == 'graduated' else Study.Status.LEFT
    study.set_status(status)
    reason = 'Abschluss des Studiums' if status == Study.Status.GRADUATED else 'Abbruch oder Wechsel des Studiums'
    study.notify('exmatrikulation', grund=reason)
    messages.success(request, f'{study.person} exmatrikuliert. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require('manage_students')
@require_POST
def change(request, pk):
    """Fachwechsel: end this study, start the new one now with the semesters so far credited."""
    study = get_object_or_404(request.user.within(Study.objects.all(), 'manage_students'), pk=pk, status=Study.Status.ENROLLED)
    form = ChangeForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Bitte Studiengang und Hauptfach wählen.')
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


@require('manage_reregistration')
def reregistration(request):
    """Rückmeldung for the upcoming term: book payments; after the deadline, exmatriculate whoever has not paid."""
    term, today = Term.upcoming(), timezone.localdate()
    if term is None:
        return without_term(request, 'das nächste Semester, für das sich Studierende zurückmelden')
    enrolled = (
        Study.objects.filter(status=Study.Status.ENROLLED).select_related('person', 'program', 'subject').order_by('person__last_name')
        .prefetch_related(Prefetch('person__loans', Loan.objects.filter(returned=None).select_related('instrument')))
    )
    registrations = {r.study_id: r for r in StudyTerm.objects.filter(term=term)}
    paid = {r.study.person_id for r in StudyTerm.objects.filter(term=term, paid__isnull=False).select_related('study')}
    if request.method == 'POST':
        if 'pay' in request.POST:
            people = set(map(int, request.POST.getlist('person')))
            with transaction.atomic():
                for s in enrolled.filter(person__in=people):
                    StudyTerm.objects.update_or_create(study=s, term=term, defaults={'paid': today})
            for p in {s.person for s in enrolled.filter(person__in=people)}:
                if missing := p.missing():
                    texts.send('rueckmeldung_offen', p, rueckmeldesemester=term, fehlt=', '.join(missing))
                else:
                    texts.send('rueckmeldung', p, rueckmeldesemester=term)
            messages.success(request, f'Zahlung für {plural(len(people), "Person", "Personen")} verbucht.')
        elif 'exmatriculate' in request.POST and today > term.deadline:
            unpaid = list(enrolled.exclude(person__in=paid))
            for s in unpaid:
                s.set_status(Study.Status.LEFT)
                s.notify('exmatrikulation_rueckmeldung', rueckmeldesemester=term)
            messages.success(request, f'{len(unpaid)} Studien wegen fehlender Rückmeldung beendet. Bescheide versandt.')
        return redirect('students:reregistration')
    rows = [(s, registrations.get(s.pk), s.person_id in paid, s.person.missing()) for s in enrolled]
    states = {'offen': ('offen', lambda row: not row[2]), 'bezahlt': ('bezahlt', lambda row: row[2]), 'hindernis': ('mit Hindernis', lambda row: row[3])}
    stand, q = request.GET.get('stand', ''), request.GET.get('q', '').strip()
    words = fold(q).split()
    shown = [
        row for row in rows if (stand not in states or states[stand][1](row))
        and all(w in fold(f'{row[0].person.name} {row[0].person.matriculation}') for w in words)
    ]
    counts = {key: sum(1 for row in rows if test(row)) for key, (_, test) in states.items()}
    return render(request, 'students/reregistration.html', {
        'term': term, 'rows': shown, 'total': len(rows), 'open': counts['offen'], 'blocked': counts['hindernis'], 'overdue': today > term.deadline,
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


def certificate(request, pk):
    """Immatrikulationsbescheinigung for the current term."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk, status=Study.Status.ENROLLED)
    if not (study.person == request.user or request.user.may('manage_students', study)):
        raise PermissionDenied
    term = Term.current()
    return render(request, 'students/certificate.html', {
        'study': study, 'term': term, 'registration': study.terms.filter(term=term).first(), 'today': timezone.localdate(),
        'sender': texts.block('absender_bescheinigungen'),
    })
