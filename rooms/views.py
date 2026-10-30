from datetime import UTC, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.access import CODENAMES, require
from core.apps import plural
from core.dates import day as understood
from core.forms import again, sent
from core.days import busy, strip
from core import texts
from core.models import Institute, Institution, Message, Office, Person
from core.text import matching
from courses.models import Course
from .forms import BookingForm, CancelForm, FindForm, ServiceForm, bookable, free_again, quota
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
        Q(person=person) | Q(participants=person) | Q(course__staff=person, ensemble=None) | Q(ensemble__coach=person)
        | Q(course__kind=Course.Kind.COURSE, course__participations__study__person=person, ensemble=None)  # an ensemble's dates are its members'
    ).filter(category__blocks=True, requested=False).distinct()


QUARTER = timedelta(minutes=15)  # the way from one building to another


def moves(bookings):
    """Mark the bookings of one person that begin within a quarter of an hour of the one before, in another building:
    `move` is where they come from."""
    ordered = sorted(bookings, key=lambda b: b.start)
    for before, b in zip(ordered, ordered[1:]):
        if timedelta(0) <= b.start - before.end <= QUARTER and before.room.building != b.room.building:
            b.move = before.room.building


def around(person, start, end, exclude=None):
    """The person's own dates ending within a quarter of an hour before `start` or beginning as soon after `end`: in
    another building, no time for the way."""
    near = Q(end__range=(start - QUARTER, start)) | Q(start__range=(end, end + QUARTER))
    return agenda(person).filter(near).exclude(pk=exclude).select_related('room')


def week(bookings, day, person=None):
    """Context for the week grid: the Monday-to-Sunday dates around `day` and the bookings placed on them. For one
    person's own plan, their lessons name the student (`who`) and a change of building without a break shows."""
    monday = day - timedelta(days=day.weekday())
    placed = list(bookings.filter(start__date__range=(monday, monday + timedelta(days=6))).select_related('room', 'course', 'category').prefetch_related('participants', 'absent'))
    if person:
        moves(placed)
        for b in placed:
            if b.course and b.course.kind == Course.Kind.LESSON and b.person_id == person.pk:
                b.who = ', '.join(f'{p.first_name} {p.last_name}' + (' (abgemeldet)' if p in b.absent.all() else '') for p in b.participants.all())
    for n, b in enumerate(placed):
        column = 2 + timezone.localtime(b.start).weekday()
        b.place = f'{line(b.start)} / {column} / {line(b.end)} / {column + 1}'
        b.short = b.end - b.start < timedelta(hours=1)  # room for two lines, the title and the room, one each
        b.tiny = b.end - b.start < timedelta(minutes=40)  # for one: the room first
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
    """Students see that a room is taken, not by whom or for what, unless it is a public event or they take part;
    teachers see teaching and events, but of practice only that the room is taken; the offices that see bookings, all
    in the rooms the right holds for. Needs the booking's participants, best prefetched."""
    return not (
        'teacher' in person.roles and booking.category.tone != BookingCategory.Tone.PRACTICE
        or booking.room_id in person.reached(Room, 'view_bookings') or booking.category.public or person in booking.participants.all()
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


ROOMS = ('student', 'teacher', *CODENAMES)  # who uses the rooms: not applicants or former students


@require(*ROOMS)
def index(request):
    person, today = request.user, timezone.localdate()
    day = understood(request.GET.get('tag', '')) or today
    allowed = set(bookable(person).values_list('pk', flat=True))
    group = request.GET.get('raeume') or ('zentral' if person.everywhere('book_any_room') else 'meine' if allowed else 'alle')
    kind, _, key = group.partition(':')
    rooms = Room.objects.prefetch_related(Prefetch(
        'bookings', Booking.objects.filter(start__date=day, requested=False).select_related('course', 'person', 'category').prefetch_related('participants'), to_attr='day',
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
        'hidden': any(b.hidden for room in rooms for b in room.day),
        'left': None if 'student' not in person.roles else Institution.get().student_daily_hours - quota(person, day),
    })


def opening():
    """The next full hour the plans show, as the time a search starts from: tomorrow morning once the day is over."""
    now = timezone.localtime()
    hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if hour.date() != now.date() or hour.hour >= CLOSE:
        return (now + timedelta(days=1)).replace(hour=OPEN, minute=0, second=0, microsecond=0)
    return max(hour, hour.replace(hour=OPEN))


@require(*ROOMS)
def find(request):
    """Free rooms for a time, a group size and equipment: the question people actually ask. Teachers also see the halls
    given out centrally that are free then, to ask for. First the rooms one gets to in time from one's dates before and
    after, then the smallest, those for one's Hauptfach first; when the smallest are all taken, when one is free again."""
    hour = opening()
    form = FindForm(request.GET or None, initial={'day': hour.date(), 'start': hour.time(), 'end': (hour + timedelta(hours=1)).time()})
    rooms = asked = start = end = trial = came = smaller = left = counts = None
    if form.is_valid():
        data, person = form.cleaned_data, request.user
        start = timezone.make_aware(datetime.combine(data['day'], data['start']))
        end = timezone.make_aware(datetime.combine(data['day'], data['end']))
        taken = Booking.objects.filter(start__lt=end, end__gt=start, category__blocks=True, requested=False).values('room')
        fitting = matching(bookable(person).filter(capacity__gte=data['seats'] or 1), data['equipment'], 'equipment', 'name', 'purpose', 'building')
        if requestable(person):
            asked = matching(Room.objects.filter(function__central=True).exclude(pk__in=taken).exclude(pk__in=fitting.values('pk')).filter(capacity__gte=data['seats'] or 1), data['equipment'], 'equipment', 'name', 'purpose', 'building')
            asked = asked.order_by('capacity', 'building', 'name')
        near = list(around(person, start, end))
        subjects = set(person.studies.filter(status='enrolled').values_list('subject__name', flat=True))
        rooms = list(fitting.exclude(pk__in=taken).order_by('capacity', 'building', 'name'))
        for r in rooms:
            r.away = next((b for b in near if b.room.building != r.building), None)
        rooms.sort(key=lambda r: (r.away is not None, r.capacity, not any(s in r.purpose for s in subjects)))
        if came := next((b for b in near if b.end <= start and any(r.away == b for r in rooms)), None):
            came.later = (came.end + QUARTER, came.end + QUARTER + (end - start))  # time enough for the way
        smallest = min(fitting.values_list('capacity', flat=True), default=None)
        if smallest is not None and min((r.capacity for r in rooms), default=smallest + 1) > smallest:  # the rooms that fit best are taken
            free = [(at, r) for r in fitting.filter(capacity=smallest)[:10] if (at := free_again(r, start, end - start))]
            if free:
                at, room = min(free, key=lambda f: f[0])
                smaller = (room, at, at + (end - start))
        trial = tried(person, rooms[0] if rooms else None, data['day'], data['start'], data['end'])
        if trial and trial.student and trial.is_valid():
            rules = Institution.get()
            left = rules.student_daily_hours - quota(person, data['day'])
            counts = trial.cleaned_data['category'].quota and start - timezone.now() > timedelta(hours=rules.last_minute_hours)
    return render(request, 'rooms/find.html', {
        'form': form, 'rooms': rooms, 'asked': asked, 'start': start, 'end': end, 'trial': trial, 'came': came, 'smaller': smaller, 'left': left, 'counts': counts,
    })


def tried(person, room, day, begin, until):
    """A booking of `room` checked against every rule before anyone fills in a form: bound, so it carries its errors,
    and holding what to send to book it at once. None without a room."""
    if room is None:
        return None
    category = BookingForm(person=person).fields['category']
    data = QueryDict(mutable=True)  # as a form sends it
    data.update({
        'room': room.pk, 'category': category.prepare_value(category.queryset.first()),
        'day': f'{day:%d.%m.%Y}', 'begin': f'{begin:%H:%M}', 'until': f'{until:%H:%M}',
    })
    return BookingForm(data, person=person)


@require(*ROOMS)
def room(request, pk):
    room = get_object_or_404(Room.objects.prefetch_related('institutes'), pk=pk)
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    return render(request, 'rooms/room.html', {
        'room': room, 'day': day, 'bookable': bookable(request.user).filter(pk=pk).exists(), 'central': Office.holding('book_any_room', linked=True),
        'edit': request.user.may('manage_rooms', room), 'requestable': room.function.central and requestable(request.user),
        **week(room.bookings.filter(requested=False), day),
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


@require(*ROOMS)
def mine(request):
    person, today = request.user, timezone.localdate()
    context = {
        'bookings': series(person.bookings.filter(end__gte=timezone.now()).select_related('room', 'course').prefetch_related('participants')),
        'attending': series(person.attending.filter(end__gte=timezone.now()).select_related('room', 'person')),
        'feed': feed(request),
    }
    if 'student' in person.roles:
        rules, used = Institution.get(), quota(person, today)
        booked = sum((b.end - b.start).total_seconds() for b in person.bookings.filter(category__quota=True, start__date=today)) / 3600
        context.update(used=used, daily=rules.student_daily_hours, left=rules.student_daily_hours - used, extra=round(booked - used, 1), last_minute=rules.last_minute_hours)
    return render(request, 'rooms/mine.html', context)


@require(*ROOMS)
def events(request):
    """What the Hochschule puts on, a month at a time: concerts, class evenings, master classes, lectures."""
    today = timezone.localdate()
    try:
        first = datetime.strptime(request.GET.get('monat', ''), '%Y-%m').date()
    except ValueError:
        first = today.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    picked, q = request.GET.get('kategorie', ''), request.GET.get('q', '').strip()
    found = Booking.objects.filter(category__public=True, requested=False, start__date__gte=first, start__date__lt=following).select_related('room', 'person', 'course', 'category')
    if picked:
        found = found.filter(category=picked)
    return render(request, 'rooms/events.html', {
        'events': matching(found, q, 'title', 'programme', 'room__name', 'room__building') if q else found, 'picked': picked, 'q': q,
        'categories': BookingCategory.objects.filter(public=True), 'month': first,
        'prev': (first - timedelta(days=1)).replace(day=1), 'next': following, 'office': 'manage_bookings' in request.user.roles,
        'feed': request.build_absolute_uri(reverse('rooms:programme')),
    })


@login_not_required
def programme(request):
    """The public events of the next half year as a calendar, for the Hochschule's website and anyone to subscribe to."""
    now = timezone.now()
    found = Booking.objects.filter(category__public=True, internal=False, requested=False, end__gte=now, start__lte=now + timedelta(days=183)).select_related('room')
    return ics(request, [(f'veranstaltung-{b.pk}', b.start, b.end, b.label, f'{b.room.purpose or b.room}, {b.room.building}') for b in found])


@require_POST
def service(request, pk):
    """Book a service for an event, in its room: tuning before, technical support during, transport after."""
    event = get_object_or_404(Booking.objects.select_related('room'), pk=pk, category__public=True)
    if not editable(event, request.user):
        raise PermissionDenied
    form = ServiceForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect('rooms:booking', pk=pk)
    data, day = form.cleaned_data, timezone.localtime(event.start).date()
    booked = Booking(
        room=event.room, parent=event, category=data['category'], person=request.user, title=f'{data["category"]}: {event.label}',
        start=timezone.make_aware(datetime.combine(day, data['begin'])), end=timezone.make_aware(datetime.combine(day, data['until'])),
    )
    try:
        booked.full_clean()
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        booked.save()
        messages.success(request, f'{data["category"]} gebucht.')
    return redirect('rooms:booking', pk=pk)


@require(*ROOMS)
def booking(request, pk=None):
    person = request.user
    booking = get_object_or_404(Booking.objects.select_related('course', 'person', 'room', 'category'), pk=pk) if pk else Booking()
    past = pk and booking.end <= timezone.now()  # what is over stays as it was: neither changed nor called off
    if pk and (past or not editable(booking, person)):
        if not editable(booking, person) and private(booking, person):
            raise PermissionDenied
        return render(request, 'rooms/details.html', {
            'booking': booking, 'participants': booking.participants.all(), 'past': past, 'history': bool(Booking.seen_by(person, booking.pk)),
        })
    initial, data = {}, request.POST.copy() or None
    if start := parse_datetime(request.GET.get('start', '')):
        end = parse_datetime(request.GET.get('ende', '')) or start + timedelta(hours=1)
        initial = {'room': request.GET.get('raum'), 'day': start.date(), 'begin': start.time(), 'until': end.time()}
        room = Room.objects.filter(pk=initial['room']).first() if str(initial['room']).isdigit() else None
        if data is None and room:  # picked in the plan: what stands in the way shows at once, not after saving
            data = tried(person, room, start.date(), start.time(), end.time()).data.copy()
    if kind := request.GET.get('kategorie'):  # e.g. a new event
        initial['category'] = kind
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
        if booking.requested:
            messages.success(request, f'{booking.room} angefragt. Wer die Säle vergibt, bestätigt oder sagt ab; die Antwort kommt in Ihr Postfach.')
            return redirect('rooms:mine')
        rules = Institution.get()
        free = form.student and (  # say why the counter did not move
            f'{booking.category} zählt nicht zu Ihren {rules.student_daily_hours} Stunden Übezeit am Tag.' if not booking.category.quota
            else f'Sie beginnt in weniger als {rules.last_minute_hours} Stunden und zählt deshalb nicht zu Ihren {rules.student_daily_hours} Stunden am Tag.'
            if booking.start - booking.created <= timedelta(hours=rules.last_minute_hours) else ''
        )
        messages.success(request, f'Buchung gespeichert. {free or ""}'.strip())
        if way := next((b for b in around(booking.person, booking.start, booking.end, exclude=booking.pk) if b.room.building != booking.room.building), None):
            messages.warning(request, f'Zwischen {way.label} in {way.room.building} und dieser Buchung bleibt keine Viertelstunde für den Weg.')
        return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")
    series = Booking.objects.filter(series=booking.series).count() if booking.series else 0
    on = timezone.localtime(form.instance.start).date() if form.instance.start else start.date() if start else timezone.localdate()  # the day the quota is for
    told = None  # a date others come to can be called off and they hear of it
    if booking.pk and (booking.course_id or booking.participants.exists()):
        told = [p.name for p in recipients(booking, person)]
        told = told if len(told) <= 3 else [f'alle {len(told)} Teilnehmenden']
    return render(request, 'rooms/booking.html', {
        'form': form, 'booking': booking, 'series': series, 'central': Office.holding('book_any_room', linked=True),
        'told': told, 'cancel': CancelForm(sent(request, 'absagen'), person=person), 'instead': form.instead(),
        'rules': Institution.get(), 'special': BookingCategory.objects.filter(students=True).exclude(ahead_hours=None, participants=0),
        'history': booking.pk and bool(Booking.seen_by(person, booking.pk)), 'now': timezone.now(), 'asking': bool(form.fields['room'].asked),
        'services': booking.services.select_related('category') if booking.pk else [],
        'serving': ServiceForm(initial={'begin': (timezone.localtime(booking.start) - timedelta(hours=2)).time(), 'until': (timezone.localtime(booking.start) - timedelta(hours=1)).time()})
        if booking.pk and booking.category.public else None,
        'left': Institution.get().student_daily_hours - quota(person, on) if form.student else None, 'on': on,
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
    if booking.course_id and booking.course.kind == Course.Kind.COURSE and not booking.ensemble_id:  # an ensemble's date, its members only
        people |= set(Person.objects.filter(studies__participations__course=booking.course_id))
    people.add(booking.person)
    people.discard(by)
    return sorted(people, key=lambda p: p.name)


def moment(start, end):
    start, end = timezone.localtime(start), timezone.localtime(end)
    return f'{date_format(start, "D j.n.Y")}, {start:%H:%M}–{end:%H:%M} Uhr'


def called_off(booking, by):
    """Call a date off: it stays on the plans as „Abgesagt“ and frees the room. Returns who is to hear of it."""
    told = recipients(booking, by)
    booking.category = BookingCategory.system('abgesagt')
    booking.save(update_fields=['category'])
    return told


@require_POST
def cancel(request, pk):
    """Call off one date: it stays on the plans as „Abgesagt“ and frees the room; whoever takes part hears of it, with
    the make-up date if there is one."""
    booking = get_object_or_404(Booking.objects.select_related('course', 'room', 'person'), pk=pk)
    if not editable(booking, request.user) or booking.end <= timezone.now():
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
    told = called_off(booking, request.user)
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


def day_off(request):
    """Call off dates of a day at once: one's own, as when ill (lessons, course dates, rehearsals one booked or teaches);
    for an office that looks after rooms, every booking of a building or room it closes; for whoever keeps the semesters,
    the teaching of a day newly unterrichtsfrei. Never exams, which whoever plans them moves. Whoever takes part hears
    once, with all their dates of the day."""
    person = request.user
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    building, teaching = request.GET.get('gebaeude', ''), request.GET.get('lehre') == '1'
    if teaching and not person.everywhere('manage_terms') or building and 'manage_bookings' not in person.roles:
        raise PermissionDenied
    found = Booking.objects.filter(start__date=day, end__gt=timezone.now(), category__blocks=True, requested=False)
    if building:  # a closure: everything there, as far as the office looks after it
        found = person.within(found.filter(room__building=building), 'manage_bookings')
    elif teaching:
        found = found.filter(course__kind__in=[Course.Kind.COURSE, Course.Kind.LESSON])
    else:
        found = found.filter(Q(person=person) | Q(course__staff=person) & (Q(ensemble=None) | Q(ensemble__coach=person)))
    dates = list(found.exclude(course__kind__in=Course.PANEL).select_related('room', 'course', 'person').prefetch_related('participants').distinct().order_by('start'))
    for b in dates:
        b.who = ', '.join(f'{p.first_name} {p.last_name}' for p in b.participants.all())
    if request.method == 'POST':
        chosen, told = [b for b in dates if str(b.pk) in request.POST.getlist('booking')], {}
        for b in chosen:
            for p in called_off(b, person):
                told.setdefault(p, []).append(f'– {b.label}, {timezone.localtime(b.start):%H:%M}–{timezone.localtime(b.end):%H:%M} Uhr, {b.room}')
        note = request.POST.get('note', '').strip()
        for p, lines in told.items():
            subject, body = texts.render('tag_abgesagt', vorname=p.first_name, nachname=p.last_name, tag=date_format(day, 'l, j. F Y'), termine='\n'.join(lines), von=person)
            Message.send(p, subject, body + (f'\n\n{note}' if note else ''), reverse('courses:timetable') + f'?tag={day:%Y-%m-%d}')
        messages.success(request, f'{plural(len(chosen), "Termin", "Termine")} abgesagt. Nachricht an {plural(len(told), "Person", "Personen")}.')
        return redirect(request.get_full_path() if building or teaching else f"{reverse('courses:timetable')}?tag={day:%Y-%m-%d}")
    return render(request, 'rooms/day_off.html', {
        'day': day, 'dates': dates, 'building': building, 'teaching': teaching,
        'buildings': person.within(Room.objects.all(), 'manage_bookings').values_list('building', flat=True).distinct().order_by('building') if 'manage_bookings' in person.roles else [],
    })


@require_POST
def absent(request, pk):
    """A participant says they cannot come to one date, such as a lesson: the one who booked it hears, with the note if
    there is one; the date stays theirs to call off or move. Or they take it back."""
    booking = get_object_or_404(Booking.objects.select_related('course', 'room', 'person'), pk=pk, participants=request.user, start__gt=timezone.now())
    if request.POST.get('zurueck'):
        booking.absent.remove(request.user)
        messages.success(request, 'Abmeldung zurückgenommen.')
    else:
        booking.absent.add(request.user)
        note = request.POST.get('note', '').strip()[:300]
        subject, body = texts.render(
            'abwesenheit', vorname=booking.person.first_name, nachname=booking.person.last_name, veranstaltung=booking.label,
            termin=moment(booking.start, booking.end), von=request.user,
        )
        Message.send(booking.person, subject, body + (f'\n\n{note}' if note else ''), booking.course.get_absolute_url() if booking.course else reverse('rooms:booking', args=[booking.pk]))
        messages.success(request, f'Abgemeldet für {moment(booking.start, booking.end)}. {booking.person} weiß Bescheid.')
    return redirect(booking.course or 'rooms:mine')


def requestable(person):
    """Whether the person asks for halls given out centrally instead of booking them: teachers without the right to."""
    return 'teacher' in person.roles and not person.everywhere('book_any_room')


@require('manage_bookings')
def requests(request):
    """Halls teachers ask for, in the rooms the office gives out: confirm, or decline with a word why."""
    pending = list(
        Booking.objects.filter(requested=True, end__gte=timezone.now(), room__in=request.user.reached(Room, 'manage_bookings'))
        .select_related('room', 'person', 'category').order_by('start')
    )
    for b in pending:
        b.taken = b.clashes().select_related('person').first()
        b.rivals = [o for o in pending if o is not b and o.room_id == b.room_id and o.start < b.end and b.start < o.end]
    return render(request, 'rooms/requests.html', {'pending': pending})


@require('manage_bookings')
@require_POST
def answer(request, pk):
    booking = get_object_or_404(Booking.objects.select_related('room', 'person'), pk=pk, requested=True)
    if booking.room_id not in request.user.reached(Room, 'manage_bookings'):
        raise PermissionDenied
    when = moment(booking.start, booking.end)
    if request.POST.get('decision') == 'yes':
        booking.requested = False
        try:
            booking.full_clean()
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect('rooms:requests')
        booking.save(update_fields=['requested'])
        texts.send('raumanfrage_bestaetigt', booking.person, reverse('rooms:booking', args=[booking.pk]), raum=booking.room, termin=when, buchung=booking.label)
        messages.success(request, f'{booking.room} für {booking.person} bestätigt.')
    else:
        texts.send('raumanfrage_abgesagt', booking.person, reverse('rooms:find'), raum=booking.room, termin=when, buchung=booking.label, grund=request.POST.get('note', '').strip() or '–')
        booking.delete()
        messages.success(request, f'Anfrage von {booking.person} abgesagt.')
    return redirect('rooms:requests')


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
    bookings = agenda(person).filter(start__gte=now - timedelta(days=7), start__lte=now + timedelta(days=183)).select_related('room', 'course', 'category')
    return ics(request, [(f'buchung-{b.pk}', b.start, b.end, b.label, f'{b.room} {b.room.purpose}, {b.room.building}'.strip()) for b in bookings])


def ics(request, events, filename=None):
    """iCalendar of (uid, start, end, summary, location) for any calendar app; with a filename, to download."""

    def text(value):
        return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

    def stamp(moment):
        return moment.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')

    name, host, now = Institution.get().name, request.get_host().split(':')[0], timezone.now()
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//{text(name)}//Campus//DE', f'X-WR-CALNAME:{text(name)}']
    for uid, start, end, summary, location in events:
        lines += [
            'BEGIN:VEVENT', f'UID:{uid}@{host}', f'DTSTAMP:{stamp(now)}', f'DTSTART:{stamp(start)}', f'DTEND:{stamp(end)}',
            f'SUMMARY:{text(summary)}', f'LOCATION:{text(location)}', 'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    response = HttpResponse('\r\n'.join(lines) + '\r\n', content_type='text/calendar; charset=utf-8')
    if filename:
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
