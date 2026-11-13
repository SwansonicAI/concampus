"""Clashes between dates, whether a course fits a student's week, and the timetable page."""
from operator import attrgetter
from copy import copy
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import F, Q
from django.shortcuts import render
from django.utils import timezone

from core.dates import day as understood
from core.models import Institution, Term
from curriculum.models import Module
from rooms.models import Booking, BookingCategory
from rooms.plans import agenda, legend, walk, week
from students.models import Study
from ..models import Course, Group, Participation, owed_module
from .common import taking


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


def limit(dates):
    """From how many of these dates clashing registering warns plainly, in red: as the Hochschule sets it, or all of
    them, for a course of fewer dates."""
    return min(Institution.get().clash_dates, len(dates))


def weight(found, dates):
    """(how plainly to warn, on how many dates) of clashes() of these dates with someone's week: 'heavy' from limit(),
    'light' below it, as a rehearsal now and then, or only a way between buildings too short."""
    clashing = sum(b.times for b, _, way in found if not way)
    return ('' if not found else 'heavy' if clashing >= limit(dates) else 'light'), clashing


def series(bookings):
    """One date of each weekly series among `bookings`, in the order they begin."""
    first = {}
    for b in sorted(bookings, key=attrgetter('start')):
        if b.series:
            first.setdefault(b.series, b)
    return list(first.values())


def clashes(person, bookings, mine=None):
    """What of these dates collides with the person's timetable, once per weekday and time and what it collides with:
    [(the first such date, the other, whether only the way between buildings is too short)], the date with `times`, on
    how many of that weekday's dates, `of`, of how many: a project week is not every week, and `weeks`, the Mondays of
    those weeks. Practice the person booked moves aside and counts not. `mine`: their dates, fetched once for many calls."""
    if not (bookings := list(bookings)):
        return []
    if mine is None:
        mine = theirs(person, bookings)
    found, dates, times, weeks, gap = {}, Counter(), Counter(), defaultdict(set), walk()
    for b in bookings:
        at = timezone.localtime(b.start)
        dates[slot := (at.weekday(), at.time())] += 1
        others = [o for o in mine if o.course_id != b.course_id and o.category.tone != BookingCategory.Tone.PRACTICE]
        at_once = [o for o in others if o.start < b.end and b.start < o.end]
        near = [o for o in others if o not in at_once and (b.start - gap <= o.end <= b.start or b.end <= o.start <= b.end + gap) and o.room.building != b.room.building]
        for other, way in [(o, False) for o in at_once] + [(o, True) for o in near]:  # each on its own: a lesson every week stays every week, a project week beside it or not
            key = (slot, other.course_id or other.label, way)
            found.setdefault(key, (copy(b), other, way))  # its own, so that another call, or another clash of it, leaves its counts alone
            times[key] += 1
            weeks[key].add(at.date() - timedelta(days=at.weekday()))
    for key, (b, _, _) in found.items():
        b.times, b.of, b.weeks = times[key], dates[key[0]], weeks[key]
    return sorted(found.values(), key=lambda f: (f[2], -f[0].times, f[0].start))  # what weighs most first: at the same time, often


def theirs(person, bookings):
    """The person's dates over the time `bookings` span, the walk() either side: what clashes() compares with."""
    if not bookings:
        return []
    return list(agenda(person).filter(start__lte=max(b.end for b in bookings) + walk(), end__gte=min(b.start for b in bookings) - walk()).select_related('room', 'course', 'category'))


def timetable(request):
    """One's week, days side by side; with `gruppe`, a group of a course drawn in, dashed, to see how it would fit before
    choosing it, the days one under the other, the whole week, so that a clash shows in a row with the hours across, and
    which other weeks of the semester it clashes in. The legend names only what the week shows."""
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    shown = agenda(request.user)
    if trial := Group.objects.filter(pk=request.GET.get('gruppe') or None, course__term__in=Term.shown(request.user)).select_related('course').first():
        shown = Booking.objects.filter(Q(pk__in=shown.values('pk')) | Q(group=trial, category__blocks=True))
    context = week(shown, day, request.user, whole=bool(trial))
    if trial:  # only what clashes with the group is marked
        for b in context['placed']:
            b.trial = b.group_id == trial.pk
        for b in context['placed']:
            b.clash = any(o is not b and (b.trial or o.trial) and o.start < b.end and b.start < o.end for o in context['placed'])
            if getattr(b, 'move', None) and not (b.trial or b.came.trial):  # a change of building, only the group's
                b.move = None
        dates = list(trial.bookings.filter(start__gte=timezone.now(), category__blocks=True).select_related('room'))
        weeks = sorted(set().union(*(b.weeks for b, _, way in clashes(request.user, dates) if not way)))
        context |= {
            'every': len(weeks) > 1 and len(weeks) == len({timezone.localdate(b.start) - timedelta(days=timezone.localdate(b.start).weekday()) for b in dates}),
            'here': context['monday'] in weeks, 'others': [m for m in weeks if m != context['monday']],
        }
    placed = context['placed']
    return render(request, 'courses/timetable.html', {
        'day': day, 'clashes': True, 'off': 'teacher' in request.user.roles, 'trial': trial, 'way_in': trial and way_in(request.user, trial),
        'tones': [(tone, label) for tone, label in legend() if any(b.tone == tone for b in placed)],
        'clashed': any(b.clash for b in placed), 'moved': any(getattr(b, 'move', None) for b in placed), **context,
    })


def way_in(person, group):
    """How the person takes a place in `group` seen in their week: ('register', study), the study with its `concern`
    (fits()) where the plan wants a reason, or ('switch', their participation); None where it is full or the course
    gives none out so."""
    course = group.course
    if not course.is_open or course.grouping != Course.Grouping.CHOICE or taking(group) >= group.capacity:
        return None
    if own := course.participations.filter(study__person=person, passed=None).first():
        return ('switch', own) if own.group_id != group.pk else None
    studies = [fits(course, s) for s in person.studies.filter(status=Study.Status.ENROLLED).select_related('program')]
    study = next((s for s in studies if not s.concern), None) or next(iter(studies), None)
    return study and ('register', study)
