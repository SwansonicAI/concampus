import re
from collections import Counter, defaultdict
from pathlib import Path
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Exists, F, Min, OuterRef, Prefetch, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import texts
from core.access import CODENAMES, require
from core.apps import plural
from core.history import tables
from core.models import Institution, Message, Term
from core.text import matching
from courses import panel
from courses.forms import SlotForm, substitutes
from courses.models import Course, Participation
from rooms.views import ics
from curriculum.models import Subject
from students.models import Document, DocumentType, Study, StudyTerm
from .forms import AcceptForm, ApplicationForm, SignupForm

CARDS = 40  # per column of the board before „alle anzeigen“
BOARD = [Study.Status.APPLIED, Study.Status.ADMITTED, Study.Status.WAITLISTED, Study.Status.REJECTED, Study.Status.ENROLLED]
Condition = DocumentType.Condition
DECISIONS = {Study.Status.ADMITTED: 'zulassung', Study.Status.WAITLISTED: 'nachrueckliste', Study.Status.REJECTED: 'ablehnung'}  # their Bescheide
UNDECIDED = [Study.Status.APPLIED, Study.Status.WAITLISTED, Study.Status.ADMITTED]  # an offer not taken up can still change
VERBS = {Study.Status.ADMITTED: 'Studienplatz anbieten', Study.Status.WAITLISTED: 'Auf die Nachrückliste', Study.Status.REJECTED: 'Ablehnen'}  # the buttons that decide
BESCHEIDE = {  # how a decision is counted
    Study.Status.ADMITTED: ('Studienplatzangebot', 'Studienplatzangebote'), Study.Status.WAITLISTED: ('Platz auf der Nachrückliste', 'Plätze auf der Nachrückliste'),
    Study.Status.REJECTED: ('Ablehnung', 'Ablehnungen'),
}


def index(request):
    roles = request.user.roles
    if 'view_applications' in roles:
        return redirect('admissions:board')
    if 'juror' in roles:
        return redirect('admissions:juries')
    if roles & {'teacher', *CODENAMES} and not roles & {'applicant', 'student', 'alumni'}:  # staff apply to nothing here
        raise PermissionDenied('Die Aufnahme ist für Bewerbende, ihre Kommissionen und die Ämter, die Bewerbungen bearbeiten.')
    return redirect('admissions:mine')


def stages():
    """Audition participations in stage order: video round, Hauptfach, shared parts."""
    return (
        Participation.objects.filter(course__kind=Course.Kind.AUDITION).select_related('course')
        .order_by(F('course__deadline').asc(nulls_last=True), F('course__subject').asc(nulls_last=True), 'course__title')
    )


def document_types():
    """The kinds of documents an application can ask for, each with its Hauptfächer; fetch once for many studies."""
    return list(DocumentType.objects.exclude(condition=Condition.NEVER).prefetch_related('subjects'))


def checklist(study, types=None):
    """What a complete application holds, as the document types say: [{kind, label, note, required, documents}]. What
    holds for all of a person's applications, like a CV, counts once handed in with any of them. Reads
    `study.person.documents.all()`; prefetch them for many studies."""
    person, handed, borrowed, today = study.person, defaultdict(list), defaultdict(list), timezone.localdate()
    types = document_types() if types is None else types
    shared = {t.key for t in types if t.shared}
    for d in person.documents.all():
        if d.study_id == study.pk:
            handed[d.kind_id].append(d)
        elif d.study_id and d.kind_id in shared:
            borrowed[d.kind_id].append(d)
    steps = getattr(study, 'stages', None)
    video = any(p.course.video for p in steps) if steps is not None else Participation.objects.filter(study=study, course__video=True).exists()
    applies = {
        Condition.ALWAYS: True,
        Condition.VIDEO: video,
        Condition.DEGREE: study.program.postgraduate,
        Condition.MINOR: bool(person.birth_date) and (today - person.birth_date).days < 18 * 365.25,
        Condition.FOREIGN: person.nationality != '000',
    }
    return [
        {'kind': t, 'label': t.name, 'note': t.note, 'required': t.required, 'documents': handed[t.key] or borrowed[t.key]}
        for t in types if applies.get(t.condition) or t.condition == Condition.SUBJECTS and study.subject in t.subjects.all()
    ]


def incomplete(study, types=None):
    return [item['label'] for item in checklist(study, types) if item['required'] and not item['documents']]


def uploads_open(study):
    """Documents and videos can be handed in and changed until the application deadline, unless the jury has closed the round."""
    return study.status == Study.Status.APPLIED and Course.objects.filter(
        kind=Course.Kind.AUDITION, program__isnull=False, participations__study=study, deadline__gte=timezone.localdate(), released=None,
    ).exists()


def owed(items):
    """Of a checklist, what can still be handed in once uploads are closed, until the application is decided: required
    documents still missing, but not a video, judged by then."""
    return [item for item in items if item['required'] and not item['documents'] and item['kind'].condition != Condition.VIDEO]


def accept_by(study, days):
    """The last day to accept an offered place; after it the offer lapses and the place goes down the Nachrückliste."""
    return study.since + timedelta(days=days)


def lapsed(study, days):
    """An offer not accepted in time; needs `accepted` from `ranked`."""
    return study.status == Study.Status.ADMITTED and not study.accepted and accept_by(study, days) < timezone.localdate()


def ranked(studies):
    """Studies best first by their points in the Hauptfach exam in person (the video round only lets in), on a tie by the
    mean of the other parts (the lot after that is drawn by hand), each with whether its place is accepted."""
    audition = Q(participations__course__kind=Course.Kind.AUDITION)
    hall = audition & Q(participations__course__subject__isnull=False, participations__course__video=False)
    return studies.annotate(
        points=Avg('participations__grade', filter=hall), others=Avg('participations__grade', filter=audition & Q(participations__course__subject=None)),
        accepted=Exists(StudyTerm.objects.filter(study=OuterRef('pk'), term=OuterRef('start_term'))),
    ).order_by(F('points').desc(nulls_last=True), F('others').desc(nulls_last=True), 'person__last_name')


def verdict(steps):
    """What the audition stages say once closed: rejected once one is failed, passed once all are and the Hauptfach is
    among them, None while results are missing."""
    results = [p.passed if p.course.released else None for p in steps]
    if False in results:
        return Study.Status.REJECTED
    if steps and all(results) and any(p.course.stage == 'Hauptfach' for p in steps):
        return Study.Status.ADMITTED
    return None


def recommendations(term, person):
    """The applications of the term the person decides that their results settle, each with `advice`: a failed stage
    means rejection; who passed gets a free place in the order of the ranking, the others the Nachrückliste, once
    everyone in the intake has all results."""
    days = Institution.get().accept_days
    studies = ranked(
        person.within(Study.objects.filter(start_term=term, status__in=[Study.Status.APPLIED, Study.Status.ADMITTED, Study.Status.ENROLLED]), 'decide_applications')
        .select_related('person', 'program', 'subject').prefetch_related(Prefetch('participations', stages(), to_attr='stages'))
    )
    places = {(c.program_id, c.subject_id): c.capacity for c in Course.objects.filter(term=term, kind=Course.Kind.AUDITION, program__isnull=False)}
    taken, passed, pending, found = Counter(), defaultdict(list), set(), []
    for s in studies:
        key = (s.program_id, s.subject_id)
        if s.status != Study.Status.APPLIED:
            taken[key] += not lapsed(s, days)
            continue
        s.advice = verdict(s.stages)
        if s.advice == Study.Status.REJECTED:
            found.append(s)
        elif s.advice:
            passed[key].append(s)  # in the order of the ranking
        else:
            pending.add(key)
    for key, ranking in passed.items():
        free = len(ranking) if places.get(key) is None else max(0, places[key] - taken[key])
        for n, s in enumerate(ranking):
            s.advice = None if key in pending else Study.Status.ADMITTED if n < free else Study.Status.WAITLISTED
        found += ranking
    return sorted(found, key=lambda s: (str(s.program), str(s.subject)))


def vacancies(term, person):
    """Intakes of the term the person decides on whose places change hands: [(intake, offers lapsed, who moves up from the
    Nachrückliste)]. A place is held by whoever is enrolled or has an offer still open or accepted; nobody moves up while
    applications are still undecided."""
    days, held = Institution.get().accept_days, defaultdict(list)
    statuses = [Study.Status.APPLIED, Study.Status.ADMITTED, Study.Status.ENROLLED, Study.Status.WAITLISTED]
    for study in ranked(Study.objects.filter(start_term=term, status__in=statuses).select_related('person')):
        study.lapsed = lapsed(study, days)
        held[study.program_id, study.subject_id].append(study)
    found = []
    intakes = Course.objects.filter(term=term, kind=Course.Kind.AUDITION, program__isnull=False, capacity__isnull=False)
    for intake in person.within(intakes, 'decide_applications').select_related('program', 'subject'):
        studies = held[intake.program_id, intake.subject_id]
        gone = [s for s in studies if s.lapsed]
        taken = sum(1 for s in studies if s.status in (Study.Status.ADMITTED, Study.Status.ENROLLED)) - len(gone)
        undecided = any(s.status == Study.Status.APPLIED for s in studies)
        waiting = [] if undecided else [s for s in studies if s.status == Study.Status.WAITLISTED][:max(0, intake.capacity - taken)]
        if gone or waiting:
            found.append((intake, gone, waiting))
    return found


def offer(study, status):
    """Decide an application and issue its Bescheid."""
    study.set_status(status)
    study.notify(DECISIONS[status], reverse('admissions:mine'), frist=f'{accept_by(study, Institution.get().accept_days):%d.%m.%Y}')


@require('view_applications')
def board(request):
    terms = Term.objects.filter(courses__kind=Course.Kind.AUDITION).distinct().order_by('-start')
    picked = request.GET.get('semester', '')
    term = (picked.isdigit() and terms.filter(pk=picked).first()) or terms.filter(start__gt=timezone.localdate()).first() or terms.last()
    studies = ranked(
        request.user.within(Study.objects.filter(start_term=term, status__in=BOARD), 'view_applications').select_related('person', 'program', 'subject')
        .annotate(slot=Min('participations__slot', filter=Q(participations__slot__gte=timezone.now(), participations__course__kind=Course.Kind.AUDITION)))
        .prefetch_related(Prefetch('participations', stages(), to_attr='stages'), 'person__documents')
    )
    subject, q = request.GET.get('fach', ''), request.GET.get('q', '').strip()
    if subject.isdigit():
        studies = studies.filter(subject=subject)
    studies = matching(studies, q, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email')
    types, days = document_types(), Institution.get().accept_days
    for study in studies:
        study.incomplete = incomplete(study, types) if study.status == Study.Status.APPLIED else []
        study.lapsed = lapsed(study, days)
    auditions = (
        request.user.within(Course.objects.filter(term=term, kind=Course.Kind.AUDITION), 'view_applications', 'run_auditions').select_related('program', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .filter(Q(size__gt=0) | Q(program__isnull=False))
        .order_by(F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    if subject.isdigit():  # its own auditions and those everyone takes
        auditions = auditions.filter(Q(subject=subject) | Q(subject__isnull=True))
    decidable = recommendations(term, request.user) if 'decide_applications' in request.user.roles else []
    for s in decidable:
        s.verb = VERBS.get(s.advice)
    accepted = (  # places taken up, waiting for the Immatrikulation
        request.user.within(Study.objects.filter(start_term=term, status=Study.Status.ADMITTED, terms__term=F('start_term')), 'manage_students')
        .select_related('person', 'program', 'subject').order_by('person__last_name')
    )
    for s in accepted:
        s.waiting = s.missing()
    shown, columns = request.GET.get('alle', ''), []
    for status in BOARD:  # long columns end in a link, so the page stays readable
        found = [s for s in studies if s.status == status]
        columns.append((Study.Status(status), found if status == shown else found[:CARDS], len(found)))
    today = timezone.localdate()
    return render(request, 'admissions/board.html', {
        'term': term,
        'terms': terms,
        'subject': subject,
        'q': q,
        'subjects': Subject.objects.filter(pk__in=request.user.within(Study.objects.filter(start_term=term, status__in=BOARD), 'view_applications').values('subject')),
        'columns': columns,
        'incomplete': sum(1 for s in studies if s.incomplete) if any(c.program_id and c.deadline and c.deadline >= today and not c.released for c in auditions) else 0,
        'auditions': auditions,
        'running': any(not c.released for c in auditions),
        'vacancies': vacancies(term, request.user),
        'decidable': decidable, 'verbs': VERBS,
        'accepted': accepted,
    })


@require('teacher')
def juries(request):
    auditions = (
        Course.objects.filter(Q(staff=request.user) | Q(participations__recusals__substitute=request.user), kind=Course.Kind.AUDITION).distinct()
        .select_related('term', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .order_by('-term__start', F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    return render(request, 'admissions/juries.html', {'auditions': auditions})


def next_stages(course):
    """The stages after this one, as the stages say they follow it: the video round leads to the Hauptfach exam of its
    programme, that to the parts every Bachelor applicant plays."""
    return course.following.filter(kind=Course.Kind.AUDITION)


def held(p):
    """(start, end, room) of an applicant's appointment: the slot, until the next one, in the booking around it."""
    booking = next((b for b in p.course.bookings.select_related('room') if b.start <= p.slot < b.end), None)
    later = p.course.participations.filter(slot__gt=p.slot).order_by('slot').values_list('slot', flat=True).first()
    return p.slot, min(filter(None, [later, booking and booking.end]), default=p.slot + timedelta(minutes=30)), booking and booking.room


def after(course):
    """What follows a stage, for whoever passes it: each next stage with its first date and room, as far as they are booked."""
    found = []
    for c in next_stages(course).prefetch_related('bookings__room'):
        first = min(c.bookings.all(), key=lambda b: b.start, default=None)
        found.append(f'{c.title}: {date_format(timezone.localtime(first.start), "l, j. F Y, H:i")} Uhr, {first.room} ({first.room.building})' if first else f'{c.title}: Termin folgt')
    return found


def invite(p):
    """The invitation to an appointment of the audition, as a letter in the Postfach and the file: when, where, what to play, what
    follows; printable, e.g. for a visa."""
    start, _, room = held(p)
    p.study.notify(
        'einladung_eignungspruefung', reverse('admissions:mine'), pruefung=p.course.title, termin=date_format(timezone.localtime(start), 'l, j. F Y, H:i'),
        raum=f'{room} ({room.building})' if room else 'wird mitgeteilt', programm=p.note or '–', danach='\n'.join(f'– {line}' for line in after(p.course)) or '–',
    )


def calendar(request, pk):
    """An applicant's appointment as a calendar file."""
    p = get_object_or_404(Participation.objects.select_related('course'), pk=pk, study__person=request.user, slot__isnull=False, course__kind=Course.Kind.AUDITION)
    start, end, room = held(p)
    return ics(request, [(f'eignungspruefung-{p.pk}', start, end, p.course.title, f'{room}, {room.building}' if room else '')], 'eignungspruefung.ics')


def programme(note):
    """An applicant's programme, a work a line: [(second it starts in the video or None, that time as written, work)]."""
    found = []
    for line in filter(None, map(str.strip, note.splitlines())):
        at = re.match(r'(\d{1,2}):([0-5]\d)\s+(.+)', line)
        found.append((int(at[1]) * 60 + int(at[2]), f'{at[1]}:{at[2]}', at[3]) if at else (None, '', line))
    return found


def blank(request, pk, kind):
    """A form to print and sign for an application, like the parents' consent, its blanks filled in where known."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk, person=request.user)
    kind = get_object_or_404(DocumentType.objects.exclude(template=''), key=kind)
    person = study.person
    body = kind.template.format_map(defaultdict(str, {
        'name': person.legal_name, 'geburtsdatum': date_format(person.birth_date, 'j. F Y') if person.birth_date else '________',
        'studiengang': study.program.title, 'hauptfach': study.subject, 'hochschule': Institution.get().name,
    }))
    return render(request, 'admissions/blank.html', {'kind': kind, 'body': body})


def audition(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'program', 'subject'), pk=pk, kind=Course.Kind.AUDITION)
    office, correct = person.may('run_auditions', course), person.may('correct_scores', course)
    member = course.staff.filter(pk=person.pk).exists()
    if not (office or correct or member or course.participations.filter(recusals__substitute=person).exists()):
        raise PermissionDenied('Eine Eignungsprüfung sehen ihre Kommission, das Amt, das sie durchführt, und der Prüfungsausschuss.')

    def sheet():  # a substitute sees only whom they mark
        staff, rows = panel.sheet(course)
        return staff, rows if office or correct or member else panel.substituting(rows, person)

    staff, rows = sheet()
    columns, refused = panel.scorers(staff, rows) if correct else [] if office and not member else [person], None  # the office that runs it marks nothing
    if request.method == 'POST':
        if course.released or not columns:
            raise PermissionDenied('Wertungen tragen die Kommission und, berichtigend, der Prüfungsausschuss ein.')
        try:
            panel.save(request.POST, rows, columns, decide=correct, correct=correct)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Wertungen gespeichert.')
            return redirect(course)
    if not (office or correct):
        panel.blind(rows, person)
    if refused:
        panel.retype(rows, request.POST, refused)
    invited = set(Participation.objects.filter(course__in=next_stages(course)).values_list('study', flat=True)) if office else set()
    if request.GET.get('sortierung') == 'punkte' and not any(getattr(p, 'blind', False) for p in rows):  # a ranking would tell the others' marks
        rows.sort(key=lambda p: p.grade if p.grade is not None else (p.mean or 0), reverse=True)
    return render(request, 'admissions/audition.html', {
        'course': course,
        'staff': staff,
        'rows': rows, 'timed': any(p.slot for p in rows), 'noted': any(p.note for p in rows),
        'columns': [e.pk for e in columns],
        'office': office, 'correct': correct, 'rule': panel.rule(rows), 'composer': office, 'short': course.panel().shortfall,
        'recusable': [] if course.released else panel.recusable(rows, person),
        'candidates': substitutes(course) if office and any(p.vacant for p in rows) else [],
        'history': bool(Course.seen_by(person, course.pk)),
        'slots': SlotForm(person=person, course=course) if office else None,
        'stages': next_stages(course) if office else [],
        'ready': sum(1 for p in rows if p.passed and p.study.status == Study.Status.APPLIED and p.study_id not in invited),  # not yet at the next stage
        'missing': sum(1 for p in rows if panel.due(p)),
        'bookings': course.bookings.select_related('room'),
        'reachable': Study.reachable(person, Study.objects.filter(participations__course=course)),
    })


@require('run_auditions')
@require_POST
def plan(request, pk):
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    form = SlotForm(request.POST, person=request.user, course=course)
    rows = list(course.participations.filter(slot=None, study__status=Study.Status.APPLIED).select_related('study__person').order_by('created'))
    if form.is_valid() and rows:
        try:
            panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user, form.cleaned_data['pause'])
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            for p in rows:
                invite(p)
            messages.success(request, f'{len(rows)} Termine vergeben, Einladungen versandt.')
    else:
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben.' if rows else 'Alle Bewerbenden haben schon einen Termin.')
    return redirect(course)


@require('run_auditions')
@require_POST
def advance(request, pk):
    """Invite everyone who passed this stage to the next one, once it is closed and nobody's result can change."""
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    if not course.released:
        messages.error(request, 'Erst wenn die Stufe abgeschlossen ist: bis dahin kann sich jedes Ergebnis noch ändern.')
        return redirect(course)
    targets = list(next_stages(course))
    names = ' und '.join(f'„{t.title}“' for t in targets)
    moved = set()
    for p in course.participations.filter(passed=True, study__status=Study.Status.APPLIED).select_related('study__person'):
        for target in targets:  # the programme goes along, without the times in the video
            if Participation.objects.get_or_create(course=target, study=p.study, defaults={'note': re.sub(r'^\d{1,2}:[0-5]\d\s+', '', p.note, flags=re.M)})[1]:
                moved.add(p.study)
    for study in moved:
        texts.send('naechste_stufe', study.person, reverse('admissions:mine'), pruefung=course.title, stufen=names)
    messages.success(request, f'{len(moved)} Bewerbende zu {names} eingeladen.')
    return redirect(course)


@require('run_auditions')
@require_POST
def close(request, pk):
    """Lock the marks once every applicant has a result: applicants see a closed stage as passed or not."""
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions', 'correct_scores'), pk=pk, kind=Course.Kind.AUDITION)
    if course.released and not request.user.may('correct_scores', course):
        raise PermissionDenied('Abgeschlossene Wertungen öffnet nur der Prüfungsausschuss wieder.')
    if not course.released and (missing := panel.gaps(panel.sheet(course)[1])):
        messages.error(request, missing)
        return redirect(course)
    course.released = None if course.released else timezone.now()
    course.save(update_fields=['released'])
    messages.success(request, 'Wertungen gesperrt.' if course.released else 'Wertungen wieder geöffnet.')
    return redirect(course)


def application(request, pk):
    person = request.user
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'start_term', 'teacher'), pk=pk)
    office = person.may('view_applications', study)
    juror = Course.objects.filter(kind=Course.Kind.AUDITION, staff=person, participations__study=study).exists()
    if not (office or juror or study.person == person):
        raise PermissionDenied
    steps = study.stages = list(stages().filter(study=study))
    missing = study.missing() if study.status == Study.Status.ADMITTED else []
    accepted = study.terms.filter(term=study.start_term).first()
    personal = office or study.person == person  # the jury judges the performance: no contact or personal data
    items = checklist(study)
    return render(request, 'admissions/application.html', {
        'study': study,
        'personal': personal,
        'stages': steps,
        'recommendation': verdict(steps),
        'pending': bool(steps) and verdict(steps) is None,  # no place nor Nachrückliste before every stage has its result
        'remind': office and (accepted and missing or study.status == Study.Status.APPLIED and uploads_open(study) and incomplete(study)),
        'show_results': office or juror,
        'checklist': items if personal else [item for item in items if item['kind'].jury],
        'documents': study.documents.exclude(message=None) if personal else [],
        'decide': person.may('decide_applications', study) and study.status in UNDECIDED and not accepted,
        'enrol': person.may('manage_students', study),
        'history': bool(tables(person.roles)),
        'accepted': accepted,
        'decisions': VERBS.items(),
        'missing': missing,
    })


@require('decide_applications')
@require_POST
def decide(request, pk):
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'decide_applications'), pk=pk)
    status = request.POST.get('status')
    if status not in DECISIONS:
        raise PermissionDenied
    if study.status not in UNDECIDED or study.terms.filter(term=study.start_term).exists():
        messages.error(request, 'Der Studienplatz ist angenommen; daran ändert keine Entscheidung mehr etwas.' if study.status == Study.Status.ADMITTED else 'Diese Bewerbung ist abgeschlossen.')
        return redirect(study.get_absolute_url())
    steps = list(stages().filter(study=study))
    if status != Study.Status.REJECTED and steps and verdict(steps) is None:
        messages.error(request, 'Noch nicht alle Prüfungsstufen abgeschlossen.')
        return redirect(study.get_absolute_url())
    offer(study, status)
    messages.success(request, f'{study.person}: {study.get_status_display()}. Bescheid versandt.')
    return redirect(study.get_absolute_url())


@require('decide_applications')
@require_POST
def decide_many(request):
    """Decide the ticked applications at once, each as its results advise or all alike; each gets its Bescheid. First a
    preview: who gets which, one of each in full; the Bescheide go out once it is confirmed."""
    term = get_object_or_404(Term, pk=request.POST.get('semester'))
    chosen = request.POST.get('status', '')
    if chosen and chosen not in DECISIONS:
        raise PermissionDenied
    advice = {s.pk: s.advice for s in recommendations(term, request.user)}
    ticked = set(map(int, request.POST.getlist('study')))
    studies = request.user.within(Study.objects.filter(pk__in=ticked & advice.keys()), 'decide_applications').select_related('person', 'program', 'subject', 'start_term')
    if not request.POST.get('bestaetigt'):
        days, plan = Institution.get().accept_days, defaultdict(list)
        for study in studies.order_by('person__last_name'):
            plan[chosen or advice[study.pk]].append(study)
        return render(request, 'admissions/decide.html', {
            'term': term, 'chosen': chosen, 'ticked': [s.pk for group in plan.values() for s in group],
            'groups': [
                (VERBS[status], BESCHEIDE[status], group, group[0].wording(DECISIONS[status], frist=f'{accept_by(group[0], days):%d.%m.%Y}'))
                for status, group in plan.items() if status
            ],
            'waiting': plan.get(None, []),
        })
    decided, skipped = Counter(), 0
    for study in studies:
        if status := chosen or advice[study.pk]:
            offer(study, status)
            decided[status] += 1
        else:
            skipped += 1
    counts = [plural(decided[s], *forms) for s, forms in BESCHEIDE.items() if decided[s]]
    messages.success(request, ' '.join(filter(None, [
        f'Bescheide versandt: {", ".join(counts)}.' if counts else 'Nichts entschieden.',
        skipped and f'{plural(skipped, "Bewerbung wartet", "Bewerbungen warten")} noch auf die Ergebnisse der anderen im selben Hauptfach.',
    ])))
    return redirect(f"{reverse('admissions:board')}?semester={term.pk}#entscheiden")


@require('view_applications')
@require_POST
def remind(request):
    """Ask for what is still missing: the documents of applications whose deadline is still to come, or what a place
    taken up needs before the Immatrikulation. The ticked ones, else every incomplete application of the term; nobody
    twice a day."""
    studies = request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'view_applications')
    if picked := request.POST.getlist('study'):
        studies = studies.filter(pk__in=picked)
    else:
        studies = studies.filter(start_term=request.POST.get('semester'), status=Study.Status.APPLIED)
        if (subject := request.POST.get('fach', '')).isdigit():
            studies = studies.filter(subject=subject)
    types, today, sent = document_types(), timezone.localdate(), 0
    for study in studies.prefetch_related('person__documents', Prefetch('participations', stages(), to_attr='stages')):
        if study.status == Study.Status.APPLIED and uploads_open(study):
            due = min(p.course.deadline for p in study.stages if p.course.program_id and p.course.deadline)
            key, items, values = 'unterlagen_fehlen', incomplete(study, types), {'frist': date_format(due, 'j. F Y')}
        elif study.status == Study.Status.ADMITTED and study.terms.filter(term=study.start_term).exists():  # the place taken up
            key, items, values = 'immatrikulation_offen', study.missing(), {}
        else:
            continue
        if not items:
            continue
        subject, body = study.wording(key, fehlt='\n'.join(f'– {item}' for item in items), **values)
        if not study.person.messages.filter(subject=subject, created__date=today).exists():
            Message.send(study.person, subject, body, reverse('admissions:mine'))
            sent += 1
    messages.success(request, f'{plural(sent, "Erinnerung", "Erinnerungen")} versandt.' if sent else 'Niemand zu erinnern, oder heute schon erinnert.')
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else 'admissions:board')


@require('decide_applications')
@require_POST
def move_up(request, pk):
    """Nachrücken: offers not accepted in time lapse, and the free places go to the best on the Nachrückliste."""
    intake = get_object_or_404(request.user.within(Course.objects.all(), 'decide_applications'), pk=pk, kind=Course.Kind.AUDITION, program__isnull=False)
    gone, waiting = next(((l, w) for c, l, w in vacancies(intake.term, request.user) if c == intake), ([], []))
    for study in gone:
        study.set_status(Study.Status.LEFT)
    for study in waiting:
        offer(study, Study.Status.ADMITTED)
    messages.success(request, f'{plural(len(waiting), "Person", "Personen")} von der Nachrückliste zugelassen. Bescheide versandt.' if waiting else 'Abgelaufene Zulassungen geschlossen.')
    return redirect(f"{reverse('admissions:board')}?semester={intake.term_id}")


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
    entrance = ['entrance', 'entrance_date', 'entrance_region']
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
        study.accept_by, study.lapsed = accept_by(study, days), lapsed(study, days)
        study.waiting = study.missing() if study.status == Study.Status.ADMITTED and study.accepted else []
        study.checklist = checklist(study, types) if study.status == Study.Status.APPLIED else []
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
    return render(request, 'admissions/mine.html', {
        'studies': studies, 'documents': person.documents.exclude(message=None), 'accept': AcceptForm(instance=person),
        'insurers': Institution.get().insurers.splitlines(), 'bank': Institution.get(),
    })


@require_POST
def upload(request, pk):
    """Hand in a document or a video for an application, until its deadline; the office running auditions at any time."""
    study = get_object_or_404(Study.objects.select_related('person'), pk=pk, status=Study.Status.APPLIED)
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
    messages.success(request, f'„{Document.hand_in(study, kind, file)}“ hochgeladen.')
    return back


def document(request, pk):
    """A handed-in file: for its owner, the student office and the office deciding results, whoever sees applications, and the candidate's
    examiners: of the audition while applying what is meant for the jury, of the thesis for the thesis. A CV or certificate
    counts for all of the person's applications."""
    d = get_object_or_404(Document.objects.select_related('study', 'kind'), pk=pk)
    person = request.user
    applications = Study.objects.filter(status__in=Study.APPLICATION)
    applications = applications.filter(person=d.person_id) if d.kind.shared else applications.filter(pk=d.study_id)
    examining = Course.objects.filter(staff=person)
    if not (
        d.person_id == person.pk or person.may('decide_results', d) or person.may('manage_students', d) or person.within(applications, 'view_applications').exists()
        or d.kind.jury and examining.filter(kind=Course.Kind.AUDITION, participations__study__in=applications).exists()
        or d.kind_id == 'abschlussarbeit' and examining.filter(kind=Course.Kind.THESIS, participations__study=d.study_id).exists()
    ):
        raise PermissionDenied
    if not d.file:
        raise Http404
    return FileResponse(d.file.open('rb'), filename=d.title if Path(d.title).suffix else d.title + Path(d.file.name).suffix)  # the type follows the name


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


def review(request, pk):
    """Sichtung: the video round one application at a time. Watch, score, on to the next without a score of one's own."""
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject', 'program'), pk=pk, kind=Course.Kind.AUDITION, video=True)
    member = course.staff.filter(pk=person.pk).exists()
    juror = member or course.participations.filter(recusals__substitute=person).exists()
    if not (juror or person.may('run_auditions', course)):
        raise PermissionDenied('Die Videos sichten die Kommission und das Amt, das die Eignungsprüfung durchführt.')
    _, everyone = panel.sheet(course)
    if juror and not member:  # a substitute watches only whom they mark
        everyone = panel.substituting(everyone, person)
    filmed = set(Document.objects.filter(study__in=[p.study_id for p in everyone], kind__condition=Condition.VIDEO).values_list('study', flat=True))
    rows = [p for p in everyone if p.study_id in filmed]  # nothing to watch yet: not in the jury's way
    if not rows:
        return redirect(course)
    panel.blind(rows, person)
    mine = {p.pk: dict((e.pk, v) for e, v in p.marks).get(person.pk) for p in rows}
    todo = {p.pk for p in rows if mine[p.pk] is None and person.pk not in p.recused}
    picked = request.GET.get('bewerbung', '')
    current = next((p for p in rows if str(p.pk) == picked), None) or next((p for p in rows if p.pk in todo), rows[0])
    at, error = rows.index(current), ''
    here = reverse('admissions:review', args=[course.pk])
    if request.method == 'POST':
        if course.released or not juror:
            raise PermissionDenied
        try:
            panel.save(request.POST, [current], [person], decide=False)
        except panel.Refused as refused:  # shown by the field, with what was typed
            error, mine[current.pk] = str(refused), request.POST.get(f'score-{current.pk}-{person.pk}', '')
        else:
            messages.success(request, f'Wertung für {current.study.person.first_name} {current.study.person.last_name} gespeichert.')
            following = next((p for p in rows[at + 1:] + rows[:at] if p.pk in todo - {current.pk}), None)
            return redirect(f'{here}?bewerbung={following.pk}' if following else course)
    return render(request, 'admissions/review.html', {
        'course': course,
        'p': current, 'error': error, 'programme': programme(current.note),
        'juror': juror,
        'mine': mine[current.pk],
        'videos': current.study.documents.filter(kind__condition=Condition.VIDEO),
        'position': everyone.index(current) + 1,  # numbered as on the list of all applications
        'count': len(everyone),
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
    if accept_by(study, Institution.get().accept_days) < timezone.localdate():
        messages.error(request, 'Die Frist zur Annahme ist abgelaufen.')
        return redirect('admissions:mine')
    form = AcceptForm(request.POST, instance=request.user)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect('admissions:mine')
    person = form.save()
    StudyTerm.objects.get_or_create(study=study, term=study.start_term)
    texts.send('studienplatz_angenommen', person, beitrag=number_format(study.start_term.fee, 2), frist=f'{study.start_term.deadline:%d.%m.%Y}')
    messages.success(request, 'Studienplatz angenommen.')
    return redirect('admissions:mine')
