"""The demo's own day: a session may live on another date, to show ConCampus as it is then (settings.DEMO only).
`timezone.now` is wrapped once, from the settings, before any model takes it as a default; a request's shift is set by
core.middleware.demo_day and holds for that request alone, so other sessions and the nightly jobs keep the real day."""
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps

from django.utils import timezone

shift = ContextVar('shift', default=timedelta(0))


def install():
    real = timezone.now

    @wraps(real)  # still django.utils.timezone.now to a migration that writes it as a default
    def now():
        return real() + shift.get()

    timezone.now = now
