"""The Eignungsprüfung as its juries run it: appointments, stages, results, Nachrücken."""
import re
from datetime import date, datetime, time, timedelta
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import history, texts
from core.dates import MomentField
from core.access import require
from core.apps import plural
from core.models import Entry, Institution, Message
from core.text import untimed
from core.shortcuts import preview
from courses import panel
from courses.forms import DateForm, ShiftForm, SlotForm, substitutes
from courses.models import Correction, Course, Participation, Score
from rooms.models import Booking, BookingCategory, Option
from students.models import Study
from ..auditions import after, booked, held, invite, need, next_stages, origin, unmarked
from ..papers import Condition, checklist, judged, shown
from .board import DECISIONS, offer, vacancies


@require('teacher')
def juries(request):
    auditions = (
        Course.objects.filter(Q(staff=request.user) | Q(participations__recusals__substitute=request.user), kind=Course.Kind.AUDITION).distinct()
        .select_related('term', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .order_by('-term__start', F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    due = {course.pk: n for course, n, _, _ in unmarked(request.user)}
    for c in auditions:
        c.mine = due.get(c.pk, 0)
    # what is the person's to mark now first, the one due first first; then what is still to come; what is closed last
    auditions = sorted(auditions, key=lambda c: (not c.mine, bool(c.released), c.mine and c.marks_by or date.max))
    return render(request, 'admissions/juries.html', {'auditions': auditions})


def above(person, course, member):
    """The way down to a stage in the menu: under one's own commissions; for the office that runs it, under the
    semester's Eignungsprüfungen and the Hauptfach's it belongs to; else under Bewerbungen."""
    if member:
        return [(None, reverse('admissions:juries'))]
    if not person.may('run_auditions', course):
        return [(None, reverse('admissions:board'))]
    way = [(None, f"{reverse('admissions:auditions')}?semester={course.term_id}")]
    if first := origin(course):
        way.append((f'{first.subject or first.title} {first.program.degree}', reverse('admissions:intake', args=[first.pk])))
    return way


def programme(note):
    """An applicant's programme, a work a line: [(second it starts in the video or None, that time as written, work)]."""
    found = []
    for line in filter(None, map(str.strip, note.splitlines())):
        at = re.match(r'(\d{1,2}):([0-5]\d)\s+(.+)', line)
        found.append((int(at[1]) * 60 + int(at[2]), f'{at[1]}:{at[2]}', at[3]) if at else (None, '', line))
    return found


def audition(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'program', 'subject'), pk=pk, kind=Course.Kind.AUDITION)
    office, correct = person.may('run_auditions', course), person.may('correct_scores', course)
    member = course.staff.filter(pk=person.pk).exists()
    juror = member or course.participations.filter(recusals__substitute=person).exists()
    if not (office or correct or juror):
        raise PermissionDenied('Eine Eignungsprüfung sehen ihre Kommission, das Amt, das sie durchführt, und der Prüfungsausschuss.')

    def sheet():  # a substitute sees only whom they mark
        staff, rows = panel.sheet(course)
        return staff, rows if office or correct or member else panel.substituting(rows, person)

    staff, rows = sheet()
    # Who enters is the stage's own setting: each member their own, a video round while watching, in the Sichtung; or the
    # chair, or the office from the Protokoll, for the whole commission; whatever it is, once the stage has begun. The
    # Prüfungsausschuss corrects any of it.
    begun = course.participations.filter(slot__lte=timezone.now()).exists()
    transcribing = course.entry == Entry.CHAIR and course.chair_id == person.pk or course.entry == Entry.OFFICE and office
    if correct:
        columns = panel.scorers(staff, rows)
    elif not (course.video or begun):
        columns = []
    elif transcribing:
        columns = panel.scorers(staff, rows)
    else:
        columns = [person] if juror and course.entry == Entry.MEMBERS and not course.video else []
    drafting, refused = not (correct or transcribing), None  # a member's own marks stay drafts until handed in
    if request.method == 'POST':
        if course.released or not columns:
            raise PermissionDenied('Wertungen tragen die Kommission und, berichtigend, der Prüfungsausschuss ein.')
        try:
            panel.save(request.POST, rows, columns, decide=correct or transcribing, correct=correct, by=person if transcribing else None, draft=drafting)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Als Entwurf gespeichert; es zählt, sobald Sie abgeben.' if drafting else 'Wertungen gespeichert.')
            return redirect(course)
    panel.blind(rows, person, office=office or correct)
    if refused:
        panel.retype(rows, request.POST, refused)
    invited = set(Participation.objects.filter(course__in=next_stages(course)).values_list('study', flat=True)) if office else set()
    left, drafts = (0, 0) if office or correct else next(((n, d) for c, n, d, _ in unmarked(person) if c.pk == course.pk), (0, 0))
    rankable = not any(getattr(p, 'blind', False) for p in rows)  # a ranking would tell the others' marks
    plan = need(course) if office and not course.video and not course.released else None
    days = list(course.bookings.select_related('room').order_by('start'))
    hours = booked(course)
    spare = next((b for b in reversed(days) if not any(p.slot and b.start <= p.slot < b.end for p in rows)), None) if plan and plan.basis is None and len(days) > 1 else None
    if spare and hours - (spare.end - spare.start).total_seconds() / 3600 < plan.hours:  # a day the invited do not need
        spare = None
    stages = list(next_stages(course)) if office and rows else []  # nobody in the stage yet: nothing to pass on
    waiting = sum(1 for p in rows if p.slot is None and p.study.status == Study.Status.APPLIED)
    scheduled = list(course.participations.filter(slot__isnull=False).select_related('study__person').order_by('slot')) if office and not course.released else []
    if request.GET.get('sortierung') == 'punkte' and rankable:
        rows.sort(key=lambda p: p.grade if p.grade is not None else (p.mean or 0), reverse=True)
    return render(request, 'admissions/audition.html', {
        'trail': above(person, course, member), 'course': course,
        'staff': staff,
        'rows': rows, 'timed': any(p.slot for p in rows), 'noted': any(p.note for p in rows),
        'columns': [e.pk for e in columns],
        'office': office, 'correct': correct, 'rule': panel.rule(rows), 'composer': office, 'short': course.panel().shortfall,
        'jury': not (office or correct),  # sees what marking needs, not how the office runs it
        'preview': not (office or correct or course.video or begun),
        'rankable': rankable and any(p.grade is not None or p.mean is not None for p in rows),
        'transcribing': transcribing,
        'unmarked': left - drafts, 'drafts': drafts,  # one's own: not marked at all, marked as drafts not handed in
        'sighting': 'Videos sichten' if course.video and course.participations.filter(study__documents__kind__condition=Condition.VIDEO).exists() else 'Mappen sichten',
        'retractable': not course.released and any(p.handed and (p.missing or p.vacant or p.unseen) for p in rows),  # handed in, not yet seen by all
        'corrections': Correction.objects.filter(participation__course=course, accepted=None).select_related('participation__study__person', 'examiner') if correct else [],
        'candidates': substitutes(course) if office and any(p.vacant for p in rows) else [],
        'history': bool(Course.seen_by(person, course.pk)),
        'slots': SlotForm(person=person, course=course) if office else None,
        'scheduled': scheduled,
        'side': office and (stages or not course.released and not course.video and waiting),  # the office's panels beside the facts
        'fixing': fixing(person, course, plan=plan) if office and not course.released and not course.video else None,
        'stages': stages, 'waiting': waiting,
        'ready': sum(1 for p in rows if p.passed and p.study.status == Study.Status.APPLIED and p.study_id not in invited),  # not yet at the next stage
        'missing': sum(1 for p in rows if panel.due(p)),
        'bookings': days, 'plan': plan, 'booked': hours, 'lacking': plan and plan.hours - hours, 'spare': spare,
        'poll': course.polls.filter(chosen=None).annotate(answered=Count('options__answers__person', distinct=True), size=Count('people', distinct=True)).first(),
        'shifting': ShiftForm(person=person, course=course) if office and not course.released and not course.video and course.bookings.exists() else None,
        'reachable': Study.reachable(person, Study.objects.filter(participations__course=course)),
    })


@require('run_auditions')
@require_POST
def plan(request, pk):
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    form = SlotForm(request.POST, person=request.user, course=course)
    rows = list(course.participations.filter(slot=None, study__status=Study.Status.APPLIED).select_related('study__person', 'study__program', 'study__subject').order_by('created'))
    if not (form.is_valid() and rows):
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben.' if rows else 'Alle Bewerbenden haben schon einen Termin.')
        return redirect(course)
    data, days = form.cleaned_data, list(course.bookings.select_related('room').order_by('start'))
    filling = 'start' not in data  # the days are booked: the times fill them one after another
    if filling:
        try:
            times = panel.fill(days, len(rows), data['minutes'], data['pause'], course.participations.exclude(slot=None).values_list('slot', flat=True))
        except ValidationError as error:
            messages.error(request, f'{" ".join(error.messages)} Einen Tag hinzufügen oder kürzere Termine?')
            return redirect(course)
    else:
        times = [data['start'] + timedelta(minutes=data['minutes'] + data['pause']) * n for n in range(len(rows))]
    if not request.POST.get('bestaetigt'):  # the times first, a day each with its end, then the letters
        times = [timezone.localtime(t) for t in times]
        rooms = {t: next((b.room for b in days if b.start <= t < b.end), None) or data.get('room') for t in times}
        letter = rows[0].study.wording(
            'einladung_eignungspruefung', pruefung=course.title, termin=date_format(times[0], 'l, j. F Y, H:i'), raum=rooms[times[0]].place(linked=True),
            programm=rows[0].note or '–', danach='\n'.join(f'– {line}' for line in after(course)) or '–',
        )
        notes = [
            f'{date_format(day, "l, j. F")}, {rooms[group[0][0]]}: {group[0][0]:%H:%M} bis {group[-1][0] + timedelta(minutes=data["minutes"]):%H:%M} Uhr · '
            + ', '.join(f'{t:%H:%M} {p.study.person.name}' for t, p in group)
            for day, group in ((d, list(g)) for d, g in groupby(zip(times, rows), key=lambda tp: tp[0].date()))
        ] + ['Einzelne Termine lassen sich danach verlegen, mit neuer Einladung.']
        return preview(request, f'Termine: {course.title}', [('Einladung', [p.study.person for p in rows], letter)], f'{plural(len(rows), "Termin", "Termine")} vergeben und einladen', course.get_absolute_url(), notes)
    try:
        if filling:
            for p, t in zip(rows, times):
                p.slot = t
            with transaction.atomic():
                Participation.objects.bulk_update(rows, ['slot'])
                history.saved(rows)
        else:
            panel.schedule(course, rows, data['start'], data['minutes'], data['room'], request.user, data['pause'])
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect(course)
    for p in rows:
        invite(p)
    messages.success(request, f'{len(rows)} Termine vergeben, Einladungen versandt.')
    return redirect(course)


@require('run_auditions')
@require_POST
def move(request, pk):
    """Give one applicant another time, with a new invitation; not one another holds."""
    p = get_object_or_404(Participation.objects.select_related('course', 'study__person'), pk=request.POST.get('participation'), course=pk, slot__isnull=False, course__released=None)
    if not request.user.may('run_auditions', p.course):
        raise PermissionDenied
    try:
        slot = MomentField().clean(request.POST.get('slot', ''))
    except ValidationError as error:
        messages.error(request, f'Neuer Termin: {error.messages[0]}')
        return redirect(p.course)
    if p.course.participations.filter(slot=slot).exclude(pk=p.pk).exists():
        messages.error(request, f'Um {timezone.localtime(slot):%H:%M} hat schon jemand einen Termin.')
        return redirect(p.course)
    p.slot = slot
    p.save(update_fields=['slot'])
    invite(p)
    messages.success(request, f'{p.study.person} verlegt auf {date_format(timezone.localtime(slot), "j.n.Y, H:i")}; neue Einladung versandt.' + ('' if held(p)[2] else ' Für diese Zeit ist noch kein Raum gebucht.'))
    return redirect(p.course)


@require('run_auditions')
@require_POST
def shift(request, pk):
    """Move a day of the stage to another, its room and everyone's time at the same hours: first the new invitations
    to look at, then they go out and the commission hears of it."""
    course = get_object_or_404(request.user.within(Course.objects.filter(released=None), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    form = ShiftForm(request.POST, person=request.user, course=course)
    if not form.is_valid():
        messages.error(request, 'Tag verlegen: ' + ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect(course)
    day, to, room = form.cleaned_data['day'], form.cleaned_data['to'], form.cleaned_data['room']
    confirmed = request.POST.get('bestaetigt')
    try:
        bookings, rows = panel.shift(course, day, to, room, save=bool(confirmed))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect(course)
    where = ', '.join(dict.fromkeys(str(b.room) for b in bookings))
    if not confirmed:
        notes = [f'Vom {date_format(day, "l, j. F")} auf {date_format(to, "l, j. F Y")}, zur selben Zeit, {where}.', f'Die Kommission erfährt es: {", ".join(p.last_name for p in course.staff.all())}.']
        groups = []
        if rows:
            letter = rows[0].study.wording(
                'einladung_eignungspruefung', pruefung=course.title, termin=date_format(timezone.localtime(rows[0].slot), 'l, j. F Y, H:i'),
                raum=bookings[0].room.place(linked=True) if bookings else 'wird mitgeteilt', programm=rows[0].note or '–', danach='\n'.join(f'– {line}' for line in after(course)) or '–',
            )
            groups = [('Neue Einladung', [p.study.person for p in rows], letter)]
        return preview(request, f'Tag verlegen: {course.title}', groups, f'Verlegen{" und neu einladen" if rows else ""}', course.get_absolute_url(), notes)
    for p in rows:
        invite(p)
    for person in course.staff.all():
        Message.send(person, f'Tag verlegt: {course.title}', f'Vom {date_format(day, "l, j. F")} auf {date_format(to, "l, j. F Y")}, zur selben Zeit, {where}.', course.get_absolute_url())
    messages.success(request, f'Verlegt auf {date_format(to, "l, j. F Y")}, {where}; {plural(len(rows), "neue Einladung", "neue Einladungen")}, die Kommission ist benachrichtigt.')
    return redirect(course)


def fixing(person, course, data=None, plan=None):
    """The form to set a day of the stage outright: from 9 to 17, or to 13 where a morning will do, in the hall such
    auditions were last held in."""
    form = DateForm(data, person=person, initial={'begin': time(9), 'until': time(13 if plan and plan.hours - booked(course) <= 4 else 17)})
    lately = Booking.objects.filter(category='eignungspruefung').order_by('-created').values_list('room', flat=True)[:40]
    form.fields['room'].recent = list(dict.fromkeys(lately))[:5]
    form.fields['room'].initial = next(iter(form.fields['room'].recent), None)
    return form


@require('run_auditions')
@require_POST
def book(request, pk):
    """Set a day of the stage outright: its room booked and the commission told. A Terminumfrage running for the stage
    ends with this day as its own, and all it asked hear of it."""
    course = get_object_or_404(request.user.within(Course.objects.filter(released=None, video=False), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    form = fixing(request.user, course, request.POST)
    if not form.is_valid():
        messages.error(request, 'Tag festlegen: ' + ' '.join(e for errors in form.errors.values() for e in errors))
        return redirect(course)
    data = form.cleaned_data
    start, end = (timezone.make_aware(datetime.combine(data['day'], data[t])) for t in ('begin', 'until'))
    booking = Booking(room=data['room'], start=start, end=end, person=request.user, course=course, category=BookingCategory.system('eignungspruefung'))
    try:
        booking.full_clean()
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect(course)
    poll = course.polls.filter(chosen=None).first()
    with transaction.atomic():
        booking.save()
        if poll:
            poll.chosen = poll.options.filter(start=start, end=end).first() or Option.objects.create(poll=poll, start=start, end=end)
            poll.booking = booking
            poll.save()
    when = f'{date_format(timezone.localtime(start), "l, j. F Y")}, {timezone.localtime(start):%H:%M}–{timezone.localtime(end):%H:%M} Uhr, {data["room"]} ({data["room"].building})'
    for person in (poll.people if poll else course.staff).exclude(pk=request.user.pk):
        Message.send(person, f'Termin: {course.title}', f'{when}.' + (' Die Terminumfrage ist damit beendet.' if poll else ''), course.get_absolute_url())
    messages.success(request, f'Festgelegt: {when}. Die Kommission ist benachrichtigt' + ('; die Terminumfrage ist beendet.' if poll else '.'))
    return redirect(course)


@require('run_auditions')
@require_POST
def release(request, pk, booking):
    """Give back a day booked for the stage that the invited do not need: the room is free again, the commission is told."""
    course = get_object_or_404(request.user.within(Course.objects.filter(released=None), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    day = get_object_or_404(course.bookings, pk=booking)
    if course.participations.filter(slot__gte=day.start, slot__lt=day.end).exists():
        messages.error(request, 'An diesem Tag haben schon Bewerbende einen Termin.')
        return redirect(course)
    when = f'{date_format(timezone.localtime(day.start), "l, j. F Y")}, {day.room}'
    day.delete()
    for person in course.staff.all():
        Message.send(person, f'Tag entfällt: {course.title}', f'{when} wird nicht gebraucht.', course.get_absolute_url())
    messages.success(request, f'{when} freigegeben; die Kommission ist benachrichtigt.')
    return redirect(course)


@require('run_auditions')
@require_POST
def advance(request, pk):
    """Invite everyone who passed this stage to the next one, once it is closed and nobody's result can change."""
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    if not course.released:
        messages.error(request, 'Erst wenn die Stufe abgeschlossen ist: bis dahin kann sich jedes Ergebnis noch ändern.')
        return redirect(course)
    targets = list(next_stages(course))
    names = ' und '.join(f'„{t.title}“' for t in targets)
    moved = set()
    for p in course.participations.filter(passed=True, study__status=Study.Status.APPLIED).select_related('study__person'):
        for target in targets:  # the programme goes along, without the times in the video
            if Participation.objects.get_or_create(course=target, study=p.study, defaults={'note': untimed(p.note)})[1]:
                moved.add(p.study)
    for study in moved:
        texts.send('naechste_stufe', study.person, reverse('admissions:mine'), pruefung=course.title, stufen=names)
    messages.success(request, f'{len(moved)} Bewerbende zu {names} eingeladen.')
    return redirect(course)


@require('run_auditions')
@require_POST
def close(request, pk):
    """Lock the marks once every applicant has a result: applicants see a closed stage as passed or not."""
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions', 'correct_scores'), pk=pk, kind=Course.Kind.AUDITION)
    if course.released and not request.user.may('correct_scores', course):
        raise PermissionDenied('Abgeschlossene Wertungen öffnet nur der Prüfungsausschuss wieder.')
    if not course.released and (missing := panel.gaps(panel.sheet(course)[1])):
        messages.error(request, missing)
        return redirect(course)
    course.released = None if course.released else timezone.now()
    course.save(update_fields=['released'])
    messages.success(request, 'Wertungen gesperrt.' if course.released else 'Wertungen wieder geöffnet.')
    return redirect(course)


@require('decide_applications')
@require_POST
def move_up(request, pk):
    """Nachrücken: offers not accepted in time lapse, and the free places go to the best on the Nachrückliste."""
    intake = get_object_or_404(request.user.within(Course.objects.all(), 'decide_applications'), pk=pk, kind=Course.Kind.AUDITION, program__isnull=False)
    gone, waiting = next(((l, w) for c, l, w in vacancies(intake.term, request.user) if c == intake), ([], []))
    back, today = f"{reverse('admissions:board')}?semester={intake.term_id}", timezone.localdate()
    until = date_format(today + timedelta(days=Institution.get().accept_days), 'j.n.Y')
    if waiting and not request.POST.get('bestaetigt'):
        notes = [f'Das {intake.term} hat am {date_format(intake.term.start, "j.n.Y")} begonnen: wer jetzt nachrückt, beginnt verspätet.'] * (intake.term.start <= today)
        notes += [f'Ohne Annahme verfallen: {", ".join(s.person.name for s in gone)}.'] * bool(gone)
        people = [f'{s.person.name} · {s.subject} · {s.program.degree}' for s in waiting]
        wording = waiting[0].wording(DECISIONS[Study.Status.ADMITTED], frist=until)
        return preview(request, f'Nachrücken: {intake.title}', [(f'Studienplatzangebot, Annahme bis {until}', people, wording)], 'Nachrücken und Bescheide versenden', back, notes)
    for study in gone:
        study.set_status(Study.Status.LEFT)
    for study in waiting:
        offer(study, Study.Status.ADMITTED)
    messages.success(request, f'Von der Nachrückliste zugelassen, Annahme bis {until}: {", ".join(s.person.name for s in waiting)}. Bescheide versandt.' if waiting else 'Abgelaufene Zulassungen geschlossen.')
    return redirect(back)


def review(request, pk):
    """Sichtung: an online stage one application at a time, its video or its Mappe. Watch or read, mark, on to the next
    without a mark of one's own; the marks stay drafts until handed in, all at once (hand_in)."""
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject', 'program'), pk=pk, kind=Course.Kind.AUDITION, video=True)
    member = course.staff.filter(pk=person.pk).exists()
    juror = member or course.participations.filter(recusals__substitute=person).exists()
    if not (juror or person.may('run_auditions', course)):
        raise PermissionDenied('Die Videos sichten die Kommission und das Amt, das die Eignungsprüfung durchführt.')
    _, everyone = panel.sheet(course)
    if juror and not member:  # a substitute watches only whom they mark
        everyone = panel.substituting(everyone, person)
    handed_in = set(judged(course.participations.all()).values_list('pk', flat=True))
    rows = [p for p in everyone if p.pk in handed_in]  # nothing to judge yet: not in the jury's way
    if not rows:
        return redirect(course)
    panel.blind(rows, person)
    marking = juror and course.entry == Entry.MEMBERS  # where the chair or the office enters, the Sichtung is for watching
    mine = {p.pk: dict((e.pk, v) for e, v in p.marks).get(person.pk) for p in rows}
    todo = {p.pk for p in rows if mine[p.pk] is None and person.pk not in p.recused}
    drafts = sum(1 for p in rows if person.pk in p.drafted)
    trail = [*above(person, course, member), (course.title, course.get_absolute_url())]
    if marking and not todo and not request.GET.get('bewerbung'):  # all marked: hand them in, then what is next
        following = next(((c, n, url) for c, n, _, url in unmarked(person) if c.pk != course.pk), None)
        return render(request, 'admissions/marked.html', {'trail': trail, 'course': course, 'count': len(rows), 'drafts': drafts, 'following': following})
    picked = request.GET.get('bewerbung', '')
    current = next((p for p in rows if str(p.pk) == picked), None) or next((p for p in rows if p.pk in todo), rows[0])
    at, error = rows.index(current), ''
    here = reverse('admissions:review', args=[course.pk])
    if request.method == 'POST':
        if course.released or not marking:
            raise PermissionDenied
        try:
            panel.save(request.POST, [current], [person], decide=False, draft=True)
        except panel.Refused as refused:  # shown by the field, with what was typed
            error, mine[current.pk] = str(refused), request.POST.get(f'score-{current.pk}-{person.pk}', '')
        else:
            following = next((p for p in rows[at + 1:] + rows[:at] if p.pk in todo - {current.pk}), None)
            return redirect(f'{here}?bewerbung={following.pk}' if following else here)
    papers = shown(checklist(current.study))
    return render(request, 'admissions/review.html', {
        'trail': trail, 'course': course,
        'p': current, 'error': error, 'programme': programme(current.note),
        'juror': juror, 'marking': marking, 'vote': [('1', 'Ja'), ('0', 'Nein')],
        'mine': mine[current.pk],
        'videos': current.study.documents.filter(kind__condition=Condition.VIDEO),
        'mappe': [d for d in papers if not d.kind.shared],  # what an online stage without a video judges
        'papers': [d for d in papers if d.kind.shared],  # read beside it, such as the CV
        'position': everyone.index(current) + 1,  # numbered as on the list of all applications
        'count': len(everyone), 'drafts': drafts,
        'asked': Correction.objects.filter(participation=current, examiner=person, accepted=None).exists(),
        'scored': current.pk not in todo,  # the wished-for teacher only after one's own score, not to sway it
        'prev': rows[at - 1] if at else None,
        'next': rows[at + 1] if at + 1 < len(rows) else None,
    })


def back(request, course):
    """Where a form about the course returns to: the page it was sent from, else the course."""
    url = request.POST.get('next', '')
    return redirect(url if url_has_allowed_host_and_scheme(url, {request.get_host()}) else course.get_absolute_url())


def marker(request, pk):
    """The audition stage the person marks in, open; else refused."""
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.AUDITION, released=None)
    if not (course.staff.filter(pk=request.user.pk).exists() or course.participations.filter(recusals__substitute=request.user).exists()):
        raise PermissionDenied
    return course


@require_POST
def hand_in(request, pk):
    """Hand in one's drafts on a stage, all at once: from then on they count; once all have, the others see them."""
    course = marker(request, pk)
    n = panel.hand_in(course, request.user)
    messages.success(request, f'{plural(n, "Wertung", "Wertungen")} abgegeben.' if n else 'Keine Entwürfe abzugeben.')
    return back(request, course)


@require_POST
def take_back(request, pk):
    """Take one's marks back as drafts, where not everyone has handed in yet: nobody has seen them."""
    course = marker(request, pk)
    n = panel.take_back(course, request.user)
    messages.success(request, f'{plural(n, "Wertung", "Wertungen")} wieder als Entwurf.' if n else 'Nichts zurückzunehmen: wo alle abgegeben haben, hilft nur eine Korrektur.')
    return back(request, course)


@require_POST
def correction(request, pk):
    """Ask the Prüfungsausschuss to change one's own mark, once the commission has seen it: a task for it until it decides."""
    p = get_object_or_404(Participation.objects.select_related('course', 'study__person'), pk=pk, course__kind=Course.Kind.AUDITION, course__released=None)
    get_object_or_404(Score, participation=p, examiner=request.user, draft=False, value__isnull=False)
    row = next(r for r in panel.sheet(p.course)[1] if r.pk == p.pk)
    if row.missing or row.vacant or row.unseen:
        messages.error(request, 'Noch haben nicht alle abgegeben: „Abgabe zurücknehmen“, dann selbst ändern.')
        return back(request, p.course)
    reason = request.POST.get('reason', '').strip()[:200]
    try:
        value = panel.grade(request.POST.get('value', '').strip(), p.course)
    except ArithmeticError:  # Refused too: not a mark of this stage
        value = None
    if value is None or not reason:
        messages.error(request, 'Bitte die richtige Wertung und den Grund angeben.')
        return back(request, p.course)
    Correction.objects.create(participation=p, examiner=request.user, value=value, reason=reason)
    messages.success(request, 'Korrektur beantragt. Der Prüfungsausschuss entscheidet; Sie erhalten eine Nachricht.')
    return back(request, p.course)


@require_POST
def settle(request, pk):
    """Take over a correction asked for, or turn it down; the juror hears either way, the mark's history keeps it."""
    c = get_object_or_404(Correction.objects.select_related('participation__course', 'participation__study__person', 'examiner'), pk=pk, accepted=None)
    course = c.participation.course
    if not request.user.may('correct_scores', course):
        raise PermissionDenied('Korrekturen entscheidet der Prüfungsausschuss.')
    c.accepted, c.settled_by = request.POST.get('uebernehmen') == '1', request.user
    if c.accepted:
        row = next(r for r in panel.sheet(course)[1] if r.pk == c.participation_id)
        typed = ('1' if c.value else '0') if course.votes else str(c.value)
        panel.save({f'score-{row.pk}-{c.examiner_id}': typed}, [row], [c.examiner], decide=True, by=request.user)
    c.save(update_fields=['accepted', 'settled_by'])
    texts.send(
        'korrektur', c.examiner, course.get_absolute_url(), pruefung=course.title, bewerbung=c.participation.study.person,
        entscheidung='übernommen' if c.accepted else 'nicht übernommen',
    )
    messages.success(request, f'Korrektur {"übernommen" if c.accepted else "abgelehnt"}; {c.examiner} erhält eine Nachricht.')
    return redirect(course)
