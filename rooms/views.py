from datetime import UTC, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.apps import plural
from core.dates import day as understood
from core.forms import again, sent
from core.days import busy, strip
from core import texts
from core.models import Institute, Institution, Message, Office, Person
from core.text import matching
from courses.models import Course
from .forms import BookingForm, CancelForm, FindForm, bookable, quota
from .models import Booking, BookingCategory, Room, RoomFunction

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
    ).filter(category__blocks=True).distinct()


def week(bookings, day):
    """Context for the week grid: the Monday-to-Sunday dates around `day` and the bookings placed on them."""
    monday = day - timedelta(days=day.weekday())
    placed = list(bookings.filter(start__date__range=(monday, monday + timedelta(days=6))).select_related('room', 'course', 'category'))
    for n, b in enumerate(placed):
        column = 2 + timezone.localtime(b.start).weekday()
        b.place = f'{line(b.start)} / {column} / {line(b.end)} / {column + 1}'
        overlapping = [o for o in placed if o is not b and o.start < b.end and b.start < o.end]
        b.clash = bool(overlapping)
        taken = {o.lane for o in overlapping if placed.index(o) < n}
        b.lane = next(i for i in range(len(placed)) if i not in taken)  # side by side instead of on top
    for b in placed:
        b.lanes = 1 + max(o.lane for o in placed if o.start < b.end and b.start < o.end)
    return {
        'dates': [monday + timedelta(days=n) for n in range(7)],
        'today': timezone.localdate(),
        'placed': placed,
        'ticks': [(f'{2 + i * PER_HOUR} / span {PER_HOUR}', OPEN + i) for i in range(CLOSE - OPEN)],
        'prev': monday - timedelta(days=7),
        'next': monday + timedelta(days=7),
    }


def editable(booking, person):
    return booking.person_id == person.pk or booking.room_id in person.reached(Room, 'manage_bookings')


def private(booking, person):
    """Students see that a room is taken, not by whom or for what, unless it is a public event or they take part; staff
    need the right to see bookings, for the rooms it holds for. Needs the booking's participants, best prefetched."""
    return not (
        'teacher' in person.roles or booking.room_id in person.reached(Room, 'view_bookings') or booking.category.public
        or person in booking.participants.all()
    )


def legend():
    """The colours in use on the plans, in their order."""
    used = set(BookingCategory.objects.filter(blocks=True).values_list('tone', flat=True))
    return [(tone.value, tone.label) for tone in BookingCategory.Tone if tone in used]


def groups():
    """The ways to look at the rooms, as in Asimut: everyone's own, by building, by Institut, by function."""
    return [
        ('', [('meine', 'Meine Räume'), ('zentral', 'Zentral vergebene Räume'), ('alle', 'Alle Räume')]),
        ('Gebäude', [(f'haus:{b}', b) for b in Room.objects.order_by('building').values_list('building', flat=True).distinct()]),
        ('Institut', [(f'institut:{i.pk}', i.name) for i in Institute.objects.all()]),
        ('Funktion', [(f'funktion:{f.key}', f.name) for f in RoomFunction.objects.all()]),
    ]


def index(request):
    person, today = request.user, timezone.localdate()
    day = understood(request.GET.get('tag', '')) or today
    allowed = set(bookable(person).values_list('pk', flat=True))
    group = request.GET.get('raeume') or ('zentral' if person.everywhere('book_any_room') else 'meine' if allowed else 'alle')
    kind, _, key = group.partition(':')
    rooms = Room.objects.prefetch_related(Prefetch(
        'bookings', Booking.objects.filter(start__date=day).select_related('course', 'person', 'category').prefetch_related('participants'), to_attr='day',
    ))
    rooms = {
        'meine': rooms.filter(pk__in=allowed), 'zentral': rooms.filter(function__central=True), 'haus': rooms.filter(building=key), 'funktion': rooms.filter(function=key),
        'institut': rooms.filter(institutes=key) if key.isdigit() else rooms,
    }.get(kind, rooms)
    for room in rooms:
        room.bookable = room.pk in allowed
        for b in room.day:
            b.columns = f'{line(b.start)} / {line(b.end)}'
            b.editable = editable(b, person)
            b.hidden = not b.editable and private(b, person)
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
        'tones': legend(),
        'left': None if 'student' not in person.roles else Institution.get().student_daily_hours - quota(person, day),
    })


def find(request):
    """Free rooms for a time, a group size and equipment: the question people actually ask."""
    now = timezone.localtime()
    hour = (now + timedelta(hours=1)).replace(minute=0)
    form = FindForm(request.GET or None, initial={'day': now.date(), 'start': hour.time(), 'end': (hour + timedelta(hours=1)).time()})
    rooms = start = end = trial = None
    if form.is_valid():
        data = form.cleaned_data
        start = timezone.make_aware(datetime.combine(data['day'], data['start']))
        end = timezone.make_aware(datetime.combine(data['day'], data['end']))
        taken = Booking.objects.filter(start__lt=end, end__gt=start, category__blocks=True).values('room')
        rooms = bookable(request.user).exclude(pk__in=taken).filter(capacity__gte=data['seats'] or 1)
        rooms = matching(rooms, data['equipment'], 'equipment', 'name', 'purpose', 'building')
        rooms = rooms.order_by('capacity', 'building', 'name')
        trial = tried(request.user, rooms.first(), data['day'], data['start'], data['end'])
    return render(request, 'rooms/find.html', {'form': form, 'rooms': rooms, 'start': start, 'end': end, 'trial': trial})


def tried(person, room, day, begin, until):
    """A booking of `room` checked against every rule before anyone fills in a form: bound, so it carries its errors,
    and holding what to send to book it at once. None without a room."""
    if room is None:
        return None
    category = BookingForm(person=person).fields['category']
    return BookingForm({
        'room': room.pk, 'category': category.prepare_value(category.queryset.first()),
        'day': f'{day:%d.%m.%Y}', 'begin': f'{begin:%H:%M}', 'until': f'{until:%H:%M}',
    }, person=person)


def room(request, pk):
    room = get_object_or_404(Room.objects.prefetch_related('institutes'), pk=pk)
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    return render(request, 'rooms/room.html', {
        'room': room, 'day': day, 'bookable': bookable(request.user).filter(pk=pk).exists(), 'central': Office.holding('book_any_room'),
        'edit': request.user.may('manage_rooms', room),
        **week(room.bookings.all(), day),
    })


def series(bookings):
    """Each weekly series once, at its next date, with the date of its last."""
    shown = {}
    for b in bookings:
        if b.series in shown:
            shown[b.series].until = b.start
        else:
            b.until, shown[b.series or b.pk] = None, b
    return list(shown.values())


def mine(request):
    person, today = request.user, timezone.localdate()
    context = {
        'bookings': series(person.bookings.filter(end__gte=timezone.now()).select_related('room', 'course')),
        'attending': series(person.attending.filter(end__gte=timezone.now()).select_related('room', 'person')),
        'feed': feed(request),
    }
    if 'student' in person.roles:
        rules, used = Institution.get(), quota(person, today)
        booked = sum((b.end - b.start).total_seconds() for b in person.bookings.filter(category__quota=True, start__date=today)) / 3600
        context.update(used=used, daily=rules.student_daily_hours, left=rules.student_daily_hours - used, extra=round(booked - used, 1), last_minute=rules.last_minute_hours)
    return render(request, 'rooms/mine.html', context)


def events(request):
    """What the Hochschule puts on: concerts, class evenings, master classes, lectures, from today on."""
    picked = request.GET.get('kategorie', '')
    found = (
        Booking.objects.filter(category__public=True, end__gte=timezone.now(), start__lt=timezone.now() + timedelta(days=120))
        .select_related('room', 'person', 'course', 'category')
    )
    return render(request, 'rooms/events.html', {
        'events': found.filter(category=picked) if picked else found, 'picked': picked,
        'categories': BookingCategory.objects.filter(public=True),
    })


def booking(request, pk=None):
    person = request.user
    booking = get_object_or_404(Booking.objects.select_related('course', 'person', 'room', 'category'), pk=pk) if pk else Booking()
    if pk and not editable(booking, person):
        if private(booking, person):
            raise PermissionDenied
        return render(request, 'rooms/details.html', {'booking': booking, 'participants': booking.participants.all()})
    initial, data = {}, request.POST.copy() or None
    if start := parse_datetime(request.GET.get('start', '')):
        end = parse_datetime(request.GET.get('ende', '')) or start + timedelta(hours=1)
        initial = {'room': request.GET.get('raum'), 'day': start.date(), 'begin': start.time(), 'until': end.time()}
        room = Room.objects.filter(pk=initial['room']).first() if str(initial['room']).isdigit() else None
        if data is None and room:  # picked in the plan: what stands in the way shows at once, not after saving
            data = tried(person, room, start.date(), start.time(), end.time()).data.copy()
    if data and (instead := data.pop('instead', [''])[0]):  # another room or a later time, offered when the room was taken
        what, _, value = instead.partition(':')
        if what == 'raum':
            data['room'] = value
        else:
            data['begin'], data['until'] = value.split('-')
    form = BookingForm(data, instance=booking, initial=initial, person=person)
    if request.method == 'POST' and form.is_valid():
        booking = form.save()
        if booking.person_id != person.pk:
            texts.send(
                'buchung_geaendert', booking.person, reverse('rooms:booking', args=[booking.pk]), buchung=booking.label, von=person, raum=booking.room,
                termin=f'{timezone.localtime(booking.start):%d.%m.%Y %H:%M}–{timezone.localtime(booking.end):%H:%M}',
            )
        rules = Institution.get()
        free = form.student and (  # say why the counter did not move
            f'{booking.category} zählt nicht zu Ihren {rules.student_daily_hours} Stunden Übezeit am Tag.' if not booking.category.quota
            else f'Sie beginnt in weniger als {rules.last_minute_hours} Stunden und zählt deshalb nicht zu Ihren {rules.student_daily_hours} Stunden am Tag.'
            if booking.start - booking.created <= timedelta(hours=rules.last_minute_hours) else ''
        )
        messages.success(request, f'Buchung gespeichert. {free or ""}'.strip())
        return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")
    series = Booking.objects.filter(series=booking.series).count() if booking.series else 0
    told = None  # a date others come to can be called off and they hear of it
    if booking.pk and (booking.course_id or booking.participants.exists()):
        told = [p.name for p in recipients(booking, person)]
        told = told if len(told) <= 3 else [f'alle {len(told)} Teilnehmenden']
    return render(request, 'rooms/booking.html', {
        'form': form, 'booking': booking, 'series': series, 'central': Office.holding('book_any_room'),
        'told': told, 'cancel': CancelForm(sent(request, 'absagen'), person=person), 'instead': form.instead(),
        'rules': Institution.get(), 'special': BookingCategory.objects.filter(students=True).exclude(ahead_hours=None, participants=0),
        'history': booking.pk and Booking.visible_to(person.roles), 'now': timezone.now(),
        'left': Institution.get().student_daily_hours - quota(person, timezone.localdate()) if form.student else None,
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
        texts.send(
            'buchung_storniert', booking.person, buchung=booking.label, von=request.user, anzahl=doomed.count(),
            termin=f'{timezone.localtime(booking.start):%d.%m.%Y %H:%M}', raum=booking.room,
        )
    doomed.delete()
    messages.success(request, 'Buchung gelöscht.')
    return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")


def recipients(booking, by):
    """Who hears when a date is called off: whoever takes part, everyone in a group course, and whoever booked it when
    someone else calls it off."""
    people = set(booking.participants.all())
    if booking.course_id and booking.course.kind == Course.Kind.COURSE:
        people |= set(Person.objects.filter(studies__participations__course=booking.course_id))
    people.add(booking.person)
    people.discard(by)
    return sorted(people, key=lambda p: p.name)


def moment(start, end):
    start, end = timezone.localtime(start), timezone.localtime(end)
    return f'{date_format(start, "D j.n.Y")}, {start:%H:%M}–{end:%H:%M} Uhr'


@require_POST
def cancel(request, pk):
    """Call off one date: it stays on the plans as „Abgesagt“ and frees the room; whoever takes part hears of it, with
    the make-up date if there is one."""
    booking = get_object_or_404(Booking.objects.select_related('course', 'room', 'person'), pk=pk)
    if not editable(booking, request.user):
        raise PermissionDenied
    form = CancelForm(request.POST, person=request.user)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return again(request, reverse('rooms:booking', args=[pk]), 'absagen')
    data, makeup = form.cleaned_data, None
    if data['day']:
        makeup = Booking(
            room=data['room'] or booking.room, person=booking.person, course=booking.course, category=booking.category, title=booking.title,
            start=timezone.make_aware(datetime.combine(data['day'], data['begin'])), end=timezone.make_aware(datetime.combine(data['day'], data['until'])),
        )
        try:
            makeup.full_clean()
        except ValidationError as error:
            messages.error(request, f'Nachholtermin: {" ".join(error.messages)}')
            return again(request, reverse('rooms:booking', args=[pk]), 'absagen')
    told = recipients(booking, request.user)
    booking.category = BookingCategory.system('abgesagt')
    booking.save(update_fields=['category'])
    if makeup:
        makeup.save()
        makeup.participants.set(booking.participants.all())
    link = booking.course.get_absolute_url() if booking.course else reverse('rooms:booking', args=[(makeup or booking).pk])
    values = {'veranstaltung': booking.label, 'termin': moment(booking.start, booking.end), 'raum': (makeup or booking).room, 'von': request.user}
    for person in told:
        if makeup:
            subject, body = texts.render('termin_verlegt', vorname=person.first_name, nachname=person.last_name, neuer_termin=moment(makeup.start, makeup.end), **values)
        else:
            subject, body = texts.render('termin_abgesagt', vorname=person.first_name, nachname=person.last_name, **values)
        Message.send(person, subject, body + (f'\n\n{data["note"]}' if data['note'] else ''), link)
    messages.success(request, ' '.join(filter(None, [
        f'Verlegt auf {moment(makeup.start, makeup.end)}.' if makeup else 'Abgesagt.',
        told and f'Nachricht an {plural(len(told), "Person", "Personen")}.',
    ])))
    return redirect(booking.course or f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")


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

    name, host = Institution.get().name, request.get_host().split(':')[0]
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//{text(name)}//Campus//DE', f'X-WR-CALNAME:{text(name)}']
    for b in agenda(person).filter(start__gte=now - timedelta(days=7), start__lte=now + timedelta(days=183)).select_related('room', 'course', 'category'):
        lines += [
            'BEGIN:VEVENT', f'UID:buchung-{b.pk}@{host}', f'DTSTAMP:{stamp(now)}', f'DTSTART:{stamp(b.start)}', f'DTEND:{stamp(b.end)}',
            f'SUMMARY:{text(b.label)}', f'LOCATION:{text(f"{b.room} {b.room.purpose}, {b.room.building}".strip())}', 'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return HttpResponse('\r\n'.join(lines) + '\r\n', content_type='text/calendar; charset=utf-8')
