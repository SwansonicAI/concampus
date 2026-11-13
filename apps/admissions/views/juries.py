"""The Eignungsprüfung as its juries run it: appointments, stages, results, Nachrücken."""
import re
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, F, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import texts
from core.dates import MomentField
from core.access import require
from core.apps import plural
from core.models import Institution
from core.text import untimed
from core.shortcuts import preview
from courses import panel
from courses.forms import SlotForm, substitutes
from courses.models import Course, Participation, marked
from students.models import Document, Study
from ..auditions import after, held, invite, next_stages
from ..papers import Condition
from .board import DECISIONS, offer, vacancies


@require('teacher')
def juries(request):
    auditions = (
        Course.objects.filter(Q(staff=request.user) | Q(participations__recusals__substitute=request.user), kind=Course.Kind.AUDITION).distinct()
        .select_related('term', 'subject')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .order_by('-term__start', F('deadline').asc(nulls_last=True), F('date').asc(nulls_last=True), 'title')
    )
    for c in auditions:  # what is still the person's own to mark, recusals left out
        c.mine = c.participations.exclude(scores__in=marked(request.user)).exclude(recusals__examiner=request.user).count() if not c.released else 0
    return render(request, 'admissions/juries.html', {'auditions': auditions})


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
    if not (office or correct or member or course.participations.filter(recusals__substitute=person).exists()):
        raise PermissionDenied('Eine Eignungsprüfung sehen ihre Kommission, das Amt, das sie durchführt, und der Prüfungsausschuss.')

    def sheet():  # a substitute sees only whom they mark
        staff, rows = panel.sheet(course)
        return staff, rows if office or correct or member else panel.substituting(rows, person)

    staff, rows = sheet()
    columns, refused = panel.scorers(staff, rows) if correct else [] if office and not member else [person], None  # the office that runs it marks nothing
    if request.method == 'POST':
        if course.released or not columns:
            raise PermissionDenied('Wertungen tragen die Kommission und, berichtigend, der Prüfungsausschuss ein.')
        try:
            panel.save(request.POST, rows, columns, decide=correct, correct=correct)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Wertungen gespeichert.')
            return redirect(course)
    panel.blind(rows, person, office=office or correct)
    if refused:
        panel.retype(rows, request.POST, refused)
    invited = set(Participation.objects.filter(course__in=next_stages(course)).values_list('study', flat=True)) if office else set()
    if request.GET.get('sortierung') == 'punkte' and not any(getattr(p, 'blind', False) for p in rows):  # a ranking would tell the others' marks
        rows.sort(key=lambda p: p.grade if p.grade is not None else (p.mean or 0), reverse=True)
    return render(request, 'admissions/audition.html', {
        'trail': [(None, reverse('admissions:juries' if member else 'admissions:board'))], 'course': course,
        'staff': staff,
        'rows': rows, 'timed': any(p.slot for p in rows), 'noted': any(p.note for p in rows),
        'columns': [e.pk for e in columns],
        'office': office, 'correct': correct, 'rule': panel.rule(rows), 'composer': office, 'short': course.panel().shortfall,
        'recusable': [] if course.released else panel.recusable(rows, person),
        'candidates': substitutes(course) if office and any(p.vacant for p in rows) else [],
        'history': bool(Course.seen_by(person, course.pk)),
        'slots': SlotForm(person=person, course=course) if office else None,
        'scheduled': course.participations.filter(slot__isnull=False).select_related('study__person').order_by('slot') if office and not course.released else [],
        'stages': next_stages(course) if office else [],
        'ready': sum(1 for p in rows if p.passed and p.study.status == Study.Status.APPLIED and p.study_id not in invited),  # not yet at the next stage
        'missing': sum(1 for p in rows if panel.due(p)),
        'bookings': course.bookings.select_related('room'),
        'reachable': Study.reachable(person, Study.objects.filter(participations__course=course)),
    })


@require('run_auditions')
@require_POST
def plan(request, pk):
    course = get_object_or_404(request.user.within(Course.objects.all(), 'run_auditions'), pk=pk, kind=Course.Kind.AUDITION)
    form = SlotForm(request.POST, person=request.user, course=course)
    rows = list(course.participations.filter(slot=None, study__status=Study.Status.APPLIED).select_related('study__person', 'study__program', 'study__subject').order_by('created'))
    if form.is_valid() and rows and not request.POST.get('bestaetigt'):  # the times first, with the end of the day, then the letters
        data = form.cleaned_data
        step = timedelta(minutes=data['minutes'] + data['pause'])
        times = [timezone.localtime(data['start'] + step * n) for n in range(len(rows))]
        end = times[-1] + timedelta(minutes=data['minutes'])
        letter = rows[0].study.wording(
            'einladung_eignungspruefung', pruefung=course.title, termin=date_format(times[0], 'l, j. F Y, H:i'), raum=data['room'].place(linked=True),
            programm=rows[0].note or '–', danach='\n'.join(f'– {line}' for line in after(course)) or '–',
        )
        notes = [
            f'{plural(len(rows), "Termin", "Termine")} am {date_format(times[0], "l, j. F Y")} in {data["room"]}, von {times[0]:%H:%M} bis {end:%H:%M} Uhr.',
            'Reihenfolge: ' + ', '.join(f'{t:%H:%M} {p.study.person.name}' for t, p in zip(times, rows)) + '.',
            'Einzelne Termine lassen sich danach verlegen, mit neuer Einladung.',
        ]
        return preview(request, f'Termine: {course.title}', [('Einladung', [p.study.person for p in rows], letter)], f'{len(rows)} Termine vergeben und einladen', course.get_absolute_url(), notes)
    if form.is_valid() and rows:
        try:
            panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user, form.cleaned_data['pause'])
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            for p in rows:
                invite(p)
            messages.success(request, f'{len(rows)} Termine vergeben, Einladungen versandt.')
    else:
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben.' if rows else 'Alle Bewerbenden haben schon einen Termin.')
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
    """Sichtung: the video round one application at a time. Watch, score, on to the next without a score of one's own."""
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject', 'program'), pk=pk, kind=Course.Kind.AUDITION, video=True)
    member = course.staff.filter(pk=person.pk).exists()
    juror = member or course.participations.filter(recusals__substitute=person).exists()
    if not (juror or person.may('run_auditions', course)):
        raise PermissionDenied('Die Videos sichten die Kommission und das Amt, das die Eignungsprüfung durchführt.')
    _, everyone = panel.sheet(course)
    if juror and not member:  # a substitute watches only whom they mark
        everyone = panel.substituting(everyone, person)
    filmed = set(Document.objects.filter(study__in=[p.study_id for p in everyone], kind__condition=Condition.VIDEO).values_list('study', flat=True))
    rows = [p for p in everyone if p.study_id in filmed]  # nothing to watch yet: not in the jury's way
    if not rows:
        return redirect(course)
    panel.blind(rows, person)
    mine = {p.pk: dict((e.pk, v) for e, v in p.marks).get(person.pk) for p in rows}
    todo = {p.pk for p in rows if mine[p.pk] is None and person.pk not in p.recused}
    done = len(rows) - len(todo) - sum(1 for p in rows if person.pk in p.recused)  # saved, not what a refused form typed
    picked = request.GET.get('bewerbung', '')
    current = next((p for p in rows if str(p.pk) == picked), None) or next((p for p in rows if p.pk in todo), rows[0])
    at, error = rows.index(current), ''
    here = reverse('admissions:review', args=[course.pk])
    if request.method == 'POST':
        if course.released or not juror:
            raise PermissionDenied
        try:
            panel.save(request.POST, [current], [person], decide=False)
        except panel.Refused as refused:  # shown by the field, with what was typed
            error, mine[current.pk] = str(refused), request.POST.get(f'score-{current.pk}-{person.pk}', '')
        else:
            messages.success(request, f'Wertung für {current.study.person.first_name} {current.study.person.last_name} gespeichert.')
            following = next((p for p in rows[at + 1:] + rows[:at] if p.pk in todo - {current.pk}), None)
            return redirect(f'{here}?bewerbung={following.pk}' if following else course)
    return render(request, 'admissions/review.html', {
        'trail': [(None, reverse('admissions:juries' if member else 'admissions:board'))], 'course': course,
        'p': current, 'error': error, 'programme': programme(current.note),
        'juror': juror,
        'mine': mine[current.pk],
        'videos': current.study.documents.filter(kind__condition=Condition.VIDEO),
        'position': everyone.index(current) + 1,  # numbered as on the list of all applications
        'count': len(everyone),
        'done': done, 'scored': current.pk not in todo,  # the wished-for teacher only after one's own score, not to sway it
        'prev': rows[at - 1] if at else None,
        'next': rows[at + 1] if at + 1 < len(rows) else None,
    })
