"""Results sheets shared by courses, exams and auditions: marks per examiner, grades, pass decisions, slots."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from core import history, texts
from curriculum.models import Rounding, rounded
from rooms.models import Booking, BookingCategory
from students.models import Document
from .models import Course, Participation, Score

SCALE = ['1,0', '1,3', '1,7', '2,0', '2,3', '2,7', '3,0', '3,3', '3,7', '4,0', '5,0']  # a single examiner's grade; means fall between
STEPS = {Decimal(n.replace(',', '.')) for n in SCALE}


class Refused(InvalidOperation):
    """A results form refused, saying why; `row` is the participation it stopped at. Nothing is saved then."""

    def __init__(self, message, row=None):
        super().__init__(message)
        self.row = row


class OffScale(Refused):
    """A grade between the steps one examiner may give."""


def number(value):
    """Decimal from a form field, None if empty. Raises InvalidOperation unless 0 ≤ n < 100."""
    if not value:
        return None
    value = Decimal(value.replace(',', '.'))
    if not (value.is_finite() and 0 <= value < 100):
        raise InvalidOperation
    return value


def grade(value, course):
    """A grade one examiner gives: in points as the course counts them, else a step of the scale (1,0 to 4,0 by 0,3, or
    5,0). Raises OffScale."""
    value = number(value)
    if value is not None and not course.points and course.kind != Course.Kind.RECOGNITION and value not in STEPS:  # a grade recognised is converted
        raise OffScale(f'{str(value).replace(".", ",")} ist keine Note: einzeln gegeben werden 1,0, 1,3, 1,7 … 4,0 und 5,0; Zwischenwerte entstehen erst als Mittel.')
    return value


def mean(values, rounding):
    """The examiners' mean, to one decimal as the candidate's Prüfungsordnung says."""
    return rounded(sum(values) / len(values), rounding) if values else None


def rounding(p):
    """How a participation's mean is cut to one decimal: as Eignungsprüfungsordnungen do for auditions, after the first
    decimal; otherwise as the candidate's Prüfungsordnung says."""
    return Rounding.CUT if p.course.kind == Course.Kind.AUDITION else p.study.program.mean_rounding


def disputed(p, values):
    """Whether a written thesis needs a third Gutachten: its two differ by more than 1,0, or one fails it (RPSO § 15 Abs. 2)."""
    return p.course.kind == Course.Kind.THESIS and len(values) == 2 and (abs(values[0] - values[1]) > 1 or max(values) > 4)


def result(p, values):
    """The panel's grade once all have marked: the mean; for a thesis with a third Gutachten that of the two better, and
    none while it waits for one."""
    if disputed(p, values):
        return None
    if p.course.kind == Course.Kind.THESIS and len(values) > 2:
        values = sorted(values)[:2]
    return mean(values, rounding(p))


def sheet(course):
    """(staff, participations). Each participation gets `marks` [(who marks in each examiner's place, value)]: the
    examiner, or the substitute of one standing back for fear of bias; `recused` {examiner pk: reason}, `substitutes`
    {examiner pk: substitute}, `vacant` (who stands back with no substitute yet), `missing` (who still has to mark),
    `reports` {examiner pk: Gutachten}, `mean` of the marks so far, `auto` (the result once every place has marked),
    `third` (a thesis waiting for a third Gutachten) and `override` (a grade set by hand instead); in an exam also
    `attempt`, which try this is, and `last`, whether the Prüfungsordnung allows no further one."""
    staff = list(course.staff.all())
    rows = list(
        course.participations.select_related('study__person', 'study__program', 'study__subject', 'module')
        .prefetch_related('scores__entered_by', 'recusals__substitute')
    )
    panel = course.kind in Course.PANEL
    for p in rows:
        p.course = course
        values = {s.examiner_id: s.value for s in p.scores.all()}
        p.reports = {s.examiner_id: s.report for s in p.scores.all()}
        p.copied = {s.examiner_id: s.entered_by for s in p.scores.all() if s.entered_by}
        recusals = {r.examiner_id: r for r in p.recusals.all()}
        p.recused = {pk: r.reason for pk, r in recusals.items()}
        p.substitutes = {pk: r.substitute for pk, r in recusals.items() if r.substitute}
        p.recusal_pks = {pk: r.pk for pk, r in recusals.items()}
        p.marks = [(e, values.get(e.pk)) for e in (p.substitutes.get(e.pk, e) for e in staff)]
        p.vacant = [e for e in staff if e.pk in p.recused and e.pk not in p.substitutes]
        p.missing = [e for e, v in p.marks if v is None and e.pk not in p.recused]
        given = [v for e, v in p.marks if v is not None and e.pk not in p.recused]
        complete = panel and given and not p.missing and not p.vacant
        p.mean = mean(given, rounding(p))
        p.third = complete and disputed(p, given)
        p.auto = result(p, given) if complete else None
        p.override = p.grade if p.grade is not None and p.grade != p.auto and not p.remark else None
    if course.kind in (Course.Kind.EXAM, Course.Kind.THESIS):
        tried = Counter(
            Participation.objects.filter(study__in=[p.study_id for p in rows], passed=False, course__released__isnull=False)
            .exclude(course=course).values_list('study', 'module')
        )
        for p in rows:
            p.attempt = tried[p.study_id, p.module_id] + 1
            p.last = p.attempt >= p.study.program.attempts
    return staff, rows


def due(p):
    """Whether a result is still to be entered: a withdrawal for good reason needs none."""
    return p.passed is None and p.remark != Participation.Remark.EXCUSED


def gaps(rows):
    """Why a sheet cannot be closed yet: whose results are missing, and whose marks for them; '' once all are in."""
    names = [
        p.study.person.last_name + (
            f' (fehlt: {", ".join([e.last_name for e in p.missing] + [f"Ersatz für {e.last_name}" for e in p.vacant])})' if p.missing or p.vacant
            else ' (drittes Gutachten nötig)' if p.third else ''
        )
        for p in rows if due(p)
    ]
    return f'Noch ohne Ergebnis: {", ".join(names[:8])}{f" und {len(names) - 8} weitere" if len(names) > 8 else ""}.' if names else ''


def off_scale(rows):
    """Why a sheet cannot be released: single grades between the scale's steps, entered before they were checked; ''
    if none."""
    found = [
        f'{p.study.person.last_name} ({e.last_name}: {f"{v:.1f}".replace(".", ",")})' for p in rows
        if not p.course.points and p.course.kind != Course.Kind.RECOGNITION for e, v in p.marks if v is not None and v not in STEPS
    ]
    return f'Keine Note der Skala: {", ".join(found)}. Bitte die Wertung berichtigen lassen.' if found else ''


def blind(rows, examiner):
    """For an examiner on the panel: until all have marked a row they see neither the others' marks nor the mean, so each
    judges on their own; once all have, the row is `locked` to them and only the office that decides changes it."""
    for p in rows:
        on = any(e == examiner for e, _ in p.marks)
        p.blind = on and bool(p.missing or p.vacant)
        p.locked = on and not p.blind


def scorers(staff, rows):
    """Everyone who marks on the sheet: the panel and the substitutes in it, for the office entering marks for them."""
    return list(dict.fromkeys([*staff, *(s for p in rows for s in p.substitutes.values())]))


def substituting(rows, person):
    """The rows a substitute marks in, the only ones they see."""
    return [p for p in rows if person in p.substitutes.values()]


def recusable(rows, examiner):
    """The rows `examiner` may still stand back from: on the panel, not yet marked by them, not declared already."""
    return [p for p in rows if (examiner, None) in p.marks and examiner.pk not in p.recused]


def waiting(rows):
    """The examiners whose marks keep results open: [(examiner, how many)]."""
    return list(Counter(e for p in rows if due(p) for e in p.missing).items())


def rule(rows):
    """How the sheet's means are cut to one decimal, in words: as the Eignungsprüfungsordnung does for auditions, else as
    the candidates' Prüfungsordnungen say."""
    found = {rounding(p) for p in rows}
    if len(found) > 1:
        return 'Das Mittel wird je nach Prüfungsordnung auf eine Nachkommastelle gerundet oder nach ihr abgeschnitten.'
    if rows and rows[0].course.kind == Course.Kind.AUDITION:
        return 'Das Mittel wird nach der ersten Nachkommastelle abgeschnitten, ohne Rundung (Eignungsprüfungsordnung § 6 Abs. 4).'
    return 'Das Mittel wird nach der ersten Nachkommastelle abgeschnitten, ohne Rundung.' if Rounding.CUT in found else 'Das Mittel wird auf eine Nachkommastelle kaufmännisch gerundet (RPSO § 15 Abs. 2).'


def retype(rows, post, error):
    """After a refused form: what was typed back in its fields, and the reason by the row it stopped at."""
    for p in rows:
        p.marks = [(e, post.get(f'score-{p.pk}-{e.pk}', v)) for e, v in p.marks]
        p.reports = {**p.reports, **{e.pk: post[f'report-{p.pk}-{e.pk}'] for e, _ in p.marks if f'report-{p.pk}-{e.pk}' in post}}
        p.override, p.reason = post.get(f'grade-{p.pk}', p.override), post.get(f'reason-{p.pk}', p.reason)
        if p.course.kind not in Course.PANEL:
            p.grade = post.get(f'grade-{p.pk}', p.grade)
        p.error = str(error) if p.pk == error.row else ''


def marks(post, p, columns, decide, correct, by=None):
    """The scores a results form makes of one panel row: (new, changed, cleared pks). Sets the row's grade and result.
    A mark `by` someone else than its examiner is copied from the Protokoll, which an exam needs uploaded first."""
    new, changed, cleared = [], [], []
    scores = {s.examiner_id: s for s in p.scores.all()}
    values = {e.pk: v for e, v in p.marks}
    for e in columns:  # only what changed, so the history shows who changed which mark
        key = f'score-{p.pk}-{e.pk}'
        if key not in post or e.pk not in values or e.pk in p.recused:  # whoever stands back, or marks elsewhere, sends nothing here
            continue
        score, value, report = scores.get(e.pk), grade(post[key], p.course), post.get(f'report-{p.pk}-{e.pk}')
        if not decide and not p.missing and not p.vacant and value != values[e.pk]:
            raise Refused('Alle haben gewertet, die Wertungen sind gesperrt. Ändern kann sie jetzt nur das zuständige Amt.')
        if p.course.kind == Course.Kind.THESIS and value is not None and not (report if report is not None else score.report if score else '').strip():
            raise Refused('Zur Note gehört das schriftliche Gutachten.')
        if value is None and (report or '').strip():
            raise Refused('Bitte zum Gutachten die Note wählen.')
        copied = by if by and by.pk != e.pk and value != values[e.pk] else None
        if copied and not correct and p.course.kind == Course.Kind.EXAM and not p.course.protocol_received:
            raise Refused('Erst den Eingang des unterschriebenen Protokolls verbuchen; danach lassen sich die Wertungen der Prüfenden übertragen.')
        values[e.pk] = value
        if score is None and value is not None:
            new.append(Score(participation=p, examiner=e, value=value, report=report or '', entered_by=copied))
        elif score and value is None:
            cleared.append(score.pk)
        elif score and (score.value != value or report is not None and score.report != report):
            score.value, score.report, score.entered_by = value, score.report if report is None else report, copied
            changed.append(score)
    given = [v for pk, v in values.items() if pk not in p.recused]
    if p.course.max_points is not None and any(v > p.course.max_points for v in given if v is not None):
        raise InvalidOperation
    auto = result(p, given) if given and None not in given and not p.vacant else None
    override = number(post.get(f'grade-{p.pk}')) if correct else p.override
    if correct:  # a grade other than the panel's says why
        p.reason = post.get(f'reason-{p.pk}', '').strip()[:200] if override is not None else ''
        if override is not None and override != auto and not p.reason:
            raise Refused('Eine abweichende Note braucht eine Begründung.')
    p.grade = override if override is not None else auto
    p.passed = p.course.passes(p.grade) if p.grade is not None else None
    if decide:
        remark = post.get(f'remark-{p.pk}', '')
        if remark == Participation.Remark.EXCUSED != p.remark and not Document.objects.filter(study=p.study_id, kind='attest', created__gte=p.created).exists():
            raise Refused('Ein Rücktritt braucht das Attest: über „Rücktritt mit Attest“ verbuchen.')
        p.remark = remark if remark in Participation.Remark.values else ''
    if p.remark == Participation.Remark.EXCUSED:
        p.grade = p.passed = None
    elif p.remark:
        p.grade, p.passed = None if p.course.points else Decimal('5.0'), False
    return new, changed, cleared


def save(post, rows, columns, decide, correct=False, by=None):
    """Apply a results form. Raises Refused, with the row it stopped at, on a malformed number or a change to locked
    marks; nothing is saved then. Fields the form did not send stay as they are.

    Panels: examiners enter marks for themselves (`columns`), for a thesis with their Gutachten, none for whom they
    stand back from; with `decide`, the office enters them from the Protokolle for every column, and a Vermerk:
    absent or cheating fails, a withdrawal for good reason leaves no result. The result follows once all have marked
    (`result`), passing from the course's rule. Without `decide`, marks are locked once all have marked. With
    `correct`, a grade entered by hand overrides the result, with its reason.
    Courses: whoever decides enters a grade, or only passed / not passed for ungraded work.
    """
    new, changed, cleared = [], [], []
    for p in rows:
        try:
            if p.course.kind in Course.PANEL:
                found = marks(post, p, columns, decide, correct, by)
                new, changed, cleared = new + found[0], changed + found[1], cleared + found[2]
            elif decide and f'grade-{p.pk}' in post:
                p.grade = grade(post[f'grade-{p.pk}'], p.course)
                choice = {'1': True, '0': False}.get(post.get(f'passed-{p.pk}'))
                p.passed = p.course.passes(p.grade) if p.grade is not None else choice
        except Refused as error:
            error.row = p.pk
            raise
        except InvalidOperation as error:
            raise Refused(f'Bitte nur Punkte zwischen 0 und {p.course.max_points} eintragen.' if p.course.points else 'Bitte eine Note wie 1,3 oder 2,7 eintragen.', p.pk) from error
    with transaction.atomic():
        Participation.objects.bulk_update(rows, ['grade', 'passed', 'remark', 'reason'])
        Score.objects.filter(pk__in=cleared).delete()
        Score.objects.bulk_create(new)
        Score.objects.bulk_update(changed, ['value', 'report', 'entered_by'])
        history.saved([*rows, *new, *changed])


def schedule(course, rows, start, minutes, room, person, pause=0):
    """Give each row a slot of `minutes`, one after another from `start` with `pause` between, and invite everyone. The
    room is booked for the block; where the course has booked it already, that booking holds the block, lengthened if needed.

    Raises ValidationError if the room is taken.
    """
    category = BookingCategory.system('eignungspruefung' if course.kind == course.Kind.AUDITION else 'pruefung')
    end = start + timedelta(minutes=(minutes + pause) * len(rows) - pause)
    booking = course.bookings.filter(room=room, start__lte=start, end__gt=start).first() or Booking(room=room, start=start, person=person, course=course, category=category)
    if booking.end and booking.end < end and booking.clashes().filter(start__lt=end).exists():
        raise ValidationError(f'Der Raum ist bis {timezone.localtime(booking.end):%H:%M} gebucht und danach belegt; {len(rows)} Termine brauchen ihn bis {timezone.localtime(end):%H:%M}.')
    booking.end = max(booking.end or end, end)
    booking.full_clean()
    for n, p in enumerate(rows):
        p.slot = start + timedelta(minutes=(minutes + pause) * n)
    with transaction.atomic():
        booking.save()
        Participation.objects.bulk_update(rows, ['slot'])
        history.saved(rows)
    if course.kind == course.Kind.AUDITION:  # applicants get a letter (admissions.views.invite)
        return
    for p in rows:
        texts.send('einladung', p.study.person, reverse('exams:index'), pruefung=course.title, termin=date_format(timezone.localtime(p.slot), 'l, j. F Y, G:i'), raum=f'{room} ({room.building})')
