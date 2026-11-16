"""An applicant's appointments in the Eignungsprüfung: when and where, what follows, and the invitation; a juror's marks
still to give."""
from datetime import date, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from courses.models import Course, marked


def unmarked(person):
    """The auditions where `person` has applicants to mark now, each with how many and where to start: [(course, n, url)],
    the one due first first. A video round counts whoever handed in a video, one in person once it has begun; a
    recusal is no one's to mark, a substitute marks only whom they stand in for."""
    found = []
    juries = Course.objects.filter(Q(staff=person) | Q(participations__recusals__substitute=person), kind=Course.Kind.AUDITION, released=None)
    for course in juries.exclude(participations=None).distinct():
        missing = course.participations.exclude(scores__in=marked(person)).exclude(recusals__examiner=person)
        if not course.staff.filter(pk=person.pk).exists():
            missing = missing.filter(recusals__substitute=person)
        if course.video:
            missing = missing.filter(study__documents__kind__condition='video').distinct()
        if (course.video or course.participations.filter(slot__lte=timezone.now()).exists()) and (n := missing.count()):
            found.append((course, n, reverse('admissions:review', args=[course.pk]) if course.video else course.get_absolute_url()))
    return sorted(found, key=lambda f: f[0].marks_by or f[0].deadline or date.max)


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
