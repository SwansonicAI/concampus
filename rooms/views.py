from datetime import UTC, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from core.dates import day as understood
from core.days import busy, strip
from core.models import OFFICES, Institute, Message, Person
from core.text import matching
from courses.models import Course
from .forms import STUDENT_DAILY, BookingForm, FindForm, bookable, quota
from .models import Booking, Room

OPEN, CLOSE, STEP = 8, 22, 5  # hours shown, minutes per grid line
PER_HOUR = 60 // STEP
FEED = 'kalender'  # salt of the calendar feed links


def line(moment):
    """Grid line of a time on the room and week plans (line 1 is the label column or header row)."""
    t = timezone.localtime(moment)
    return 2 + min(max((t.hour - OPEN) * 60 + t.minute, 0), (CLOSE - OPEN) * 60) // STEP


def agenda(person):
    """Bookings a person attends: booked for them or with them, taught or examined by them, or a group course they take part in."""
    return Booking.objects.filter(
        Q(person=person) | Q(participants=person) | Q(course__staff=person)
        | Q(course__kind=Course.Kind.COURSE, course__participations__study__person=person)
    ).exclude(category=Booking.Category.CANCELLED).distinct()


def week(bookings, day):
    """Context for the week grid: the Monday-to-Sunday dates around `day` and the bookings placed on them."""
    monday = day - timedelta(days=day.weekday())
    placed = list(bookings.filter(start__date__range=(monday, monday + timedelta(days=6))).select_related('room', 'course'))
    for n, b in enumerate(placed):
        column = 2 + timezone.localtime(b.start).weekday()
        b.place = f'{line(b.start)} / {column} / {line(b.end)} / {column + 1}'
        overlapping = [o for o in placed if o is not b and o.start < b.end and b.start < o.end]
        b.clash = bool(overlapping)
        b.lane = sum(1 for o in overlapping if placed.index(o) < n) % 2  # side by side instead of on top
    return {
        'dates': [monday + timedelta(days=n) for n in range(7)],
        'today': timezone.localdate(),
        'placed': placed,
        'ticks': [(f'{2 + i * PER_HOUR} / span {PER_HOUR}', OPEN + i) for i in range(CLOSE - OPEN)],
        'prev': monday - timedelta(days=7),
        'next': monday + timedelta(days=7),
    }


def editable(booking, person):
    return 'Veranstaltungsbüro' in person.roles or booking.person_id == person.pk


def private(person):
    """Students see that a room is taken, not by whom or for what, unless it is a public event."""
    return not person.roles & {'teacher', *OFFICES}


def groups():
    """The ways to look at the rooms, as in Asimut: everyone's own, by building, by Institut, by function."""
    return [
        ('', [('meine', 'Meine Räume'), ('alle', 'Alle Räume')]),
        ('Gebäude', [(f'haus:{b}', b) for b in Room.objects.order_by('building').values_list('building', flat=True).distinct()]),
        ('Institut', [(f'institut:{i.pk}', i.name) for i in Institute.objects.all()]),
        ('Funktion', [(f'funktion:{value}', label) for value, label in Room.Function.choices]),
    ]


def index(request):
    person, today = request.user, timezone.localdate()
    day = understood(request.GET.get('tag', '')) or today
    group = request.GET.get('raeume') or ('funktion:veranstaltung' if person.roles & set(OFFICES) else 'meine')
    kind, _, key = group.partition(':')
    rooms = Room.objects.prefetch_related(Prefetch(
        'bookings', Booking.objects.filter(start__date=day).select_related('course', 'person').prefetch_related('participants'), to_attr='day',
    ))
    allowed = set(bookable(person).values_list('pk', flat=True))
    rooms = {
        'meine': rooms.filter(pk__in=allowed), 'haus': rooms.filter(building=key), 'funktion': rooms.filter(function=key),
        'institut': rooms.filter(institutes=key) if key.isdigit() else rooms,
    }.get(kind, rooms)
    hidden = private(person)
    for room in rooms:
        room.bookable = room.pk in allowed
        for b in room.day:
            b.columns = f'{line(b.start)} / {line(b.end)}'
            b.editable = editable(b, person)
            b.hidden = hidden and not b.editable and b.category not in Booking.PUBLIC and person not in b.participants.all()
    monday = day - timedelta(days=day.weekday())
    now = timezone.localtime()
    return render(request, 'rooms/index.html', {
        'day': day,
        'prev': day - timedelta(days=1),
        'next': day + timedelta(days=1),
        'days': strip(monday, day, busy(agenda(person), monday)),
        'rooms': rooms,
        'group': group,
        'groups': groups(),
        'hours': [(f'{2 + i * PER_HOUR} / span {PER_HOUR}', OPEN + i) for i in range(CLOSE - OPEN)],
        'slots': [
            (f'{2 + i * PER_HOUR // 2} / span {PER_HOUR // 2}', f'{OPEN + i // 2:02d}:{i % 2 * 30:02d}')
            for i in range((CLOSE - OPEN) * 2)
        ],
        'now': line(now) if day == now.date() and OPEN <= now.hour < CLOSE else None,
        'tones': [('teach', 'Unterricht und Probe'), ('practice', 'Üben'), ('exam', 'Prüfung'), ('event', 'Veranstaltung'), ('service', 'Service'), ('blocked', 'Blockiert')],
        'left': None if 'student' not in person.roles else STUDENT_DAILY - quota(person, day),
    })


def find(request):
    """Free rooms for a time, a group size and equipment: the question people actually ask."""
    now = timezone.localtime()
    hour = (now + timedelta(hours=1)).replace(minute=0)
    form = FindForm(request.GET or None, initial={'day': now.date(), 'start': hour.time(), 'end': (hour + timedelta(hours=1)).time()})
    rooms = start = end = None
    if form.is_valid():
        data = form.cleaned_data
        start = timezone.make_aware(datetime.combine(data['day'], data['start']))
        end = timezone.make_aware(datetime.combine(data['day'], data['end']))
        taken = Booking.objects.filter(start__lt=end, end__gt=start).exclude(category=Booking.Category.CANCELLED).values('room')
        rooms = bookable(request.user).exclude(pk__in=taken).filter(capacity__gte=data['seats'] or 1)
        rooms = matching(rooms, data['equipment'], 'equipment', 'name', 'purpose', 'building')
        rooms = rooms.order_by('capacity', 'building', 'name')
    return render(request, 'rooms/find.html', {'form': form, 'rooms': rooms, 'start': start, 'end': end})


def room(request, pk):
    room = get_object_or_404(Room.objects.prefetch_related('institutes'), pk=pk)
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    return render(request, 'rooms/room.html', {
        'room': room, 'day': day, 'bookable': bookable(request.user).filter(pk=pk).exists(), **week(room.bookings.all(), day),
    })


def mine(request):
    person, today = request.user, timezone.localdate()
    context = {
        'bookings': person.bookings.filter(end__gte=timezone.now()).select_related('room', 'course'),
        'attending': person.attending.filter(end__gte=timezone.now()).select_related('room', 'person'),
        'feed': feed(request),
    }
    if 'student' in person.roles:
        context['used'], context['daily'] = quota(person, today), STUDENT_DAILY
    return render(request, 'rooms/mine.html', context)


def events(request):
    """What the Hochschule puts on: concerts, class evenings, master classes, lectures, from today on."""
    picked = request.GET.get('kategorie', '')
    shown = [picked] if picked in Booking.PUBLIC else Booking.PUBLIC
    found = (
        Booking.objects.filter(category__in=shown, end__gte=timezone.now(), start__lt=timezone.now() + timedelta(days=120))
        .select_related('room', 'person', 'course')
    )
    return render(request, 'rooms/events.html', {
        'events': found, 'picked': picked,
        'categories': [(value, label) for value, label in Booking.Category.choices if value in Booking.PUBLIC],
    })


def booking(request, pk=None):
    person = request.user
    booking = get_object_or_404(Booking.objects.select_related('course', 'person', 'room'), pk=pk) if pk else Booking()
    if pk and not editable(booking, person):
        if private(person) and booking.category not in Booking.PUBLIC and person not in booking.participants.all():
            raise PermissionDenied
        return render(request, 'rooms/details.html', {'booking': booking, 'participants': booking.participants.all()})
    initial = {}
    if start := parse_datetime(request.GET.get('start', '')):
        end = parse_datetime(request.GET.get('ende', '')) or start + timedelta(hours=1)
        initial = {'room': request.GET.get('raum'), 'day': start.date(), 'begin': start.time(), 'until': end.time()}
    form = BookingForm(request.POST or None, instance=booking, initial=initial, person=person)
    if form.is_valid():
        booking = form.save()
        if booking.person_id != person.pk:
            Message.send(
                booking.person, f'Buchung geändert: {booking.label}',
                f'Das Veranstaltungsbüro hat Ihre Buchung geändert: {booking.room}, '
                f'{timezone.localtime(booking.start):%d.%m.%Y %H:%M}–{timezone.localtime(booking.end):%H:%M}.',
                reverse('rooms:booking', args=[booking.pk]),
            )
        messages.success(request, 'Buchung gespeichert.')
        return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")
    series = Booking.objects.filter(series=booking.series).count() if booking.series else 0
    return render(request, 'rooms/booking.html', {
        'form': form, 'booking': booking, 'series': series,
        'left': STUDENT_DAILY - quota(person, timezone.localdate()) if form.student else None,
    })


@require_POST
def delete(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    if not editable(booking, request.user):
        raise PermissionDenied
    doomed = Booking.objects.filter(pk=booking.pk)
    if request.POST.get('serie') and booking.series:
        doomed = Booking.objects.filter(series=booking.series, start__gte=booking.start)
    if booking.person_id != request.user.pk:
        Message.send(
            booking.person, f'Buchung storniert: {booking.label}',
            f'Das Veranstaltungsbüro hat {doomed.count()} Termin(e) ab {timezone.localtime(booking.start):%d.%m.%Y %H:%M} in {booking.room} storniert.',
        )
    doomed.delete()
    messages.success(request, 'Buchung gelöscht.')
    return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")


def feed(request):
    """The address of the person's calendar feed: secret, signed, for any calendar app to subscribe to."""
    return request.build_absolute_uri(reverse('rooms:calendar', args=[signing.dumps(request.user.pk, salt=FEED)]))


@login_not_required
def calendar(request, token):
    """The person's agenda as iCalendar, a week back and six months ahead."""
    try:
        person = get_object_or_404(Person, pk=signing.loads(token, salt=FEED))
    except signing.BadSignature:
        raise Http404
    now = timezone.now()

    def text(value):
        return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

    def stamp(moment):
        return moment.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')

    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//HfM Weimar//Campus//DE', 'X-WR-CALNAME:Campus HfM Weimar']
    for b in agenda(person).filter(start__gte=now - timedelta(days=7), start__lte=now + timedelta(days=183)).select_related('room', 'course'):
        lines += [
            'BEGIN:VEVENT', f'UID:buchung-{b.pk}@campus.hfm-weimar.de', f'DTSTAMP:{stamp(now)}', f'DTSTART:{stamp(b.start)}', f'DTEND:{stamp(b.end)}',
            f'SUMMARY:{text(b.label)}', f'LOCATION:{text(f"{b.room} {b.room.purpose}, {b.room.building}".strip())}', 'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return HttpResponse('\r\n'.join(lines) + '\r\n', content_type='text/calendar; charset=utf-8')
