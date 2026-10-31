import csv
from operator import attrgetter
import re
from copy import copy
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Prefetch, Q, Subquery
from django.http import HttpResponse
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
from core.forms import ComposeForm, again, sent
from core.models import Export, Holiday, Institute, Institution, Office, Person, Term
from core.text import fold, matching
from core.views import without_term
from curriculum.models import Module, Program
from rooms.models import Booking, BookingCategory
from rooms.views import QUARTER, agenda, around, called_off, legend, side_by_side, week
from students.models import Study
from . import panel
from .forms import AddForm, CourseForm, DateForm, EnsembleForm, GroupForm, Lessons, PlanForm, TeachingForm
from .models import Appointment, Area, Course, Ensemble, Format, Group, Participation, Recusal, Reduction, Signup, marked, owed_module

TEACHING = [Course.Kind.COURSE, Course.Kind.LESSON]
MANAGERS = {Course.Kind.EXAM: 'manage_exams', Course.Kind.RECOGNITION: 'recognise', Course.Kind.AUDITION: 'run_auditions'}


def deputat(courses, person):
    """A teacher's load in SWS: each course's hours; per participant in one-to-one teaching, per ensemble they coach in
    ensemble courses, per group they teach in courses taught in groups."""
    return sum(sws(c, c.coached) for c in coaching(courses.filter(cancelled=None), person).select_related('format', 'term').prefetch_related(
        'participations__module__program', 'origin__participations__module__program'))


def coaching(courses, person):
    """The courses with `size`, `grouped` (how many groups) and `coached`: the ensembles the person coaches, or the
    groups they teach, as share() counts them."""
    return courses.annotate(
        size=Count('participations__study', distinct=True), grouped=Count('groups', distinct=True),
        coached=Count('ensembles', filter=Q(ensembles__coach=person), distinct=True) + Count('groups', filter=Q(groups__staff=person), distinct=True),
    )


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
    """The SWS of one lesson in a plan whose form says its weekly extent, 'E 90' in minutes or 'E 2' in SWS as its
    Studiengang reads it (Program.weekly); None where it does not."""
    found = module and re.search(r'(\d+)\s*$', module.description)
    return Decimal(found[1]) / (60 if module.program.weekly == 'minuten' else 1) if found else None


def share(course, coached):
    """How many times a course's hours count for one of its teachers: per participant in one-to-one teaching, whether a
    lesson class or a course whose Lehrform counts so; per ensemble coached in ensemble courses, per group taught in a
    course taught in groups (`grouped`); else once."""
    if course.kind == Course.Kind.LESSON or course.format_id and course.format.per_head:
        return course.size
    return coached if course.allocation == Course.Allocation.ENSEMBLE or course.grouped else 1


def credited(course, own=()):
    """The modules a course counts for, by title: [(title, [(program, codes)])], so a course in many plans reads in a few lines;
    the Studiengänge in `own` first."""
    found = defaultdict(lambda: defaultdict(list))
    for m in course.modules.select_related('program').order_by('title', 'program__degree', 'program__name', 'code'):
        found[m.title][m.program].append(m.code)
    rows = [(title, sorted(programs.items(), key=lambda p: p[0].pk not in own)) for title, programs in found.items()]
    return sorted(rows, key=lambda row: row[1][0][0].pk not in own)


def teaches(person, course):
    """Who runs a course: its staff, and whoever manages courses of its kind where it belongs."""
    return course.staff.filter(pk=person.pk).exists() or person.may(MANAGERS.get(course.kind, 'manage_courses'), course)


def ahead():
    """Subquery: the other students of the same course who registered earlier, or at the same moment and were entered
    earlier."""
    earlier = Participation.objects.filter(
        Q(created__lt=OuterRef('created')) | Q(created=OuterRef('created'), pk__lt=OuterRef('pk')), course=OuterRef('course'),
    ).exclude(study=OuterRef('study'))
    return Subquery(earlier.values('course').annotate(n=Count('study', distinct=True)).values('n')[:1])


def queued(participation):
    """Where a participation annotated with ahead() stands on its course's waitlist, from 1; None with a place."""
    course, before = participation.course, participation.ahead or 0
    if course.allocation == Course.Allocation.ORDER and course.capacity is not None and before >= course.capacity:
        return before - course.capacity + 1
    return None


def admit(course, before):
    """Tell who has moved up from the waitlist, `before` being Course.waitlist() then, that they have a place."""
    for person in {p.study.person for p in course.participations.filter(pk__in=set(before) - set(course.waitlist())).select_related('study__person')}:
        texts.send('nachgerueckt', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)


def seen(course, person, rows):
    """The participations of a course its teacher sees: a coach of an ensemble course, not its office, those of their own
    ensembles' members; a teacher of groups of a course, those in them."""
    if person.may('manage_courses', course):
        return rows
    if course.allocation == Course.Allocation.ENSEMBLE:
        coached = set(Study.objects.filter(ensembles__course=course, ensembles__coach=person).values_list('pk', flat=True))
        return [p for p in rows if p.study_id in coached]
    taught = set(course.groups.filter(staff=person).values_list('pk', flat=True))
    return [p for p in rows if p.group_id in taught] if taught else rows


def fits(course, study):
    """The study with `owed`, the module the course counts for in it, `counts`, whether its Studienplan has the course at
    all, and `concern`, what the plan says against taking it: a module before it not passed, nothing left for it to
    count for, or no place in the plan. The student is warned and gives a reason, as a transfer or out of interest; never
    turned away."""
    study.owed = owed_module(course, study)
    study.counts = study.owed is not None or course.modules.filter(program=study.program_id).exists()
    missing = unmet([(study.pk, study.owed.pk)])[study.pk, study.owed.pk] if study.owed else []
    study.concern = (
        f'Vorher laut Studienplan: {", ".join(m.title for m in missing)}, noch nicht bestanden.' if missing
        else '' if study.owed
        else 'Die Module dafür sind schon bestanden.' if study.counts
        else f'Sie gehört nicht zum Studienplan {study.program.title}.'
    )
    return study


def unmet(rows):
    """{(study pk, module pk): the modules the Studienplan wants passed before it that the study has not}; a
    recognition counts as passed."""
    rows = [r for r in rows if r[1]]
    needs = defaultdict(list)
    for m in Module.objects.filter(required_by__in={m for _, m in rows}).annotate(by=F('required_by')):
        needs[m.by].append(m)
    passed = set(Participation.objects.filter(study__in={s for s, _ in rows}, module__in={m.pk for ms in needs.values() for m in ms}, passed=True).values_list('study', 'module'))
    return {(s, m): [r for r in needs[m] if (s, r.pk) not in passed] for s, m in rows}


def weight(course, found):
    """(how plainly to warn, on how many dates) of a course's clashes() with someone's week: 'heavy' from the number of
    dates the course sets, 'light' below it, as a rehearsal now and then, or only a way between buildings too short."""
    dates = sum(b.times for b, _, way in found if not way)
    return ('heavy' if dates >= course.clash_limit() else 'light' if found else ''), dates


def series(bookings):
    """One date of each weekly series among `bookings`, in the order they begin."""
    first = {}
    for b in sorted(bookings, key=attrgetter('start')):
        if b.series:
            first.setdefault(b.series, b)
    return list(first.values())


FIT = {'': 0, 'light': 1, 'heavy': 2}  # what fits a student's week first


def weekly_at(group):
    """When a group meets in the week, to sort by: its first weekly date's weekday and time; without dates, last."""
    first = group.times[:1]
    return timezone.localtime(first[0].start).strftime('%u%H%M') if first else '9'


def week_of(course, groups, before=()):
    """The course's groups in a week, each a block at its time, where a new one is placed among them; `before`: the
    course's own weekly time, about to become its first group. Context for courses/_weekpick.html, None if empty."""
    items = [SimpleNamespace(booking=b, group=g, before=False) for g in groups for b in g.times] + [SimpleNamespace(booking=b, group=None, before=True) for b in before]
    if not items:
        return None
    for i in items:
        start, end = timezone.localtime(i.booking.start), timezone.localtime(i.booking.end)
        i.day, i.start, i.end = start.weekday(), start.hour * 60 + start.minute, end.hour * 60 + end.minute
    side_by_side(items, lambda a, b: a.day == b.day and a.start < b.end and b.start < a.end)
    first, last = min(9, *(i.start // 60 for i in items)), max(18, *(-(-i.end // 60) for i in items))
    for i in items:
        i.place = f'{2 + (i.start - first * 60) // 5} / {2 + i.day} / {2 + (i.end - first * 60) // 5} / {3 + i.day}'
    return {
        'items': items, 'days': ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag'][:6 if any(i.day == 5 for i in items) else 5],
        'rows': (last - first) * 12, 'ticks': [(f'{2 + n * 12} / span 12', first + n) for n in range(last - first)],
        'first': first, 'last': last,
    }


def grouped(course, person=None):
    """A course's groups, each with `size`, the students in it (one counting for two modules is in it once), and `free`;
    with `person`, each with `clashing`, what of its dates collides with their week (clashes())."""
    groups = list(course.groups.annotate(size=Count('participations__study', distinct=True)).prefetch_related(
        'staff', Prefetch('bookings', Booking.objects.filter(start__gte=timezone.now()).select_related('room')),
    ))
    for g in groups:
        g.free = max(g.capacity - g.size, 0)
        g.clashing = clashes(person, g.bookings.all()) if person else []
        g.clash, g.clash_dates = weight(course, g.clashing)
    return groups


def join(course, study, module, group, note=''):
    """A place in a course taught in groups, or why not: one place a person in a course, none past a group's last; without
    a group where the Institut gives them out. The course is locked meanwhile, so neither slips through, however many
    register in the same moment."""
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        if mine := course.participations.filter(study__person=study.person_id).select_related('group').first():
            return f'Sie haben schon einen Platz{f" in Gruppe {mine.group.number}" if mine.group else ""}.' + (' Die Gruppe wechseln Sie oben unter „Meine Anmeldung“.' if group else '')
        if group and taking(group) >= group.capacity:
            return f'Gruppe {group.number} ist inzwischen voll. Bitte eine andere wählen.'
        Participation.objects.create(course=course, study=study, module=module, group=group, note=note)
    return ''


def moved(course, group, participation):
    """Into another group of the course, or why not: only where a place is free, while the course is locked; the old
    place is given up in the same step."""
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        if taking(group) >= group.capacity:
            return f'Gruppe {group.number} ist voll.'
        course.participations.filter(study=participation.study_id).update(group=group)  # each module it counts for
    return ''


def taking(group):
    """How many students a group's places are taken by."""
    return group.participations.values('study').distinct().count()


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
    if not person.everywhere('manage_courses'):  # of a course in groups, one's own group's; the office sees the first of any
        upcoming = upcoming.filter(Q(group=None) | Q(group__staff=person) | Q(group__participations__study__person=person))
    courses = (
        term.courses.filter(kind__in=TEACHING).select_related('subject', 'area', 'format').prefetch_related('staff')
        .annotate(
            size=Count('participations__study', distinct=True),  # students: one counting for two modules is one
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
    if node := Module.objects.filter(pk=request.GET.get('modul', '')).first() if request.GET.get('modul', '').isdigit() else None:  # from the Studienplan
        courses = courses.filter(pk__in=Course.objects.filter(modules__in=node.within()).values('pk'))
    settled = set().union(*(s.settled() for s in studies))
    counting = Module.objects.filter(courses=OuterRef('pk'), program__in=studies.values('program'))
    courses = courses.annotate(counting=Exists(counting), owed=Exists(counting.exclude(pk__in=settled)))
    done = request.GET.get('bestandene') == '1'
    if studies and not done:  # a student sees what still counts first; the rest on asking
        courses = courses.exclude(counting=True, owed=False)
    mine = {p.course_id: queued(p) or 0 for p in Participation.objects.filter(study__in=studies, course__term=term).select_related('course').annotate(ahead=ahead())}
    asked = set(Signup.objects.filter(study__in=studies, course__term=term).values_list('course', flat=True))
    today = timezone.localdate()
    running = term.end >= today  # registered late, the teachers may still take someone
    if studies:  # lesson classes are given out: a student sees their own
        courses = courses.exclude(Q(kind=Course.Kind.LESSON) & ~Q(pk__in=list(mine)))
    joinable = request.GET.get('anmeldung') == 'moeglich'
    if studies and joinable:  # what one can still register for, or ask to join late
        can = (Q(opens=None) | Q(opens__lte=today)) & (Q(deadline=None) | Q(deadline__gte=today))
        if running:
            can |= Q(allocation__in=[Course.Allocation.ORDER, Course.Allocation.CHOICE])
        courses = courses.filter(can, owed=True, cancelled=None, kind=Course.Kind.COURSE).exclude(allocation=Course.Allocation.STAFF).exclude(pk__in=list(mine))
    courses = list(courses.order_by(F('area__order').asc(nulls_last=True), 'area__name', 'title').prefetch_related(
        Prefetch('groups', Group.objects.annotate(size=Count('participations__study', distinct=True))),
    ))
    for c in courses:  # in groups: their places, and in how many one is still free
        groups = c.groups.all()
        c.open_groups = sum(g.size < g.capacity for g in groups) if groups else None
        if groups:
            c.capacity, c.free = sum(g.capacity for g in groups), sum(max(g.capacity - g.size, 0) for g in groups)
    return render(request, 'courses/catalogue.html', {
        'term': term,
        'terms': Term.shown(person).filter(courses__kind__in=TEACHING).distinct().order_by('-start').filter(**since(request.user)), 'open': open_,
        'courses': courses,
        'programs': Program.planned(),
        'program': program,
        'areas': Area.objects.filter(courses__in=offered.values('pk')).distinct(),
        'area': area,
        'formats': Format.objects.filter(courses__in=offered.values('pk')).distinct(),
        'form': form,
        'q': q,
        'office': 'manage_courses' in person.roles,
        'scoped': scoped, 'reach': reach, 'student': studies.exists(), 'done': done, 'mine': mine, 'asked': asked,
        'running': running, 'joinable': joinable, 'node': node,
    })


def over(course):
    """Whether a course's teaching is over: its last date past, or without dates its semester's lectures."""
    last = course.bookings.aggregate(Max('end'))['end__max']
    return last < timezone.now() if last else course.term.lectures_end < timezone.localdate()


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
        return staff, seen(course, person, rows) if staffed else rows

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
    waitlist = course.waitlist()
    for p in rows:
        p.waiting = waitlist.get(p.pk)
    if refused:
        panel.retype(rows, request.POST, refused)
    own = [p for p in rows if p.study.person_id == person.pk]
    eligible = sorted(  # who may register: in any study, those it counts for first; warned of what the plan says against it
        (fits(course, s) for s in person.studies.filter(status=Study.Status.ENROLLED).select_related('program')), key=lambda s: bool(s.concern),
    ) if course.kind == Course.Kind.COURSE else []
    if course.allocation == Course.Allocation.ENSEMBLE:  # an ensemble registers in a study the course counts for
        eligible = [s for s in eligible if s.counts]
    studied = {s.program_id for s in eligible if s.counts}
    gaps = unmet([(p.study_id, p.module_id) for p in rows]) if staffed and course.kind == Course.Kind.COURSE else {}
    for p in rows:  # what the teachers see of a registration the plan would not have
        p.missing = gaps.get((p.study_id, p.module_id))
    upcoming = course.bookings.filter(start__gte=timezone.now())
    if course.allocation == Course.Allocation.ENSEMBLE and staffed and not person.may('manage_courses', course):  # a coach: the course's own dates and their ensembles'
        upcoming = upcoming.filter(Q(ensemble=None) | Q(ensemble__coach=person))
    if course.kind == Course.Kind.LESSON and not staffed:
        upcoming = upcoming.filter(participants=person)  # one's own lessons, not everyone's
    elif not staffed:  # the dates for everyone, and those of one's own ensemble
        upcoming = upcoming.filter(Q(participants=None) | Q(participants=person) | Q(person=person)).distinct()
    student = bool(eligible or own) and not staffed  # what of the groups' times clashes with their week
    groups = grouped(course, person if student else None)
    wished = course.signups.filter(study__person=person).first()
    taught = [g for g in groups if person in g.staff.all()]
    if groups and not office and (taught or not staffed):  # a group's dates are its own
        upcoming = upcoming.filter(Q(group=None) | Q(group__staff=person) | Q(group__participations__study__person=person)).distinct()
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
        'lengths': sorted(Counter(minutes(p.module) or course.hours for p in course.participations.select_related('module__program')).items(), reverse=True) if course.kind == Course.Kind.LESSON and staffed else [],
        'share': minutes(own[0].module) or course.hours if own and course.kind == Course.Kind.LESSON else None,
        'staff': staff,
        'rows': rows if staffed else [],
        'own': own,
        'wished': wished,
        'signups': course.signups.select_related('study__person', 'study__program', 'study__subject') if staffed else [],
        'count': (count := len({p.study_id for p in rows})), 'placed': count - (waiting := len(set(waitlist.values()))), 'waiting': waiting,
        'free': course.capacity - count + waiting if course.capacity is not None else None,
        'dropping': staffed and course.kind == Course.Kind.COURSE and course.allocation != Course.Allocation.ENSEMBLE,
        'staffed': staffed,
        'eligible': eligible,
        'unmarked': sum(1 for p in rows if p.passed is None and not p.remark) if staffed and course.kind == Course.Kind.COURSE and over(course) and not course.modules.filter(exam=True).exists() else 0,
        'counts_for': besides_focus(course, eligible[0]) if eligible and not own else '',
        'clashing': (clashing := clashes(person, course.bookings.filter(start__gte=timezone.now(), participants=None).select_related('room')) if eligible and not own and not groups else []),
        'clash': weight(course, clashing),
        'groups': groups, 'taught': taught, 'regrouping': groups and staffed and not course.term.over,
        'choosing': bool(groups and eligible and not own and not wished and course.is_open and course.grouping == Course.Grouping.CHOICE),
        'switching': bool(groups and own and own[0].group_id and own[0].passed is None and course.is_open and course.grouping == Course.Grouping.CHOICE),
        'placing': bool(groups and eligible and not own and not wished and course.is_open and course.grouping != Course.Grouping.CHOICE),
        'limit': course.clash_limit(),
        'current': (current := next((g for g in groups if own and g.pk == own[0].group_id), None)),
        'offered': sorted((g for g in groups if g.free and g != current), key=lambda g: (FIT[g.clash], weekly_at(g))),  # what fits first
        'booked_out': sorted((g for g in groups if not g.free and g != current), key=weekly_at),
        'deletable': office and not course.participations.exists() and not course.signups.exists() and not course.ensembles.exists(),
        'growing': office and course.kind == Course.Kind.COURSE and course.allocation != Course.Allocation.ENSEMBLE and (groups or course.bookings.exists()),
        'unplaced': course.participations.filter(group=None).values('study').distinct().count() if groups and office else 0,
        'open_groups': sum(1 for g in groups if g.free), 'places': sum(g.capacity for g in groups), 'taken': sum(g.size for g in groups),
        'running': running, 'cancelling': office and not course.term.planning and not course.cancelled and running,
        'from': max(course.term.lectures_start, timezone.localdate()), 'office': office,
        'modules': credited(course, studied), 'studied': studied,
        'bookings': upcoming.select_related('room', 'category', 'group').prefetch_related('participants', 'absent')[:8],
        'plan': PlanForm(sent(request, 'plan'), person=person, course=course) if staffed and running and not course.cancelled and course.kind != Course.Kind.LESSON else None,
        'date': DateForm(sent(request, 'date'), person=person, course=course) if staffed and running and not course.cancelled else None,
        'lessons': timetable,
        'lesson_rows': zip([s for s, _ in lessons], timetable or []),
        'ensembles': ensembles,
        'grouping': EnsembleForm(sent(request, 'ensemble'), course=course, person=person) if course.allocation == Course.Allocation.ENSEMBLE else None,
        'adding': AddForm(course=course) if staffed else None,
        'reachable': Study.reachable(person, Study.objects.filter(Q(participations__course=course) | Q(signups__course=course))) if staffed else set(),
        'panel': False,
    })


def twin_of(data, term):
    """A course of the semester that a new one would duplicate: one of the same title, or one for the same modules, as a
    Satztechniken already taught at another time. Better a group of it."""
    same = Q(title__iexact=data['title'].strip())
    if chosen := {m.pk for m in data.get('modules', ())}:
        alike = Course.objects.annotate(n=Count('modules', distinct=True), hit=Count('modules', filter=Q(modules__in=chosen), distinct=True)).filter(n=len(chosen), hit=len(chosen))
        same |= Q(pk__in=alike.values('pk'))
    return Course.objects.filter(same, term=data.get('term', term), kind__in=TEACHING, cancelled=None).prefetch_related('staff').first()


@require_POST
def remove(request, pk):
    """Delete a course made by mistake, with its dates: the office's, while nobody is in it, has asked for a place or plays
    in an ensemble of it. One that people are in is called off instead (cancel), so that they hear of it."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not request.user.may('manage_courses', course):
        raise PermissionDenied
    if course.participations.exists() or course.signups.exists() or course.ensembles.exists():
        messages.error(request, 'Hier hat sich schon jemand angemeldet: bitte absagen statt löschen, damit alle davon erfahren.')
        return redirect(course)
    course.delete()
    messages.success(request, f'„{course.title}“ gelöscht, mit den Terminen.')
    return redirect(f"{reverse('courses:catalogue')}?semester={course.term_id}")


COPIED = ['kind', 'title', 'area', 'format', 'subject', 'rhythm', 'hours', 'capacity', 'allocation', 'grouping', 'opens', 'deadline', 'clash_dates', 'description']  # what „Duplizieren“ takes


def edit(request, pk=None):
    """Create or change a course: the office for its Institute and Studiengänge; its teachers what is theirs to decide
    (TeachingForm)."""
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING) if pk else Course(term=term, opens=term and term.course_signup_start, deadline=term and term.course_signup_end)
    initial, source = {}, None
    if not pk and (source := Course.objects.filter(pk=request.GET.get('von') or None, kind__in=TEACHING).first()):  # „Duplizieren“: filled in, saved once saved
        course = Course(term=source.term, **{f: getattr(source, f) for f in COPIED})
        if course.deadline and course.deadline < timezone.localdate():  # a registration over is not copied
            course.opens = course.deadline = None
        initial = {'staff': list(source.staff.values_list('pk', flat=True)), 'modules': list(source.modules.values_list('pk', flat=True))}
    office = 'manage_courses' in person.roles and (not pk or person.may('manage_courses', course))
    if not (office or pk and course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    over = bool(pk) and course.term.over
    if over and not office:
        raise PermissionDenied(f'Das {course.term} ist vorbei. Korrekturen trägt {Office.holding("manage_courses") or "das zuständige Amt"} ein, mit Grund.')
    planning = person.may('plan_teaching', course) if pk else 'plan_teaching' in person.roles
    form = CourseForm(request.POST or None, instance=course, initial=initial, person=person, planning=planning, over=over) if office else TeachingForm(request.POST or None, instance=course, person=person)
    before = course.waitlist() if pk else []  # before the form changes the places
    twin = not pk and not source and form.is_valid() and 'doppelt' not in request.POST and twin_of(form.cleaned_data, course.term)
    if form.is_valid() and not twin:
        with history.because(form.cleaned_data.get('correction', '')):
            saved = kept(form, person, 'manage_courses') if office else form.save()
        if saved:
            admit(saved, before)
            messages.success(request, 'Gespeichert. Jetzt Termine planen und Teilnehmende eintragen.' if not pk else 'Gespeichert.')
            return redirect(saved)
    back = course.get_absolute_url() if pk else reverse('courses:catalogue')
    return render(request, 'courses/form.html', {
        'form': form, 'course': course, 'menu': 'courses/_nav.html', 'heading': f'Kopie von {source.title}' if source else 'Neue Veranstaltung', 'back': back, 'twin': twin,
        'joining': twin and [p.pk for p in form.cleaned_data.get('staff', ())],
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
    form = AddForm(request.POST, course=course)
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
    group = form.cleaned_data.get('group')
    if not exam and (there := course.participations.filter(study__person=study.person_id).select_related('group').first()):
        messages.success(request, f'{study.person} ist bereits eingetragen{f", in Gruppe {there.group.number}" if there.group else ""}.')
        return redirect(course)
    participation, created = Participation.objects.get_or_create(course=course, study=study, module=module, defaults={'group': group})
    if not created:
        messages.success(request, f'{study.person} ist bereits eingetragen.')
    elif exam:  # the office registers whoever the regulations allow, in the registration period or not
        texts.send('pruefungsanmeldung', study.person, reverse('exams:index'), pruefung=course.title, modul=module)
        attempt = next(p.attempt for p in panel.sheet(course)[1] if p.pk == participation.pk)
        messages.success(request, f'{study.person} eingetragen, {attempt}. Versuch{"" if course.is_open else ", außerhalb der Anmeldefrist"}.')
    else:
        texts.send('eingetragen', study.person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
        fits(course, study)
        dates = (group.bookings if group else course.bookings.filter(group=None, participants=None)).filter(start__gte=timezone.now()).select_related('room')
        _, clashing = weight(course, clashes(study.person, dates))
        notes = ' '.join(filter(None, [study.concern, clashing and f'An {plural(clashing, "Termin", "Terminen")} überschneidet sie sich mit dem Stundenplan.']))
        done = f'{study.person} eingetragen' + (f', für {module.code}' if module else '') + (f', in Gruppe {group.number}' if group else '') + '.'
        (messages.warning if notes else messages.success)(request, f'{done} {notes}'.strip())
    return redirect(course)


@require_POST
def drop(request, pk):
    """Take someone out while no mark is given: of an exam the office entered them in by mistake, of a course its
    teachers, say someone who never came. They hear of it, the waitlist moves up, the Verlauf keeps it."""
    course = get_object_or_404(Course.objects.exclude(allocation=Course.Allocation.ENSEMBLE), pk=pk, kind__in=[Course.Kind.EXAM, Course.Kind.COURSE], released=None)
    exam = course.kind == Course.Kind.EXAM
    if not (request.user.may('manage_exams', course) if exam else teaches(request.user, course)):
        raise PermissionDenied
    rows = course.participations.select_related('study__person').filter(scores=None, grade=None)
    if exam:
        chosen = [get_object_or_404(rows, pk=request.POST.get('participation'))]
    else:
        chosen = [p for p in seen(course, request.user, list(rows.filter(passed=None, study__in=request.POST.getlist('study'))))]
        if not chosen:
            messages.error(request, 'Niemand ausgetragen. Bitte ankreuzen, wen; wer ein Ergebnis hat, bleibt.')
            return redirect(course)
    before = course.waitlist()
    for participation in chosen:
        participation.delete()
        texts.send('ausgetragen', participation.study.person, reverse('exams:index') if exam else course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    admit(course, before)
    messages.success(request, f'{", ".join(str(p.study.person) for p in chosen)} ausgetragen.')
    return redirect(course)


def roster(request, pk):
    """A course's participants as CSV, to print or keep, as its teachers see them."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    rows = seen(course, request.user, list(course.participations.select_related('study__person', 'study__program', 'study__subject', 'module', 'group').annotate(ahead=ahead())))
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="teilnehmende-{course.pk}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Matrikelnummer', 'Nachname', 'Vorname', 'Studiengang', 'Hauptfach', 'Modul', 'Gruppe', 'Angemeldet', 'Warteliste', 'Note', 'Bestanden'])
    Export.log(request.user, f'Teilnehmende {course.title}, {course.term}', len(rows))
    for p in sorted(rows, key=lambda p: (p.study.person.last_name, p.study.person.first_name)):
        writer.writerow([
            p.study.person.matriculation, p.study.person.last_name, p.study.person.first_name, p.study.program.title, p.study.subject,
            p.module.code if p.module else '', p.group.number if p.group else '', f'{timezone.localtime(p.created):%d.%m.%Y %H:%M}', queued(p) or '',
            str(p.grade if p.grade is not None else '').replace('.', ','), {True: 'ja', False: 'nein'}.get(p.passed, ''),
        ])
    return response


def write(request, pk):
    """A message from a course's teachers to its participants, the waitlist included: the ticked, else all."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    chosen = set(request.POST.getlist('study'))
    rows = seen(course, request.user, list(course.participations.select_related('study__person')))
    studies = list({p.study_id: p.study for p in rows if not chosen or str(p.study_id) in chosen}.values())
    people = sorted({s.person for s in studies}, key=lambda p: p.name)
    form = ComposeForm(request.POST if 'subject' in request.POST else None)
    if people and form.is_valid():
        form.send(people, str(request.user), course.get_absolute_url())
        messages.success(request, f'Nachricht an {plural(len(people), "Person", "Personen")} gesendet.')
        return redirect(course)
    return render(request, 'students/compose.html', {
        'form': form, 'people': people, 'studies': studies, 'back': course.get_absolute_url(), 'sender': request.user, 'course': course, 'menu': 'courses/_nav.html',
    })


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    study = fits(course, get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED))
    module, reason = study.owed, request.POST.get('grund', '').strip()[:200]
    late = not course.is_open and not course.upcoming  # the teachers may still take someone while the term runs
    besides = besides_focus(course, study)
    choosing = course.grouping == Course.Grouping.CHOICE
    group = course.groups.filter(pk=request.POST.get('gruppe') or None).first() if choosing else None
    full = choosing and not group and course.groups.exists()  # every group full: the teachers may open another
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
    elif study.concern and not reason:
        messages.error(request, 'Bitte kurz angeben, warum Sie sich dennoch anmelden; die Lehrenden sehen es.')
    elif lost(study):
        messages.error(request, 'Der Prüfungsanspruch ist erloschen; neue Anmeldungen sind nicht möglich.')
    elif course.allocation == Course.Allocation.CHOICE or late or full:
        if Signup.objects.get_or_create(course=course, study=study, defaults={'module': module, 'note': reason or request.POST.get('note', '').strip()})[1]:
            texts.send('angefragt', request.user, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
        messages.success(request, f'{"Nachmeldung angefragt" if late else "Angefragt"}. Die Lehrenden entscheiden; die Antwort kommt in Ihr Postfach.{besides}')
    elif group or course.groups.exists():
        if refused := join(course, study, module, group, reason):
            messages.error(request, refused)
        else:
            messages.success(request, f'Sie sind angemeldet, in Gruppe {group.number}.{besides}' if group else f'Sie sind angemeldet. Ihre Gruppe teilt das Institut zu; Sie erfahren sie per Nachricht.{besides}')
    elif course.participations.filter(study__person=request.user).exists():
        messages.error(request, 'Sie sind bereits angemeldet.')
    else:
        participation, created = Participation.objects.get_or_create(course=course, study=study, module=module, defaults={'note': reason})
        if place := course.waitlist().get(participation.pk):
            if created:
                texts.send('warteliste', request.user, course.get_absolute_url(), veranstaltung=course.title, semester=course.term, platz=place)
            messages.success(request, f'Sie stehen auf Platz {place} der Warteliste. Wird ein Platz frei, rücken Sie nach und erhalten eine Nachricht.{besides}')
        else:
            messages.success(request, f'Sie sind angemeldet.{besides}')
    return redirect(course)


def clashes(person, bookings, mine=None):
    """What of these dates collides with the person's timetable, once per weekday and time and what it collides with:
    [(the first such date, the other, whether only the way between buildings is too short)], the date with `times`, on
    how many of that weekday's dates, and `of`, of how many: a project week is not every week. Practice the person booked
    moves aside and counts not. `mine`: their dates, fetched once for many calls."""
    if not (bookings := list(bookings)):
        return []
    if mine is None:
        mine = list(agenda(person).filter(start__lte=max(b.end for b in bookings) + QUARTER, end__gte=min(b.start for b in bookings) - QUARTER).select_related('room', 'course', 'category'))
    found, dates, times = {}, Counter(), Counter()
    for b in bookings:
        at = timezone.localtime(b.start)
        dates[slot := (at.weekday(), at.time())] += 1
        others = [o for o in mine if o.course_id != b.course_id and o.category.tone != BookingCategory.Tone.PRACTICE]
        at_once = [o for o in others if o.start < b.end and b.start < o.end]
        near = [o for o in others if o not in at_once and (b.start - QUARTER <= o.end <= b.start or b.end <= o.start <= b.end + QUARTER) and o.room.building != b.room.building]
        for other, way in [(o, False) for o in at_once] + [(o, True) for o in near]:  # each on its own: a lesson every week stays every week, a project week beside it or not
            key = (slot, other.course_id or other.label, way)
            found.setdefault(key, (copy(b), other, way))  # its own, so that another call, or another clash of it, leaves its counts alone
            times[key] += 1
    for key, (b, _, _) in found.items():
        b.times, b.of = times[key], dates[key[0]]
    return sorted(found.values(), key=lambda f: (f[2], -f[0].times, f[0].start))  # what weighs most first: at the same time, often


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
        group = course.groups.filter(pk=request.POST.get('gruppe') or None).first()
        Participation.objects.get_or_create(course=course, study=signup.study, module=signup.module, defaults={'group': group})
        texts.send('eingetragen', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    else:
        texts.send('kein_platz', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    signup.delete()
    messages.success(request, f'{person}: {"zugelassen" if request.POST.get("decision") == "yes" else "abgesagt"}. Nachricht versandt.')
    return redirect(course)


def back(request, course):
    """Where a form that came with `next` leads back to, else the course."""
    to = request.POST.get('next', '')
    return redirect(to if url_has_allowed_host_and_scheme(to, {request.get_host()}) else course.get_absolute_url())


@require_POST
def retract(request, pk):
    signup = get_object_or_404(Signup, pk=pk, study__person=request.user)
    signup.delete()
    messages.success(request, f'Anfrage für „{signup.course.title}“ zurückgezogen.')
    return back(request, signup.course)


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
    booking.group = data.get('group')
    booking.save()
    if student := data.get('student'):
        booking.participants.set([student])
    elif ensemble:  # its members see it in their timetable
        booking.participants.set(Person.objects.filter(studies__ensembles=ensemble))
    messages.success(request, f'Termin am {date_format(start, "j.n.Y")} in {booking.room} gebucht.')
    return redirect(course)


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation.objects.select_related('course__term'), pk=pk, study__person=request.user, course__kind__in=TEACHING, passed=None)
    if participation.course.leavable:
        before = participation.course.waitlist()
        participation.course.participations.filter(study=participation.study_id, passed=None).delete()  # each module it counts for
        admit(participation.course, before)
        messages.success(request, f'Von „{participation.course.title}“ abgemeldet.')
    elif participation.course.is_open:
        messages.error(request, 'Abmelden geht hier über die Lehrenden.')
    else:
        messages.error(request, 'Der Abmeldeschluss ist vorbei.')
    return back(request, participation.course)


@require_POST
def switch(request, pk):
    """A participant takes a free place in another group of the course while registration runs, giving up their own in
    the same step."""
    participation = get_object_or_404(Participation.objects.select_related('course__term', 'group'), pk=pk, study__person=request.user, group__isnull=False, passed=None)
    course = participation.course
    group = get_object_or_404(course.groups, pk=request.POST.get('gruppe') or None)
    if not course.is_open or course.grouping != Course.Grouping.CHOICE:
        messages.error(request, 'Die Gruppe wechseln geht bis zum Anmeldeschluss, wo Sie sie selbst wählen; sonst über die Lehrenden.')
    elif refused := moved(course, group, participation):
        messages.error(request, refused)
    else:
        messages.success(request, f'Gewechselt: jetzt in Gruppe {group.number}.')
    return back(request, course)


@require_POST
def distribute(request, pk):
    """The office puts everyone in the course without a group into one with a place: the one whose dates clash least with
    their week, in the order they registered. Each hears of their group; who finds no place stays without."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind=Course.Kind.COURSE)
    if not request.user.may('manage_courses', course) or course.term.over:
        raise PermissionDenied
    groups, first, placed = grouped(course), {}, []
    for p in course.participations.filter(group=None).select_related('study__person').order_by('created', 'pk'):
        first.setdefault(p.study_id, p)
    dates = [b for g in groups for b in g.bookings.all()]
    span = dates and (min(b.start for b in dates) - QUARTER, max(b.end for b in dates) + QUARTER)  # a date ending as the window opens counts
    for p in first.values():
        if not (free := [g for g in groups if g.free]):
            break
        mine = list(agenda(p.study.person).filter(start__lte=span[1], end__gte=span[0]).select_related('room', 'course', 'category')) if span else []
        best = min(free, key=lambda g: (weight(course, clashes(p.study.person, g.bookings.all(), mine))[1], g.number))
        course.participations.filter(study=p.study_id).update(group=best)
        best.free -= 1
        placed.append((p.study.person, best))
    for person, g in placed:
        texts.send('gruppe', person, course.get_absolute_url(), veranstaltung=course.title, gruppe=g.number, zeit=meets(g), von=request.user)
    left = len(first) - len(placed)
    messages.success(request, f'{plural(len(placed), "Person", "Personen")} auf die Gruppen verteilt und benachrichtigt.' + (f' {left} ohne Platz: bitte eine Gruppe hinzufügen.' if left else ''))
    return redirect(course)


@require_POST
def regroup(request, pk):
    """The course's teachers or office move the ticked into another group, past its places if need be: they decide. Each
    hears of their new group."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind=Course.Kind.COURSE)
    if not teaches(request.user, course) or course.term.over:
        raise PermissionDenied
    group = get_object_or_404(course.groups.prefetch_related('bookings__room'), pk=request.POST.get('gruppe') or None)
    chosen = seen(course, request.user, list(course.participations.filter(study__in=request.POST.getlist('study')).exclude(group=group).select_related('study__person')))
    if not chosen:
        messages.error(request, 'Niemand verschoben. Bitte ankreuzen, wen.')
        return redirect(course)
    Participation.objects.filter(pk__in=[p.pk for p in chosen]).update(group=group)
    people = {p.study_id: p.study.person for p in chosen}.values()  # once, whatever modules the course counts for
    for person in people:
        texts.send('gruppe', person, course.get_absolute_url(), veranstaltung=course.title, gruppe=group.number, zeit=meets(group), von=request.user)
    messages.success(request, f'{", ".join(map(str, people))}: jetzt in Gruppe {group.number}.' + ('' if taking(group) <= group.capacity else f' Sie hat damit mehr Teilnehmende als ihre {group.capacity} Plätze.'))
    return redirect(course)


def meets(group):
    """When and where a group meets, as a message tells it: 'dienstags 09:00–10:00 Uhr in HzH 204'."""
    return '; '.join(f'{date_format(s, "l").lower()}s {s:%H:%M}–{e:%H:%M} Uhr in {b.room}' for b in group.times for s, e in [(timezone.localtime(b.start), timezone.localtime(b.end))]) or 'Termine folgen'


def group(request, pk, number=None):
    """Add a group to a course, or change one: its teachers, places and weekly time. The office's, as each group counts
    in the Deputat. A course taught to all at one time becomes its first group, with its dates and everyone in it, when
    another time is added. A new time is booked from today to the end of the lecture period in place of the old, where
    the room and the teachers are free, and the group hears of it; a group nobody is in can go."""
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind=Course.Kind.COURSE)
    person = request.user
    if not person.may('manage_courses', course) or course.term.over or course.allocation == Course.Allocation.ENSEMBLE:
        raise PermissionDenied
    group = get_object_or_404(course.groups.prefetch_related('bookings'), number=number) if number else None
    if group and 'loeschen' in request.POST:
        if group.participations.exists():
            messages.error(request, f'In Gruppe {group.number} ist noch jemand: erst in andere Gruppen verschieben.')
        else:
            group.delete()
            messages.success(request, f'Gruppe {group.number} gelöscht, mit ihren Terminen.')
        return redirect(course)
    groups = grouped(course)
    before = [] if groups else series(course.bookings.filter(group=None, ensemble=None).select_related('room'))  # the course's own time, to become Gruppe 1
    like = group or next((g for g in groups if str(g.number) == request.GET.get('wie')), None)  # „Speichern und nächste Gruppe“: one like it
    initial = {'staff': [int(pk) for pk in request.GET.getlist('lehrende') if pk.isdigit()]}
    if like or groups or before:
        model = like or (groups[-1] if groups else None)
        start = model.times[:1] if model else before[:1]
        initial['capacity'] = model.capacity if model else course.capacity or before[0].room.capacity
        if start:
            b, at = start[0], timezone.localtime(start[0].start)
            initial |= {'weekday': at.weekday(), 'minutes': int((b.end - b.start).total_seconds() // 60), 'room': b.room_id} | ({'time': at.time()} if group else {})
        if like:
            initial['staff'] = list(like.staff.values_list('pk', flat=True))
    form = GroupForm(request.POST or None, initial=initial, person=person, course=course)
    if form.is_valid():
        data, timed = form.cleaned_data, group is None or {'weekday', 'time', 'minutes', 'room'} & set(form.changed_data)
        old = group.bookings.filter(start__gte=timezone.now(), series__isnull=False) if group else Booking.objects.none()
        dates = weekly(course, data['staff'][0], data) if timed else []
        if taken := [b for b in dates if b.clashes().exclude(pk__in=old).exists()]:
            form.add_error(None, 'Der Raum ist belegt am ' + ', '.join(f'{timezone.localtime(b.start):%d.%m.}' for b in taken[:8]) + '.')
        for teacher in data['staff'] if dates else []:  # nobody teaches two groups at once
            theirs = agenda(teacher).filter(start__lt=max(b.end for b in dates), end__gt=min(b.start for b in dates)).exclude(pk__in=old)
            if hit := next(((b, o) for o in theirs for b in dates if o.start < b.end and b.start < o.end), None):
                form.add_error('staff', f'{teacher} hat am {timezone.localtime(hit[0].start):%d.%m.} um diese Zeit schon „{hit[1].label}“.')
        if form.is_valid():
            with transaction.atomic():
                if before:  # the one time so far becomes Gruppe 1, with everyone in it
                    first = Group.objects.create(course=course, number=1, capacity=initial['capacity'])
                    first.staff.set(course.staff.all())
                    course.bookings.filter(group=None, ensemble=None).update(group=first)
                    course.participations.update(group=first)
                if group is None:
                    group = Group.objects.create(course=course, number=max([g.number for g in groups] + [1 if before else 0]) + 1, capacity=data['capacity'])
                else:
                    group.capacity = data['capacity']
                    group.save(update_fields=['capacity'])
                group.staff.set(data['staff'])
                course.staff.add(*data['staff'])
                if course.capacity is not None:  # the places are the groups'
                    course.capacity = None
                    course.save(update_fields=['capacity'])
                if timed:
                    old.delete()
                    for b in dates:
                        b.group = group
                    history.saved(Booking.objects.bulk_create(dates))
            if timed and number and dates:
                start, end = timezone.localtime(dates[0].start), timezone.localtime(dates[0].end)
                for p in Person.objects.filter(studies__participations__group=group).distinct():
                    texts.send(
                        'unterrichtszeit', p, course.get_absolute_url(), veranstaltung=f'{course.title}, Gruppe {group.number}', von=person, raum=data['room'],
                        zeit=f'{date_format(start, "l").lower()}s {start:%H:%M}–{end:%H:%M} Uhr', ab=date_format(start, 'j.n.Y'),
                    )
            messages.success(request, ' '.join(filter(None, [
                before and f'Die bisherige Zeit ist jetzt Gruppe 1, mit {plural(course.participations.values("study").distinct().count(), "Teilnahme", "Teilnahmen")}.',
                f'Gruppe {group.number} gespeichert' + (f', {plural(len(dates), "Termin", "Termine")} in {data["room"]} gebucht.' if timed else '.'),
            ])))
            if 'weitere' in request.POST:
                return redirect(f"{reverse('courses:group', args=[course.pk])}?wie={group.number}")
            return redirect(f'{course.get_absolute_url()}#gruppen')
    return render(request, 'courses/form.html', {
        'form': form, 'course': course, 'menu': 'courses/_nav.html', 'back': course.get_absolute_url(), 'group': group, 'another': True,
        'part': f'Gruppe {group.number}' if group else 'Neue Gruppe',
        'week': week_of(course, groups, before=before), 'groups': groups, 'before': before,
        'note': 'Bucht den Raum jede Woche bis zum Ende der Unterrichtszeit, ohne die unterrichtsfreien Tage, nur wenn Raum und Lehrende an allen Terminen frei sind. Jede Gruppe zählt im Deputat ihrer Lehrenden.',
    })


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
                ensemble=data.get('ensemble'), group=data.get('group'),
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
    planned = {p.study.person_id: minutes(p.module) for p in course.participations.select_related('module__program', 'study')}  # 'E 45': the plan's length
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
        taught_here = list(coaching(teaching, person).annotate(
            asked=Count('signups', distinct=True), mine=Count('ensembles__members', filter=Q(ensembles__coach=person), distinct=True),
            ingroups=Count('participations__study', filter=Q(participations__group__staff=person), distinct=True),
        ).select_related('subject', 'format', 'term').prefetch_related(
            'participations__module__program', 'origin__participations__module__program', Prefetch('groups', Group.objects.filter(staff=person), to_attr='own'),
        ))
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
            .select_related('course__term', 'module', 'group').annotate(ahead=ahead())
        )
        coming = {}  # each course's next date the student comes to: of a lesson class, their own
        for b in agenda(person).filter(course__term=term, start__gte=timezone.now()).select_related('room').order_by('start'):
            coming.setdefault(b.course_id, b)
        for p in registrations:
            p.next, p.queue = coming.get(p.course_id), queued(p)
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
    """One's week, days side by side; with `gruppe`, a group of a course drawn in, dashed, to see how it would fit before
    choosing it, the days one under the other, the whole week, so that a clash shows in a row with the hours across."""
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    shown = agenda(request.user)
    if trial := Group.objects.filter(pk=request.GET.get('gruppe') or None, course__term__in=Term.shown(request.user)).select_related('course').first():
        shown = Booking.objects.filter(Q(pk__in=shown.values('pk')) | Q(group=trial, category__blocks=True))
    context = week(shown, day, request.user, whole=bool(trial))
    for b in context['placed']:
        b.trial = trial is not None and b.group_id == trial.pk
    return render(request, 'courses/timetable.html', {'day': day, 'clashes': True, 'tones': legend(), 'off': 'teacher' in request.user.roles, 'trial': trial, **context})


def hours(term, seen):
    """{teacher: [(course, SWS)]} of a term's teaching, for the teachers `seen` (pks; None: all)."""
    courses = (
        term.courses.filter(kind__in=TEACHING, cancelled=None).select_related('format', 'term')
        .prefetch_related('staff', 'ensembles', 'groups__staff', 'participations__module__program', 'origin__participations__module__program').annotate(size=Count('participations__study', distinct=True))
    )
    if seen is not None:
        courses = courses.filter(pk__in=Course.objects.filter(staff__in=seen).values('pk'))
    found = defaultdict(list)
    for c in courses:
        coached = Counter(e.coach_id for e in c.ensembles.all()) + Counter(t.pk for g in c.groups.all() for t in g.staff.all())
        c.grouped = len(c.groups.all())
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


def usual(courses, by='course'):
    """{course pk: (weekday, time, minutes, room)}: each course's weekly date as it mostly took place, its groups' left
    out; by='group', each group's of these."""
    found = defaultdict(Counter)
    dates = Booking.objects.filter(**{f'{by}__in': courses}, ensemble=None, category__blocks=True)
    for b in (dates.filter(group=None) if by == 'course' else dates).select_related('room'):
        start = timezone.localtime(b.start)
        found[getattr(b, f'{by}_id')][start.weekday(), start.time(), int((b.end - b.start).total_seconds() // 60), b.room] += 1
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


def predecessor(course):
    """The course of the semester before whose groups a course continues: one taught in groups that counts for a module
    the course's own modules require, as Gehörbildung 1 for Gehörbildung 2."""
    before = Term.objects.filter(start__lt=course.term.start).order_by('-start').first()
    return Course.objects.filter(term=before, kind=Course.Kind.COURSE, modules__required_by__courses=course, groups__isnull=False).distinct().first()


def take_over(request, target, pending):
    """The ticked courses as drafts of `target`, from what they were: staff, Turnus, and the modules they counted for, also
    as the same semester a year before had them; registration as the semester sets it; lesson classes without their
    students, who come once they have re-registered (fill)."""
    chosen, titles = [], set()
    for c in pending.filter(pk__in=request.POST.getlist('course')).prefetch_related('modules', 'staff', 'groups__staff').order_by('-term__start'):
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
                allocation=c.allocation, grouping=c.grouping, clash_dates=c.clash_dates, description=c.description, rhythm=c.rhythm, origin=c,
                opens=target.course_signup_start, deadline=target.course_signup_end,
            )
            copy.modules.set({m.pk for m in c.modules.all()} | earlier[c.title])
            copy.staff.set(c.staff.all())
            source = predecessor(copy) if c.grouping == Course.Grouping.CONTINUED else c  # groups that stay together, with their teachers
            for g in source.groups.prefetch_related('staff') if source else []:
                Group.objects.create(course=copy, number=g.number, capacity=g.capacity).staff.set(g.staff.all())
                copy.staff.add(*g.staff.all())
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
    courses = list(target.courses.filter(pk__in=request.POST.getlist('course'), kind=Course.Kind.COURSE, cancelled=None, bookings__isnull=True).exclude(origin=None).prefetch_related('groups__staff'))
    slots, booked, taken = usual([c.origin_id for c in courses]), 0, []
    source = {c.pk: (p.pk if c.grouping == Course.Grouping.CONTINUED and (p := predecessor(c)) else c.origin_id) for c in courses}  # where its groups met
    before = {(g.course_id, g.number): g.pk for g in Group.objects.filter(course__in=source.values())}
    times = usual(before.values(), 'group')
    for c in courses:
        if groups := c.groups.all():  # each group at its time before, booked by its teacher
            dates = [
                b for g in groups if (slot := times.get(before.get((source[c.pk], g.number))))
                for b in weekly(c, g.staff.first() or request.user, dict(zip(['weekday', 'time', 'minutes', 'room'], slot), group=g))
            ]
        elif (slot := slots.get(c.origin_id)) is None:
            continue
        else:
            weekday, time, minutes, room = slot
            dates = weekly(c, request.user, {'weekday': weekday, 'time': time, 'minutes': minutes, 'room': room})
        if not dates:
            continue
        busy = Booking.objects.filter(room__in={b.room_id for b in dates}, category__blocks=True, start__lt=max(b.end for b in dates), end__gt=min(b.start for b in dates)).values_list('room', 'start', 'end')
        if clashing := {str(b.room) for r, s, e in busy for b in dates if r == b.room_id and s < b.end and b.start < e}:
            taken.append(f'{c.title} ({", ".join(sorted(clashing))})')
            continue
        history.saved(Booking.objects.bulk_create(dates))
        booked += 1
    messages.success(request, f'{plural(booked, "Veranstaltung hat", "Veranstaltungen haben")} ihre wöchentlichen Termine.')
    if taken:
        messages.error(request, f'Raum belegt, nicht gebucht: {"; ".join(taken)}. Diese bitte auf ihrer Seite planen.')


def fill(request, target, pending):
    """After the Rückmeldefrist: each lesson class of `target` gets whoever was in it and re-registered, not on leave, and
    the new students of its teachers in its Hauptfach; each with the module of their next Fachsemester. Each group that
    stays together gets whoever was in it the semester before and did not fail."""
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
    for c in target.courses.filter(kind=Course.Kind.COURSE, cancelled=None, grouping=Course.Grouping.CONTINUED).prefetch_related('groups', 'modules'):
        if (p := predecessor(c)) is None:
            continue
        taken, rows = set(c.participations.values_list('study', flat=True)), []
        was = {g.number: set(g.participations.exclude(passed=False).values_list('study', flat=True)) for g in p.groups.all()}
        for g in c.groups.all():
            for pk in was.get(g.number, set()) & (studies.keys() - taken):
                s, taken = studies[pk], taken | {pk}
                rows.append(Participation(course=c, study=s, group=g, module=min((m for m in c.modules.all() if m.program_id == s.program_id), key=lambda m: m.semester or 99, default=None)))
        history.saved(Participation.objects.bulk_create(rows))
        added += len(rows)
    messages.success(request, f'{plural(added, "Person", "Personen")} in die Klassen und die fortgeführten Gruppen des {target} eingetragen.')


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
    return back(request, course)


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
