"""Groups and ensembles within a course: forming, distributing, coaching."""
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import history, texts
from core.apps import plural
from core.forms import again
from core.models import Person
from rooms.models import Booking
from rooms.plans import agenda, side_by_side, walk
from students.models import Study
from ..forms import EnsembleForm, GroupForm
from ..models import Course, Ensemble, Group, Participation
from .common import TEACHING, seen, taking, teaches
from .dates import weekly
from .timetable import clashes, limit, series, theirs, weight


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
    mine = theirs(person, [b for g in groups for b in g.bookings.all()]) if person else None
    for g in groups:
        g.free = max(g.capacity - g.size, 0)
        g.clashing = clashes(person, g.bookings.all(), mine) if person else []
        g.clash, g.clash_dates = weight(g.clashing, g.bookings.all())
        g.limit = limit(g.bookings.all())
    return groups


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
    span = dates and (min(b.start for b in dates) - walk(), max(b.end for b in dates) + walk())  # a date ending as the window opens counts
    for p in first.values():
        if not (free := [g for g in groups if g.free]):
            break
        mine = list(agenda(p.study.person).filter(start__lte=span[1], end__gte=span[0]).select_related('room', 'course', 'category')) if span else []
        best = min(free, key=lambda g: (weight(clashes(p.study.person, g.bookings.all(), mine), g.bookings.all())[1], g.number))
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
