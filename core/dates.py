"""Dates and times as people type them: 'heute', 'morgen', 'fr', 'nächste woche', '12.11.', '12. nov', '+3',
'in 2 wochen', and for a moment also a time after it: 'fr 14', '12.11. 9:30', 'morgen um 10 uhr'."""
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django import forms
from django.forms.utils import from_current_timezone
from django.utils import timezone
from django.utils.formats import date_format

from .text import fold

WORDS = {'heute': 0, 'morgen': 1, 'ubermorgen': 2, 'gestern': -1, 'vorgestern': -2}
WEEKDAYS = ['montag', 'dienstag', 'mittwoch', 'donnerstag', 'freitag', 'samstag', 'sonntag']
MONTHS = ['jan', 'feb', 'mar', 'apr', 'mai', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dez']


def months_before(day, months):
    """The same day `months` earlier (later if negative), the month's last where it is shorter."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, monthrange(year, month + 1)[1]))


def starting(first, last=None):
    """Filter for what starts on the local day `first`, or from it to `last`: a range of `start` itself, which its index
    serves, where start__date would convert every row first."""
    def midnight(day):
        return timezone.make_aware(datetime.combine(day, time.min))

    return {'start__gte': midnight(first), 'start__lt': midnight((last or first) + timedelta(days=1))}


def weekday(word):
    """0–6 for 'mo', 'Mi.', 'donnerstag'; None otherwise."""
    word = word.strip('.,')
    return next((n for n, name in enumerate(WEEKDAYS) if len(word) >= 2 and name.startswith(word)), None)


def day(text, today=None):
    """The date `text` means, or None. Without a year, the nearest fitting date; a bare '12.' is the next 12th."""
    today = today or timezone.localdate()
    words = fold(text).replace(',', ' ').split()
    if len(words) > 1 and weekday(words[0]) is not None and words[1][:1].isdigit():
        words = words[1:]  # a label such as 'Do 22.10.2026'
    text = ' '.join(words)
    if text in WORDS:
        return today + timedelta(days=WORDS[text])
    if found := re.fullmatch(r'(?:in |\+|(-))(\d+) ?(t|tage?n?|d|w|wochen?)?', text):
        days = int(found[2]) * (7 if (found[3] or 't').startswith('w') else 1)
        return today + timedelta(days=-days if found[1] else days)
    later = bool(words) and words[0].startswith('nachste')
    if later:
        words = words[1:]
    monday = today - timedelta(days=today.weekday())
    if later and words == ['woche']:
        return monday + timedelta(days=7)
    if len(words) == 1 and (n := weekday(words[0])) is not None:
        return monday + timedelta(days=7 + n) if later else today + timedelta(days=(n - today.weekday()) % 7)
    if found := re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', text):
        return build(int(found[1]), int(found[2]), int(found[3]))
    parts = text.replace('.', ' ').split()
    if not parts or not parts[0].isdigit() or len(parts) > 3 or (len(parts) == 1 and not text.endswith('.')):
        return None
    if len(parts) == 1:  # '12.': this month's 12th unless it has passed
        found = build(today.year, today.month, int(parts[0]))
        if found and found >= today:
            return found
        return build(today.year + today.month // 12, today.month % 12 + 1, int(parts[0]))
    month = int(parts[1]) if parts[1].isdigit() else next((n for n, name in enumerate(MONTHS, 1) if parts[1].startswith(name)), None)
    if month is None:
        return None
    if len(parts) == 3:
        if not parts[2].isdigit():
            return None
        year = int(parts[2])
        if year < 100:
            year += 2000 if year <= today.year % 100 + 10 else 1900
        return build(year, month, int(parts[0]))
    found = build(today.year, month, int(parts[0]))
    if found and found < today - timedelta(days=183):  # in January, '12.11.' is the coming November
        found = build(today.year + 1, month, int(parts[0]))
    return found


def build(year, month, number):
    try:
        return date(year, month, number)
    except ValueError:
        return None


def clock(text):
    """The time in '14', '1430', '14:30', '14.30' or '9 uhr'; None otherwise."""
    found = re.fullmatch(r'(\d{1,2})(?:[:.]?(\d{2}))?', fold(text).removesuffix('uhr').strip())
    if found and int(found[1]) < 24 and int(found[2] or 0) < 60:
        return time(int(found[1]), int(found[2] or 0))
    return None


def moment(text, today=None):
    """(date, time or None): a date as `day` reads it, then optionally a time; a time alone means today."""
    text = re.sub(r'\s+uhr\b', 'uhr', fold(text))
    words = [w for w in text.split() if w not in ('um', 'ab')]
    if not words:
        return None
    *rest, last = words
    at = clock(last)
    if at and (rest or ':' in last or 'uhr' in last or not day(last, today)):
        found = day(' '.join(rest), today) if rest else (today or timezone.localdate())
        return (found, at) if found else None
    found = day(' '.join(words), today)
    return (found, None) if found else None


def working(day):
    """The day a Frist ends that would end on `day`: after a Saturday, Sunday or public holiday the next working day
    (§ 31 Abs. 3 VwVfG)."""
    from .models import Holiday

    closed = Holiday.days(day, day + timedelta(days=14), public=True)
    while day.weekday() >= 5 or day in closed:
        day += timedelta(days=1)
    return day


class DayField(forms.DateField):
    widget = forms.DateInput(format='%d.%m.%Y', attrs={'data-day': '', 'autocomplete': 'off', 'placeholder': 'z. B. morgen, fr, 12.11.'})

    def to_python(self, value):
        if value in self.empty_values or isinstance(value, date):
            return value or None
        if found := day(value):
            return found
        raise forms.ValidationError('Datum nicht erkannt, z. B. 12.11.2026, morgen oder fr.')


class ReceivedField(DayField):
    """The day something came in, as offices type it („gestern“, „12.11.2026“): never in the future."""

    def to_python(self, value):
        if (found := super().to_python(value)) and found > timezone.localdate():
            raise forms.ValidationError('Der Eingang kann nicht in der Zukunft liegen.')
        return found


def received(text):
    """A day typed as for ReceivedField; empty, today. Raises ValidationError with the reason."""
    return ReceivedField(required=False).clean(text) or timezone.localdate()


def past(text, today=None):
    """A day that has been, as forms ask for it: digits split by dots, dashes or slashes with the year in full, or as a
    browser sends it. Refused with the reason."""
    today, text = today or timezone.localdate(), text.strip()
    if found := re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', text):
        y, m, d = (int(n) for n in found.groups())
    elif found := re.fullmatch(r'(\d{1,2})[./-](\d{1,2})[./-](\d+)', text):
        if len(found[3]) != 4:
            raise forms.ValidationError('Bitte das Jahr vierstellig, z. B. 14.06.2025.')
        d, m, y = (int(n) for n in found.groups())
    else:
        raise forms.ValidationError('Bitte Tag, Monat und Jahr in Ziffern, z. B. 14.06.2025.')
    if y < 1900:
        raise forms.ValidationError('Bitte das Jahr prüfen.')
    if not 1 <= m <= 12:
        raise forms.ValidationError(f'Einen {m}. Monat gibt es nicht.')
    if not 1 <= d <= (last := monthrange(y, m)[1]):
        raise forms.ValidationError(f'Der {date_format(date(y, m, 1), "F Y")} hat {last} Tage.')
    if (found := date(y, m, d)) > today:
        raise forms.ValidationError('Das Datum liegt in der Zukunft.')
    return found


class PastField(forms.DateField):
    """A day that has been, such as a birth date or when a certificate was gained: digits only (past)."""

    widget = forms.DateInput(format='%d.%m.%Y', attrs={'data-past': '', 'inputmode': 'numeric', 'autocomplete': 'off', 'placeholder': 'TT.MM.JJJJ'})

    def to_python(self, value):
        if value in self.empty_values or isinstance(value, date):
            return value or None
        return past(value)


class ClockField(forms.TimeField):
    widget = forms.TimeInput(format='%H:%M', attrs={'autocomplete': 'off', 'placeholder': 'z. B. 14:15', 'size': 9})

    def to_python(self, value):
        if value in self.empty_values or isinstance(value, time):
            return value or None
        if found := clock(value):
            return found
        raise forms.ValidationError('Uhrzeit nicht erkannt, z. B. 14 oder 14:30.')


class MomentField(forms.DateTimeField):
    widget = forms.DateTimeInput(format='%d.%m.%Y %H:%M', attrs={'data-moment': '', 'autocomplete': 'off', 'placeholder': 'z. B. fr 9:30 oder 12.11. 14'})

    def to_python(self, value):
        if value in self.empty_values or isinstance(value, datetime):
            return super().to_python(value)
        found = moment(value)
        if not found:
            raise forms.ValidationError('Zeitpunkt nicht erkannt, z. B. fr 9:30 oder 12.11. 14.')
        if found[1] is None:
            raise forms.ValidationError('Bitte auch die Uhrzeit angeben, z. B. „fr 9:30“.')
        return from_current_timezone(datetime.combine(*found))
