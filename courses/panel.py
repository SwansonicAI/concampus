"""Results sheets shared by courses, exams and auditions: marks per examiner, grades, pass decisions, slots."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core import history
from core.models import Message
from curriculum.models import rounded
from rooms.models import Booking, BookingCategory
from .models import Course, Participation, Score

def number(value):
    """Decimal from a form field, None if empty. Raises InvalidOperation unless 0 ≤ n < 100."""
    if not value:
        return None
    value = Decimal(value.replace(',', '.'))
    if not (value.is_finite() and 0 <= value < 100):
        raise InvalidOperation
    return value


def mean(values, rounding):
    """The examiners' mean, to one decimal as the candidate's Prüfungsordnung says."""
    return rounded(sum(values) / len(values), rounding) if values else None


def sheet(course):
    """(staff, participations). Each participation gets `marks` [(examiner, value)], `mean` of the marks so far,
    `auto` (the mean once every examiner has marked) and `override` (a grade set by hand instead)."""
    staff = list(course.staff.all())
    rows = list(
        course.participations.select_related('study__person', 'study__program', 'study__subject', 'module')
        .prefetch_related('scores')
    )
    panel = course.kind in Course.PANEL
    for p in rows:
        values = {s.examiner_id: s.value for s in p.scores.all()}
        p.marks = [(e, values.get(e.pk)) for e in staff]
        p.mean = mean(list(values.values()), p.study.program.mean_rounding)
        p.auto = p.mean if panel and staff and len(values) == len(staff) else None
        p.override = p.grade if p.grade is not None and p.grade != p.auto else None
    return staff, rows


def save(post, rows, columns, decide):
    """Apply a results form. Raises InvalidOperation on a malformed number; nothing is saved then.

    Panels: examiners enter marks for themselves (`columns`); the result follows: once all have marked, the grade
    is the mean and passing follows from the course's rule. With `decide`, a grade entered by hand overrides it.
    Courses: whoever decides enters a grade, or only passed / not passed for ungraded work.
    """
    new, changed, cleared = [], [], []
    for p in rows:
        if p.course.kind in Course.PANEL:
            marks = {e.pk: value for e, value in p.marks}
            marks.update({e.pk: number(post.get(f'score-{p.pk}-{e.pk}')) for e in columns})
            scores = {s.examiner_id: s for s in p.scores.all()}
            for e in columns:  # only what changed, so the history shows who changed which mark
                score, value = scores.get(e.pk), marks[e.pk]
                if score is None and value is not None:
                    new.append(Score(participation=p, examiner=e, value=value))
                elif score and value is None:
                    cleared.append(score.pk)
                elif score and score.value != value:
                    score.value = value
                    changed.append(score)
            values = [v for v in marks.values() if v is not None]
            if p.course.max_points is not None and any(v > p.course.max_points for v in values):
                raise InvalidOperation
            auto = mean(values, p.study.program.mean_rounding) if marks and len(values) == len(marks) else None
            override = number(post.get(f'grade-{p.pk}')) if decide else p.override
            p.grade = override if override is not None else auto
            p.passed = p.course.passes(p.grade) if p.grade is not None else None
        elif decide:
            p.grade = number(post.get(f'grade-{p.pk}'))
            choice = {'1': True, '0': False}.get(post.get(f'passed-{p.pk}'))
            p.passed = p.course.passes(p.grade) if p.grade is not None else choice
    with transaction.atomic():
        Participation.objects.bulk_update(rows, ['grade', 'passed'])
        Score.objects.filter(pk__in=cleared).delete()
        Score.objects.bulk_create(new)
        Score.objects.bulk_update(changed, ['value'])
        history.saved([*rows, *new, *changed])


def schedule(course, rows, start, minutes, room, person):
    """Give each row a slot of `minutes`, one after another from `start`, book `room` for the block and invite everyone.

    Raises ValidationError if the room is taken.
    """
    category = BookingCategory.system('eignungspruefung' if course.kind == course.Kind.AUDITION else 'pruefung')
    booking = Booking(room=room, start=start, end=start + timedelta(minutes=minutes * len(rows)), person=person, course=course, category=category)
    booking.full_clean()
    for n, p in enumerate(rows):
        p.slot = start + timedelta(minutes=minutes * n)
    with transaction.atomic():
        booking.save()
        Participation.objects.bulk_update(rows, ['slot'])
        history.saved(rows)
    for p in rows:
        Message.send(
            p.study.person, f'Einladung: {course.title}',
            f'Ihr Termin: {timezone.localtime(p.slot):%d.%m.%Y, %H:%M} Uhr, {room} ({room.building}).\n\n'
            'Bitte erscheinen Sie 30 Minuten vorher. Einspielräume stehen bereit.',
            reverse('admissions:mine') if course.kind == course.Kind.AUDITION else reverse('exams:index'),
        )
