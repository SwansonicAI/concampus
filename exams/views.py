import csv
import io
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DateTimeField, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Cast, Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.views.decorators.http import require_POST

from core import destatis, texts
from core.access import kept, require
from core.apps import plural
from core.dates import months_before
from core.models import Message, Office, Term
from core.text import matching
from core.views import without_term
from courses import panel
from courses.forms import AddForm, ExamForm, SlotForm, ThesisForm, substitutes
from courses.views import credited
from courses.models import Course, Participation, attempts
from curriculum.models import Module, Program
from curriculum.views import plan as study_plan
from students.models import Document, DocumentType, Study

EXAMS = [Course.Kind.EXAM, Course.Kind.RECOGNITION, Course.Kind.THESIS]
STATES = [('offen', 'offen'), ('bewertung', 'in Bewertung'), ('freigegeben', 'freigegeben')]
GRADES = [(1.5, 'sehr gut', 'very good'), (2.5, 'gut', 'good'), (3.5, 'befriedigend', 'satisfactory'), (4.0, 'ausreichend', 'sufficient')]


def verbal(grade, english=False):
    """German grade in words, as on a Zeugnis (RPSO § 15), or as the Diploma Supplement translates it."""
    for limit, word, translated in GRADES:
        if grade <= limit:
            return translated if english else word
    return 'fail' if english else 'nicht ausreichend'


def handed_in(participation):
    """The file handed in for a thesis: the latest since it was registered."""
    return participation.study.documents.filter(kind='abschlussarbeit', created__gte=participation.created).last()


def exhausted(study):
    """Modules the study has failed as often as its Prüfungsordnung allows and never passed: the Prüfungsanspruch is lost.
    Each comes with its `failures` and `appeal`, the last day to object: a month after the Bescheid is taken as delivered,
    the day after it was opened in the Postfach where the person agreed to that, else on the fourth day after it went out
    (before it has, counted from the result)."""
    failed = defaultdict(list)
    for p in Participation.objects.filter(study=study, passed=False, course__released__isnull=False).select_related('course__term').order_by('course__released'):
        failed[p.module_id].append(p)
    passed = set(Participation.objects.filter(study=study, passed=True).values_list('module', flat=True))
    lost = list(Module.objects.filter(pk__in=[m for m, ps in failed.items() if len(ps) >= study.program.attempts and m not in passed]))
    for module in lost:
        module.failures = failed[module.pk]
        released = module.failures[-1].course.released
        notice = study.documents.filter(kind='notice', created__gte=released).select_related('message').order_by('created').first()
        opened = study.person.electronic and notice and notice.message and notice.message.read
        known = timezone.localtime(opened).date() + timedelta(days=1) if opened else timezone.localtime(notice.created if notice else released).date() + timedelta(days=4)
        module.appeal = months_before(known, -1)
    return lost


def outcome(p):
    """A result in words, as lists and notices give it."""
    return 'bestanden' if p.passed else 'nicht bestanden' if p.passed is False else 'entschuldigt' if p.remark else ''


def register_resits(course):
    """Register for an exam everyone who failed one of its modules, may try again and is not registered elsewhere: the
    Wiederholungsprüfung needs no Anmeldung (RPSO § 19). Returns the new participations."""
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


def offered(person):
    """Exams of this term the person may still register for: their programme's modules, due by now, not yet passed;
    artistic exams held per Hauptfach only in their own."""
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    passed = Participation.objects.filter(study__in=studies, passed=True, module__isnull=False).values('module')  # NOT IN with a NULL matches nothing
    semester = max((s.semester for s in studies), default=0)
    return (  # with those whose registration opens later in the term
        Course.objects.filter(term=Term.current(), kind=Course.Kind.EXAM, modules__program__studies__in=studies, deadline__gte=timezone.localdate())
        .filter(Q(subject=None) | Q(subject__in=studies.values('subject')))
        .filter(modules__semester__lte=semester).exclude(participations__study__in=studies).exclude(modules__in=passed)
        .annotate(date=Min('bookings__start')).distinct()
    )


@require('manage_exams', 'decide_results', 'correct_scores', 'teacher', 'student')
def index(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.objects.filter(courses__kind=Course.Kind.EXAM).distinct().order_by('-start')}
    exams = (
        Course.objects.filter(term=term, kind__in=[Course.Kind.EXAM, Course.Kind.THESIS]).select_related('subject').prefetch_related('staff')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True), candidate=Min('participations__study__person__last_name'))
        .annotate(graded=Count('participations', filter=Q(participations__passed__isnull=False), distinct=True))
        .order_by(Coalesce('date', Cast('deadline', DateTimeField())).asc(nulls_last=True), 'title')  # a thesis by when it is due
    )
    office = bool(person.roles & {'manage_exams', 'decide_results', 'correct_scores'})
    if office or 'teacher' in person.roles:
        program, stand, q = (request.GET.get(key, '').strip() for key in ('studiengang', 'stand', 'q'))
        if stand == 'kommission':
            exams = exams.filter(pk__in=Course.short_panels(Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], term=term)))
        exams = exams.filter(pk__in=person.within(Course.objects.all(), 'manage_exams', 'decide_results', 'correct_scores').values('pk')) if office else exams.filter(staff=person)
        if program.isdigit():
            exams = exams.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
        candidate = [f'participations__study__person__{name}' for name in ('first_name', 'last_name', 'matriculation')]
        exams = list(matching(exams, q, 'title', 'subject__name', 'staff__last_name', *candidate))
        for c in exams:
            c.state = 'freigegeben' if c.released else 'bewertung' if c.graded or c.date and c.date <= timezone.now() else 'offen'  # held: marks are due
        context.update(
            exams=[c for c in exams if stand in ('', 'kommission') or c.state == stand], q=q, stand=stand, program=program, programs=Program.planned(),
            states=[(key, label, sum(c.state == key for c in exams)) for key, label in STATES],
        )
    if 'student' in person.roles:
        studies = person.studies.filter(status=Study.Status.ENROLLED)
        mine = (
            Participation.objects.filter(study__in=studies, course__kind=Course.Kind.EXAM).select_related('course__term', 'module')
            .prefetch_related('course__staff', 'course__bookings__room').order_by('-course__term__start')
        )
        ahead = [p for p in mine if not p.course.released]
        for p in ahead:
            held = min(p.course.bookings.all(), key=lambda b: b.start, default=None)
            p.when, p.where = p.slot or (held and held.start), held and held.room
        theses = list(
            Participation.objects.filter(study__in=studies, course__kind=Course.Kind.THESIS).select_related('course', 'module', 'study')
            .prefetch_related('course__staff').order_by('-created')
        )
        for p in theses:
            p.handed = handed_in(p)
        context.update(mine=[p for p in mine if p.course.released], ahead=ahead, offered=offered(person), studies=studies, theses=theses)
    return render(request, 'exams/index.html', context)


def exam(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=EXAMS)
    office, decide, correct = person.may('manage_exams', course), person.may('decide_results', course), person.may('correct_scores', course)
    member = course.staff.filter(pk=person.pk).exists()
    if not (office or decide or correct or member or course.participations.filter(recusals__substitute=person).exists()):
        raise PermissionDenied('Eine Prüfung sehen ihre Prüfenden, die Ämter, die Prüfungen planen oder Ergebnisse eintragen, und der Prüfungsausschuss.')

    def sheet():  # a substitute sees only whom they mark
        staff, rows = panel.sheet(course)
        return staff, rows if office or decide or correct or member else panel.substituting(rows, person)

    staff, rows = sheet()
    columns, refused = panel.scorers(staff, rows) if decide or correct else [person], None
    if request.method == 'POST':
        if course.released:
            raise PermissionDenied('Freigegebene Ergebnisse öffnet nur der Prüfungsausschuss wieder.')
        try:
            panel.save(request.POST, rows, columns, decide=decide or correct, correct=correct)
        except panel.Refused as error:  # shown with what was typed, by its row
            refused, (staff, rows) = error, sheet()
            messages.error(request, 'Nichts gespeichert. Bitte die markierte Zeile berichtigen.')
        else:
            messages.success(request, 'Gutachten gespeichert.' if course.kind == Course.Kind.THESIS else 'Ergebnisse gespeichert.')
            return redirect(course)
    if not (decide or correct):
        panel.blind(rows, person)
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
        'decide': decide or correct, 'correct': correct, 'rule': panel.rule(rows), 'composer': office,
        'recusable': [] if course.released else panel.recusable(rows, person),
        'candidates': substitutes(course) if office and any(p.vacant for p in rows) else [],
        'history': bool(Course.seen_by(person, course.pk)),
        'slots': SlotForm(request.GET or None, person=person, course=course) if office and not held else None,
        'modules': credited(course),
        'bookings': course.bookings.select_related('room'),
        'missing': sum(1 for p in rows if panel.due(p)),
        'short': course.panel().shortfall,
        'dropping': office and not course.released and course.kind == Course.Kind.EXAM,
        'waiting': panel.waiting(rows) if (office or decide) and held and not course.released else [],
        'remarks': Participation.Remark.choices if (decide or correct) and course.kind in (Course.Kind.EXAM, Course.Kind.THESIS) else [],
        'handed': handed,
        'adding': AddForm(),
        'importing': not course.released and course.kind == Course.Kind.EXAM and (decide or member),
        'reachable': Study.reachable(person, Study.objects.filter(participations__course=course)),
    })


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


@require('manage_exams')
def thesis(request, pk):
    """Register an Abschlussarbeit: its topic is given out today, due after the time the Prüfungsordnung allows."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person', 'program', 'subject', 'start_term'), 'manage_exams'), pk=pk, status=Study.Status.ENROLLED)
    term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester, in dem das Thema ausgegeben wird')
    weeks = study.program.thesis_weeks
    course = Course(term=term, kind=Course.Kind.THESIS, subject=study.subject, deadline=timezone.localdate() + timedelta(weeks=weeks) if weeks else None)
    form = ThesisForm(request.POST or None, instance=course, study=study)
    if form.is_valid():
        module = form.cleaned_data['module']
        with transaction.atomic():
            course = form.save()
            course.modules.set([module])
            Participation.objects.create(course=course, study=study, module=module)
        study.notify(
            'abschlussarbeit', reverse('exams:index'), modul=module, thema=course.title, frist=f'{course.deadline:%d.%m.%Y}',
            gutachter=', '.join(map(str, course.staff.all())),
        )
        messages.success(request, 'Abschlussarbeit angemeldet. Bescheid versandt.')
        return redirect(course)
    return render(request, 'courses/form.html', {
        'form': form, 'course': course, 'menu': 'exams/_nav.html', 'heading': f'Abschlussarbeit {study.person}', 'back': study.get_absolute_url(),
    })


@require_POST
def submit(request, pk):
    """Hand in a thesis as a file until it is due; the office until its result is out."""
    p = get_object_or_404(Participation.objects.select_related('course', 'study__person'), pk=pk, course__kind=Course.Kind.THESIS)
    office = request.user.may('manage_exams', p.course)
    if not (office or p.study.person == request.user):
        raise PermissionDenied
    kind, file = DocumentType.system('abschlussarbeit'), request.FILES.get('file')
    if p.course.released or not (office or p.course.is_open):
        messages.error(request, 'Die Abgabefrist ist abgelaufen.')
    elif not file:
        messages.error(request, 'Bitte eine Datei wählen.')
    elif refusal := kind.refuses(file):
        messages.error(request, refusal)
    else:
        Document.hand_in(p.study, kind, file)
        messages.success(request, 'Abschlussarbeit abgegeben. Die Gutachter:innen können sie jetzt öffnen.')
    return redirect(p.course if office else 'exams:index')


@require('manage_exams')
@require_POST
def plan(request, pk):
    course = get_object_or_404(request.user.within(Course.objects.all(), 'manage_exams'), pk=pk, kind=Course.Kind.EXAM)
    form = SlotForm(request.POST, person=request.user, course=course)
    rows = list(course.participations.filter(slot=None).select_related('study__person').order_by('created'))
    if not form.is_valid() or not rows:
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben; alle Teilnehmenden haben schon Termine.' if rows else 'Alle Teilnehmenden haben schon Termine.')
        return redirect(course)
    try:
        panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user, form.cleaned_data['pause'])
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f'{len(rows)} Termine vergeben und Einladungen versandt.')
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
        'course': course, 'staff': staff, 'rows': sorted(rows, key=lambda p: p.study.person.matriculation or '') if notice else rows,
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
        panel.save(post, rows, [column], decide=not own)
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
            raise PermissionDenied('Freigegebene Ergebnisse öffnet nur der Prüfungsausschuss wieder.')
        course.released = None
        messages.success(request, 'Ergebnisse wieder zur Bearbeitung geöffnet.')
    else:
        _, rows = panel.sheet(course)
        if missing := panel.gaps(rows) or panel.off_scale(rows) or (not rows and 'Niemand ist angemeldet.'):
            messages.error(request, missing)
            return redirect(course)
        course.released = timezone.now()
        final = 0
        for p in rows:
            texts.send('ergebnis', p.study.person, reverse('exams:index'), pruefung=course.title)
            if p.passed is False and getattr(p, 'last', False):
                p.study.notify('endgueltig', reverse('exams:index'), pruefung=course.title, modul=p.module, versuch=p.attempt)
                final += 1
        messages.success(request, 'Ergebnisse freigegeben und gesperrt.' + (f' {plural(final, "Bescheid", "Bescheide")} über endgültiges Nichtbestehen versandt.' if final else ''))
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


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    module = course.modules.filter(program=study.program).first()
    if course.upcoming:
        messages.error(request, f'Die Anmeldung beginnt am {date_format(course.opens, "j.n.Y")}.')
    elif not course.is_open:
        messages.error(request, 'Die Anmeldefrist ist abgelaufen.')
    elif refused := course.refuses(study, module):
        messages.error(request, refused)
    elif (module.semester or 0) > study.semester:
        messages.error(request, f'{module.code} ist erst ab dem {module.semester}. Fachsemester vorgesehen.')
    else:
        Participation.objects.get_or_create(course=course, study=study, module=module)
        texts.send('pruefungsanmeldung', request.user, reverse('exams:index'), pruefung=course.title, modul=module)
        messages.success(request, 'Angemeldet. Die Bestätigung liegt in Ihrem Postfach.')
    return redirect('exams:index')


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation, pk=pk, study__person=request.user, course__kind=Course.Kind.EXAM)
    if participation.course.withdrawable and participation.passed is None:
        participation.delete()
        messages.success(request, 'Abgemeldet.')
    else:
        messages.error(request, f'Abmelden ging nur bis zum {date_format(participation.course.withdraw_by, "j.n.Y")}. Bei triftigem Grund ist ein Rücktritt mit Nachweis möglich.')
    return redirect('exams:index')


class RecognitionForm(forms.Form):
    """An Anerkennung in two steps: whose, then which of their open modules, with the grade as it is or converted from
    another grading system by the modified Bavarian formula."""

    who = forms.CharField(label='Studierende:r', widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}))
    module = forms.ChoiceField(label='Modul', help_text='Ein Teil, oder das ganze Modul mit allen noch offenen Teilen.')
    grade = forms.DecimalField(label='Note', required=False, min_value=1, max_value=4, decimal_places=1, localize=True, help_text='Leer: bestanden ohne Note.')
    foreign = forms.DecimalField(label='Oder umrechnen: erzielte Note', required=False, decimal_places=2, localize=True)
    best = forms.DecimalField(label='Beste Note dort', required=False, decimal_places=2, localize=True)
    worst = forms.DecimalField(label='Schlechteste bestandene Note dort', required=False, decimal_places=2, localize=True, help_text='Modifizierte bayerische Formel.')
    origin = forms.ChoiceField(label='Hochschule', choices=[('', 'Bitte wählen'), *destatis.universities()], help_text='Im Ausland: das Land.')
    place = forms.CharField(label='Name der Hochschule', required=False, help_text='Bei einer Hochschule im Ausland, z. B. „mdw Wien“.')
    taken = forms.CharField(label='Semester dort', required=False, widget=forms.TextInput(attrs={'placeholder': 'z. B. SoSe 2024'}))
    credits = forms.DecimalField(label='ECTS dort', required=False, min_value=0, decimal_places=1, localize=True)
    proof = forms.FileField(label='Nachweis', required=False, help_text='Transcript oder Bescheinigung; kommt in die Studienakte.')
    FIELDS = ['module', 'grade', 'foreign', 'best', 'worst', 'origin', 'place', 'taken', 'credits', 'proof']

    def __init__(self, *args, study=None, **kwargs):
        super().__init__(*args, **kwargs)
        if study is None:
            for name in self.FIELDS:
                del self.fields[name]
            return
        del self.fields['who']
        passed = Participation.objects.filter(study=study, passed=True, module__isnull=False).values('module')
        self.open = {str(m.pk): m for m in Module.objects.filter(program=study.program, children=None).exclude(pk__in=passed).select_related('parent').order_by('code')}
        groups = {}
        for m in self.open.values():
            groups.setdefault(m.parent, []).append(m)
        self.fields['module'].choices = [('', '–')] + [
            (f'{parent.code} {parent.title}' if parent else 'Weitere', ([(f'm{parent.pk}', 'Ganzes Modul')] if parent and len(parts) > 1 else []) + [(str(m.pk), f'{m.code} {m.title}') for m in parts])
            for parent, parts in groups.items()
        ]

    def clean_who(self):
        found = Study.find(self.cleaned_data['who'])
        if len(found) != 1:
            raise forms.ValidationError(f'Mehrere passen: {", ".join(str(s.person) for s in found)}.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return found[0]

    def clean_module(self):
        value = self.cleaned_data['module']
        return [m for m in self.open.values() if f'm{m.parent_id}' == value] if value.startswith('m') else [self.open[value]]

    def clean(self):
        data = super().clean()
        given = [data.get(name) for name in ('foreign', 'best', 'worst')]
        if 'module' in self.fields and any(v is not None for v in given):
            foreign, best, worst = given
            if data.get('grade') is not None:
                raise forms.ValidationError('Entweder eine Note eintragen oder umrechnen, nicht beides.')
            if None in given or best == worst:
                raise forms.ValidationError('Zum Umrechnen erzielte, beste und schlechteste bestandene Note angeben.')
            grade = 1 + 3 * (best - foreign) / (best - worst)  # 1,0 for the best, 4,0 for the worst passing grade
            data['grade'] = max(Decimal('1.0'), min(Decimal('4.0'), grade.quantize(Decimal('0.1'), ROUND_DOWN)))
        if data.get('origin'):  # kept as one line of the record: Hochschule, Land, Semester, ECTS
            name = dict(choice for _, group in destatis.universities() for choice in group)[data['origin']]
            abroad = data['origin'].startswith(destatis.ABROAD)
            credits = data.get('credits') is not None and f'{number_format(data["credits"])} ECTS'
            data['note'] = ', '.join(filter(None, [data.get('place') if abroad else name, name if abroad else '', data.get('taken'), credits]))
        if (proof := data.get('proof')) and (refusal := DocumentType.system('anerkennung').refuses(proof)):
            self.add_error('proof', refusal)
        return data


@require('recognise')
def recognition(request):
    """Anerkennung: from the study file, or by name or Matrikelnummer first."""
    term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester, in dem Anerkennungen verbucht werden')
    course, _ = Course.objects.get_or_create(term=term, kind=Course.Kind.RECOGNITION, defaults={'title': f'Anerkennungen {term}'})
    study, pk = None, request.GET.get('studium', '')
    if pk:
        studies = request.user.within(Study.objects.select_related('person', 'program', 'subject'), 'recognise')
        study = get_object_or_404(studies, pk=pk if pk.isdigit() else 0, status=Study.Status.ENROLLED)
    form = RecognitionForm(request.POST or None, request.FILES or None, study=study)
    if form.is_valid() and study is None:
        if request.user.may('recognise', found := form.cleaned_data['who']):
            return redirect(f'{reverse("exams:recognition")}?studium={found.pk}')
        form.add_error(None, 'Diese Person studiert außerhalb der Institute und Studiengänge, für die Ihr Amt zuständig ist.')
    elif form.is_valid():
        data = form.cleaned_data
        for module in data['module']:
            Participation.objects.update_or_create(course=course, study=study, module=module, defaults={'grade': data['grade'], 'passed': True, 'note': data['note']})
        codes = ', '.join(m.code for m in data['module'])
        if data['proof']:
            Document.hand_in(study, DocumentType.system('anerkennung'), data['proof'])
        study.notify('anerkennung', reverse('exams:transcript', args=[study.pk]), modul=codes, herkunft=data['note'])
        messages.success(request, f'{codes} für {study.person} anerkannt' + (f' mit {data["grade"]}.' if data['grade'] is not None else '.'))
        return redirect(f'{reverse("exams:recognition")}?studium={study.pk}')
    return render(request, 'exams/recognition.html', {
        'form': form, 'course': course, 'study': study,
        'recent': course.participations.select_related('study__person', 'module').order_by('-created')[:20],
    })


def achievements(study):
    """What a Leistungsübersicht, a Zeugnis and a Diploma Supplement show: the modules with their credits, and the grade so far."""
    tree, average = study.progress()
    earned, total, honours = sum(m.earned for m in tree), sum(m.credits for m in tree), study.program.honours
    last = study.participations.filter(passed=True, course__released__isnull=False).aggregate(last=Max('course__released'))['last']
    rows = []
    for r in study_plan(tree, study.program.semesters):
        if r['kind'] != 'part' and r['node'].earned or r['kind'] == 'part' and r['exam'] and any(i.done and not i.excused for i in r['items']):  # an exam waived is no result
            above = rows[-1] if rows else None
            if above and above['kind'] == 'group' and r['kind'] != 'part' and r['node'].parent_id == above['node'].pk and above['node'].offered > above['node'].credits:
                rows[-1] = {**r, 'choice': above['node']}  # a choice and what was chosen, on one line with the choice's weight
            else:
                rows.append({**r, 'choice': None})
    thesis = study.participations.filter(course__kind=Course.Kind.THESIS, passed=True).select_related('course').first()
    return {
        'study': study, 'rows': rows, 'earned': earned, 'total': total, 'finished': earned >= total, 'average': average,
        'completed': last and timezone.localdate(last),  # the Zeugnis is dated by the last result
        'thesis': thesis and thesis.course.title,
        'honours': None not in (average, honours) and average <= honours, 'today': timezone.localdate(), 'signature': texts.block('unterschrift_zeugnisse'),
    }


def finished(studies):
    """The studies among these that have earned every credit, each with its `achievements`: (study, achievements)."""
    totals = dict(Module.objects.filter(parent=None).values_list('program').annotate(n=Sum('credits')))  # only who has as many is checked exactly
    credited = Participation.objects.filter(study=OuterRef('pk'), passed=True).exclude(course__kind__in=['exam', 'thesis'], course__released=None)
    near = studies.annotate(credited=Subquery(credited.values('study').annotate(n=Sum('module__credits')).values('n')[:1])).select_related('person', 'program')
    for study in near:
        if study.credited and study.credited >= totals.get(study.program_id, 0) and (done := achievements(study))['finished']:
            yield study, done


def leaving(study):
    from students.views import leaving

    return leaving(study)


def transcript(request, pk):
    """Leistungsübersicht or, once all credits are earned, Zeugnis."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject', 'focus'), pk=pk)
    if not (study.person == request.user or request.user.may('view_files', study)):
        raise PermissionDenied
    found, office = achievements(study), request.user.may('decide_results', study)  # a Zeugnis comes from the office
    copy = not office and study.person == request.user and study.issued and found['finished']  # graduates print their own
    return render(request, 'exams/transcript.html', found | {
        'complete': found['finished'] and (office or copy), 'copy': copy, 'supplement': (office or copy) and study.program.level,
        'issue': office and found['finished'] and study.issued is None and study.status == Study.Status.ENROLLED,
        'still': [item for item in leaving(study) if not item[0].startswith('Zeugnis')] if office and found['finished'] and study.issued is None else [],
        'verbal': verbal(found['average']) if found['average'] else '', 'sender': texts.block('absender_leistungen'),
    })


@require_POST
def issue(request, pk):
    """The Zeugnis issued once every credit is earned: noted in the file and told to the student; the Exmatrikulation follows."""
    study = get_object_or_404(Study.objects.select_related('person', 'program'), pk=pk, status=Study.Status.ENROLLED, issued=None)
    if not request.user.may('decide_results', study):
        raise PermissionDenied
    if not achievements(study)['finished']:
        messages.error(request, 'Es fehlen noch Leistungen.')
    else:
        study.issued = timezone.localdate()
        study.save(update_fields=['issued'])
        texts.send('zeugnis', study.person, reverse('exams:transcript', args=[study.pk]), studiengang=study.program.title)
        messages.success(request, f'Zeugnis ausgestellt. Die Exmatrikulation steht an: {Office.holding("manage_students") or "Studierendenverwaltung"}.')
    return redirect('exams:transcript', study.pk)


def certificate(request, pk):
    """The Urkunde conferring the degree, once the Zeugnis is issued."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk, issued__isnull=False)
    office = request.user.may('decide_results', study)
    if not (office or study.person == request.user):
        raise PermissionDenied
    return render(request, 'exams/certificate.html', {
        'study': study, 'completed': achievements(study)['completed'], 'copy': not office,
        'signature': texts.block('unterschrift_urkunden'), 'sender': texts.block('absender_leistungen'),
    })


def supplement(request, pk):
    """The Diploma Supplement to the Zeugnis: in English, in the sections of the European model. For the office a draft until
    every credit is earned; for graduates a copy of their own."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk)
    office = request.user.may('decide_results', study)
    if not (office or study.person == request.user and study.issued):
        raise PermissionDenied
    if not study.program.level:
        raise Http404
    found = achievements(study)
    if not (office or found['finished']):
        raise Http404
    return render(request, 'exams/supplement.html', found | {
        'copy': not office,
        'verbal': verbal(found['average'], english=True) if found['average'] else '', 'grades': GRADES,
        'awarding': texts.block('ds_hochschule'), 'system': texts.block('ds_hochschulsystem'),
    })
