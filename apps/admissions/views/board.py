"""The office's side: the board of applications, deciding them and their Bescheide."""
from collections import Counter, defaultdict

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Exists, F, Min, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import texts
from core.access import CODENAMES, require
from core.apps import plural
from core.history import tables
from core.models import Institution, Message, Term
from core.shortcuts import ids
from core.text import matching
from core.templatetags.marks import mark
from courses.models import Course
from curriculum.models import Subject
from students.models import DocumentType, Study, StudyTerm
from ..papers import checklist, document_types, incomplete, papers, stages
from .common import accept_by, lapsed, uploads_open

CARDS = 40  # per column of the board before „alle anzeigen“
BOARD = [Study.Status.APPLIED, Study.Status.ADMITTED, Study.Status.WAITLISTED, Study.Status.REJECTED, Study.Status.ENROLLED]
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
        raise PermissionDenied('Bewerbungen sind für Bewerbende, ihre Kommissionen und die Ämter, die sie bearbeiten.')
    return redirect('admissions:mine')


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
    study.notify(DECISIONS[status], reverse('admissions:mine'), frist=f'{accept_by(study, Institution.get().accept_days):%d.%m.%Y}', begruendung=grounds(study))


def grounds(study):
    """Why an application is turned down, for its Bescheid: the part of the Eignungsprüfung not passed, with its result."""
    failed = study.participations.filter(course__kind=Course.Kind.AUDITION, course__released__isnull=False, passed=False).select_related('course').first()
    if failed is None:
        return 'Die Voraussetzungen für eine Zulassung liegen nicht vor.'
    course = failed.course
    part = {'Video': 'die Videostufe', 'Hauptfach': 'die Prüfung im Hauptfach'}.get(course.stage, f'den Prüfungsteil „{course.stage}“')
    result = (
        'nicht angetreten' if failed.grade is None
        else f'{mark(failed.grade)} von {course.max_points} Punkten, bestanden ab {mark(course.pass_mark)}' if course.points
        else f'Note {mark(failed.grade)}'
    )
    return texts.block('ablehnung_begruendung', pruefungsteil=part, ergebnis=result)


@require('view_applications')
def board(request):
    terms = Term.objects.filter(courses__kind=Course.Kind.AUDITION).distinct().order_by('-start')
    picked = request.GET.get('semester', '')
    term = (picked.isdigit() and terms.filter(pk=picked).first()) or terms.filter(start__gt=timezone.localdate()).first() or terms.last()
    studies = ranked(
        request.user.within(Study.objects.filter(start_term=term, status__in=BOARD), 'view_applications').select_related('person', 'program', 'subject')
        .annotate(slot=Min('participations__slot', filter=Q(participations__slot__gte=timezone.now(), participations__course__kind=Course.Kind.AUDITION)))
        .prefetch_related(Prefetch('participations', stages(), to_attr='stages'), 'person__documents', 'person__studies')
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
        papers(request.user.within(Study.objects.filter(start_term=term, status=Study.Status.ADMITTED, terms__term=F('start_term')), 'manage_students'))
        .select_related('subject').order_by('person__last_name')
    )
    for s in accepted:
        s.waiting = s.missing()
    ready = any(not s.waiting for s in accepted)
    shown, columns = request.GET.get('alle', ''), []
    for status in BOARD:  # long columns end in a link, so the page stays readable
        found = [s for s in studies if s.status == status]
        if status == Study.Status.WAITLISTED:  # a list per Studiengang and Hauptfach, each in the order of its ranking
            found.sort(key=lambda s: (str(s.program), str(s.subject)))
            for n, s in enumerate(found):
                s.place = 1 if n == 0 or (s.program_id, s.subject_id) != (found[n - 1].program_id, found[n - 1].subject_id) else found[n - 1].place + 1
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
        'accepted': accepted, 'ready': ready,
    })


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
        'trail': [(None, reverse('admissions:board' if office else 'admissions:juries'))], 'study': study,
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
    ticked = set(ids(request.POST.getlist('study')))
    studies = request.user.within(Study.objects.filter(pk__in=ticked & advice.keys()), 'decide_applications').select_related('person', 'program', 'subject', 'start_term')
    if not request.POST.get('bestaetigt'):
        days, plan = Institution.get().accept_days, defaultdict(list)
        for study in studies.order_by('person__last_name'):
            plan[chosen or advice[study.pk]].append(study)
        return render(request, 'admissions/decide.html', {
            'term': term, 'chosen': chosen, 'ticked': [s.pk for group in plan.values() for s in group],
            'groups': [
                (VERBS[status], BESCHEIDE[status], group, group[0].wording(DECISIONS[status], frist=f'{accept_by(group[0], days):%d.%m.%Y}', begruendung=grounds(group[0])))
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
    if picked := ids(request.POST.getlist('study')):
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
