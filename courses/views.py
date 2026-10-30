import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import history, texts
from core.access import CODENAMES, kept, require
from core.apps import plural
from core.dates import day as understood
from core.forms import again, sent
from core.models import Holiday, Institute, Institution, Office, Person, Term
from core.text import fold, matching
from core.views import without_term
from curriculum.models import Module, Program
from rooms.models import Booking, BookingCategory
from rooms.views import agenda, around, called_off, legend, week
from students.models import Study
from . import panel
from .forms import AddForm, CourseForm, DateForm, EnsembleForm, Lessons, PlanForm, TeachingForm
from .models import Appointment, Area, Course, Ensemble, Format, Participation, Recusal, Reduction, Signup, marked, owed_module

TEACHING = [Course.Kind.COURSE, Course.Kind.LESSON]
MANAGERS = {Course.Kind.EXAM: 'manage_exams', Course.Kind.RECOGNITION: 'recognise', Course.Kind.AUDITION: 'run_auditions'}


def deputat(courses, person):
    """A teacher's load in SWS: each course's hours; per participant in one-to-one teaching, per ensemble they coach in
    ensemble courses."""
    courses = (
        courses.filter(cancelled=None).select_related('format', 'term').prefetch_related('participations__module', 'origin__participations__module')
        .annotate(size=Count('participations', distinct=True), coached=Count('ensembles', filter=Q(ensembles__coach=person), distinct=True))
    )
    return sum(sws(c, c.coached) for c in courses)


def sws(course, coached):
    """A course's SWS for one of its teachers: a lesson class each participation by the weekly minutes its plan gives,
    60 to an SWS (ThürLVVO § 2 Abs. 2), else the course's hours; a class of a coming term not yet filled as the one it was
    taken over from. Otherwise the hours as often as `share` says. Reads `participations.all()` with their modules, and
    the origin's; prefetch them for many courses."""
    if course.kind == Course.Kind.LESSON:
        taking = course.participations.all() or (course.origin.participations.all() if course.origin_id and course.term.start > timezone.localdate() else [])
        return sum(minutes(p.module) or course.hours or 0 for p in taking)
    return (course.hours or 0) * share(course, coached)


def minutes(module):
    """The SWS of one lesson in a plan whose form says its weekly minutes, 'E 60' or 'E 90'; None where it does not."""
    found = module and re.search(r'(\d+)\s*$', module.description)
    return Decimal(found[1]) / 60 if found else None


def share(course, coached):
    """How many times a course's hours count for one of its teachers: per participant in one-to-one teaching, whether a
    lesson class or a course whose Lehrform counts so; per ensemble coached in ensemble courses; else once."""
    if course.kind == Course.Kind.LESSON or course.format_id and course.format.per_head:
        return course.size
    return coached if course.allocation == Course.Allocation.ENSEMBLE else 1


def credited(course):
    """The modules a course counts for, by title: [(title, [(program, codes)])], so a course in many plans reads in a few lines."""
    found = defaultdict(lambda: defaultdict(list))
    for m in course.modules.select_related('program').order_by('title', 'program__degree', 'program__name', 'code'):
        found[m.title][m.program].append(m.code)
    return [(title, list(programs.items())) for title, programs in found.items()]


def teaches(person, course):
    """Who runs a course: its staff, and whoever manages courses of its kind where it belongs."""
    return course.staff.filter(pk=person.pk).exists() or person.may(MANAGERS.get(course.kind, 'manage_courses'), course)


def ahead():
    """Subquery: participations of the same course registered earlier (waitlist position)."""
    earlier = Participation.objects.filter(course=OuterRef('course'), created__lt=OuterRef('created'))
    return Subquery(earlier.values('course').annotate(n=Count('pk')).values('n')[:1])


def index(request):
    return redirect('courses:catalogue' if 'manage_courses' in request.user.roles else 'courses:mine')


def catalogue(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term and not Term.shown(person).filter(pk=term.pk).exists():
        term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester mit Veranstaltungen')
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    program = request.GET.get('studiengang', str(studies[0].program_id) if studies else '')
    upcoming = Booking.objects.filter(course=OuterRef('pk'), start__gte=timezone.now()).order_by('start')
    courses = (
        term.courses.filter(kind__in=TEACHING).select_related('subject', 'area', 'format').prefetch_related('staff')
        .annotate(
            size=Count('participations'),
            next_start=Subquery(upcoming.values('start')[:1]),
            next_room=Subquery(upcoming.values('room__name')[:1]),
            booked=Exists(Booking.objects.filter(course=OuterRef('pk'))),
        )
    )
    offered = courses
    scoped = 'manage_courses' in person.roles and not person.everywhere('manage_courses')  # an Institut's office starts with its own
    reach = request.GET.get('bereich', 'eigene' if scoped else '')
    if scoped and reach == 'eigene':
        courses = person.within(courses, 'manage_courses')
    if program.isdigit():
        courses = courses.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
    area, form = request.GET.get('fachgebiet', ''), request.GET.get('lehrform', '')
    if area.isdigit():
        courses = courses.filter(area=area)
    if form.isdigit():
        courses = courses.filter(format=form)
    if open_ := term.planning and request.GET.get('stand') == 'offen':  # what its teachers have yet to confirm
        courses = courses.filter(confirmed=None, cancelled=None).exclude(origin=None)
    if q := request.GET.get('q', '').strip():
        courses = matching(courses, q, 'title', 'staff__last_name', 'subject__name', 'area__name', 'description', 'modules__title', 'modules__code')  # also by the module's name in the Studienplan
    passed = Participation.objects.filter(study__in=studies, passed=True, module__isnull=False).values('module')
    counting = Module.objects.filter(courses=OuterRef('pk'), program__in=studies.values('program'))
    courses = courses.annotate(counting=Exists(counting), owed=Exists(counting.exclude(pk__in=passed)))
    done = request.GET.get('bestandene') == '1'
    if studies and not done:  # a student sees what is still to pass first; the passed on asking
        courses = courses.exclude(counting=True, owed=False)
    mine = set(Participation.objects.filter(study__in=studies, course__term=term).values_list('course', flat=True))
    return render(request, 'courses/catalogue.html', {
        'term': term,
        'terms': Term.shown(person).filter(courses__kind__in=TEACHING).distinct().order_by('-start').filter(**since(request.user)), 'open': open_,
        'courses': courses.order_by(F('area__order').asc(nulls_last=True), 'area__name', 'title'),
        'programs': Program.planned(),
        'program': program,
        'areas': Area.objects.filter(courses__in=offered.values('pk')).distinct(),
        'area': area,
        'formats': Format.objects.filter(courses__in=offered.values('pk')).distinct(),
        'form': form,
        'q': q,
        'office': 'manage_courses' in person.roles,
        'scoped': scoped, 'reach': reach, 'student': studies.exists(), 'done': done, 'mine': mine,
    })


def accompanied(course):
    """The auditions of a Korrepetition course's participants this term, where someone of its staff plays with them."""
    return Participation.objects.filter(
        course__term=course.term, course__kind=Course.Kind.AUDITION, course__video=False, course__subject__isnull=False, study__in=course.participations.values('study'),
    )


def course(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=TEACHING, term__in=Term.shown(person))
    staffed = teaches(person, course)
    office = person.may('manage_courses', course)

    def sheet():  # a coach of an ensemble course sees and grades the members of their own ensembles
        staff, rows = panel.sheet(course)
        if course.allocation == Course.Allocation.ENSEMBLE and staffed and not office:
            coached = set(Study.objects.filter(ensembles__course=course, ensembles__coach=person).values_list('pk', flat=True))
            rows = [p for p in rows if p.study_id in coached]
        return staff, rows

    staff, rows = sheet()
    refused = None
    if request.method == 'POST' and 'begleitung' in request.POST:  # the accompaniment: who plays at which audition
        if not staffed:
            raise PermissionDenied
        players = {str(t.pk): t for t in course.staff.all()}
        for p in accompanied(course).filter(pk__in=request.POST.getlist('teilnahme')):
            p.accompanist = players.get(request.POST.get(f'begleitung-{p.pk}'))
            p.save(update_fields=['accompanist'])
        messages.success(request, 'Begleitung eingeteilt.')
        return redirect(course)
    if request.method == 'POST':
        if not staffed or course.released:
            raise PermissionDenied('Ergebnisse tragen die Lehrenden der Veranstaltung ein.')
        post = request.POST  # „alle bestanden“: every row without a result passed, as a Studienleistung is
        if request.POST.get('alle_bestanden'):
            post = {f'grade-{p.pk}': '' for p in rows if p.passed is None and not p.remark} | {f'passed-{p.pk}': '1' for p in rows if p.passed is None and not p.remark}
        try:
            panel.save(post, rows, [], decide=True)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Ergebnisse gespeichert.')
            return redirect(course)
    rows.sort(key=lambda p: (p.created, p.pk))
    for n, p in enumerate(rows):
        p.waiting = course.capacity is not None and n >= course.capacity
    if refused:
        panel.retype(rows, request.POST, refused)
    own = [p for p in rows if p.study.person_id == person.pk]
    counting = list(person.studies.filter(status=Study.Status.ENROLLED, program__modules__courses=course).distinct())
    eligible = [s for s in counting if owed_module(course, s)]  # a module of it still to pass
    upcoming = course.bookings.filter(start__gte=timezone.now())
    if course.allocation == Course.Allocation.ENSEMBLE and staffed and not person.may('manage_courses', course):  # a coach: the course's own dates and their ensembles'
        upcoming = upcoming.filter(Q(ensemble=None) | Q(ensemble__coach=person))
    if course.kind == Course.Kind.LESSON and not staffed:
        upcoming = upcoming.filter(participants=person)  # one's own lessons, not everyone's
    elif not staffed:  # the dates for everyone, and those of one's own ensemble
        upcoming = upcoming.filter(Q(participants=None) | Q(participants=person) | Q(person=person)).distinct()
    playing = None
    if course.kind == Course.Kind.LESSON and Course.objects.filter(accompaniment=course).exists():  # accompaniment at auditions: who plays when, what
        from admissions.views import held  # which imports from here

        playing = list(accompanied(course).select_related('study__person', 'study__subject', 'course').order_by(F('slot').asc(nulls_last=True), 'study__person__last_name'))
        times = {p.pk: held(p) for p in playing if p.slot}  # where, and who plays at the same time: one accompanist cannot be in two places
        for p in playing:
            p.room = p.pk in times and times[p.pk][2]
            p.together = ', '.join(
                o.study.person.name for o in playing if o is not p and o.accompanist_id == p.accompanist_id and p.pk in times and o.pk in times
                and times[o.pk][0] < times[p.pk][1] and times[p.pk][0] < times[o.pk][1]
            )
    running = course.term.end >= timezone.localdate()  # what is over is planned no more
    lessons = lesson_times(course) if staffed and running and course.kind == Course.Kind.LESSON and playing is None else []
    timetable = lessons and Lessons(sent(request, 'lessons'), initial=[row for _, row in lessons], form_kwargs={'person': person})
    ensembles = list(
        (course.ensembles if staffed else course.ensembles.filter(members__person=person).distinct())
        .select_related('coach', 'wish').prefetch_related('members__person', 'members__subject')
    )
    ensembles.sort(key=lambda e: (e.coach_id != person.pk, e.coach_id is not None, e.wish_id != person.pk))  # one's own, then those still open
    return render(request, 'courses/course.html', {
        'course': course, 'playing': playing, 'players': list(course.staff.all()) if playing is not None else [],
        'lengths': sorted(Counter(minutes(p.module) or course.hours for p in course.participations.select_related('module')).items(), reverse=True) if course.kind == Course.Kind.LESSON else [],
        'staff': staff,
        'rows': rows if staffed else [],
        'own': own,
        'wished': course.signups.filter(study__person=person).first(),
        'signups': course.signups.select_related('study__person', 'study__program', 'study__subject') if staffed else [],
        'count': len(rows),
        'staffed': staffed,
        'eligible': eligible, 'completed': counting and not eligible and not own,
        'unmarked': sum(1 for p in rows if p.passed is None and not p.remark) if staffed and course.kind == Course.Kind.COURSE and not course.modules.filter(exam=True).exists() else 0,
        'counts_for': besides_focus(course, eligible[0]) if eligible and not own else '',
        'clashing': clashes(person, course.bookings.filter(start__gte=timezone.now(), participants=None)[:8]) if eligible and not own else [],
        'running': running, 'cancelling': office and not course.term.planning and not course.cancelled and running,
        'from': max(course.term.lectures_start, timezone.localdate()), 'office': office,
        'modules': credited(course),
        'bookings': upcoming.select_related('room', 'category').prefetch_related('participants', 'absent')[:8],
        'plan': PlanForm(sent(request, 'plan'), person=person, course=course) if staffed and running and not course.cancelled and course.kind != Course.Kind.LESSON else None,
        'date': DateForm(sent(request, 'date'), person=person, course=course) if staffed and running and not course.cancelled else None,
        'lessons': timetable,
        'lesson_rows': zip([s for s, _ in lessons], timetable or []),
        'ensembles': ensembles,
        'grouping': EnsembleForm(sent(request, 'ensemble'), course=course, person=person) if course.allocation == Course.Allocation.ENSEMBLE else None,
        'adding': AddForm() if staffed else None,
        'reachable': Study.reachable(person, Study.objects.filter(Q(participations__course=course) | Q(signups__course=course))) if staffed else set(),
        'panel': False,
    })


COPIED = ['kind', 'title', 'area', 'format', 'subject', 'rhythm', 'hours', 'capacity', 'allocation', 'opens', 'deadline', 'description']  # what „Duplizieren“ takes


def edit(request, pk=None):
    """Create or change a course: the office for its Institute and Studiengänge; its teachers what is theirs to decide
    (TeachingForm)."""
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING) if pk else Course(term=term, opens=term and term.course_signup_start, deadline=term and term.course_signup_end)
    initial = {}
    if not pk and (source := Course.objects.filter(pk=request.GET.get('von') or None, kind__in=TEACHING).first()):  # „Duplizieren“: filled in, saved once saved
        course = Course(term=source.term, **{f: getattr(source, f) for f in COPIED})
        initial = {'staff': list(source.staff.values_list('pk', flat=True)), 'modules': list(source.modules.values_list('pk', flat=True))}
    office = 'manage_courses' in person.roles and (not pk or person.may('manage_courses', course))
    if not (office or pk and course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    over = bool(pk) and course.term.over
    if over and not office:
        raise PermissionDenied(f'Das {course.term} ist vorbei. Korrekturen trägt {Office.holding("manage_courses") or "das zuständige Amt"} ein, mit Grund.')
    planning = person.may('plan_teaching', course) if pk else 'plan_teaching' in person.roles
    form = CourseForm(request.POST or None, instance=course, initial=initial, person=person, planning=planning, over=over) if office else TeachingForm(request.POST or None, instance=course, person=person)
    if form.is_valid():
        with history.because(form.cleaned_data.get('correction', '')):
            saved = kept(form, person, 'manage_courses') if office else form.save()
        if saved:
            messages.success(request, 'Gespeichert. Jetzt Termine planen und Teilnehmende eintragen.' if not pk else 'Gespeichert.')
            return redirect(saved)
    back = course.get_absolute_url() if pk else reverse('courses:catalogue')
    return render(request, 'courses/form.html', {
        'form': form, 'course': course, 'menu': 'courses/_nav.html', 'heading': 'Neue Veranstaltung', 'back': back,
        'note': None if office and planning else format_html(
            '{} trägt {} ein: sie zählen im Deputat und in den Studienplänen.', 'Umfang (SWS), Lehrform und Module' if office else 'Umfang (SWS), Lehrende, Module und Lehrform',
            Office.holding('plan_teaching', linked=True) or 'das zuständige Amt',
        ),
    })


@require_POST
def add(request, pk):
    """Enter an enrolled student into any course, exam included, by name or Matrikelnummer."""
    course = get_object_or_404(Course, pk=pk)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = AddForm(request.POST)
    found = Study.find(form.cleaned_data['who']) if form.is_valid() else []
    if len(found) != 1:
        names = ', '.join(str(s.person) for s in found)
        messages.error(request, f'Mehrere passen: {names}. Bitte genauer.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return redirect(course)
    study, exam = found[0], course.kind == Course.Kind.EXAM
    module = owed_module(course, study) or exam and course.modules.filter(program=study.program_id).first()  # the next module due, as the class filling does
    if exam and (refused := course.refuses(study, module)):
        messages.error(request, f'{study.person} nicht eingetragen: {refused}')
        return redirect(course)
    participation, created = Participation.objects.get_or_create(course=course, study=study, module=module)
    if not created:
        messages.success(request, f'{study.person} ist bereits eingetragen.')
    elif exam:  # the office registers whoever the regulations allow, in the registration period or not
        texts.send('pruefungsanmeldung', study.person, reverse('exams:index'), pruefung=course.title, modul=module)
        attempt = next(p.attempt for p in panel.sheet(course)[1] if p.pk == participation.pk)
        messages.success(request, f'{study.person} eingetragen, {attempt}. Versuch{"" if course.is_open else ", außerhalb der Anmeldefrist"}.')
    else:
        texts.send('eingetragen', study.person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
        if module:
            messages.success(request, f'{study.person} eingetragen, für {module.code}.')
        else:
            counts = course.modules.filter(program=study.program_id).exists()
            messages.warning(request, f'{study.person} eingetragen, ohne Modul: {"alle Module dieser Veranstaltung sind schon bestanden" if counts else "im Studiengang nicht anrechenbar"}.')
    return redirect(course)


@require_POST
def drop(request, pk):
    """Take someone out of an exam the office entered them in by mistake, while no mark is given; the Verlauf keeps it."""
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM, released=None)
    if not request.user.may('manage_exams', course):
        raise PermissionDenied
    participation = get_object_or_404(course.participations.select_related('study__person'), pk=request.POST.get('participation'), scores=None, grade=None)
    participation.delete()
    texts.send('ausgetragen', participation.study.person, reverse('exams:index'), veranstaltung=course.title, semester=course.term)
    messages.success(request, f'{participation.study.person} ausgetragen.')
    return redirect(course)


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    module = owed_module(course, study)
    late = not course.is_open and not course.upcoming  # the teachers may still take someone while the term runs
    besides = besides_focus(course, study)
    if course.cancelled or course.term.planning:
        messages.error(request, 'Diese Veranstaltung entfällt.' if course.cancelled else 'Das Semester ist noch nicht veröffentlicht.')
    elif course.allocation == Course.Allocation.STAFF:
        messages.error(request, 'Die Lehrenden teilen die Plätze ein.')
    elif course.allocation == Course.Allocation.ENSEMBLE:
        messages.error(request, 'Bitte als Ensemble anmelden.')
    elif course.upcoming:
        messages.error(request, f'Die Anmeldung beginnt am {date_format(course.opens, "j.n.Y")}.')
    elif late and course.term.end < timezone.localdate():
        messages.error(request, 'Der Anmeldeschluss ist vorbei.')
    elif module is None:
        counts = course.modules.filter(program=study.program_id).exists()
        messages.error(request, 'Die Module dieser Veranstaltung haben Sie bereits bestanden.' if counts else 'Diese Veranstaltung ist in Ihrem Studiengang nicht anrechenbar.')
    elif lost(study):
        messages.error(request, 'Der Prüfungsanspruch ist erloschen; neue Anmeldungen sind nicht möglich.')
    elif course.allocation == Course.Allocation.CHOICE or late:
        Signup.objects.get_or_create(course=course, study=study, defaults={'module': module, 'note': request.POST.get('note', '').strip()})
        messages.success(request, f'{"Nachmeldung angefragt" if late else "Angefragt"}. Die Lehrenden entscheiden; die Antwort kommt in Ihr Postfach.{besides}')
    else:
        participation, _ = Participation.objects.get_or_create(course=course, study=study, module=module)
        place = course.participations.filter(created__lt=participation.created).count() + 1
        if course.capacity and place > course.capacity:
            messages.success(request, f'Sie stehen auf Platz {place - course.capacity} der Warteliste.{besides}')
        else:
            messages.success(request, f'Sie sind angemeldet.{besides}')
    return redirect(course)


def clashes(person, bookings):
    """Of these dates, those that overlap something already in the person's timetable."""
    mine = agenda(person)
    return [b for b in bookings if mine.filter(start__lt=b.end, end__gt=b.start).exclude(course=b.course_id).exists()]


def lost(study):
    """Whether the study's Prüfungsanspruch is lost: nothing new to take then."""
    from exams.views import exhausted  # which imports from here

    return bool(exhausted(study))


def besides_focus(course, study):
    """What to tell a student who registers for a course that counts only for a Schwerpunkt other than theirs, or for
    one while none is chosen; '' otherwise."""
    chosen_by = study.program.chosen_by()
    options = {chosen_by.get(m) for m in course.modules.filter(program=study.program).values_list('pk', flat=True)}
    if not options or None in options or study.focus_id in {o.pk for o in options}:
        return ''
    names = ' oder '.join(sorted(o.title for o in options))
    if study.focus_id:
        return f' Sie zählt für {names}, nicht für Ihren Schwerpunkt {study.focus.title}.'
    return f' Sie zählt für {names}; Ihren Schwerpunkt haben Sie noch nicht gewählt.'


@require_POST
def ensemble(request, pk):
    """Register an ensemble: everyone in it takes part in the course, and the others hear who registered them."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING, allocation=Course.Allocation.ENSEMBLE)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    form = EnsembleForm(request.POST, course=course, person=request.user)
    if not course.is_open:
        messages.error(request, 'Der Anmeldeschluss ist vorbei.' if not course.upcoming else f'Die Anmeldung beginnt am {date_format(course.opens, "j.n.Y")}.')
        return redirect(course)
    if not form.is_valid():  # back with what was typed
        messages.error(request, 'Nicht angemeldet. Bitte die markierten Angaben berichtigen.')
        return again(request, course, 'ensemble')
    members = list(dict.fromkeys([study, *form.cleaned_data['members']]))
    if not course.modules.filter(program=study.program_id).exists():
        messages.error(request, 'Diese Veranstaltung ist in Ihrem Studiengang nicht anrechenbar.')
        return redirect(course)
    with transaction.atomic():
        group = Ensemble.objects.create(course=course, name=form.cleaned_data['name'], repertoire=form.cleaned_data['repertoire'], wish=form.cleaned_data['wish'])
        group.members.set(members)
        for s in members:
            Participation.objects.get_or_create(course=course, study=s, defaults={'module': course.modules.filter(program=s.program_id).order_by('semester').first()})
    for s in members[1:]:
        texts.send('ensemble', s.person, course.get_absolute_url(), veranstaltung=course.title, besetzung=group.name, von=request.user)
    messages.success(request, f'„{group.name}“ angemeldet. Wer es betreut, erfahren Sie im Postfach.')
    return redirect(course)


@require_POST
def disband(request, pk):
    """A member takes their ensemble back while registration runs and before anyone coaches it: its members leave the
    course, unless they play in another ensemble of it, and hear why."""
    group = get_object_or_404(Ensemble.objects.select_related('course__term'), pk=pk, members__person=request.user, coach=None)
    course = group.course
    if not course.is_open:
        messages.error(request, 'Der Anmeldeschluss ist vorbei; auflösen kann jetzt nur das Institut.')
        return redirect(course)
    members = list(group.members.select_related('person'))
    with transaction.atomic():
        group.delete()
        course.participations.filter(study__in=members, passed=None).exclude(study__ensembles__course=course).delete()
    for s in members:
        if s.person != request.user:
            texts.send('ensemble_zurueckgezogen', s.person, course.get_absolute_url(), veranstaltung=course.title, besetzung=group.name, von=request.user)
    messages.success(request, f'„{group.name}“ zurückgezogen.')
    return redirect(course)


@require_POST
def coach(request, pk):
    """A teacher of the course takes an ensemble nobody coaches yet, or gives their own back; the office that runs the
    course gives any to any of its teachers, or dissolves it. The members hear who coaches them."""
    group = get_object_or_404(Ensemble.objects.select_related('course__term'), pk=pk)
    course, person = group.course, request.user
    office = person.may('manage_courses', course)
    if not (office or course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    if 'dissolve' in request.POST:
        if not office:
            raise PermissionDenied
        group.delete()
        messages.success(request, f'„{group.name}“ aufgelöst. Die Teilnahmen bleiben.')
        return redirect(course)
    chosen = course.staff.filter(pk=request.POST.get('coach') or None).first()
    if not office and not (chosen == person and group.coach_id is None or chosen is None and group.coach_id == person.pk):
        raise PermissionDenied
    group.coach = chosen
    group.save(update_fields=['coach'])
    if group.coach:
        for s in group.members.select_related('person'):
            texts.send('betreuung', s.person, course.get_absolute_url(), veranstaltung=course.title, besetzung=group.name, betreuung=group.coach)
    messages.success(request, f'„{group.name}“: {group.coach or "ohne Betreuung"}.')
    return redirect(course)


@require_POST
def recuse(request, pk):
    """An examiner stands back from a candidate before marking them, giving the reason. The office that makes up the
    panel appoints a substitute in their place, or lifts it again. The result waits for every place to be marked."""
    course = get_object_or_404(Course, pk=pk, kind__in=Course.PANEL, released=None)
    person = request.user
    office = course.composes(person)
    overrides = {p.pk: p.override for p in panel.sheet(course)[1]}  # a grade set by hand stays; one that was the mean follows the marks
    if lifted := request.POST.get('aufheben', ''):
        participation, _, examiner = lifted.partition('-')
        if not office:
            raise PermissionDenied('Eine Befangenheit hebt nur das Amt auf, das die Kommission zusammenstellt.')
        Recusal.objects.filter(participation__course=course, participation=participation, examiner=examiner).delete()
        participation, done = int(participation), 'Befangenheit aufgehoben.'
    elif replaced := request.POST.get('ersetzen', ''):
        if not office:
            raise PermissionDenied('Einen Ersatz bestellt nur das Amt, das die Kommission zusammenstellt.')
        recusal = get_object_or_404(Recusal.objects.select_related('participation__study__person', 'examiner'), pk=replaced, participation__course=course)
        substitute = Person.teachers().exclude(pk__in=course.staff.values('pk')).filter(pk=request.POST.get(f'ersatz-{recusal.pk}') or None).first()
        if substitute is None:
            messages.error(request, 'Bitte einen Ersatz wählen.')
            return redirect(course)
        recusal.substitute = substitute
        recusal.save(update_fields=['substitute'])
        texts.send(
            'ersatz', substitute, course.get_absolute_url(), pruefung=course.title, kandidat=recusal.participation.study.person,
            fuer=recusal.examiner, von=person,
        )
        participation, done = recusal.participation_id, f'{substitute} wertet {recusal.participation.study.person} an Stelle von {recusal.examiner}.'
    else:
        participation = get_object_or_404(course.participations.exclude(scores__in=marked(person)), pk=request.POST.get('participation'))
        reason = request.POST.get('reason', '').strip()[:200]
        if not (reason and course.staff.filter(pk=person.pk).exists()):
            raise PermissionDenied
        Recusal.objects.get_or_create(participation=participation, examiner=person, defaults={'reason': reason})
        participation, done = participation.pk, f'Befangenheit angezeigt. Sie werten {participation.study.person} nicht.'
    rows = [p for p in panel.sheet(course)[1] if p.pk == participation]
    for p in rows:
        p.override = overrides[p.pk]
    panel.save({}, rows, [], decide=False)  # the result as the others' marks make it
    messages.success(request, done)
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, request.get_host()) else course.get_absolute_url())


@require_POST
def choose(request, pk):
    """Accept a wish for a place, or decline it; either way the student hears."""
    signup = get_object_or_404(Signup.objects.select_related('course__term', 'study__person'), pk=pk)
    course, person = signup.course, signup.study.person
    if not teaches(request.user, course):
        raise PermissionDenied
    if request.POST.get('decision') == 'yes':
        Participation.objects.get_or_create(course=course, study=signup.study, module=signup.module)
        texts.send('eingetragen', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    else:
        texts.send('kein_platz', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    signup.delete()
    messages.success(request, f'{person}: {"zugelassen" if request.POST.get("decision") == "yes" else "abgesagt"}. Nachricht versandt.')
    return redirect(course)


@require_POST
def retract(request, pk):
    signup = get_object_or_404(Signup, pk=pk, study__person=request.user)
    signup.delete()
    messages.success(request, 'Anfrage zurückgezogen.')
    return redirect(signup.course)


@require_POST
def date(request, pk):
    """Book one date of a course in a room: a block seminar day, a rehearsal, a make-up lesson."""
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = DateForm(request.POST, person=request.user, course=course)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return again(request, course, 'date')
    data = form.cleaned_data
    start = timezone.make_aware(datetime.combine(data['day'], data['begin']))
    booking = Booking(
        room=data['room'], start=start, end=timezone.make_aware(datetime.combine(data['day'], data['until'])),
        person=request.user, course=course, category=BookingCategory.system('unterricht'),
    )
    try:
        booking.full_clean()
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return again(request, course, 'date')
    if ensemble := data.get('ensemble'):
        booking.title, booking.ensemble = ensemble.name, ensemble
    booking.save()
    if student := data.get('student'):
        booking.participants.set([student])
    elif ensemble:  # its members see it in their timetable
        booking.participants.set(Person.objects.filter(studies__ensembles=ensemble))
    messages.success(request, f'Termin am {date_format(start, "j.n.Y")} in {booking.room} gebucht.')
    return redirect(course)


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation, pk=pk, study__person=request.user, course__kind__in=TEACHING)
    if participation.course.leavable:
        participation.delete()
        messages.success(request, 'Sie sind abgemeldet.')
    elif participation.course.is_open:
        messages.error(request, 'Abmelden geht hier über die Lehrenden.')
    else:
        messages.error(request, 'Der Abmeldeschluss ist vorbei.')
    return redirect(participation.course)


@require_POST
def plan(request, pk):
    """Book the course's room weekly across the lecture period; all dates or none."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    form = PlanForm(request.POST, person=request.user, course=course)
    if not form.is_valid():
        messages.error(request, 'Bitte Wochentag, Beginn, Dauer und Raum angeben.')
        return again(request, course, 'plan')
    data = form.cleaned_data
    bookings = weekly(course, request.user, data)
    if taken := [b for b in bookings if b.clashes().exists()]:
        messages.error(request, 'Nicht gebucht, belegt am ' + ', '.join(f'{b.start:%d.%m.}' for b in taken) + '.')
        return again(request, course, 'plan')
    history.saved(Booking.objects.bulk_create(bookings))
    messages.success(request, f'{len(bookings)} Termine in {data["room"]} gebucht.')
    return redirect(course)


def weekly(course, person, data):
    """A weekly time (PlanForm) as unsaved bookings across what is still to come of the lecture period, the unterrichtsfreie
    Tage left out; one series."""
    term, series, now = course.term, uuid.uuid4(), timezone.now()
    first = max(term.lectures_start, timezone.localdate())
    first += timedelta(days=(data['weekday'] - first.weekday()) % 7)
    free, lesson, found = Holiday.days(term.lectures_start, term.lectures_end), BookingCategory.system('unterricht'), []
    for day in (first + timedelta(weeks=n) for n in range((term.lectures_end - first).days // 7 + 1)):
        start = timezone.make_aware(datetime.combine(day, data['time']))
        if day not in free and start > now:
            found.append(Booking(
                room=data['room'], start=start, end=start + timedelta(minutes=data['minutes']), person=person, course=course, series=series, category=lesson,
                ensemble=data.get('ensemble'),
            ))
    return found


def lesson_times(course):
    """The class timetable of a one-to-one course: [(student, initial row of the Lessons form)], each as their next lesson
    shows it, else in the room the course is mostly taught in."""
    students = Person.objects.filter(studies__participations__course=course).distinct().order_by('last_name', 'first_name')
    upcoming = course.bookings.filter(start__gte=timezone.now(), category__blocks=True).select_related('room').prefetch_related('participants')
    nexts, rooms = {}, Counter(b.room_id for b in upcoming)
    for b in upcoming:
        for p in b.participants.all():
            nexts.setdefault(p.pk, b)
    planned = {p.study.person_id: minutes(p.module) for p in course.participations.select_related('module', 'study')}  # 'E 45': the plan's length
    rows = []
    for student in students:
        row = {'person': student.pk, 'room': rooms.most_common(1)[0][0] if rooms else None}
        if hours := planned.get(student.pk):
            row['minutes'] = int(hours * 60)
        if b := nexts.get(student.pk):
            start = timezone.localtime(b.start)
            row.update(weekday=start.weekday(), time=start.time(), minutes=int((b.end - b.start).total_seconds() // 60), room=b.room_id)
        rows.append((student, row))
    return rows


@require_POST
def lessons(request, pk):
    """Book the changed rows of a class timetable: each student's future weekly lessons give way to the new weekly time,
    to the end of the lecture period; one-off dates such as a make-up lesson, and dates called off, stay. All or none:
    a time is refused where the room is taken or teacher or student are elsewhere, the way between buildings
    included. The students hear their new time."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind=Course.Kind.LESSON)
    if not teaches(request.user, course):
        raise PermissionDenied
    rows = lesson_times(course)
    formset = Lessons(request.POST, initial=[row for _, row in rows], form_kwargs={'person': request.user})
    if not formset.is_valid():
        messages.error(request, ' '.join(e for form in formset for errors in form.errors.values() for e in errors))
        return again(request, course, 'lessons')
    students, future = {s.pk: s for s, _ in rows}, course.bookings.filter(start__gte=timezone.now())
    dropped, added = set(), []
    for form in formset:
        student = students.get(form.cleaned_data['person'])
        if student and form.has_changed():
            dropped |= set(future.filter(participants=student, series__isnull=False, category__blocks=True).values_list('pk', flat=True))
            if form.cleaned_data['time']:
                added += [(b, student, form.cleaned_data) for b in weekly(course, request.user, form.cleaned_data)]
    def elsewhere(person, b):  # at that time in another date of theirs, or one too near in another building
        return agenda(person).exclude(pk__in=dropped).filter(Q(start__lt=b.end, end__gt=b.start) | Q(pk__in=around(person, b.start, b.end)) & ~Q(room__building=b.room.building)).exists()

    taken = [
        (b, s) for b, s, _ in added
        if b.clashes().exclude(pk__in=dropped).exists() or any(o is not b and o.start < b.end and b.start < o.end for o, _, _ in added)
        or elsewhere(request.user, b) or elsewhere(s, b)
    ]
    if taken:
        messages.error(request, 'Nicht gebucht, belegt oder überschneidend: ' + ', '.join(f'{s.name} am {timezone.localtime(b.start):%d.%m.}' for b, s in taken[:6]) + '.')
        return again(request, course, 'lessons')
    with transaction.atomic():
        Booking.objects.filter(pk__in=dropped).delete()
        history.saved(Booking.objects.bulk_create(b for b, _, _ in added))
        Booking.participants.through.objects.bulk_create(Booking.participants.through(booking_id=b.pk, person_id=s.pk) for b, s, _ in added)
    told = {}
    for b, s, data in added:
        told.setdefault(s, (b, data))
    for student, (first, data) in told.items():
        start, end = timezone.localtime(first.start), timezone.localtime(first.end)
        texts.send(
            'unterrichtszeit', student, course.get_absolute_url(), veranstaltung=course.title, von=request.user, raum=data['room'],
            zeit=f'{date_format(start, "l").lower()}s {start:%H:%M}–{end:%H:%M} Uhr', ab=date_format(start, 'j.n.Y'),
        )
    messages.success(request, f'Unterrichtszeiten gebucht: {plural(len(told), "Person", "Personen")}, {plural(len(added), "Termin", "Termine")}.' if told else 'Nichts geändert.')
    return redirect(course)


def mine(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.shown(person).filter(courses__isnull=False).distinct().order_by('-start').filter(**since(person))}
    if 'teacher' in person.roles:
        teaching = person.teaching.filter(term=term, kind__in=TEACHING)
        taught_here = list(teaching.annotate(
            size=Count('participations', distinct=True), mine=Count('ensembles__members', filter=Q(ensembles__coach=person), distinct=True),
            coached=Count('ensembles', filter=Q(ensembles__coach=person), distinct=True),
        ).select_related('subject', 'format', 'term').prefetch_related('participations__module', 'origin__participations__module'))
        for c in taught_here:  # as the Deputat counts it: a lesson class by its students' minutes
            c.sws = None if c.cancelled else sws(c, c.coached)
        context.update(
            teaching=taught_here,
            load=deputat(teaching, person),
            coached=person.coaching.filter(course__term=term).select_related('course').prefetch_related('members__person'),
            students=taught(person, term),
        )
    if 'student' in person.roles:
        registrations = list(
            Participation.objects.filter(study__person=person, course__term=term, course__kind__in=TEACHING)
            .select_related('course', 'module').annotate(ahead=ahead())
        )
        coming = {}  # each course's next date the student comes to: of a lesson class, their own
        for b in agenda(person).filter(course__term=term, start__gte=timezone.now()).select_related('room').order_by('start'):
            coming.setdefault(b.course_id, b)
        for p in registrations:
            p.next = coming.get(p.course_id)
        context['registrations'] = registrations
        context['requests'] = Signup.objects.filter(study__person=person, course__term=term).select_related('course', 'module')  # the teachers still decide
    return render(request, 'courses/mine.html', context)


def taught(person, term):
    """The person's class, each study with `lesson`, the person's own lesson class it is in in `term`, and else `until`,
    the last semester its Studienplan has Hauptfach lessons in: past it, none is due; before it, one is `missing`."""
    today = timezone.localdate()
    studies = list(
        Study.objects.filter(teacher=person, status=Study.Status.ENROLLED).select_related('person', 'program', 'subject', 'start_term')
        .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=today)))
    )
    lessons = dict(Participation.objects.filter(  # Hauptfach classes only: Orchesterstudien do not stand in for them
        study__in=studies, course__term=term, course__kind=Course.Kind.LESSON, course__subject__isnull=False, course__staff=person,
    ).values_list('study', 'course__title'))
    until = dict(  # the Hauptfach's lessons: classes of a Hauptfach, not Pflichtfach Klavier or Orchesterstudien
        Module.objects.filter(program__in={s.program_id for s in studies}, courses__kind=Course.Kind.LESSON, courses__subject__isnull=False, semester__isnull=False)
        .values('program').annotate(last=Max('semester')).values_list('program', 'last')
    )
    passed = set(Participation.objects.filter(study__in=studies, passed=True).values_list('study', 'module'))
    failed = Counter(
        Participation.objects.filter(study__in=studies, passed=False, course__kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], course__released__isnull=False)
        .values_list('study', 'module')
    )
    choices = {}
    for s in studies:  # a plan without lessons, such as a doctorate's, owes none
        s.lesson, s.until = lessons.get(s.pk), until.get(s.program_id)
        s.missing = not s.lesson and s.until is not None and s.fs <= s.until
        tries = [n for (study, module), n in failed.items() if study == s.pk and (study, module) not in passed]
        s.lost, s.last = any(n >= s.program.attempts for n in tries), any(n == s.program.attempts - 1 for n in tries)
        s.over = s.fs > s.program.semesters
        options = choices.setdefault(s.program_id, s.program.choices())
        s.open = options[0].choice.title if options and not s.focus_id and s.fs >= min(o.first or 99 for o in options) - 1 else ''
    return studies


def timetable(request):
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    return render(request, 'courses/timetable.html', {
        'day': day, 'clashes': True, 'tones': legend(), 'off': 'teacher' in request.user.roles, **week(agenda(request.user), day, request.user),
    })


def hours(term, seen):
    """{teacher: [(course, SWS)]} of a term's teaching, for the teachers `seen` (pks; None: all)."""
    courses = (
        term.courses.filter(kind__in=TEACHING, cancelled=None).select_related('format', 'term')
        .prefetch_related('staff', 'ensembles', 'participations__module', 'origin__participations__module').annotate(size=Count('participations'))
    )
    if seen is not None:
        courses = courses.filter(pk__in=Course.objects.filter(staff__in=seen).values('pk'))
    found = defaultdict(list)
    for c in courses:
        coached = Counter(e.coach_id for e in c.ensembles.all())
        for s in c.staff.all():
            if seen is None or s.pk in seen:
                found[s].append((c, sws(c, coached[s.pk])))
    return found


def loads(term, person, institute='', words=()):
    """The Deputat of the teachers in the person's view: their own, and that of the Institute in reach. Each row has
    `teacher`, `courses` [(course, SWS)], `total`, the term's `reductions` (ThürLVVO § 8), `due` (the Lehrverpflichtung
    less them), `balance` (total less due), `low` (under half of it, which § 7 allows in no term) and `account`: the
    balances of the three Studienjahre up to this term, to be evened out within them (§ 7), counted from when the
    Hochschule began to keep it. A Lehrauftrag has no account: its hours are agreed each term."""
    teaching = Person.objects.filter(teaching__term=term, teaching__kind__in=TEACHING)
    seen = None if person.everywhere('view_teaching_load') else {person.pk, *person.within(teaching, 'view_teaching_load').values_list('pk', flat=True)}
    now = {t: cs for t, cs in hours(term, seen).items() if institute in ('', str(t.institute_id)) and all(w in fold(t.name) for w in words)}
    since = Institution.get().load_since
    terms = [term, *(Term.objects.filter(start__lt=term.start, start__gte=since).order_by('-start')[:5] if since else [])]
    reduced = defaultdict(list)
    for r in Reduction.objects.filter(person__in=now, term__in=terms):
        reduced[r.person_id, r.term_id].append(r)

    def due(teacher, on):
        return None if teacher.obligation is None else teacher.obligation - sum(r.hours for r in reduced[teacher.pk, on.pk])

    account, first = defaultdict(Decimal), dict(Course.objects.filter(staff__in=now, kind__in=TEACHING).values('staff').annotate(first=Min('term__start')).values_list('staff', 'first'))
    for earlier in terms[1:]:
        taught = hours(earlier, {t.pk for t in now})
        for t in now:
            if first.get(t.pk) and first[t.pk] <= earlier.start and (owed := due(t, earlier)) is not None:
                account[t.pk] += sum(h for _, h in taught.get(t, [])) - owed
    rows = []
    for t, cs in now.items():
        total, owed = sum(h for _, h in cs), due(t, term)
        rows.append(SimpleNamespace(
            teacher=t, courses=cs, total=total, reductions=reduced[t.pk, term.pk], due=owed, balance=None if owed is None else total - owed,
            low=owed is not None and total * 2 < owed,
            account=None if owed is None or t.employment == Person.Employment.LEHRAUFTRAG or not since or term.start < since else account[t.pk] + total - owed,
        ))
    return sorted(rows, key=lambda row: row.teacher.last_name)


@require('view_teaching_load', 'teacher')
def load(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Veranstaltungen')
    ours = person.ours('view_teaching_load')  # an Institut's office sees its own; the list says so
    institute, q, stand = request.GET.get('institut', str(min(ours)) if len(ours) == 1 else ''), request.GET.get('q', '').strip(), request.GET.get('stand', '')
    rows = loads(term, person, institute, fold(q).split())
    if stand:
        rows = [row for row in rows if row.balance is not None and (row.balance > 0 if stand == 'ueber' else row.balance < 0)]
    return render(request, 'courses/load.html', {
        'term': term, 'terms': Term.objects.order_by('-start'), 'rows': rows, 'total': sum(row.total for row in rows), 'everyone': 'view_teaching_load' in person.roles,
        'since': Institution.get().load_since and Term.objects.filter(start__gte=Institution.get().load_since).order_by('start').first(),
        'institutes': Institute.objects.filter(pk__in=ours) if not person.everywhere('view_teaching_load') and ours else Institute.objects.all(),
        'institute': institute, 'q': q, 'stand': stand,
    })


def since(person):
    """For a student and nobody else: terms from their first, as filter arguments."""
    if person.roles & {'teacher', *CODENAMES} or 'student' not in person.roles:
        return {}
    return {'start__gte': person.studies.aggregate(first=Min('start_term__start'))['first']}


def missing(target):
    """What `target` may take over, each course by its Turnus: lesson classes and what runs every semester from the term
    before, what runs yearly from the same semester a year before, one-off courses from either, offered again only by
    choice; nothing `target` has already."""
    before = Term.objects.filter(start__lt=target.start).order_by('-start').first()
    year = Term.objects.filter(start__lte=target.start - timedelta(days=365)).order_by('-start').first()
    every = Q(kind=Course.Kind.LESSON) | Q(rhythm=Course.Rhythm.SEMESTER)
    return Course.objects.filter(kind__in=TEACHING).filter(
        Q(term=before) & (every | Q(rhythm=Course.Rhythm.ONCE)) | Q(term=year, kind=Course.Kind.COURSE) & ~Q(rhythm=Course.Rhythm.SEMESTER),
    ).exclude(title__in=target.courses.values('title'))


def untimed(participations):
    """Places in this term's one-to-one classes that no lesson is booked for; accompaniment at auditions is no class."""
    booked = Booking.objects.filter(course=OuterRef('course'), participants=OuterRef('study__person'))
    return participations.filter(course__term=Term.current(), course__kind=Course.Kind.LESSON, study__status=Study.Status.ENROLLED).exclude(
        Exists(booked)).exclude(course__in=Course.objects.filter(accompaniment__isnull=False).values('accompaniment'))


WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']


def usual(courses):
    """{course pk: (weekday, time, minutes, room)}: each course's weekly date as it mostly took place."""
    found = defaultdict(Counter)
    for b in Booking.objects.filter(course__in=courses, ensemble=None, category__blocks=True).select_related('room'):
        start = timezone.localtime(b.start)
        found[b.course_id][start.weekday(), start.time(), int((b.end - b.start).total_seconds() // 60), b.room] += 1
    return {pk: seen.most_common(1)[0][0] for pk, seen in found.items()}


def described(slot):
    """A weekly date (usual) as people read it: 'Di 10:00, 90 Min. · FH 311'."""
    return slot and f'{WEEKDAYS[slot[0]]} {slot[1]:%H:%M}, {slot[2]} Min. · {slot[3]}'


def gaps(target):
    """Course work of a Studienplan that everyone on it takes in the Fachsemester they reach in `target`, taught as a
    course before, that no course of `target` offers: [(module, students)], the most students first."""
    today = timezone.localdate()
    reach = Counter(
        Study.objects.filter(status=Study.Status.ENROLLED)
        .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__withdrawn=False, terms__term__start__lte=today)))
        .values_list('program', 'fs')
    )
    taught = set(Module.objects.filter(courses__kind__in=TEACHING).values_list('pk', flat=True))
    offered = set(Module.objects.filter(courses__term=target, courses__cancelled=None).values_list('pk', flat=True))
    found = []

    def walk(node, program):
        if node.nodes and node.credits < node.offered:  # a choice: each option only some take
            return
        if not node.nodes and node.pk in taught and node.pk not in offered and (n := reach[program.pk, (node.semester or 0) - 1]):
            found.append((node, n))
        for kid in node.nodes:
            walk(kid, program)

    for program in Program.objects.filter(pk__in={program for program, _ in reach}):
        for root in program.curriculum():
            walk(root, program)
    return sorted(found, key=lambda f: -f[1])


@require('roll_over', 'publish_term')
def rollover(request):
    """The next semester planned in steps, unseen by students until it is published: take its courses over as drafts, ask
    the teachers to confirm them, book last year's weekly times, fill the classes after the Rückmeldung, check, publish."""
    target = Term.upcoming()
    if target is None:
        return without_term(request, 'das nächste Semester, in das die Veranstaltungen übernommen werden')
    person = request.user
    pending = person.within(missing(target), 'roll_over')
    if request.method == 'POST':
        step = {'uebernehmen': take_over, 'fragen': ask, 'termine': schedule, 'klassen': fill, 'veroeffentlichen': publish, 'ermaessigungen': carry}.get(request.POST.get('schritt'))
        if step is None or {publish: 'publish_term', carry: 'manage_teaching_load'}.get(step, 'roll_over') not in person.roles:
            raise PermissionDenied
        step(request, target, pending)
        return redirect('courses:rollover')
    institute, area, kind, q = (request.GET.get(name, '').strip() for name in ('institut', 'fachgebiet', 'art', 'q'))
    if institute.isdigit():
        pending = pending.filter(Q(subject__institute=institute) | Q(staff__institute=institute))
    if area.isdigit():
        pending = pending.filter(area=area)
    if kind in TEACHING:
        pending = pending.filter(kind=kind)
    if q:
        pending = matching(pending, q, 'title', 'staff__last_name')
    ours, rows, titles = person.ours('roll_over'), [], set()
    for c in pending.distinct().select_related('term', 'area', 'subject__institute').prefetch_related('staff__institute').order_by('-term__start', 'title'):
        if c.title not in titles:  # the newest offering once
            titles.add(c.title)
            c.others = sorted({str(s.institute) for s in c.staff.all() if s.institute_id and s.institute_id not in ours}) if ours else []
            c.group = f'Einzelunterricht · {c.subject.institute}' if c.kind == Course.Kind.LESSON and c.subject and c.subject.institute else c.area.name if c.area else 'Ohne Fachgebiet'
            rows.append(c)
    slots = usual([c.pk for c in rows if c.kind == Course.Kind.COURSE])
    for c in rows:
        c.slot = described(slots.get(c.pk))
    rows.sort(key=lambda c: (c.kind == Course.Kind.LESSON, c.group, c.title))
    planned = target.courses.filter(kind__in=TEACHING).exclude(origin=None)  # taken over: for their teachers to confirm
    live = planned.filter(cancelled=None)
    waiting = live.filter(confirmed=None)
    timeless = list(target.courses.filter(kind=Course.Kind.COURSE, cancelled=None, bookings__isnull=True).select_related('origin').order_by('title'))
    slots = usual([c.origin_id for c in timeless if c.origin_id])
    for c in timeless:
        c.slot = described(slots.get(c.origin_id))
    load = loads(target, person) if 'view_teaching_load' in person.roles else []
    contracts = [row.total for row in load if row.teacher.employment == Person.Employment.LEHRAUFTRAG]
    return render(request, 'courses/rollover.html', {
        'target': target, 'rows': rows, 'planner': 'roll_over' in person.roles, 'publisher': 'publish_term' in person.roles,
        'institutes': Institute.objects.all(), 'areas': Area.objects.all(), 'institute': institute, 'area': area, 'kind': kind, 'q': q,
        'planned': planned.count(), 'confirmed': live.exclude(confirmed=None).count(), 'dropped': planned.exclude(cancelled=None).select_related('origin'),
        'waiting': Person.objects.filter(teaching__in=waiting).annotate(n=Count('teaching', filter=Q(teaching__in=waiting))).order_by('last_name'),
        'timeless': timeless, 'fillable': timezone.localdate() > target.deadline,
        'over': [row for row in load if row.balance and row.balance > 0], 'under': [row for row in load if row.low],
        'contracts': len(contracts), 'contract_hours': sum(contracts),
        'leave': Reduction.objects.filter(term=target, reason=Reduction.Reason.LEAVE, person__teaching__in=live).distinct().select_related('person'),
        'gaps': gaps(target)[:12] if planned.exists() else [],
        'publishers': Office.holding('publish_term', linked=True),
        'reductions': (target.reductions.count(), Term.current() and Term.current().reductions.exclude(reason=Reduction.Reason.LEAVE).count()),
        'reducing': 'manage_teaching_load' in person.roles,
        'proposed': target.ready_by and max(timezone.localdate() + timedelta(weeks=1), target.ready_by - timedelta(weeks=2)),  # time to answer, and to check after 'unpublishers': Office.holding('manage_terms'),
    })


def take_over(request, target, pending):
    """The ticked courses as drafts of `target`, from what they were: staff, Turnus, and the modules they counted for, also
    as the same semester a year before had them; registration as the semester sets it; lesson classes without their
    students, who come once they have re-registered (fill)."""
    chosen, titles = [], set()
    for c in pending.filter(pk__in=request.POST.getlist('course')).prefetch_related('modules', 'staff').order_by('-term__start'):
        if c.title not in titles:
            titles.add(c.title)
            chosen.append(c)
    year = Term.objects.filter(start__lte=target.start - timedelta(days=365)).order_by('-start').first()
    earlier = defaultdict(set)  # what runs every semester may count for other semesters' modules in summer and in winter
    for title, module in Course.objects.filter(term=year, title__in=titles, kind__in=TEACHING).values_list('title', 'modules'):
        if module:
            earlier[title].add(module)
    with transaction.atomic():
        for c in chosen:
            copy = Course.objects.create(
                term=target, kind=c.kind, title=c.title, area=c.area, format=c.format, subject=c.subject, hours=c.hours, capacity=c.capacity,
                allocation=c.allocation, description=c.description, rhythm=c.rhythm, origin=c, opens=target.course_signup_start, deadline=target.course_signup_end,
            )
            copy.modules.set({m.pk for m in c.modules.all()} | earlier[c.title])
            copy.staff.set(c.staff.all())
    hidden = ' als Entwurf; Studierende sehen sie, sobald das Semester veröffentlicht ist' if target.planning else ''
    messages.success(request, f'{plural(len(chosen), "Veranstaltung", "Veranstaltungen")} nach {target} übernommen{hidden}.')


def ask(request, target, pending):
    """Ask every teacher with a course still unconfirmed to confirm, change or call it off by a day; asked again, those
    still open."""
    by = understood(request.POST.get('bis', ''))
    if by is None or by <= timezone.localdate():
        messages.error(request, 'Bitte einen Tag in der Zukunft angeben, bis zu dem die Lehrenden antworten.')
        return
    if target.ready_by and by >= target.ready_by:
        messages.error(request, f'Die Antworten braucht es vor dem {date_format(target.ready_by, "j.n.Y")}, wenn das Vorlesungsverzeichnis fertig sein soll.')
        return
    target.replies_by = by
    target.save(update_fields=['replies_by'])
    waiting = target.courses.filter(kind__in=TEACHING, confirmed=None, cancelled=None).exclude(origin=None)
    teachers = list(Person.objects.filter(teaching__in=waiting).distinct().prefetch_related(Prefetch('teaching', waiting, to_attr='open')))
    for t in teachers:
        texts.send(
            'semesterplanung', t, f"{reverse('courses:mine')}?semester={target.pk}", semester=str(target), frist=date_format(by, 'j. F Y'),
            veranstaltungen='\n'.join(f'– {c.title}' for c in t.open),
        )
    messages.success(request, f'{plural(len(teachers), "Lehrperson", "Lehrpersonen")} gefragt, Antwort bis zum {date_format(by, "j.n.Y")}.')


def schedule(request, target, pending):
    """Book the ticked drafts weekly as their course took place last time, where the room is free on every date."""
    courses = list(target.courses.filter(pk__in=request.POST.getlist('course'), kind=Course.Kind.COURSE, cancelled=None, bookings__isnull=True).exclude(origin=None))
    slots, booked, taken = usual([c.origin_id for c in courses]), 0, []
    for c in courses:
        if (slot := slots.get(c.origin_id)) is None:
            continue
        weekday, time, minutes, room = slot
        dates = weekly(c, request.user, {'weekday': weekday, 'time': time, 'minutes': minutes, 'room': room})
        if not dates:
            continue
        busy = Booking.objects.filter(room=room, category__blocks=True, start__lt=dates[-1].end, end__gt=dates[0].start).values_list('start', 'end')
        if any(s < b.end and b.start < e for s, e in busy for b in dates):
            taken.append(f'{c.title} ({room})')
            continue
        history.saved(Booking.objects.bulk_create(dates))
        booked += 1
    messages.success(request, f'{plural(booked, "Veranstaltung hat", "Veranstaltungen haben")} ihre wöchentlichen Termine.')
    if taken:
        messages.error(request, f'Raum belegt, nicht gebucht: {"; ".join(taken)}. Diese bitte auf ihrer Seite planen.')


def fill(request, target, pending):
    """After the Rückmeldefrist: each lesson class of `target` gets whoever was in it and re-registered, not on leave, and
    the new students of its teachers in its Hauptfach; each with the module of their next Fachsemester."""
    if timezone.localdate() <= target.deadline:
        messages.error(request, f'Erst nach der Rückmeldefrist am {date_format(target.deadline, "j.n.Y")}.')
        return
    today = timezone.localdate()
    studies = Study.objects.filter(status=Study.Status.ENROLLED, terms__term=target, terms__leave=False, terms__withdrawn=False).annotate(
        fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__withdrawn=False, terms__term__start__lte=today)),
    ).in_bulk()
    added = 0
    for c in target.courses.filter(kind=Course.Kind.LESSON, cancelled=None).prefetch_related('modules', 'staff'):
        staff = {p.pk for p in c.staff.all()}
        had = set(Participation.objects.filter(course=c.origin_id).values_list('study', flat=True)) if c.origin_id else set()
        new = {s.pk for s in studies.values() if s.start_term_id == target.pk and s.teacher_id in staff and s.subject_id == c.subject_id}
        rows = []
        for pk in (had | new) & (studies.keys() - set(c.participations.values_list('study', flat=True))):
            s = studies[pk]
            plan = [m for m in c.modules.all() if m.program_id == s.program_id]
            module = next((m for m in plan if m.semester == s.fs + 1), None) or min(plan, key=lambda m: m.semester or 99, default=None)
            rows.append(Participation(course=c, study=s, module=module))
        history.saved(Participation.objects.bulk_create(rows))
        added += len(rows)
    messages.success(request, f'{plural(added, "Person", "Personen")} in die Klassen des {target} eingetragen.')


@require('appoint_examiners')
@require_POST
def appoint_again(request):
    """The latest Studienjahr's appointments into the next, for whoever is still active and not appointed there yet;
    each in the same Hauptfächer. The Prüfungsausschuss then changes what differs."""
    last = Appointment.objects.order_by('-year').values_list('year', flat=True).first()
    if last is None:
        messages.error(request, 'Noch keine Bestellungen, die sich übernehmen ließen.')
        return redirect('setup:list', 'pruefende')
    taken = set(Appointment.objects.filter(year=last + 1).values_list('person', flat=True))
    made = []
    with transaction.atomic():
        for a in Appointment.objects.filter(year=last, person__is_active=True).exclude(person__in=taken).prefetch_related('subjects'):
            made.append(Appointment.objects.create(person_id=a.person_id, year=last + 1, general=a.general))
            made[-1].subjects.set(a.subjects.all())
    messages.success(request, f'{plural(len(made), "Bestellung", "Bestellungen")} aus {last}/{(last + 1) % 100:02d} für {last + 1}/{(last + 2) % 100:02d} übernommen.')
    return redirect('setup:list', 'pruefende')


@require('run_auditions')
@require_POST
def auditions_again(request):
    """Each intake into the same semester a year on, for a semester to come that has no Eignungsprüfung yet: every stage
    the office keeps, with its jury, its Fristen a year on by whole weeks, the stages linked as before and to the new
    semester's Korrepetition. Rooms and times are planned anew."""
    made = []
    with transaction.atomic():
        for target in Term.objects.filter(start__gt=timezone.localdate()).exclude(courses__kind=Course.Kind.AUDITION).order_by('start'):
            source = Term.objects.filter(start__range=(target.start - timedelta(days=400), target.start - timedelta(days=330)), courses__kind=Course.Kind.AUDITION).distinct().first()
            if source:
                made.append((source, target, again(request.user, source, target)))
    if not made:
        messages.error(request, 'Kein Semester ohne Eignungsprüfungen, das ein Jahr nach einem mit Eignungsprüfungen beginnt: bitte erst das Semester anlegen.')
    for source, target, n in made:
        messages.success(request, f'{plural(n, "Eignungsprüfung", "Eignungsprüfungen")} aus {source} nach {target} übernommen.')
    return redirect('setup:list', 'eignungspruefungen')


def again(person, source, target):
    """The auditions of `source` the person keeps, copied into `target`; returns how many."""
    shift = timedelta(weeks=round((target.start - source.start).days / 7))
    versions = {(p.degree, p.name): p for p in Program.current(target)}  # each Studiengang in the PO-Version that applies then
    old = list(person.within(Course.objects.filter(term=source, kind=Course.Kind.AUDITION), 'run_auditions').select_related('accompaniment').prefetch_related('staff', 'after'))
    new = {}
    for c in old:
        new[c.pk] = Course.objects.create(
            term=target, kind=c.kind, title=c.title, area=c.area, format=c.format, subject=c.subject, oral=c.oral, hours=c.hours,
            program=c.program and versions.get((c.program.degree, c.program.name), c.program),
            capacity=c.capacity, allocation=c.allocation, pass_mark=c.pass_mark, max_points=c.max_points, video=c.video, description=c.description,
            rhythm=c.rhythm, origin=c, opens=c.opens and c.opens + shift, deadline=c.deadline and c.deadline + shift, marks_by=c.marks_by and c.marks_by + shift,
            accompaniment=c.accompaniment and Course.objects.filter(term=target, kind=c.accompaniment.kind, title=c.accompaniment.title).first(),
        )
        new[c.pk].staff.set(c.staff.all())
    for c in old:
        new[c.pk].after.set(new[a.pk] for a in c.after.all() if a.pk in new)
    return len(new)


def carry(request, target, pending):
    """Carry this semester's Ermäßigungen over to the next for whoever has none there yet; a Freisemester is for its
    semester alone."""
    now, taken = Term.current(), set(target.reductions.values_list('person', flat=True))
    made = [
        Reduction(person_id=r.person_id, term=target, reason=r.reason, hours=r.hours, note=r.note)
        for r in now.reductions.exclude(reason=Reduction.Reason.LEAVE).exclude(person__in=taken)
    ] if now else []
    history.saved(Reduction.objects.bulk_create(made))
    messages.success(request, f'{plural(len(made), "Ermäßigung", "Ermäßigungen")} aus dem {now} übernommen.' if made else 'Nichts zu übernehmen.')


def publish(request, target, pending):
    """Publish `target`: students see its courses; those its teachers called off, never offered, are deleted."""
    dropped = target.courses.filter(kind__in=TEACHING, cancelled__isnull=False, participations=None)
    with transaction.atomic():
        n = dropped.count()
        dropped.delete()
        target.planning = False
        target.save(update_fields=['planning'])
    messages.success(request, f'{target} veröffentlicht: Studierende sehen seine Lehrveranstaltungen.' + (f' {plural(n, "abgesagte Veranstaltung", "abgesagte Veranstaltungen")} gelöscht.' if n else ''))


@require_POST
def reply(request, pk):
    """For a semester in planning, the answer of a course's teachers or of its Institut: it takes place as planned, or it
    is called off with its reason; or the answer taken back."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING, term__planning=True)
    if not teaches(request.user, course):
        raise PermissionDenied
    answer, reason = request.POST.get('antwort'), request.POST.get('grund', '').strip()[:200]
    if answer == 'entfaellt' and not reason:
        messages.error(request, 'Bitte den Grund angeben, etwa ein Forschungssemester.')
    else:
        course.confirmed = timezone.now() if answer == 'findet' else None
        course.cancelled, course.cancel_reason = (timezone.now(), reason) if answer == 'entfaellt' else (None, '')
        course.save(update_fields=['confirmed', 'cancelled', 'cancel_reason'])
        messages.success(request, {'findet': f'{course.title}: findet so statt.', 'entfaellt': f'{course.title}: entfällt.'}.get(answer, f'{course.title}: Antwort zurückgenommen.'))
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, {request.get_host()}) else course.get_absolute_url())


@require_POST
def cancel(request, pk):
    """A published course that does not take place after all: its coming dates are called off, registrations without a
    result and requests lifted, and whoever had one hears of it with the reason. It stays listed as „entfällt“."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING, term__planning=False, cancelled=None)
    if not request.user.may('manage_courses', course) or course.term.over:
        raise PermissionDenied
    if not (reason := request.POST.get('grund', '').strip()[:200]):
        messages.error(request, 'Bitte den Grund angeben; er steht in der Nachricht an die Angemeldeten.')
        return redirect(course)
    people = list(Person.objects.filter(
        Q(studies__participations__course=course, studies__participations__passed=None) | Q(studies__signups__course=course)).distinct())
    with transaction.atomic():
        course.cancelled, course.cancel_reason = timezone.now(), reason
        course.save(update_fields=['cancelled', 'cancel_reason'])
        for b in course.bookings.filter(start__gte=timezone.now(), category__blocks=True):
            called_off(b, request.user)
        course.participations.filter(passed=None).delete()
        course.signups.all().delete()
    for p in people:
        texts.send('entfaellt', p, course.get_absolute_url(), veranstaltung=course.title, semester=str(course.term), grund=reason)
    messages.success(request, f'{course.title} entfällt. {plural(len(people), "Person hat", "Personen haben")} Nachricht erhalten.')
    return redirect(course)
