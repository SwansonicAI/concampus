from datetime import timedelta

from .dates import starting


def strip(start, selected, busy, count=21):
    """The day strip: `count` days from `start`, marking the selected day and days in `busy`."""
    return [{'date': d, 'on': d == selected, 'busy': d in busy} for d in (start + timedelta(n) for n in range(count))]


def busy(bookings, start, count=21):
    """Local dates with at least one of `bookings` within `count` days from `start`."""
    return {d.date() for d in bookings.filter(**starting(start, start + timedelta(count))).datetimes('start', 'day')}
