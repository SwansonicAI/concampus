"""An exam as the office and examiners run it: dates, lists, marks, release."""
import csv
import io
from collections import Counter
from datetime import datetime, timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, DateTimeField, Min, Q
from django.db.models.functions import Cast, Coalesce
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import dates, texts
from core.access import kept, require
from core.apps import plural
from core.models import Export, Institution, Message, Office, Term
from core.text import matching
from core.forms import again, sent
from courses import panel
from courses.forms import AddForm, DateForm, ExamForm, SlotForm, ThesisForm, substitutes
from courses.models import Course, Participation, attempts
from courses.modules import credited
from curriculum.models import Program
from rooms.models import Booking, BookingCategory
from students.models import DocumentType, Study
from .common import EXAMS, STATES, handed_in, offered


def told_now(rows):
    """(rows whose result differs from what they were told, how many of them get the Bescheid über endgültiges
    Nichtbestehen): released again after a correction, only those whose result changed hear of it."""
    told = [p for p in rows if p.told_result != p.result]
    return told, sum(1 for p in told if p.passed is False and getattr(p, 'last', False))


def outcome(p):
    """A result in words, as lists and notices give it."""
    return 'bestanden' if p.passed else 'nicht bestanden' if p.passed is False else 'entschuldigt' if p.remark else ''


def register_resits(course):
    """Register for an exam everyone who failed one of its modules, may try again and is not registered elsewhere, where
    the Hochschule says the Wiederholungsprüfung needs no Anmeldung (resits_registered). Returns the new participations."""
    if not Institution.get().resits_registered:
        return []
    modules = course.modules.all()
    failed = Counter(
        Participation.objects.filter(module__in=modules, passed=False, course__released__isnull=False, study__status=Study.Status.ENROLLED)
        .values_list('study', 'module')
    )
    settled = set(Participation.objects.filter(module__in=modules).filter(Q(passed=True) | Q(course__released=None)).values_list('study', 'module'))
    studies = Study.objects.select_related('program', 'person').in_bulk({study for study, _ in failed})
    taken, new = set(course.participations.values_list('study', flat=True)), []
    for (study, module), n in failed.items():
        s = studies[study]
        if (study, module) not in settled and study not in taken and n < s.program.attempts and course.subject_id in (None, s.subject_id):
            taken.add(study)
            new.append(Participation(course=course, study=s, module_id=module))
    Participation.objects.bulk_create(new)
    for p in new:
        texts.send('pruefungsanmeldung', p.study.person, reverse('exams:index'), pruefung=course.title, modul=p.module)
    return new


@require('manage_exams', 'decide_results', 'correct_scores', 'teacher', 'student')
def index(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.objects.filter(courses__kind=Course.Kind.EXAM).distinct().order_by('-start')}
    exams = (
        Course.objects.filter(term=term, kind__in=[Course.Kind.EXAM, Course.Kind.THESIS]).select_related('subject', 'assessor').prefetch_related('staff', 'modules__program')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True), candidate=Min('participations__study__person__last_name'))
        .annotate(graded=Count('participations', filter=Q(participations__passed__isnull=False), distinct=True))
        .order_by(Coalesce('date', Cast('deadline', DateTimeField())).asc(nulls_last=True), 'title')  # a thesis by when it is due
    )
    office = bool(person.roles & {'manage_exams', 'decide_results', 'correct_scores'})
    if office or 'teacher' in person.roles:
        program, stand, q = (request.GET.get(key, '').strip() for key in ('studiengang', 'stand', 'q'))
        if stand == 'ohne-termin':  # not yet given a day: one to book before registration opens
            exams = exams.filter(kind=Course.Kind.EXAM, bookings=None, released=None)
        if stand == 'kommission':
            exams = exams.filter(pk__in=Course.short_panels(Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], term=term)))
        exams = exams.filter(pk__in=person.within(Course.objects.all(), 'manage_exams', 'decide_results', 'correct_scores').values('pk')) if office else exams.filter(staff=person)
        own = office and 'teacher' in person.roles and request.GET.get('meine') == '1'  # an office that also examines: its own exams and theses
        if own:
            exams = exams.filter(Q(staff=person) | Q(participations__recusals__substitute=person)).distinct()
        if program.isdigit():
            exams = exams.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
        candidate = [f'participations__study__person__{name}' for name in ('first_name', 'last_name', 'matriculation')]
        exams = list(matching(exams, q, 'title', 'subject__name', 'staff__last_name', *candidate))
        for c in exams:
            c.programs = sorted({m.program.title for m in c.modules.all()})
            c.state = 'freigegeben' if c.released else 'bewertung' if c.graded or c.date and c.date <= timezone.now() else 'offen'  # held: marks are due
        context.update(
            exams=[c for c in exams if stand in ('', 'kommission', 'ohne-termin') or c.state == stand], q=q, stand=stand, program=program, programs=Program.planned(),
            examining=office and 'teacher' in person.roles, own=own,
            states=[(key, label, sum(c.state == key for c in exams)) for key, label in STATES],
        )
    if 'student' in person.roles:
        studies = person.studies.filter(status=Study.Status.ENROLLED)
        mine = (
            Participation.objects.filter(study__in=studies, course__kind=Course.Kind.EXAM).select_related('course__term', 'module', 'study__program')
            .prefetch_related('course__staff', 'course__bookings__room').order_by('-course__term__start')
        )
        ahead = [p for p in mine if not p.course.released]
        studies = list(studies.select_related('program'))
        for p in ahead:
            held = min(p.course.bookings.all(), key=lambda b: b.start, default=None)
            p.when, p.where = p.begins, held and held.room
            p.attempt = attempts(person, p.module) + 1 if p.module else 1
            p.last = p.attempt >= p.study.program.attempts
        withdrawing = Office.holding('decide_results', studies[0].program) if studies else None
        theses = list(
            Participation.objects.filter(study__in=studies, course__kind=Course.Kind.THESIS).select_related('course', 'module', 'study')
            .prefetch_related('course__staff').order_by('-created')
        )
        for p in theses:
            p.handed = handed_in(p)
        offers = list(offered(person))
        for c in offers:  # which try this would be
            module = c.modules.filter(program__studies__in=studies).first()
            c.attempt = attempts(person, module) + 1 if module else 1
        context.update(
            mine=[p for p in mine if p.course.released], ahead=ahead, offered=offers, studies=studies, theses=theses, withdrawing=withdrawing,
            thesis_kind=DocumentType.system('abschlussarbeit'),
            allowed=studies[0].program.attempts if studies else None,
        )
    return render(request, 'exams/index.html', context)


def exam(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=EXAMS)
    office, decide, correct = person.may('manage_exams', course), person.may('decide_results', course), person.may('correct_scores', course)
    member = course.staff.filter(pk=person.pk).exists()
    if not (office or decide or correct or member or course.participations.filter(recusals__substitute=person).exists()):
        raise PermissionDenied('Eine Prüfung sehen ihre Prüfenden und die Ämter, die Prüfungen planen, Ergebnisse eintragen oder berichtigen.')

    def sheet():  # a substitute sees only whom they mark
        staff, rows = panel.sheet(course)
        return staff, rows if office or decide or correct or member else panel.substituting(rows, person)

    staff, rows = sheet()
    columns, refused = panel.scorers(staff, rows) if decide or correct else [person], None
    waiting = decide and not correct and course.kind == Course.Kind.EXAM and not course.protocol_received and Institution.get().protocol_first
    if waiting:  # the examiners' marks come from the signed Protokoll: none to enter before it is in
        columns = [e for e in columns if e.pk == person.pk]
    if request.method == 'POST':
        if course.released:
            raise PermissionDenied(f'Freigegebene Ergebnisse öffnet nur wieder, wer Wertungen berichtigt: {Office.holding("correct_scores") or "das zuständige Amt"}.')
        post = request.POST.copy()  # a Gutachten saved as a draft, or handed in without a grade, keeps its text only
        draft = bool(post.pop('entwurf', None)) or any(k.startswith('report-') for k in post) and any(k.startswith('score-') and not post[k] for k in post)
        for key in [k for k in post if k.startswith('score-')] if draft else []:
            post[key] = ''
        try:
            panel.save(post, rows, columns, decide=decide or correct, correct=correct, by=person)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Entwurf gespeichert; abgegeben ist das Gutachten mit seiner Note.' if draft else 'Gutachten abgegeben.' if course.kind == Course.Kind.THESIS else 'Ergebnisse gespeichert.')
            return redirect(course)
    panel.blind(rows, person, office=decide or correct)
    if refused:
        panel.retype(rows, request.POST, refused)
    handed = handed_in(rows[0]) if course.kind == Course.Kind.THESIS and rows else None
    held = handed or course.bookings.filter(start__lte=timezone.now()).exists()  # marks are owed from then on, slots no more
    return render(request, 'exams/exam.html', {
        'course': course,
        'staff': staff,
        'rows': rows, 'timed': any(p.slot for p in rows), 'noted': any(p.note for p in rows),
        'columns': [e.pk for e in columns],
        'panel': course.kind in Course.PANEL,
        'office': office,
        'decide': decide or correct, 'correct': correct, 'books': decide, 'rule': panel.rule(rows), 'composer': office,  # the Protokoll and a Rücktritt: who enters results
        'recusable': [] if course.released else panel.recusable(rows, person),
        'candidates': substitutes(course) if office and any(p.vacant for p in rows) else [],
        'history': bool(Course.seen_by(person, course.pk)),
        'slots': SlotForm(sent(request, 'slots') or request.GET or None, person=person, course=course) if office and not held else None,
        'booking': DateForm(sent(request, 'tag'), person=person) if office and not course.released and course.kind == Course.Kind.EXAM else None,
        'modules': credited(course),
        'bookings': course.bookings.select_related('room'),
        'missing': sum(1 for p in rows if panel.due(p)),
        'short': course.panel().shortfall,
        'dropping': office and not course.released and course.kind == Course.Kind.EXAM,
        'waiting': panel.waiting(rows) if (office or decide) and held and not course.released else [],
        'remarks': Participation.Remark.choices if (decide or correct) and course.kind in (Course.Kind.EXAM, Course.Kind.THESIS) else [],
        'handed': handed, 'grading': handed and timezone.localdate(handed.created) + timedelta(weeks=Institution.get().review_weeks),
        'adding': AddForm(),
        'importing': not course.released and course.kind == Course.Kind.EXAM and (decide or member),
        'releasing': release_note(course, rows) if rows and not course.released else '',
        'reachable': Study.reachable(person, Study.objects.filter(participations__course=course)),
    })


def release_note(course, rows):
    """What releasing does, for its confirm: who hears, who gets a Bescheid, that only the office correcting results reopens."""
    told, final = told_now(rows)
    return ' '.join([
        f'Freigeben und sperren? {plural(len(told), "Person erhält", "Personen erhalten")} das Ergebnis'
        + (f', {plural(final, "davon den Bescheid", "davon den Bescheid")} über endgültiges Nichtbestehen.' if final else '.'),
        *(['Das unterschriebene Protokoll ist noch nicht verbucht.'] if course.kind == Course.Kind.EXAM and not course.protocol_received and Institution.get().protocol_first else []),
        f'Danach öffnet nur wieder, wer Wertungen berichtigt: {Office.holding("correct_scores") or "das zuständige Amt"}.',
    ])


@require('manage_exams')
def edit(request, pk=None):
    exams = request.user.within(Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS]), 'manage_exams')
    term = Term.pick(request.GET.get('semester'))  # a new exam takes the term's registration period
    course = get_object_or_404(exams, pk=pk) if pk else Course(term=term, kind=Course.Kind.EXAM, opens=term and term.exam_signup_start, deadline=term and term.exam_signup_end)
    thesis = course.kind == Course.Kind.THESIS
    form = (ThesisForm if thesis else ExamForm)(request.POST or None, instance=course, person=request.user)
    if form.is_valid() and (saved := kept(form, request.user, 'manage_exams')):
        course = saved
        if thesis and 'deadline' in form.changed_data:
            study = course.participations.select_related('study__person', 'study__program', 'study__subject', 'study__start_term').get().study
            study.notify('verlaengerung', reverse('exams:index'), thema=course.title, frist=f'{course.deadline:%d.%m.%Y}')
        resits = [] if thesis else register_resits(course)
        messages.success(request, ('Gespeichert.' if pk else 'Gespeichert. Kandidat:innen melden sich bis zum Anmeldeschluss selbst an.')
                         + (f' {plural(len(resits), "Person", "Personen")} zur Wiederholung automatisch angemeldet.' if resits else ''))
        return redirect(course)
    back = course.get_absolute_url() if pk else reverse('exams:index')
    return render(request, 'courses/form.html', {'form': form, 'course': course, 'menu': 'exams/_nav.html', 'heading': 'Neue Prüfung', 'back': back})


def protocol(request, pk):
    """The signed Protokoll of an exam: the office books its arrival, on paper or as a scan; whoever sees the exam's
    results reads the scan."""
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM)
    person = request.user
    office = person.may('decide_results', course) or person.may('manage_exams', course)
    if request.method == 'POST':
        if not office or course.released:
            raise PermissionDenied('Das Protokoll lädt das Amt hoch, das die Ergebnisse einträgt, bis sie freigegeben sind.')
        try:
            course.protocol_received = dates.received(request.POST.get('received', ''))
        except forms.ValidationError as error:
            messages.error(request, f'Protokoll eingegangen am: {error.messages[0]}')
            return redirect(course)
        course.protocol = request.FILES.get('file') or course.protocol
        course.save(update_fields=['protocol', 'protocol_received'])
        messages.success(request, f'Protokoll vom {date_format(course.protocol_received, "j.n.Y")} verbucht. Die Wertungen lassen sich jetzt übertragen.')
        return redirect(course)
    if not (office or person.may('correct_scores', course) or course.staff.filter(pk=person.pk).exists()) or not course.protocol:
        raise PermissionDenied('Das Protokoll lesen die Prüfenden und die Ämter, die Ergebnisse eintragen oder berichtigen.')
    return FileResponse(course.protocol.open('rb'), filename=f'Protokoll {course.title}.pdf')


@require('manage_exams')
@require_POST
def plan(request, pk):
    course = get_object_or_404(request.user.within(Course.objects.all(), 'manage_exams'), pk=pk, kind=Course.Kind.EXAM)
    form = SlotForm(request.POST, person=request.user, course=course)
    rows = list(course.participations.filter(slot=None).select_related('study__person').order_by('created'))
    if not rows:
        messages.error(request, 'Niemand ohne Termin angemeldet. Tag und Raum der Prüfung bucht „Tag und Raum buchen“.')
        return redirect(course)
    if not form.is_valid():  # back with what was typed, and what is wrong with it
        messages.error(request, ' '.join(f'{form.fields[name].label}: {" ".join(errors)}' for name, errors in form.errors.items()))
        return again(request, course, 'slots')
    try:
        panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user, form.cleaned_data['pause'])
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f'{len(rows)} Termine vergeben und Einladungen versandt.')
    return redirect(course)


@require('manage_exams')
@require_POST
def book(request, pk):
    """Book the exam's day and room, before anyone has registered: its candidates get their times later."""
    course = get_object_or_404(request.user.within(Course.objects.all(), 'manage_exams'), pk=pk, kind=Course.Kind.EXAM, released=None)
    form = DateForm(request.POST, person=request.user)
    if not form.is_valid():
        messages.error(request, ' '.join(e for errors in form.errors.values() for e in errors))
        return again(request, course, 'tag')
    data = form.cleaned_data
    booking = Booking(
        room=data['room'], start=timezone.make_aware(datetime.combine(data['day'], data['begin'])), end=timezone.make_aware(datetime.combine(data['day'], data['until'])),
        person=request.user, course=course, category=BookingCategory.system('pruefung'),
    )
    try:
        booking.full_clean()
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return again(request, course, 'tag')
    booking.save()
    messages.success(request, f'{course.title}: {date_format(booking.start, "j.n.Y")} in {booking.room} gebucht.')
    return redirect(course)


def roster(request, pk):
    """The exam's list to print and sign; once released also as a notice by Matrikelnummer (?aushang); or as CSV."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=[Course.Kind.EXAM, Course.Kind.THESIS])
    person = request.user
    if not (person.may('manage_exams', course) or person.may('decide_results', course) or course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    staff, rows = panel.sheet(course)
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="notenliste-{course.pk}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Matrikelnummer', 'Nachname', 'Vorname', 'Studiengang', 'Hauptfach', 'Termin', *(e.last_name for e in staff), 'Note', 'Ergebnis', 'Vermerk'])
        Export.log(person, f'Notenliste {course.title}, {course.term}', len(rows))
        for p in rows:
            writer.writerow([
                p.study.person.matriculation, p.study.person.last_name, p.study.person.first_name, p.study.program.title,
                p.study.subject, f'{timezone.localtime(p.slot):%d.%m.%Y %H:%M}' if p.slot else '', *(str(v or '').replace('.', ',') for _, v in p.marks),
                str(p.grade if p.grade is not None else '').replace('.', ','), outcome(p), p.remark.upper(),
            ])
        return response
    notice = 'aushang' in request.GET
    if notice and not course.released:
        raise Http404
    return render(request, 'exams/roster.html', {
        'trail': [('Prüfungen', reverse('exams:index')), (course.title, course.get_absolute_url())], 'course': course, 'staff': staff, 'rows': sorted(rows, key=lambda p: p.study.person.matriculation or '') if notice else rows,
        'notice': notice, 'bookings': course.bookings.select_related('room'), 'today': timezone.localdate(), 'sender': texts.block('absender_leistungen'),
    })


def marks(request, pk):
    """Take marks from a file, as a Klausur's examiner keeps them: Matrikelnummer and mark per line. An examiner fills
    their own column; the office entering results the column of the examiner it names."""
    course = get_object_or_404(Course, pk=pk, kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], released=None)
    person = request.user
    own, decide = course.staff.filter(pk=person.pk).exists(), person.may('decide_results', course)
    if request.method != 'POST' or not (own or decide):
        raise PermissionDenied('Noten übernehmen die Prüfenden und das Amt, das Ergebnisse einträgt.')
    staff, rows = panel.sheet(course)
    column = person if own else next((e for e in staff if str(e.pk) == request.POST.get('spalte')), None)
    if column is None:
        messages.error(request, 'Bitte angeben, wessen Wertungen die Datei enthält.')
        return redirect(course)
    text = request.FILES['file'].read().decode('utf-8-sig', errors='replace') if 'file' in request.FILES else ''
    delimiter = ';' if ';' in text else '\t' if '\t' in text else ','  # a semicolon first: German marks have decimal commas
    found = {line[0].strip(): line[1].strip() for line in csv.reader(io.StringIO(text), delimiter=delimiter) if len(line) >= 2 and line[0].strip().isdigit()}
    by_number = {p.study.person.matriculation: p for p in rows}
    post = {f'score-{p.pk}-{column.pk}': value for number, value in found.items() if (p := by_number.get(number))}
    unknown = [number for number in found if number not in by_number]
    try:
        panel.save(post, rows, [column], decide=not own, by=request.user)
    except panel.Refused as error:
        name = next(p.study.person.name for p in rows if p.pk == error.row)
        messages.error(request, f'Nichts übernommen. {name}: {error}')
    else:
        messages.success(request, f'{plural(len(found) - len(unknown), "Note", "Noten")} für {column.last_name} übernommen.' + (f' Nicht angemeldet: {", ".join(unknown)}.' if unknown else ''))
    return redirect(course)


@require_POST
def release(request, pk):
    """Release results to the candidates and lock them; only whoever decides results can reopen. A last attempt failed
    ends the Prüfungsanspruch: the candidate gets the Bescheid."""
    course = get_object_or_404(Course, pk=pk, kind__in=EXAMS)
    office, correct = request.user.may('decide_results', course), request.user.may('correct_scores', course)
    if not (office or correct or course.kind == Course.Kind.EXAM and course.staff.filter(pk=request.user.pk).exists()):  # a thesis, the office releases
        raise PermissionDenied('Ergebnisse geben die Prüfenden und das Amt frei, das Ergebnisse einträgt.')
    if course.released:
        if not correct:
            raise PermissionDenied(f'Freigegebene Ergebnisse öffnet nur wieder, wer Wertungen berichtigt: {Office.holding("correct_scores") or "das zuständige Amt"}.')
        course.released = None
        messages.success(request, 'Ergebnisse wieder zur Bearbeitung geöffnet.')
    else:
        _, rows = panel.sheet(course)
        if missing := panel.gaps(rows) or panel.off_scale(rows) or (not rows and 'Niemand ist angemeldet.'):
            messages.error(request, missing)
            return redirect(course)
        course.released = timezone.now()
        told, final = told_now(rows)
        for p in told:
            texts.send('ergebnis', p.study.person, reverse('exams:index'), pruefung=course.title)
            if p.passed is False and getattr(p, 'last', False):
                p.study.notify('endgueltig', reverse('exams:index'), pruefung=course.title, modul=p.module, versuch=p.attempt)
            p.told, p.told_result = course.released, p.result
        Participation.objects.bulk_update(told, ['told', 'told_result'])
        messages.success(request, 'Ergebnisse freigegeben und gesperrt; ' + (
            f'{plural(len(told), "Person", "Personen")} benachrichtigt' if told else 'nichts geändert, niemand benachrichtigt'
        ) + (f', {plural(final, "Bescheid", "Bescheide")} über endgültiges Nichtbestehen versandt.' if final else '.'))
    course.save(update_fields=['released'])
    if course.released:  # who failed is registered for the next exam of the module already planned
        later = (
            Course.objects.filter(kind=Course.Kind.EXAM, released=None, modules__in=course.modules.all())
            .filter(Q(deadline=None) | Q(deadline__gte=timezone.localdate())).exclude(pk=course.pk).distinct()
        )
        if resits := [p for c in later for p in register_resits(c)]:
            messages.info(request, f'{plural(len(resits), "Person", "Personen")} zur Wiederholung automatisch angemeldet.')
    return redirect(course)


@require_POST
def remind(request, pk):
    """Ask the examiners whose marks keep results open for them; nobody twice a day."""
    course = get_object_or_404(Course, pk=pk, kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], released=None)
    if not (request.user.may('manage_exams', course) or request.user.may('decide_results', course)):
        raise PermissionDenied
    sent = []
    for examiner, n in panel.waiting(panel.sheet(course)[1]):
        subject, body = texts.render(
            'wertung_fehlt', vorname=examiner.first_name, nachname=examiner.last_name, pruefung=course.title, anzahl=plural(n, 'Teilnahme', 'Teilnahmen'),
        )
        if not examiner.messages.filter(subject=subject, created__date=timezone.localdate()).exists():
            Message.send(examiner, subject, body, course.get_absolute_url())
            sent.append(examiner.last_name)
    messages.success(request, f'Erinnert: {", ".join(sent)}.' if sent else 'Heute schon erinnert.')
    return redirect(course)
