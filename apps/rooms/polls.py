"""Terminumfragen: a time asked of several people, the times ranked by who can come, booked once one is chosen."""
from collections import Counter
from datetime import datetime
from itertools import groupby
from math import ceil
from types import SimpleNamespace

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core.apps import plural
from core.dates import ClockField, DayField
from core.models import Institution, Message
from core.shortcuts import ids
from courses.models import Course
from .forms import ChooseForm, PollForm, overlapping
from .models import Answer, Booking, Option, Poll
from .plans import agenda


MARKS = {Answer.Choice.YES: ('yes', '✓', 'passt'), Answer.Choice.MOVE: ('wait', '~', 'mit Umplanen'), Answer.Choice.NO: ('no', '✗', 'geht nicht')}


def may_ask(person):
    """Teachers and offices start Terminumfragen, where the Hochschule uses them."""
    return Institution.get().polls and bool(person.roles - {'student', 'applicant', 'alumni', 'juror'})


def when(option):
    start, end = timezone.localtime(option.start), timezone.localtime(option.end)
    return f'{date_format(start, "D j.n.")}, {start:%H:%M}–{end:%H:%M}'


def weigh(poll, options):
    """Each option with its answers by person (`by`), how many said ✓ and ~, and `best` on those that speak least against
    them, once anyone answered: fewest who must come but cannot, then who must rearrange, then optional who cannot, then
    fewest not yet answered."""
    people = {p.pk for p in poll.people.all()}
    optional = {p.pk for p in poll.optional.all()}
    for o in options:
        o.by = {a.person_id: a.choice for a in o.answers.all()}
        needed = [o.by.get(pk) for pk in people - optional]
        o.rank = (needed.count(Answer.Choice.NO), needed.count(Answer.Choice.MOVE), sum(o.by.get(pk) == Answer.Choice.NO for pk in optional), len(people - set(o.by)))
        o.yes, o.move = list(o.by.values()).count(Answer.Choice.YES), list(o.by.values()).count(Answer.Choice.MOVE)
    top = min(o.rank for o in options) if any(o.by for o in options) else None
    for o in options:
        o.best = o.rank == top


def during(bookings, option, into):
    """What of `bookings` overlaps the option's time, set on `into`: `blocks` as (left, width) in per cent of the time,
    `busy` each by its time and name, one a line."""
    length, into.blocks, lines = option.end - option.start, [], []
    for b in bookings:
        start, end = max(b.start, option.start), min(b.end, option.end)
        if start < end:
            into.blocks.append((round((start - option.start) / length * 100, 1), round((end - start) / length * 100, 1)))
            lines.append(f'{timezone.localtime(b.start):%H:%M}–{timezone.localtime(b.end):%H:%M} {b.label}')
    into.busy = '\n'.join(lines)


def index(request):
    person = request.user
    polls = Poll.objects.filter(Q(person=person) | Q(people=person)).distinct().select_related('person', 'chosen', 'booking__room').annotate(
        times=Count('options', distinct=True), answered=Count('options__answers__person', distinct=True), size=Count('people', distinct=True),
        mine=Count('options__answers', filter=Q(options__answers__person=person), distinct=True),
    )
    return render(request, 'rooms/polls.html', {'polls': polls, 'may_ask': may_ask(person), 'today': timezone.localdate()})


def new(request):
    person = request.user
    if not may_ask(person):
        raise PermissionDenied
    course = None
    if pk := request.GET.get('zu'):
        reached = Course.objects.filter(staff=person) | person.within(Course.objects.all(), 'run_auditions', 'manage_exams', 'manage_courses')
        course = get_object_or_404(reached.distinct(), pk=pk)
    form = PollForm(request.POST or None, person=person, course=course)
    if form.is_valid():
        poll = form.save()
        first, last = timezone.localtime(form.slots[0][0]), timezone.localtime(form.slots[-1][0])
        for p in poll.people.exclude(pk=person.pk):
            Message.send(
                p, f'Terminumfrage: {poll.title}',
                f'{person.name} fragt, wann Sie können: {plural(len(form.slots), "Zeit", "Zeiten")} vom {first:%d.%m.} bis {last:%d.%m.%Y}. Bitte bis {poll.by:%d.%m.%Y} antworten.',
                poll.get_absolute_url(),
            )
        messages.success(request, f'Gefragt: {plural(poll.people.count(), "Person", "Personen")}.')
        return redirect(poll)
    return render(request, 'rooms/poll_form.html', {'form': form, 'course': course, 'trail': trail(person, course)})


def detail(request, pk):
    person = request.user
    poll = get_object_or_404(Poll.objects.select_related('person', 'course', 'room', 'category', 'chosen', 'booking__room'), pk=pk)
    organiser, asked = poll.person_id == person.pk, poll.people.filter(pk=person.pk).exists()
    if not (organiser or asked):
        raise PermissionDenied
    options = list(poll.options.prefetch_related('answers'))
    if request.method == 'POST' and asked and not poll.chosen_id:
        given = {o: request.POST.get(f'antwort-{o.pk}') for o in options}
        with transaction.atomic():
            Answer.objects.filter(option__poll=poll, person=person).delete()
            Answer.objects.bulk_create(Answer(option=o, person=person, choice=c) for o, c in given.items() if c in Answer.Choice.values)
        messages.success(request, 'Antworten gespeichert.')
        return redirect(poll)
    weigh(poll, options)
    answering, choosing = asked and not poll.chosen_id, organiser and not poll.chosen_id
    if options:
        span = Q(start__lt=options[-1].end, end__gt=options[0].start)
        if answering:
            mine = list(agenda(person).filter(span).exclude(pk=poll.booking_id).select_related('category', 'course'))
            for o in options:
                during(mine, o, o)
        if choosing and poll.room_id:
            taken = list(Booking.objects.filter(span, room=poll.room_id, category__blocks=True, requested=False))
            for o in options:
                o.taken = next((b for b in taken if b.start < o.end and b.end > o.start), None)
    optional = set(poll.optional.values_list('pk', flat=True))
    shown = poll.people.all() if organiser or poll.open else poll.people.filter(pk=person.pk)
    rows = sorted((p for p in shown if not (answering and p.pk == person.pk)), key=lambda p: (p.pk in optional, p.last_name))
    for p in rows:  # one's own row, while answering, is the one to click
        p.optional, p.cells = p.pk in optional, [MARKS.get(o.by.get(p.pk)) for o in options]
    for o in options:
        o.mine = o.by.get(person.pk, '')
        o.mark = MARKS.get(o.mine)
    wanted, lacking = wanting(poll, options) if choosing else (0, None)
    together = Counter(timezone.localdate(o.start) for o in options if o.best)  # a whole day rather than halves of several
    best = sorted((o for o in options if o.by), key=lambda o: (o.rank, -together[timezone.localdate(o.start)], o.start))[:wanted]  # ticked to begin with
    for o in best:
        o.picked = True
    return render(request, 'rooms/poll.html', {
        'poll': poll, 'options': options, 'rows': rows, 'choosing': choosing, 'answering': answering,
        'days': [(day, list(group)) for day, group in groupby(options, key=lambda o: timezone.localdate(o.start))],
        'done': len({pk for o in options for pk in o.by}), 'size': poll.people.count(), 'trail': trail(person, poll.course),
        'late': [p.last_name for p in rows if not any(p.pk in o.by for o in options)] if choosing else [], 'wanted': wanted, 'lacking': lacking,
        'held': poll.course.bookings.select_related('room').order_by('start') if poll.chosen_id and poll.course_id else [],
    })


def wanting(poll, options):
    """How many of the poll's times to choose, and the hours still lacking that say so (None where nothing does): for an
    audition as many as it lacks by the time its applicants will take (admissions.auditions.need), at least one; else
    one."""
    course = poll.course
    if not options or course is None or course.kind != Course.Kind.AUDITION:
        return 1, None
    from admissions.auditions import booked, need

    plan = need(course)
    if plan is None:
        return 1, None
    lacking = plan.hours - booked(course)
    return max(1, ceil(lacking / ((options[0].end - options[0].start).total_seconds() / 3600))), lacking


def trail(person, course):
    """The way down to a poll in the menu: under the exam or audition it finds the day for, else under Terminumfragen."""
    if course is None:
        return None
    if course.kind == Course.Kind.AUDITION:
        from admissions.views.juries import above

        return above(person, course, course.staff.filter(pk=person.pk).exists()) + [(course.title, course.get_absolute_url())]
    return [(None, reverse('exams:index')), (course.title, course.get_absolute_url())]


def owned(request, pk):
    poll = get_object_or_404(Poll, pk=pk, person=request.user)
    if poll.chosen_id:
        raise PermissionDenied
    return poll


def choose(request, pk):
    """The times ticked made the poll's: each booked in the room chosen, if one is, and everyone asked told, with what of
    their own clashes. An audition or exam may take several days; a meeting takes one."""
    poll = owned(request, pk)
    picked = ids(request.POST.getlist('zeit') or request.GET.getlist('zeit'))
    options = list(poll.options.filter(pk__in=picked).prefetch_related('answers'))
    if not options or len(options) > 1 and not poll.course_id:
        messages.error(request, 'Bitte eine Zeit ankreuzen.' if not options else 'Bitte nur eine Zeit ankreuzen.')
        return redirect(poll)
    form = ChooseForm(request.POST or None, person=request.user, poll=poll, options=options)
    if form.is_valid():
        room, spans = form.cleaned_data['room'], []
        for o in options:  # a morning and the afternoon after it are one day
            if spans and spans[-1].end == o.start:
                spans[-1].end = o.end
            else:
                spans.append(SimpleNamespace(start=o.start, end=o.end))
        bookings = [
            Booking(room=room, start=s.start, end=s.end, category=poll.category, person=poll.person, course=poll.course, title='' if poll.course_id else poll.title)
            for s in spans
        ] if room else []
        try:
            for b in bookings:
                b.full_clean()
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return redirect(poll)
        with transaction.atomic():
            for b in bookings:
                b.save()
                b.participants.set(poll.people.exclude(pk=poll.person_id))
            poll.chosen, poll.booking = options[0], bookings[0] if bookings else None
            poll.save()
        place = f', {room} ({room.building})' if room else ''
        times = ' und '.join(when(s) for s in spans)
        for p in poll.people.exclude(pk=request.user.pk):
            clashes = agenda(p).filter(overlapping(options)).exclude(pk__in=[b.pk for b in bookings])
            also = ''.join(f'\n– {date_format(timezone.localtime(b.start), "D j.n.")}, {timezone.localtime(b.start):%H:%M}–{timezone.localtime(b.end):%H:%M} {b.label}' for b in clashes)
            Message.send(p, f'Termin steht: {poll.title}', f'{times}{place}.' + (f'\n\nZur selben Zeit bei Ihnen:{also}' if also else ''), poll.get_absolute_url())
        messages.success(request, f'Festgelegt: {times}{place}. Alle Gefragten sind benachrichtigt.')
        return redirect(poll)
    weigh(poll, options)
    optional = set(poll.optional.values_list('pk', flat=True))
    people = sorted(poll.people.all(), key=lambda p: (p.pk in optional, p.last_name))
    for p in people:  # each with their answers and how much of the times they have on: what is to move, not what it is
        p.optional, mine = p.pk in optional, list(agenda(p).filter(overlapping(options)).exclude(pk=poll.booking_id))
        p.cells = []
        for o in options:
            cell = SimpleNamespace(mark=MARKS.get(o.by.get(p.pk)))
            during(mine, o, cell)
            p.cells.append(cell)
    way = (trail(request.user, poll.course) or [(None, reverse('rooms:polls'))]) + [('Terminumfrage', poll.get_absolute_url())]
    return render(request, 'rooms/poll_choose.html', {
        'poll': poll, 'options': options, 'form': form, 'people': people, 'trail': way,
        'days': [(day, list(group)) for day, group in groupby(options, key=lambda o: timezone.localdate(o.start))],
    })


@require_POST
def add(request, pk):
    poll = owned(request, pk)
    try:
        day, begin, until = DayField().clean(request.POST.get('day')), ClockField().clean(request.POST.get('begin')), ClockField().clean(request.POST.get('until'))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect(poll)
    start, end = (timezone.make_aware(datetime.combine(day, t)) for t in (begin, until))
    if end <= start:
        messages.error(request, '„Bis“ muss nach „Von“ liegen.')
    else:
        Option.objects.create(poll=poll, start=start, end=end)
    return redirect(poll)


@require_POST
def remind(request, pk):
    """A word to those who have not answered yet."""
    poll = owned(request, pk)
    late = poll.people.exclude(pk__in=Answer.objects.filter(option__poll=poll).values('person')).exclude(pk=poll.person_id)
    for p in late:
        Message.send(p, f'Erinnerung: Terminumfrage {poll.title}', f'{poll.person.name} wartet noch auf Ihre Antwort, bis {poll.by:%d.%m.%Y}.', poll.get_absolute_url())
    messages.success(request, f'Erinnert: {", ".join(p.name for p in late)}.' if late else 'Alle haben geantwortet.')
    return redirect(poll)


@require_POST
def optional(request, pk, person):
    """Whether someone asked must come or only can: what they cannot do then counts last."""
    poll = owned(request, pk)
    person = get_object_or_404(poll.people, pk=person)
    if poll.optional.filter(pk=person.pk).exists():
        poll.optional.remove(person)
    else:
        poll.optional.add(person)
    return redirect(poll)


@require_POST
def delete(request, pk):
    owned(request, pk).delete()
    messages.success(request, 'Terminumfrage gelöscht.')
    return redirect('rooms:polls')
