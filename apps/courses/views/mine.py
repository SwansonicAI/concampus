"""One's own courses: what one teaches, takes and still has to take."""
from copy import copy
from collections import defaultdict

from django.db.models import Count, OuterRef, Prefetch, prefetch_related_objects
from django.shortcuts import render
from django.utils import timezone

from core.models import Office, Term
from rooms.models import Booking
from rooms.plans import agenda
from students.models import Study
from ..models import Assignment, Course, Ensemble, Group, Participation, Signup
from .assignments import held
from .common import TEACHING, ahead, queued, since
from .load import coaching, counted, deputat, sws, taught
from .timetable import clashes, fits, limit, series, theirs, weight


def registering():
    """The semester students register for now: the next, once its registration has begun, else the one under way."""
    coming = Term.upcoming()
    if coming and not coming.planning and coming.courses.filter(kind=Course.Kind.COURSE, opens__lte=timezone.localdate()).exists():
        return coming
    return Term.current()


def to_take(study, term):
    """What the student's Studienplan has up to the Fachsemester they are in during `term` (`study.fs`) and no course in
    `term` taken or asked for yet: [(module, the courses of `term` to take for it)], where there are any. A part of the
    plan to choose from comes once, from the semester it begins in, with the courses of all it offers; once something in
    it is taken in `term`, only in its last semester, while its credits fall short (`short`). Never a Schwerpunkt
    not chosen, nor what the teachers give out, as Einzelunterricht. `study.until`: the first Anmeldeschluss among them."""
    study.fs = study.semester + (term.start > timezone.localdate())  # registering before the semester: they are one further then
    tree, _ = study.progress()
    options = study.program.choices()
    areas, hidden = {o.choice.pk for o in options}, {o.pk for o in options} - {study.focus_id}
    taken = {*study.participations.filter(course__term=term).values_list('module', flat=True), *study.signups.filter(course__term=term).values_list('module', flat=True)}
    links = list(Course.modules.through.objects.filter(module__program=study.program_id, course__term=term).values_list('module', 'course'))
    offered = {c.pk: c for c in term.courses.filter(pk__in={c for _, c in links}, kind=Course.Kind.COURSE, cancelled=None).exclude(allocation=Course.Allocation.STAFF).annotate(
        size=Count('participations__study', distinct=True),
    )}
    courses = defaultdict(list)
    for module, course in links:
        if course in offered:
            courses[module].append(offered[course])
    found = []

    def leaves(node):
        return [node] if not node.nodes else [leaf for child in node.nodes for leaf in leaves(child)]

    def visit(node):
        if node.done or node.pk in hidden:
            return
        if not node.nodes:
            if node.semester and node.semester <= study.fs and node.pk not in taken and courses[node.pk]:
                found.append((node, courses[node.pk]))
        elif node.offered > node.credits and node.pk not in areas:  # to choose from: once, with all it offers
            open_ = [leaf for leaf in leaves(node) if not leaf.done]
            there = list({c.pk: c for leaf in open_ for c in courses[leaf.pk] if leaf.pk not in taken}.values())
            started = sum(leaf.credits for leaf in open_ if leaf.pk in taken)
            node.short = node.credits - node.earned - started
            if there and node.first and node.first <= study.fs and (not started or study.fs >= node.last and node.short > 0):
                found.append((node, there))
        else:
            for child in node.nodes:
                visit(child)

    for root in tree:
        visit(root)
    study.until = min((c.deadline for _, there in found for c in there if c.is_open and c.deadline), default=None)
    return found


def fitted(study, found):
    """The courses of to_take(), each with `times`, `free`, `concern` and how it fits the student's week (clashes()):
    for the list, not the start page, which counts them only."""
    courses = list({c.pk: c for _, there in found for c in there}.values())
    prefetch_related_objects(courses, 'staff', Prefetch('groups', Group.objects.annotate(size=Count('participations__study', distinct=True))), Prefetch(
        'bookings', Booking.objects.filter(start__gte=timezone.now()).select_related('room'),
    ))
    mine = theirs(study.person, [b for c in courses for b in c.bookings.all()])
    for c in courses:
        groups = c.groups.all()
        c.times = sorted(series(c.bookings.all()), key=lambda b: (timezone.localtime(b.start).weekday(), timezone.localtime(b.start).time()))
        c.free = sum(max(g.capacity - g.size, 0) for g in groups) if groups else c.capacity - c.size if c.capacity is not None else None
        c.open_groups, c.limit = sum(g.size < g.capacity for g in groups), limit(c.bookings.all())
        c.clashing = [] if groups else clashes(study.person, c.bookings.all(), mine)
        c.clash, _ = weight(c.clashing, c.bookings.all())
        c.concern = fits(c, copy(study)).concern
    return found


def mine(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.shown(person).filter(courses__isnull=False).distinct().order_by('-start').filter(**since(person))}
    if 'teacher' in person.roles:
        teaching = person.teaching.filter(term=term, kind__in=TEACHING)
        here = OuterRef('pk')
        taught_here = list(coaching(teaching, person).annotate(
            asked=counted(Signup.objects.filter(course=here), 'course'),
            mine=counted(Ensemble.members.through.objects.filter(ensemble__course=here, ensemble__coach=person), 'ensemble__course', 'study'),
            ingroups=counted(Participation.objects.filter(course=here, group__staff=person), 'course', 'study'),
        ).select_related('subject', 'format', 'term').prefetch_related(
            'participations__module__program', 'origin__participations__module__program', Prefetch('groups', Group.objects.filter(staff=person), to_attr='own'),
        ))
        for c in taught_here:  # as the Deputat counts it: a lesson class by its students' minutes
            c.sws = None if c.cancelled else sws(c, c.coached)
        assignment = person.assignments.filter(term=term).exclude(status=Assignment.Status.WITHDRAWN).prefetch_related('courses').first()
        if assignment:  # after the lectures, the hours held of a granted one, to confirm
            assignment.so_far = None if assignment.status != Assignment.Status.GRANTED or timezone.localdate() <= term.lectures_end else (
                assignment.held if assignment.settled_on else held([assignment]).get(assignment.pk, 0))
        context.update(
            assignment=assignment, granting=assignment and Office.holding('grant_assignments', linked=True),
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
        if term == registering():  # what the Studienplan still wants this semester
            context['to_take'] = [(s, fitted(s, found)) for s in person.studies.filter(status=Study.Status.ENROLLED).select_related('program', 'person') if (found := to_take(s, term))]
    return render(request, 'courses/mine.html', context)
