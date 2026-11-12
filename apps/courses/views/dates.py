"""A course's dates: single dates, weekly plans, lesson times, replies and cancellations."""
import uuid
from collections import Counter
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import history, texts
from core.apps import plural
from core.forms import again
from core.models import Holiday, Institution, Person
from rooms.models import Booking, BookingCategory
from rooms.plans import agenda, around, called_off
from ..forms import DateForm, Lessons, PlanForm
from ..models import Course
from .common import TEACHING, back, teaches
from .load import minutes


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
            row['minutes'] = int(hours * Institution.get().lesson_minutes)
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
