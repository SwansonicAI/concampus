"""The applicant's side: account, application, details, documents, accepting a place."""
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.views.decorators.http import require_POST

from core import texts
from core.forms import again, sent
from core.models import Consent, Institution, Office, Term
from core.shortcuts import ics
from courses.models import Course, Participation
from students.models import Document, DocumentType, Study, StudyTerm
from ..auditions import after, held
from ..forms import AcceptForm, ApplicationForm, DetailsForm, EntranceForm, SignupForm
from ..papers import Condition, checklist, document_types, owed, stages
from .common import accept_by, lapsed, uploads_open
from .juries import programme

SHOWN = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif'}  # opened in the browser; any other kind of file only downloads


def standing(study, days):
    """(how far an application got, its state in the applicant's words): an offer lapsed counts for little, one accepted
    most; invited while an appointment lies ahead."""
    if study.status == Study.Status.ADMITTED:
        if study.terms.filter(term=study.start_term).exists():
            return 6, 'Studienplatz angenommen'
        return (1, 'Annahmefrist verstrichen') if accept_by(study, days) < timezone.localdate() else (5, 'Studienplatz angeboten')
    if study.status == Study.Status.WAITLISTED:
        return 4, 'Nachrückliste'
    if study.status == Study.Status.APPLIED:
        return (3, 'Eingeladen') if study.participations.filter(slot__gt=timezone.now(), course__released=None).exists() else (2, 'Beworben')
    return 0, study.get_status_display()


def calendar(request, pk):
    """An applicant's appointment as a calendar file."""
    p = get_object_or_404(Participation.objects.select_related('course'), pk=pk, study__person=request.user, slot__isnull=False, course__kind=Course.Kind.AUDITION)
    start, end, room = held(p)
    return ics(request, [(f'eignungspruefung-{p.pk}', start, end, p.course.title, room.place() if room else '')], 'eignungspruefung.ics')


@login_not_required
def signup(request):
    if request.user.is_authenticated:
        return redirect('admissions:apply')
    if not Institution.get().signup:
        messages.error(request, 'Neue Bewerbungskonten sind derzeit nicht möglich.')
        return redirect('login')
    form = SignupForm(request.POST or None)
    if form.is_valid():
        person = form.save()
        login(request, person, backend='core.backends.Login')
        texts.send('willkommen', person, reverse('admissions:apply'))
        return redirect('admissions:apply')
    return render(request, 'admissions/signup.html', {'form': form})


def apply(request):
    person = request.user
    entrance = ['entrance', 'entrance_date', 'entrance_region', 'entrance_town']
    form = ApplicationForm(request.POST or None, request.FILES or None, initial={field: getattr(person, field) for field in entrance}, person=person)
    if form.is_valid():
        data, audition = form.cleaned_data, form.cleaned_data['audition']
        with transaction.atomic():
            study, created = Study.objects.get_or_create(
                person=person, program=audition.program, subject=audition.subject, start_term=audition.term,
                defaults={'teacher': data['teacher']},
            )
            if not created:
                messages.error(request, 'Für diesen Studiengang und dieses Hauptfach haben Sie sich bereits beworben.')
                return redirect('admissions:mine')
            given = [name for name in ('nationality', 'gender') if name in data]
            for field in entrance:
                setattr(person, field, data[field] or getattr(person, field))
            for field in given:
                setattr(person, field, data[field])
            person.save(update_fields=entrance + given)
            Participation.objects.create(course=audition, study=study, note=data['repertoire'])
            if data['accompanist'] and audition.accompaniment:
                Participation.objects.create(course=audition.accompaniment, study=study)
        texts.send(
            'bewerbung', person, reverse('admissions:mine'), studiengang=audition.program.title,
            hauptfach=audition.subject, frist=f'{audition.deadline:%d.%m.%Y}',
        )
        messages.success(request, f'Bewerbung angelegt. Laden Sie jetzt bis zum {date_format(audition.deadline, "j.n.Y")} Ihr Video und Ihre Unterlagen hoch.')
        return redirect('admissions:mine')
    return render(request, 'admissions/apply.html', {'form': form})


def mine(request):
    person = request.user
    current = Term.current()
    newly = Q(status=Study.Status.ENROLLED, start_term__start__gte=current.start) if current else Q(pk=None)  # just enrolled from an application
    studies = (
        person.studies.filter(Q(status__in=Study.APPLICATION) | newly)
        .select_related('person', 'program', 'subject', 'start_term').prefetch_related('person__documents')
    )
    types, days = document_types(), Institution.get().accept_days
    for study in studies:
        study.stages = stages().filter(study=study)
        study.accepted = study.terms.filter(term=study.start_term).exists()
        study.accept_by, study.lapsed, study.standing = accept_by(study, days), lapsed(study, days), standing(study, days)[1]
        study.yours, study.theirs = study.steps() if study.status == Study.Status.ADMITTED and study.accepted else ([], [])
        study.checklist = (
            checklist(study, types) if study.status == Study.Status.APPLIED
            else [item for item in checklist(study, types) if item['enrolment']] if study.status == Study.Status.ADMITTED else []
        )
        study.open = uploads_open(study)
        study.late = [] if study.open else owed(study.checklist)
        for item in study.late:
            item['late'] = True
        study.deadline = next((p.course.deadline for p in study.stages if p.course.program_id), None)
        study.stages = list(study.stages)
        for p in study.stages:  # where an appointment takes place: the course's booking around it
            p.room = p.slot and held(p)[2]
        ahead = [p for p in study.stages if p.slot and p.slot > timezone.now() and not p.course.released]
        study.invited, study.ahead = bool(ahead), ahead
        study.after = after(ahead[-1].course) if ahead else []
        if applied := next((p for p in study.stages if p.course.program_id), None):  # what the application says, changed until the deadline
            joined = study.participations.filter(course=applied.course.accompaniment_id).exists()
            study.applied, study.accompanied, study.programme = applied, joined, programme(applied.note)
            study.details = study.open and DetailsForm(
                initial={'repertoire': applied.note, 'teacher': study.teacher_id, 'accompanist': joined}, study=study, accompaniment=applied.course.accompaniment_id,
            )
    return render(request, 'admissions/mine.html', {
        'studies': studies, 'documents': person.documents.exclude(message=None), 'accept': AcceptForm(sent(request, 'accept'), instance=person),
        'entrance': EntranceForm(sent(request, 'entrance'), instance=person) if not person.entrance else None,
        'insurers': Institution.get().insurers.splitlines(), 'bank': Institution.get(), 'deciders': Office.holding('decide_applications', linked=True),
    })


@require_POST
def retract(request, pk):
    """Withdraw an application, while it runs or from the Nachrückliste: its appointments are freed."""
    study = get_object_or_404(Study, pk=pk, person=request.user, status__in=[Study.Status.APPLIED, Study.Status.WAITLISTED])
    with transaction.atomic():
        study.participations.filter(course__released=None).delete()
        study.set_status(Study.Status.LEFT)
    messages.success(request, f'Bewerbung {study.program.title} · {study.subject} zurückgezogen.')
    return redirect('admissions:mine')


@require_POST
def details(request, pk):
    """Change the Prüfungsprogramm, the Wunschlehrer:in or the wish for Korrepetition, until the application's deadline."""
    study = get_object_or_404(Study.objects.select_related('subject'), pk=pk, person=request.user, status=Study.Status.APPLIED)
    applied = get_object_or_404(stages().filter(study=study, course__program__isnull=False)[:1])
    form = DetailsForm(request.POST, study=study, accompaniment=applied.course.accompaniment_id)
    if not uploads_open(study):
        messages.error(request, 'Die Frist für Änderungen ist abgelaufen.')
    elif not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
    else:
        data = form.cleaned_data
        with transaction.atomic():
            applied.note = data['repertoire']
            applied.save(update_fields=['note'])
            study.teacher = data['teacher']
            study.save(update_fields=['teacher'])
            if applied.course.accompaniment_id and data['accompanist']:
                Participation.objects.get_or_create(course_id=applied.course.accompaniment_id, study=study)
            elif applied.course.accompaniment_id:
                Participation.objects.filter(course_id=applied.course.accompaniment_id, study=study).delete()
        messages.success(request, 'Angaben gespeichert.')
    return redirect('admissions:mine')


@require_POST
def entrance(request, pk):
    """The Hochschulzugangsberechtigung of whoever was admitted without one yet."""
    get_object_or_404(Study, pk=pk, person=request.user, status=Study.Status.ADMITTED)
    form = EntranceForm(request.POST, instance=request.user)
    if not form.is_valid():
        messages.error(request, 'Bitte die markierten Angaben berichtigen.')
        return again(request, reverse('admissions:mine'), 'entrance')
    form.save()
    messages.success(request, 'Hochschulzugangsberechtigung eingetragen.')
    return redirect('admissions:mine')


@require_POST
def upload(request, pk):
    """Hand in a document or a video for an application, until its deadline; the office running auditions at any time."""
    study = get_object_or_404(Study.objects.select_related('person'), pk=pk, status__in=[Study.Status.APPLIED, Study.Status.ADMITTED])
    office = request.user.may('run_auditions', study)
    if not (office or study.person == request.user):
        raise PermissionDenied
    back = redirect('admissions:mine' if study.person == request.user else study.get_absolute_url())
    kind = DocumentType.objects.exclude(condition=Condition.NEVER).filter(key=request.POST.get('kind')).first()
    file = request.FILES.get('file')
    if not (office or uploads_open(study) or any(item['kind'] == kind for item in owed(checklist(study)))):
        messages.error(request, 'Die Frist für Unterlagen ist abgelaufen.')
        return back
    if not (kind and file):
        messages.error(request, 'Bitte eine Datei wählen.')
        return back
    if refusal := kind.refuses(file):
        messages.error(request, refusal)
        return back
    if not kind.single and study.documents.filter(kind=kind).count() >= kind.max_files:
        messages.error(request, f'Für „{kind}“ höchstens {kind.max_files} Dateien; bitte erst eine entfernen.')
        return back
    if kind.health and not request.POST.get('consent'):
        messages.error(request, 'Ohne Einwilligung lässt sich der Nachweis nicht hochladen.')
        return back
    consent = Consent.give(study.person, 'zu meiner Bewerbung', request.user) if kind.health else None
    messages.success(request, f'„{Document.hand_in(study, kind, file, consent)}“ hochgeladen.')
    return back


@require_POST
def reuse(request, pk):
    """The video of another application in the same Hauptfach, taken over without uploading it again."""
    study = get_object_or_404(Study, pk=pk, person=request.user, status=Study.Status.APPLIED)
    source = get_object_or_404(
        Document.objects.select_related('kind'), pk=request.POST.get('document'), person=request.user, study__subject=study.subject_id, kind__condition=Condition.VIDEO,
    )
    if not uploads_open(study):
        messages.error(request, 'Die Frist für Unterlagen ist abgelaufen.')
        return redirect('admissions:mine')
    for old in study.documents.filter(kind=source.kind):
        old.drop_file()
        old.delete()
    Document.objects.create(person=request.user, study=study, kind=source.kind, title=source.title, file=source.file.name, url=source.url)
    messages.success(request, f'„{source.title}“ gilt jetzt auch für diese Bewerbung.')
    return redirect('admissions:mine')


def document(request, pk):
    """A handed-in file: for its owner, the student office and the office deciding results, whoever sees applications, and the candidate's
    examiners: of the audition while applying what is meant for the jury, of the thesis for the thesis. A CV or certificate
    counts for all of the person's applications."""
    d = get_object_or_404(Document.objects.select_related('study', 'kind'), pk=pk)
    person = request.user
    applications = Study.objects.filter(status__in=Study.APPLICATION)
    applications = applications.filter(person=d.person_id) if d.kind.shared else applications.filter(pk=d.study_id)
    examining = Course.objects.filter(staff=person)
    examined = d.kind_id == 'attest' and any(person.may(c, d) for c in ('decide_results', 'grant_compensation', 'manage_students'))  # a Rücktritt, Nachteilsausgleich or Urlaubssemester depends on it
    if d.kind.health and not (d.person_id == person.pk or person.within(applications, 'decide_applications').exists() or examined):
        raise PermissionDenied('Gesundheitsdaten öffnen nur die Person und das Amt, dessen Entscheidung von ihnen abhängt.')
    if not (
        d.person_id == person.pk or person.may('decide_results', d) or person.may('manage_students', d) or person.within(applications, 'view_applications').exists()
        or d.kind.jury and examining.filter(kind=Course.Kind.AUDITION, participations__study__in=applications).exists()
        or d.kind_id == 'abschlussarbeit' and examining.filter(kind=Course.Kind.THESIS, participations__study=d.study_id).exists()
    ):
        raise PermissionDenied
    if not d.file or not d.file.storage.exists(d.file.name):  # never uploaded, or lost from the storage
        raise Http404
    suffix = Path(d.file.name).suffix.lower()  # of the file stored, checked at upload; a title may be cut short
    response = FileResponse(d.file.open('rb'), as_attachment=suffix not in SHOWN, filename=Path(d.title).stem + suffix)
    response['Content-Security-Policy'] = 'sandbox'  # whatever it holds never runs as part of ConCampus
    return response


@require_POST
def discard(request, pk):
    """Take back a document, until the deadline."""
    document = get_object_or_404(Document.objects.select_related('study'), pk=pk, person=request.user, message=None)
    if not uploads_open(document.study):
        raise PermissionDenied
    document.drop_file()
    document.delete()
    messages.success(request, f'„{document.title}“ entfernt.')
    return redirect('admissions:mine')


@require_POST
def accept(request, pk):
    study = get_object_or_404(Study.objects.select_related('start_term'), pk=pk, person=request.user, status=Study.Status.ADMITTED)
    if request.POST.get('decision') != 'yes':
        study.set_status(Study.Status.LEFT)
        messages.success(request, 'Sie haben den Studienplatz abgelehnt.')
        return redirect('admissions:mine')
    if accept_by(study, Institution.get().accept_days) < timezone.localdate():
        messages.error(request, 'Die Frist zur Annahme ist abgelaufen.')
        return redirect('admissions:mine')
    form = AcceptForm(request.POST, instance=request.user)
    if not form.is_valid():  # back with what was typed, each error at its field
        messages.error(request, 'Noch nicht angenommen. Bitte die markierten Angaben berichtigen.')
        return again(request, reverse('admissions:mine'), 'accept')
    person = form.save()
    StudyTerm.objects.get_or_create(study=study, term=study.start_term)
    texts.send('studienplatz_angenommen', person, beitrag=number_format(study.start_term.fee, 2), frist=f'{study.fee_due:%d.%m.%Y}')
    messages.success(request, 'Studienplatz angenommen.')
    return redirect('admissions:mine')
