"""The Vorlesungsverzeichnis and a course's own page, with creating, editing and deleting courses."""
from collections import Counter

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, F, Max, OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from admissions.auditions import held
from core import history
from core.access import kept
from core.forms import sent
from core.models import Office, Term
from core.shortcuts import without_term
from core.text import matching
from curriculum.models import Module, Program
from rooms.models import Booking
from students.models import Study
from .. import panel
from ..forms import AddForm, CourseForm, DateForm, EnsembleForm, Lessons, PlanForm, TeachingForm
from ..models import Area, Course, Format, Group, Participation, Signup
from ..modules import credited
from .common import TEACHING, admit, ahead, queued, seen, since, teaches
from .dates import lesson_times
from .groups import grouped, weekly_at
from .load import minutes
from .registration import besides_focus
from .timetable import clashes, fits, unmet, weight


def index(request):
    return redirect('courses:catalogue' if 'manage_courses' in request.user.roles else 'courses:mine')


def catalogue(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term and not Term.shown(person).filter(pk=term.pk).exists():
        term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester mit Lehrveranstaltungen')
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
            raise PermissionDenied('Ergebnisse tragen die Lehrenden der Lehrveranstaltung ein.')
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
        'trail': [('Meine Lehre', reverse('courses:mine')) if staffed and not office else ('Meine Lehrveranstaltungen', reverse('courses:mine')) if own else ('Vorlesungsverzeichnis', reverse('courses:catalogue'))],
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
        'clashing': (clashing := clashes(person, dates := course.bookings.filter(start__gte=timezone.now(), participants=None).select_related('room')) if eligible and not own and not groups else []),
        'clash': weight(clashing, dates if clashing else []),
        'groups': groups, 'taught': taught, 'regrouping': groups and staffed and not course.term.over,
        'choosing': bool(groups and eligible and not own and not wished and course.is_open and course.grouping == Course.Grouping.CHOICE),
        'switching': bool(groups and own and own[0].group_id and own[0].passed is None and course.is_open and course.grouping == Course.Grouping.CHOICE),
        'placing': bool(groups and eligible and not own and not wished and course.is_open and course.grouping != Course.Grouping.CHOICE),
        'current': (current := next((g for g in groups if own and g.pk == own[0].group_id), None)),
        'offered': sorted((g for g in groups if g.free and g != current), key=weekly_at),
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


COPIED = ['kind', 'title', 'area', 'format', 'subject', 'rhythm', 'hours', 'capacity', 'allocation', 'grouping', 'opens', 'deadline', 'description']  # what „Duplizieren“ takes


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
        'form': form, 'course': course,'heading': f'Kopie von {source.title}' if source else 'Neue Lehrveranstaltung', 'back': back, 'twin': twin,
        'joining': twin and [p.pk for p in form.cleaned_data.get('staff', ())],
        'note': None if office and planning else format_html(
            '{} trägt {} ein: sie zählen im Deputat und in den Studienplänen.', 'Umfang (SWS), Lehrform und Module' if office else 'Umfang (SWS), Lehrende, Module und Lehrform',
            Office.holding('plan_teaching', linked=True) or 'das zuständige Amt',
        ),
    })
