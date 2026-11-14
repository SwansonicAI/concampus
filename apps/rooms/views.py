from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.access import CODENAMES, require
from core.apps import plural
from core.dates import day as understood, starting
from core.forms import again, sent
from core.days import busy, strip
from core import texts
from core.models import Institute, Institution, Message, Office, Person
from core.shortcuts import ics
from core.text import matching
from courses.models import Course
from .forms import BookingForm, CancelForm, FindForm, ServiceForm, bookable, categories, horizon, quota
from .models import Booking, BookingCategory, Room, RoomFunction, span
from .plans import PER_HOUR, agenda, around, called_off, legend, line, recipients, week

FEED = 'kalender'  # salt of the calendar feed links
HALF = timedelta(minutes=30)  # the plan's step for booking; a click books two


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
    """The rooms of a day. Free time one may book is booked right here (rooms/_quick.html): in the rooms one books or,
    as a teacher, asks for, from now on as far ahead as one books. The filter shows only the rooms free from–until,
    big enough, with the equipment asked for: those one reaches in time from one's dates before and after first, then
    the smallest, those for one's Hauptfach first."""
    person, today, now = request.user, timezone.localdate(), timezone.now()
    day = understood(request.GET.get('tag', '')) or today
    allowed = set(bookable(person).values_list('pk', flat=True))
    asked = set(Room.objects.filter(function__central=True).exclude(pk__in=allowed).values_list('pk', flat=True)) if requestable(person) else set()
    group = request.GET.get('raeume') or ('zentral' if person.everywhere('book_any_room') else 'meine' if allowed else 'alle')
    kind, _, key = group.partition(':')
    rooms = Room.objects.select_related('function').prefetch_related(Prefetch(
        'bookings', Booking.objects.filter(**starting(day), requested=False).select_related('course', 'person', 'category').prefetch_related('participants'), to_attr='day',
    ))
    rooms = {
        'meine': rooms.filter(pk__in=allowed), 'zentral': rooms.filter(function__central=True), 'haus': rooms.filter(building=key), 'funktion': rooms.filter(function=key),
        'institut': rooms.filter(institutes=key) if key.isdigit() else rooms,
    }.get(kind, rooms)
    rules = Institution.get()
    opens = timezone.make_aware(datetime.combine(day, time(rules.opens)))
    closes, earliest, ahead = opens + timedelta(hours=rules.closes - rules.opens), now - timedelta(minutes=15), horizon(person)
    latest = ahead and now + timedelta(hours=ahead)
    shut = [(f'2 / {line(earliest)}', '')] if earliest > opens else []  # what is over, and what lies beyond how far ahead one books
    if latest and latest < closes:
        shut.append((f'{line(latest)} / -1', f'Sie buchen höchstens {span(ahead)} im Voraus.'))
    rooms = list(rooms)
    for room in rooms:
        room.asked, ours = room.pk in asked, room.pk in allowed | asked
        room.shut = shut if ours else [('2 / -1', 'Wird zentral vergeben.' if room.function.central else 'Gehört einem anderen Institut.')]
        taken = [b for b in room.day if b.category.blocks]
        room.slots = [  # the half hours free and bookable
            (f'{line(start)} / {line(start + HALF)}', f'{timezone.localtime(start):%H:%M}')
            for start in (opens + HALF * i for i in range((rules.closes - rules.opens) * 2))
            if earliest <= start and not (latest and start > latest) and not any(b.start < start + HALF and start < b.end for b in taken)
        ] if ours else []
        for b in room.day:
            b.columns = f'{line(b.start)} / {line(b.end)}'
            b.editable = editable(b, person)
            b.hidden = not b.editable and private(b, person)
            b.quick = b.person_id == person.pk and not (b.course_id or b.series) and b.end > now  # changed or cancelled right there
    finding, found = FindForm(request.GET), None
    if finding.is_valid() and any(finding.cleaned_data.values()):
        data, start = finding.cleaned_data, None
        fitting = matching(Room.objects.filter(pk__in=allowed | asked, capacity__gte=data['personen'] or 1), data['ausstattung'], 'equipment', 'name', 'purpose', 'building')
        fitting = set(fitting.values_list('pk', flat=True))
        if data['von']:
            start, end = (timezone.make_aware(datetime.combine(day, t)) for t in (data['von'], data['bis']))
            near = list(around(person, start, end))
        subjects = set(person.studies.filter(status='enrolled').values_list('subject__name', flat=True))
        rooms = [r for r in rooms if r.pk in fitting and not (start and any(b.category.blocks and b.start < end and start < b.end for b in r.day))]
        for room in rooms:
            room.span = start and f'{line(start)} / {line(end)}'
            room.away = start and next((b for b in near if b.room.building != room.building), None)
        rooms.sort(key=lambda r: (r.away is not None, r.capacity, not any(s in r.purpose for s in subjects)))
        found = {'start': start, 'end': start and end}
    monday, local = day - timedelta(days=day.weekday()), timezone.localtime(now)
    shown = {b.tone for room in rooms for b in room.day if not b.hidden}
    return render(request, 'rooms/index.html', {
        'day': day,
        'prev': day - timedelta(days=1),
        'next': day + timedelta(days=1),
        'days': strip(monday, day, busy(agenda(person), monday)),
        'rooms': rooms,
        'group': group,
        'groups': groups(),
        'finding': finding,
        'found': found,
        'hours': [(f'{2 + i * PER_HOUR} / span {PER_HOUR}', rules.opens + i) for i in range(rules.closes - rules.opens)],
        'now': line(now) if day == local.date() and rules.opens <= local.hour < rules.closes else None,
        'tones': [(tone, label) for tone, label in legend() if tone in shown],
        'hidden': any(b.hidden for room in rooms for b in room.day),
        'mine': any(b.person_id == person.pk for room in rooms for b in room.day),
        'left': None if 'student' not in person.roles else rules.student_daily_hours - quota(person, day),
        'reach': [  # how far ahead each kind of booking goes, where that ends before the day does
            (c, until) for c in categories(person) if (until := now + timedelta(hours=c.ahead_hours or rules.student_ahead_hours)) < closes
        ] if ahead and 'teacher' not in person.roles else [],
    })


@require(*ROOMS)
def find(request):
    """Old links to the search for free rooms, which is now the room plan's filter."""
    names = {'day': 'tag', 'start': 'von', 'end': 'bis', 'seats': 'personen', 'equipment': 'ausstattung'}
    return redirect(f"{reverse('rooms:index')}?{urlencode({names[k]: v for k, v in request.GET.items() if k in names})}")


def tried(person, room, day, begin, until):
    """A booking of `room` checked against every rule before anyone fills in a form: bound, so it carries its errors,
    and holding what to send to book it at once: as the first category booked that far ahead; a rehearsal, of one's
    first ensemble."""
    form, rules = BookingForm(person=person), Institution.get()
    start, kinds = timezone.make_aware(datetime.combine(day, begin)), form.fields['category'].queryset
    kind = next((c for c in kinds if not form.student or start <= timezone.now() + timedelta(hours=c.ahead_hours or rules.student_ahead_hours)), kinds.first())
    data = QueryDict(mutable=True)  # as a form sends it
    data.update({
        'room': room.pk, 'category': kind.key,
        'day': f'{day:%d.%m.%Y}', 'begin': f'{begin:%H:%M}', 'until': f'{until:%H:%M}',
    })
    if kind.participants and 'ensemble' in form.fields:
        data['ensemble'] = form.fields['ensemble'].queryset.first().pk
    return BookingForm(data, person=person)


@require(*ROOMS)
def room(request, pk):
    room = get_object_or_404(Room.objects.prefetch_related('institutes'), pk=pk)
    day = understood(request.GET.get('tag', '')) or timezone.localdate()
    plan = week(room.bookings.filter(requested=False), day)
    for b in plan['placed']:  # as the room plan shows them: practice and others' private bookings only as taken
        b.hidden = not editable(b, request.user) and private(b, request.user)
        if b.hidden:
            b.title, b.course = 'belegt', None
    return render(request, 'rooms/room.html', {
        'trail': [(None, reverse('rooms:index'))], 'room': room, 'day': day, 'bookable': bookable(request.user).filter(pk=pk).exists(), 'central': Office.holding('book_any_room', linked=True),
        'edit': request.user.may('manage_rooms', room), 'requestable': room.function.central and requestable(request.user),
        **plan,
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
        booked = sum((b.end - b.start).total_seconds() for b in person.bookings.filter(category__quota=True, **starting(today))) / 3600
        context.update(used=used, daily=rules.student_daily_hours, left=rules.student_daily_hours - used, extra=round(booked - used, 1), last_minute=rules.last_minute_hours)
    return render(request, 'rooms/mine.html', context)


@require(*ROOMS)
def events(request):
    """What the Hochschule puts on, a month at a time: concerts, class evenings, master classes, lectures; at first what
    is coming, from today through the next month."""
    today, coming = timezone.localdate(), False
    try:
        first = datetime.strptime(request.GET.get('monat', ''), '%Y-%m').date()
    except ValueError:
        first, coming = today.replace(day=1), True
    following = (first + timedelta(days=32)).replace(day=1)
    until = (following + timedelta(days=32)).replace(day=1) if coming else following
    picked, q = request.GET.get('kategorie', ''), request.GET.get('q', '').strip()
    found = Booking.objects.filter(category__public=True, requested=False, **starting(today if coming else first, until - timedelta(days=1))).select_related('room', 'person', 'course', 'category')
    if picked:
        found = found.filter(category=picked)
    return render(request, 'rooms/events.html', {
        'events': matching(found, q, 'title', 'programme', 'room__name', 'room__building') if q else found, 'picked': picked, 'q': q,
        'categories': BookingCategory.objects.filter(public=True), 'month': first, 'coming': coming and until,
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
            'trail': [(None, reverse('rooms:mine' if booking.person_id == person.pk else 'rooms:index'))], 'booking': booking, 'participants': booking.participants.all(), 'past': past, 'history': bool(Booking.seen_by(person, booking.pk)),
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
    if request.method == 'POST' and 'mehr' not in data and form.is_valid():  # „Weitere Angaben“: the whole form, as filled in so far
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
            messages.warning(request, f'Zwischen {way.label} in {way.room.building} und dieser Buchung bleiben keine {rules.walk_minutes} Minuten für den Weg.')
        return redirect(f"{reverse('rooms:index')}?tag={timezone.localtime(booking.start):%Y-%m-%d}")
    series = Booking.objects.filter(series=booking.series).count() if booking.series else 0
    on = timezone.localtime(form.instance.start).date() if form.instance.start else start.date() if start else timezone.localdate()  # the day the quota is for
    told = None  # a date others come to can be called off and they hear of it
    if booking.pk and (booking.course_id or booking.participants.exists()):
        told = [p.name for p in recipients(booking, person)]
        told = told if len(told) <= 3 else [f'alle {len(told)} Teilnehmenden']
    return render(request, 'rooms/_quick.html' if request.headers.get('X-Quick') else 'rooms/booking.html', {  # beside the room plan, or a page
        'trail': [(None, reverse('rooms:mine' if booking.pk and booking.person_id == person.pk else 'rooms:index'))], 'form': form, 'booking': booking, 'series': series, 'central': Office.holding('book_any_room', linked=True),
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


def moment(start, end):
    start, end = timezone.localtime(start), timezone.localtime(end)
    return f'{date_format(start, "D j.n.Y")}, {start:%H:%M}–{end:%H:%M} Uhr'


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
    found = Booking.objects.filter(**starting(day), end__gt=timezone.now(), category__blocks=True, requested=False)
    if building:  # a closure: everything there, as far as the office looks after it
        found = person.within(found.filter(room__building=building), 'manage_bookings')
    elif teaching:
        found = found.filter(course__kind__in=[Course.Kind.COURSE, Course.Kind.LESSON])
    else:
        found = found.filter(Q(person=person) | Q(course__staff=person, ensemble=None, group=None) | Q(ensemble__coach=person) | Q(group__staff=person))
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
        texts.send('raumanfrage_abgesagt', booking.person, reverse('rooms:index'), raum=booking.room, termin=when, buchung=booking.label, grund=request.POST.get('note', '').strip() or '–')
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
        person = get_object_or_404(Person, pk=signing.loads(token, salt=FEED), is_active=True)  # ends with the account
    except signing.BadSignature:
        raise Http404
    now = timezone.now()
    bookings = agenda(person).filter(start__gte=now - timedelta(days=7), start__lte=now + timedelta(days=183)).select_related('room', 'course', 'category')
    return ics(request, [(f'buchung-{b.pk}', b.start, b.end, b.label, f'{b.room} {b.room.purpose}, {b.room.building}'.strip()) for b in bookings])
