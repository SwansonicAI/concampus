from django.db.models import Exists, OuterRef

from core.models import Term
from rooms.models import Booking
from students.models import Study
from .models import Course


def untimed(participations):
    """Places in this term's one-to-one classes that no lesson is booked for; accompaniment at auditions is no class."""
    booked = Booking.objects.filter(course=OuterRef('course'), participants=OuterRef('study__person'))
    return participations.filter(course__term=Term.current(), course__kind=Course.Kind.LESSON, study__status=Study.Status.ENROLLED).exclude(
        Exists(booked)).exclude(course__in=Course.objects.filter(accompaniment__isnull=False).values('accompaniment'))
