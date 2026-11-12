"""The room and week plans: whose dates a person attends, the grid they are placed on, and calling a date off."""
from datetime import timedelta
from operator import attrgetter

from django.db.models import Q
from django.utils import timezone

from core.dates import starting
from core.models import Institution, Person
from courses.models import Course, Group, Participation
from .models import Booking, BookingCategory

STEP = 5  # minutes per grid line
PER_HOUR = 60 // STEP


def walk():
    """The way from one building to another."""
    return timedelta(minutes=Institution.get().walk_minutes)


def line(moment):
    """Grid line of a time on the room plan, from the hour rooms open (line 1 is the label column)."""
    t, institution = timezone.localtime(moment), Institution.get()
    return 2 + min(max((t.hour - institution.opens) * 60 + t.minute, 0), (institution.closes - institution.opens) * 60) // STEP


def agenda(person):
    """Bookings a person attends: booked for them or with them, taught or examined by them, or a group course they take
    part in; of an ensemble or a group of a course, only its own."""
    taking = Participation.objects.filter(study__person=person)
    return Booking.objects.filter(  # by subqueries, not joins: no row many times over to make distinct again
        Q(person=person) | Q(pk__in=Booking.participants.through.objects.filter(person=person).values('booking'))
        | Q(course__in=Course.objects.filter(staff=person).values('pk'), ensemble=None, group=None) | Q(ensemble__coach=person)
        | Q(group__in=Group.objects.filter(staff=person).values('pk'))
        | Q(course__in=taking.filter(course__kind=Course.Kind.COURSE).values('course'), ensemble=None, group=None) | Q(group__in=taking.values('group'))
    ).filter(category__blocks=True, requested=False)


def moves(bookings):
    """Mark the bookings of one person that begin within the walk() from the one before, in another building: `move`
    is where they come from, `came` the date there."""
    ordered, gap = sorted(bookings, key=lambda b: b.start), walk()
    for before, b in zip(ordered, ordered[1:]):
        if timedelta(0) <= b.start - before.end <= gap and before.room.building != b.room.building:
            b.move, b.came = before.room.building, before


def around(person, start, end, exclude=None):
    """The person's own dates ending within the walk() before `start` or beginning as soon after `end`: in another
    building, no time for the way."""
    gap = walk()
    near = Q(end__range=(start - gap, start)) | Q(start__range=(end, end + gap))
    return agenda(person).filter(near).exclude(pk=exclude).select_related('room')


def side_by_side(items, overlap):
    """Lanes for what one grid shows, so that what overlaps stands side by side instead of on top: each item gets `lane`
    and `lanes`, how many share its time."""
    for n, item in enumerate(items):
        taken = {o.lane for o in items[:n] if overlap(o, item)}
        item.lane = next(i for i in range(len(items)) if i not in taken)
    for item in items:
        item.lanes = 1 + max(o.lane for o in items if o is item or overlap(o, item))


def week(bookings, day, person=None, whole=False):
    """Context for the week grid: the bookings of the week around `day` placed on the days that have any, Monday to Friday
    always, every day with `whole`, in the hours they need, at least 9 to 18; side by side where they overlap; and by day,
    for a phone's list. For one person's own plan, their lessons name the student (`who`) and a change of building
    without a break shows."""
    monday = day - timedelta(days=day.weekday())
    placed = list(bookings.filter(**starting(monday, monday + timedelta(days=6))).select_related('room', 'course', 'category').prefetch_related('participants', 'absent'))
    if person:
        moves(placed)
        for b in placed:
            if b.course and b.course.kind == Course.Kind.LESSON and b.person_id == person.pk:
                b.who = ', '.join(f'{p.first_name} {p.last_name}' + (' (abgemeldet)' if p in b.absent.all() else '') for p in b.participants.all())
    local = {b.pk: (timezone.localtime(b.start), timezone.localtime(b.end)) for b in placed}
    days = list(range(7)) if whole else sorted({0, 1, 2, 3, 4} | {start.weekday() for start, _ in local.values()})
    first = min([9] + [start.hour for start, _ in local.values()])
    last = max([18] + [end.hour + bool(end.minute) for _, end in local.values()])

    def row(t):
        return 2 + ((t.hour - first) * 60 + t.minute) // STEP

    for b in placed:
        start, end = local[b.pk]
        column, minutes = 2 + days.index(start.weekday()), (end - start).total_seconds() // 60
        b.place = f'{row(start)} / {column} / {row(end)} / {column + 1}'
        b.across = f'{column} / {row(start)} / {column + 1} / {row(end)}'  # days down, hours across (_across.html)
        b.size = 'tiny' if minutes < 40 else 'short' if minutes < 75 else ''  # one line, two, or a title of two
        b.clash = any(o is not b and o.start < b.end and b.start < o.end for o in placed)
    side_by_side(placed, lambda a, b: a.start < b.end and b.start < a.end)
    now, dates = timezone.localtime(), [monday + timedelta(days=d) for d in days]
    return {
        'dates': dates, 'monday': monday, 'sunday': monday + timedelta(days=6), 'today': now.date(), 'placed': placed, 'rows': (last - first) * PER_HOUR,
        'ticks': [(f'{2 + i * PER_HOUR} / span {PER_HOUR}', first + i) for i in range(last - first)],
        'now': f'{row(now)} / {2 + dates.index(now.date())}' if now.date() in dates and first <= now.hour < last else None,
        'agenda': [(d, sorted((b for b in placed if local[b.pk][0].date() == d), key=attrgetter('start'))) for d in dates if any(local[b.pk][0].date() == d for b in placed)],
        'prev': monday - timedelta(days=7),
        'next': monday + timedelta(days=7),
    }


def legend():
    """The colours in use on the plans, in their order."""
    used = set(BookingCategory.objects.filter(blocks=True).values_list('tone', flat=True))
    return [(tone.value, tone.label) for tone in BookingCategory.Tone if tone in used]


def recipients(booking, by):
    """Who hears when a date is called off: whoever takes part, everyone in a group course, and whoever booked it when
    someone else calls it off."""
    people = set(booking.participants.all())
    if booking.group_id:  # a group's date, its own
        people |= set(Person.objects.filter(studies__participations__group=booking.group_id))
    elif booking.course_id and booking.course.kind == Course.Kind.COURSE and not booking.ensemble_id:  # an ensemble's date, its members only
        people |= set(Person.objects.filter(studies__participations__course=booking.course_id))
    people.add(booking.person)
    people.discard(by)
    return sorted(people, key=lambda p: p.name)


def called_off(booking, by):
    """Call a date off: it stays on the plans as „Abgesagt“ and frees the room. Returns who is to hear of it."""
    told = recipients(booking, by)
    booking.category = BookingCategory.system('abgesagt')
    booking.save(update_fields=['category'])
    return told
