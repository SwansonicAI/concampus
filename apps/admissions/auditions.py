"""An applicant's appointments in the Eignungsprüfung: when and where, what follows, and the invitation; a juror's marks
still to give."""
from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from core.models import Entry
from core.text import collated
from courses.models import Course, marked
from .papers import judged


def unmarked(person):
    """The auditions where `person` has marks to give now, each with how many are not handed in, how many of those are
    drafts, and where to start: [(course, n, drafts, url)], the one due first first. An online stage counts whoever
    handed in something to judge, one in person once it has begun. Where each member marks: their own marks, none for
    whom they stand back from, a substitute only for whom they stand in for; where the chair enters for the
    commission: every result still open, for the chair."""
    found = []
    juries = Course.objects.filter(Q(staff=person) | Q(participations__recusals__substitute=person), kind=Course.Kind.AUDITION, released=None)
    for course in juries.exclude(participations=None).distinct():
        if not (course.video or course.participations.filter(slot__lte=timezone.now()).exists()):
            continue
        rows = judged(course.participations.all()) if course.video else course.participations.all()
        if course.entry == Entry.CHAIR:
            n, drafts = (rows.filter(passed=None).count() if course.chair_id == person.pk else 0), 0
        elif course.entry == Entry.MEMBERS:
            missing = rows.exclude(scores__in=marked(person)).exclude(recusals__examiner=person)
            if not course.staff.filter(pk=person.pk).exists():
                missing = missing.filter(recusals__substitute=person)
            n, drafts = missing.count(), missing.filter(scores__examiner=person, scores__draft=True).count()
        else:  # the office enters from the Protokoll
            n = drafts = 0
        if n:
            found.append((course, n, drafts, reverse('admissions:review', args=[course.pk]) if course.video else course.get_absolute_url()))
    return sorted(found, key=lambda f: f[0].marks_by or f[0].deadline or date.max)


def chains(courses):
    """A semester's stages as each Hauptfach's Eignungsprüfung: [(its own stages in order, from the one applied for;
    the stages it shares with others after them, as a written part everyone sits)], those shared ones last."""
    by_pk, before, following = {c.pk: c for c in courses}, defaultdict(set), defaultdict(set)
    for c, b in Course.after.through.objects.filter(from_course__in=by_pk, to_course__in=by_pk).values_list('from_course', 'to_course'):
        before[c].add(b)
        following[b].add(c)
    for c in courses:
        c.next_pks, c.prev_pks = following[c.pk], before[c.pk]
    seen = set()

    def walk(c):  # a stage, then those that follow it alone
        seen.add(c.pk)
        found = [c]
        for pk in sorted(following[c.pk], key=lambda pk: collated(by_pk[pk].title)):
            if len(before[pk]) == 1 and pk not in seen:
                found += walk(by_pk[pk])
        return found

    own = [walk(c) for c in courses if not before[c.pk]]
    shared = [c for c in courses if c.pk not in seen]
    return [(stages, [s for s in shared if before[s.pk] & {c.pk for c in stages}]) for stages in own], shared


def origin(course):
    """The stage applied for that a stage's own chain starts with; None for a stage several Hauptfächer share."""
    c, seen = course, set()
    while not c.program_id:
        before = list(c.after.all()[:2])
        if len(before) != 1 or c.pk in seen:
            return None
        seen.add(c.pk)
        c = before[0]
    return c


def days(first, last):
    """The days a stage is held on, from its first booking's to its last's: '15.1.', '13.–14.1.', '29.1.–2.2.'."""
    if not first:
        return ''
    a, b = timezone.localtime(first).date(), timezone.localtime(last).date()
    if a == b:
        return f'{a.day}.{a.month}.'
    return f'{a.day}.–{b.day}.{b.month}.' if a.month == b.month else f'{a.day}.{a.month}.–{b.day}.{b.month}.'


def next_stages(course):
    """The stages after this one, as the stages say they follow it: the video round leads to the Hauptfach exam of its
    programme, that to the parts every Bachelor applicant plays."""
    return course.following.filter(kind=Course.Kind.AUDITION)


def held(p):
    """(start, end, room) of an applicant's appointment: the slot, until the next one, in the booking around it."""
    booking = next((b for b in p.course.bookings.select_related('room') if b.start <= p.slot < b.end), None)
    later = p.course.participations.filter(slot__gt=p.slot).order_by('slot').values_list('slot', flat=True).first()
    return p.slot, min(filter(None, [later, booking and booking.end]), default=p.slot + timedelta(minutes=30)), booking and booking.room


def after(course):
    """What follows a stage, for whoever passes it: each next stage with its first date and room, as far as they are booked."""
    found = []
    for c in next_stages(course).prefetch_related('bookings__room'):
        first = min(c.bookings.all(), key=lambda b: b.start, default=None)
        found.append(f'{c.title}: {date_format(timezone.localtime(first.start), "l, j. F Y, H:i")} Uhr, {first.room.place()}' if first else f'{c.title}: Termin folgt')
    return found


def invite(p):
    """The invitation to an appointment of the audition, as a letter in the Postfach and the file: when, where, what to play, what
    follows; printable, e.g. for a visa."""
    start, _, room = held(p)
    p.study.notify(
        'einladung_eignungspruefung', reverse('admissions:mine'), pruefung=p.course.title, termin=date_format(timezone.localtime(start), 'l, j. F Y, H:i'),
        raum=room.place(linked=True) if room else 'wird mitgeteilt', programm=p.note or '–', danach='\n'.join(f'– {line}' for line in after(p.course)) or '–',
    )
