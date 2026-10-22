from collections import defaultdict
from datetime import timedelta
from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Avg, Count, Exists, F, Min, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.access import require
from core.history import tables
from core.models import Message, Term
from courses import panel
from courses.forms import SlotForm
from courses.models import Course, Participation
from students.models import Document, Study, StudyTerm
from .forms import INSURERS, AcceptForm, ApplicationForm, SignupForm

BOARD = [Study.Status.APPLIED, Study.Status.ADMITTED, Study.Status.WAITLISTED, Study.Status.REJECTED, Study.Status.ENROLLED]
ACCEPT_DAYS = 14
POSTGRADUATE = ['M.Mus.', 'M.Ed.', 'Konzertdiplom', 'Zertifikat', 'Dr. phil.']
Kind = Document.Kind
VIDEO_TYPES = ('.mp4', '.mov', '.webm', '.m4v', '.ogg', '.ogv', '.qt')
VIDEO_SIZE = 1536 * 1024 * 1024  # 1.5 GB
NOTES = {
    Kind.VIDEO: 'Eine Datei, ungeschnitten, 10–15 Minuten, beginnend mit einer kurzen Vorstellung; die Kamera 3–5 m entfernt, '
                'eine professionelle Aufnahme ist nicht nötig. Nicht älter als ein Jahr. Höchstens 1,5 GB als .mp4, .mov, .webm, .m4v, .ogg, .ogv oder .qt.',
    Kind.FEE: '50 €, Verwendungszweck: Eignungsprüfung, Name, Hauptfach.',
    Kind.CONSENT: 'Sie sind noch nicht volljährig.',
    Kind.LANGUAGE: 'Niveau B2.',
}
DECISIONS = {
    Study.Status.ADMITTED: (
        'Zulassungsbescheid',
        'Sie haben die Eignungsprüfung bestanden. Wir freuen uns, Ihnen einen Studienplatz im Studiengang {program}, '
        'Hauptfach {subject}, ab dem {term} anzubieten.\n\nBitte nehmen Sie den Studienplatz bis zum {deadline} im Portal an.',
    ),
    Study.Status.WAITLISTED: (
        'Bescheid: Nachrückliste',
        'Sie haben die Eignungsprüfung bestanden. Alle Studienplätze im Hauptfach {subject} sind derzeit vergeben; '
        'Sie stehen auf der Nachrückliste. Wird ein Platz frei, erhalten Sie umgehend Nachricht.',
    ),
    Study.Status.REJECTED: (
        'Bescheid zur Eignungsprüfung',
        'Leider können wir Ihnen für den Studiengang {program}, Hauptfach {subject}, keinen Studienplatz anbieten. '
        'Wir danken Ihnen für Ihre Bewerbung und wünschen Ihnen für Ihren weiteren Weg alles Gute.',
    ),
}


def index(request):
    roles = request.user.roles
    if 'view_applications' in roles:
        return redirect('admissions:board')
    if 'teacher' in roles:
        return redirect('admissions:juries')
    return redirect('admissions:mine')


def stages():
    """Audition participations in stage order: video round, Hauptfach, shared parts."""
    return (
        Participation.objects.filter(course__kind=Course.Kind.AUDITION).select_related('course')
        .order_by(F('course__deadline').asc(nulls_last=True), F('course__subject').asc(nulls_last=True), 'course__title')
    )


def checklist(study):
    """What a complete application holds, as the HfM lists it: [{kind, label, note, required, documents}].
    Reads `study.documents.all()`; prefetch them for many studies."""
    person, handed, today = study.person, defaultdict(list), timezone.localdate()
    for d in study.documents.all():
        handed[d.kind].append(d)
    steps = getattr(study, 'stages', None)
    video = any(p.course.video for p in steps) if steps is not None else Participation.objects.filter(study=study, course__video=True).exists()
    items = [(Kind.VIDEO, True)] * video + [(Kind.CV, True), (Kind.SCHOOL, True)]
    if study.program.degree in POSTGRADUATE:
        items.append((Kind.DEGREE, True))
    items.append((Kind.FEE, True))
    if person.birth_date and (today - person.birth_date).days < 18 * 365.25:
        items.append((Kind.CONSENT, True))
    if study.subject.name in ('Gesang', 'Jazz-Gesang'):
        items.append((Kind.VOICE, False))
    if person.nationality != 'deutsch':
        items.append((Kind.LANGUAGE, False))
    return [{'kind': kind, 'label': kind.label, 'note': NOTES.get(kind, ''), 'required': required, 'documents': handed[kind]} for kind, required in items]


def incomplete(study):
    return [item['label'] for item in checklist(study) if item['required'] and not item['documents']]


def uploads_open(study):
    """Documents and videos can be handed in and changed until the application deadline, unless the jury has closed the round."""
    return study.status == Study.Status.APPLIED and Course.objects.filter(
        kind=Course.Kind.AUDITION, program__isnull=False, participations__study=study, deadline__gte=timezone.localdate(), released=None,
    ).exists()


@require('view_applications')
def board(request):
    terms = Term.objects.filter(courses__kind=Course.Kind.AUDITION).distinct()
    picked = request.GET.get('semester', '')
    term = (picked.isdigit() and terms.filter(pk=picked).first()) or terms.filter(start__gt=timezone.localdate()).first() or terms.last()
    studies = (
        Study.objects.filter(start_term=term, status__in=BOARD).select_related('person', 'program', 'subject')
        .annotate(
            points=Avg('participations__grade', filter=Q(participations__course__kind=Course.Kind.AUDITION, participations__course__subject__isnull=False)),
            slot=Min('participations__slot', filter=Q(participations__slot__gte=timezone.now(), participations__course__kind=Course.Kind.AUDITION)),
            accepted=Exists(StudyTerm.objects.filter(study=OuterRef('pk'), term=OuterRef('start_term'))),
        )
        .prefetch_related(Prefetch('participations', stages(), to_attr='stages'), 'documents')
        .order_by('-points', 'person__last_name')
    )
    subject = request.GET.get('fach', '')
    if subject.isdigit():
        studies = studies.filter(subject=subject)
    for study in studies:
        study.incomplete = incomplete(study) if study.status == Study.Status.APPLIED else []
    auditions = (
        Course.objects.filter(term=term, kind=Course.Kind.AUDITION).select_related('program', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .filter(Q(size__gt=0) | Q(program__isnull=False))
        .order_by(F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    return render(request, 'admissions/board.html', {
        'term': term,
        'terms': terms,
        'subject': subject,
        'subjects': sorted({s.subject for s in Study.objects.filter(start_term=term).select_related('subject')}, key=str),
        'columns': [(Study.Status(status), [s for s in studies if s.status == status]) for status in BOARD],
        'auditions': auditions,
    })


@require('teacher')
def juries(request):
    auditions = (
        request.user.teaching.filter(kind=Course.Kind.AUDITION).select_related('term', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .order_by('-term__start', F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    return render(request, 'admissions/juries.html', {'auditions': auditions})


def next_stages(course):
    """The stage after this one: the video round leads to the Hauptfach exam of its subject, that to the shared parts."""
    later = Course.objects.filter(term=course.term, kind=Course.Kind.AUDITION, program=None).exclude(pk=course.pk)
    if course.video:
        return later.filter(subject=course.subject_id)
    return later.filter(subject=None) if course.subject_id else later.none()


def audition(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'program', 'subject'), pk=pk, kind=Course.Kind.AUDITION)
    office = 'run_auditions' in person.roles
    if not (office or course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    staff, rows = panel.sheet(course)
    columns = staff if office else [person]
    if request.method == 'POST':
        if course.released:
            raise PermissionDenied
        try:
            panel.save(request.POST, rows, columns, decide=office)
        except InvalidOperation:
            messages.error(request, 'Bitte nur Punkte zwischen 0 und 25 eintragen.')
        else:
            messages.success(request, 'Wertungen gespeichert.')
        return redirect(course)
    if request.GET.get('sortierung') == 'punkte':
        rows.sort(key=lambda p: p.grade if p.grade is not None else (p.mean or 0), reverse=True)
    return render(request, 'admissions/audition.html', {
        'course': course,
        'staff': staff,
        'rows': rows,
        'columns': [e.pk for e in columns],
        'office': office,
        'history': Course.visible_to(person.roles),
        'slots': SlotForm(person=person) if office else None,
        'stages': next_stages(course) if office else [],
        'ready': sum(1 for p in rows if p.passed and p.study.status == Study.Status.APPLIED),
        'bookings': course.bookings.select_related('room'),
    })


@require('run_auditions')
@require_POST
def plan(request, pk):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.AUDITION)
    form = SlotForm(request.POST, person=request.user)
    rows = list(course.participations.filter(slot=None, study__status=Study.Status.APPLIED).select_related('study__person').order_by('created'))
    if form.is_valid() and rows:
        try:
            panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, f'{len(rows)} Termine vergeben, Einladungen versandt.')
    else:
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben.' if rows else 'Alle Bewerbenden haben schon einen Termin.')
    return redirect(course)


@require('run_auditions')
@require_POST
def advance(request, pk):
    """Invite everyone who passed this stage to the next one."""
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.AUDITION)
    targets = list(next_stages(course))
    names = ' und '.join(f'„{t.title}“' for t in targets)
    moved = set()
    for p in course.participations.filter(passed=True, study__status=Study.Status.APPLIED).select_related('study__person'):
        for target in targets:
            if Participation.objects.get_or_create(course=target, study=p.study, defaults={'note': p.note})[1]:
                moved.add(p.study)
    for study in moved:
        Message.send(study.person, 'Einladung zur nächsten Prüfungsstufe', f'Sie haben „{course.title}“ bestanden und sind zu {names} eingeladen. Den Termin erhalten Sie gesondert.', reverse('admissions:mine'))
    messages.success(request, f'{len(moved)} Bewerbende zu {names} eingeladen.')
    return redirect(course)


@require('run_auditions')
@require_POST
def close(request, pk):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.AUDITION)
    course.released = None if course.released else timezone.now()
    course.save(update_fields=['released'])
    messages.success(request, 'Wertungen gesperrt.' if course.released else 'Wertungen wieder geöffnet.')
    return redirect(course)


def application(request, pk):
    person = request.user
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term', 'teacher'), pk=pk)
    office = 'view_applications' in person.roles
    juror = Course.objects.filter(kind=Course.Kind.AUDITION, staff=person, participations__study=study).exists()
    if not (office or juror or study.person == person):
        raise PermissionDenied
    steps = study.stages = list(stages().filter(study=study))
    hall = next((p for p in steps if p.course.stage == 'Hauptfach'), None)
    recommendation = None
    if hall and hall.passed is not None and all(p.passed is not None for p in steps):
        recommendation = Study.Status.ADMITTED if all(p.passed for p in steps) else Study.Status.REJECTED
    return render(request, 'admissions/application.html', {
        'study': study,
        'stages': steps,
        'recommendation': recommendation,
        'show_results': office or juror,
        'checklist': checklist(study),
        'documents': study.documents.filter(kind__in=[Kind.NOTICE, Kind.OTHER]),
        'decide': 'decide_applications' in person.roles,
        'enrol': 'manage_students' in person.roles,
        'history': bool(tables(person.roles)),
        'accepted': study.terms.filter(term=study.start_term).first(),
        'decisions': [Study.Status(s) for s in DECISIONS],
        'missing': study.missing() if study.status == Study.Status.ADMITTED else [],
    })


@require('decide_applications')
@require_POST
def decide(request, pk):
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term'), pk=pk)
    status = request.POST.get('status')
    if status not in DECISIONS:
        raise PermissionDenied
    study.set_status(status)
    subject, body = DECISIONS[status]
    deadline = timezone.localdate() + timedelta(days=ACCEPT_DAYS)
    study.notify(
        subject,
        body.format(program=f'{study.program.degree} {study.program.name}', subject=study.subject, term=study.start_term, deadline=f'{deadline:%d.%m.%Y}'),
        reverse('admissions:mine'), appeal=status == Study.Status.REJECTED,
    )
    messages.success(request, f'{study.person}: {study.get_status_display()}. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@login_not_required
def signup(request):
    if request.user.is_authenticated:
        return redirect('admissions:apply')
    form = SignupForm(request.POST or None)
    if form.is_valid():
        person = form.save()
        login(request, person, backend='core.backends.Login')
        Message.send(person, 'Willkommen', 'Ihr Konto ist angelegt. Sie können sich jetzt für die Eignungsprüfung bewerben.', reverse('admissions:apply'))
        return redirect('admissions:apply')
    return render(request, 'admissions/signup.html', {'form': form})


def apply(request):
    person = request.user
    entrance = ['entrance', 'entrance_date', 'entrance_place']
    form = ApplicationForm(request.POST or None, request.FILES or None, initial={field: getattr(person, field) for field in entrance})
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
            for field in entrance:
                setattr(person, field, data[field] or getattr(person, field))
            person.save(update_fields=entrance)
            Participation.objects.create(course=audition, study=study, note=data['repertoire'])
            accompanist = Course.objects.filter(term=audition.term, kind=Course.Kind.LESSON, title__startswith='Korrepetition').first()
            if data['accompanist'] and accompanist:
                Participation.objects.create(course=accompanist, study=study)
        Message.send(
            person, 'Bewerbung eingegangen',
            f'Ihre Bewerbung für {audition.program.degree} {audition.program.name}, Hauptfach {audition.subject}, ist eingegangen. '
            f'Laden Sie bis zum {audition.deadline:%d.%m.%Y} Ihr Video und Ihre Unterlagen unter „Meine Bewerbung“ hoch.',
            reverse('admissions:mine'),
        )
        messages.success(request, f'Bewerbung angelegt. Laden Sie jetzt bis zum {audition.deadline:%d.%m.%Y} Ihr Video und Ihre Unterlagen hoch.')
        return redirect('admissions:mine')
    return render(request, 'admissions/apply.html', {'form': form})


def mine(request):
    person = request.user
    studies = (
        person.studies.filter(Q(status__in=Study.APPLICATION) | Q(status=Study.Status.ENROLLED, start_term__start__gte=Term.current().start))
        .select_related('person', 'program', 'subject', 'start_term').prefetch_related('documents')
    )
    for study in studies:
        study.stages = stages().filter(study=study)
        study.accepted = study.terms.filter(term=study.start_term).exists()
        study.waiting = study.missing() if study.status == Study.Status.ADMITTED and study.accepted else []
        study.checklist = checklist(study) if study.status == Study.Status.APPLIED else []
        study.open = uploads_open(study)
        study.deadline = next((p.course.deadline for p in study.stages if p.course.program_id), None)
        study.stages = list(study.stages)
    return render(request, 'admissions/mine.html', {
        'studies': studies, 'documents': person.documents.filter(kind__in=[Kind.NOTICE, Kind.OTHER]), 'accept': AcceptForm(), 'insurers': INSURERS,
    })


@require_POST
def upload(request, pk):
    """Hand in a document or a video for an application, until its deadline; the office running auditions at any time."""
    study = get_object_or_404(Study.objects.select_related('person'), pk=pk, status=Study.Status.APPLIED)
    office = 'run_auditions' in request.user.roles
    if not (office or study.person == request.user):
        raise PermissionDenied
    back = redirect('admissions:mine' if study.person == request.user else study.get_absolute_url())
    kind, file, url = request.POST.get('kind'), request.FILES.get('file'), request.POST.get('url', '').strip()
    if not (office or uploads_open(study)):
        messages.error(request, 'Die Frist für Unterlagen ist abgelaufen.')
        return back
    if kind not in Kind.values or kind == Kind.NOTICE or not (file or url):
        messages.error(request, 'Bitte eine Datei wählen oder einen Link angeben.' if kind == Kind.VIDEO else 'Bitte eine Datei wählen.')
        return back
    if kind == Kind.VIDEO:  # one uncut file, as the HfM asks; a new one replaces the old
        if not file or not file.name.lower().endswith(VIDEO_TYPES):
            messages.error(request, 'Bitte das Video als Datei hochladen: .mp4, .mov, .webm, .m4v, .ogg, .ogv oder .qt.')
            return back
        if file.size > VIDEO_SIZE:
            messages.error(request, 'Das Video ist größer als 1,5 GB. Bitte komprimieren und erneut hochladen.')
            return back
        for old in study.documents.filter(kind=Kind.VIDEO):
            old.file.delete(save=False)
            old.delete()
    if url and not file:
        try:
            URLValidator(schemes=['https', 'http'])(url)
        except ValidationError:
            messages.error(request, 'Der Link ist ungültig.')
            return back
    title = (file.name if kind == Kind.VIDEO else request.POST.get('title', '').strip())[:120] or Kind(kind).label
    Document.objects.create(person=study.person, study=study, kind=kind, title=title, file=file or '', url='' if file else url)
    messages.success(request, f'„{title}“ hochgeladen.')
    return back


@require_POST
def discard(request, pk):
    """Take back a document, until the deadline."""
    document = get_object_or_404(Document.objects.select_related('study'), pk=pk, person=request.user, message=None)
    if not uploads_open(document.study):
        raise PermissionDenied
    document.file.delete(save=False)
    document.delete()
    messages.success(request, f'„{document.title}“ entfernt.')
    return redirect('admissions:mine')


def review(request, pk):
    """Sichtung: the video round one application at a time. Watch, score, on to the next without a score of one's own."""
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject', 'program'), pk=pk, kind=Course.Kind.AUDITION, video=True)
    juror = course.staff.filter(pk=person.pk).exists()
    if not (juror or 'run_auditions' in person.roles):
        raise PermissionDenied
    _, rows = panel.sheet(course)
    if not rows:
        return redirect(course)
    rows.sort(key=lambda p: p.created)
    mine = {p.pk: dict((e.pk, v) for e, v in p.marks).get(person.pk) for p in rows}
    picked = request.GET.get('bewerbung', '')
    current = next((p for p in rows if str(p.pk) == picked), None) or next((p for p in rows if mine[p.pk] is None), rows[0])
    at = rows.index(current)
    here = reverse('admissions:review', args=[course.pk])
    if request.method == 'POST':
        if course.released or not juror:
            raise PermissionDenied
        try:
            panel.save(request.POST, [current], [person], decide=False)
        except InvalidOperation:
            messages.error(request, 'Bitte Punkte zwischen 0 und 25 eintragen.')
            return redirect(f'{here}?bewerbung={current.pk}')
        messages.success(request, f'Wertung für {current.study.person.first_name} {current.study.person.last_name} gespeichert.')
        following = next((p for p in rows[at + 1:] + rows[:at] if mine[p.pk] is None), None)
        return redirect(f'{here}?bewerbung={following.pk}' if following else course)
    return render(request, 'admissions/review.html', {
        'course': course,
        'p': current,
        'juror': juror,
        'mine': mine[current.pk],
        'videos': current.study.documents.filter(kind=Kind.VIDEO),
        'position': at + 1,
        'count': len(rows),
        'done': sum(1 for value in mine.values() if value is not None),
        'prev': rows[at - 1] if at else None,
        'next': rows[at + 1] if at + 1 < len(rows) else None,
    })


@require_POST
def accept(request, pk):
    study = get_object_or_404(Study.objects.select_related('start_term'), pk=pk, person=request.user, status=Study.Status.ADMITTED)
    if request.POST.get('decision') != 'yes':
        study.set_status(Study.Status.LEFT)
        messages.success(request, 'Sie haben den Studienplatz abgelehnt.')
        return redirect('admissions:mine')
    form = AcceptForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Bitte Krankenkasse und Versicherungsstatus angeben.')
        return redirect('admissions:mine')
    person = request.user
    person.insurer, person.insurance = form.cleaned_data['insurer'], form.cleaned_data['insurance']
    person.save(update_fields=['insurer', 'insurance'])
    StudyTerm.objects.get_or_create(study=study, term=study.start_term)
    Message.send(
        person, 'Studienplatz angenommen',
        f'Sie haben den Studienplatz angenommen. Bitte überweisen Sie den Semesterbeitrag von {study.start_term.fee} € bis zum '
        f'{study.start_term.deadline:%d.%m.%Y}, und bitten Sie Ihre Krankenkasse, Ihren Versicherungsstatus elektronisch an die '
        'Hochschule zu melden. Sobald beides da ist, werden Sie immatrikuliert und erhalten Ihre Matrikelnummer.',
    )
    messages.success(request, 'Studienplatz angenommen.')
    return redirect('admissions:mine')
