"""Helpers the office's and the applicant's pages share: offers and their deadlines, whether uploads are open."""
from datetime import timedelta

from django.utils import timezone

from courses.models import Course
from students.models import Study


def uploads_open(study):
    """Documents and videos can be handed in and changed until the application deadline, unless the jury has closed the round."""
    return study.status == Study.Status.APPLIED and Course.objects.filter(
        kind=Course.Kind.AUDITION, program__isnull=False, participations__study=study, deadline__gte=timezone.localdate(), released=None,
    ).exists()


def accept_by(study, days):
    """The last day to accept an offered place; after it the offer lapses and the place goes down the Nachrückliste."""
    return study.since + timedelta(days=days)


def lapsed(study, days):
    """An offer not accepted in time; needs `accepted` from `ranked`."""
    return study.status == Study.Status.ADMITTED and not study.accepted and accept_by(study, days) < timezone.localdate()
