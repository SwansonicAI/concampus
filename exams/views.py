from datetime import timedelta
from decimal import InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Min, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import texts
from core.access import kept, require
from core.models import Term
from core.text import matching
from core.views import without_term
from courses import panel
from courses.forms import AddForm, ExamForm, SlotForm, ThesisForm
from courses.views import credited, enrolled
from courses.models import Course, Participation
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


def attempts(person, module):
    return Participation.objects.filter(study__person=person, module=module, passed=False, course__released__isnull=False).count()


def offered(person):
    """Exams of this term the person may still register for: their programme's modules, due by now, not yet passed;
    artistic exams held per Hauptfach only in their own."""
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    passed = Participation.objects.filter(study__in=studies, passed=True).values('module')
    semester = max((s.semester for s in studies), default=0)
    return (
        Course.objects.filter(term=Term.current(), kind=Course.Kind.EXAM, modules__program__studies__in=studies, deadline__gte=timezone.localdate())
        .filter(Q(subject=None) | Q(subject__in=studies.values('subject')))
        .filter(modules__semester__lte=semester).exclude(participations__study__in=studies).exclude(modules__in=passed)
        .annotate(date=Min('bookings__start')).distinct()
    )


def index(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.objects.filter(courses__kind=Course.Kind.EXAM).distinct()}
    exams = (
        Course.objects.filter(term=term, kind__in=EXAMS).select_related('subject').prefetch_related('staff')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True), candidate=Min('participations__study__person__last_name'))
        .annotate(graded=Count('participations', filter=Q(participations__passed__isnull=False), distinct=True))
        .order_by('date', 'title')
    )
    office = bool(person.roles & {'manage_exams', 'decide_results'})
    if office or 'teacher' in person.roles:
        program, stand, q = (request.GET.get(key, '').strip() for key in ('studiengang', 'stand', 'q'))
        exams = exams.filter(pk__in=person.within(Course.objects.all(), 'manage_exams', 'decide_results').values('pk')) if office else exams.filter(staff=person)
        if program.isdigit():
            exams = exams.filter(pk__in=Course.objects.filter(modules__program=program).values('pk'))
        candidate = [f'participations__study__person__{name}' for name in ('first_name', 'last_name', 'matriculation')]
        exams = list(matching(exams, q, 'title', 'subject__name', 'staff__last_name', *candidate))
        for c in exams:
            c.state = 'freigegeben' if c.released else 'bewertung' if c.graded else 'offen'
        context.update(
            exams=[c for c in exams if not stand or c.state == stand], q=q, stand=stand, program=program, programs=Program.objects.all(),
            states=[(key, label, sum(c.state == key for c in exams)) for key, label in STATES],
        )
    if 'student' in person.roles:
        studies = person.studies.filter(status=Study.Status.ENROLLED)
        mine = Participation.objects.filter(study__in=studies, course__kind=Course.Kind.EXAM).select_related('course__term', 'module')
        mine = list(mine.order_by('-course__term__start'))
        theses = list(
            Participation.objects.filter(study__in=studies, course__kind=Course.Kind.THESIS).select_related('course', 'module', 'study')
            .prefetch_related('course__staff').order_by('-created')
        )
        for p in theses:
            p.handed = handed_in(p)
        context.update(mine=mine, timed=any(p.slot for p in mine), offered=offered(person), studies=studies, theses=theses)
    return render(request, 'exams/index.html', context)


def exam(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=EXAMS)
    office, decide = person.may('manage_exams', course), person.may('decide_results', course)
    if not (office or decide or course.staff.filter(pk=person.pk).exists()):
        raise PermissionDenied
    staff, rows = panel.sheet(course)
    columns = staff if decide else [person]
    if request.method == 'POST':
        if course.released:
            raise PermissionDenied
        try:
            panel.save(request.POST, rows, columns, decide=decide)
        except InvalidOperation:
            messages.error(request, 'Bitte nur Zahlen zwischen 0 und 99 eintragen.')
        else:
            messages.success(request, 'Ergebnisse gespeichert.')
        return redirect(course)
    slots = SlotForm(request.GET or None, person=person) if office else None
    return render(request, 'exams/exam.html', {
        'course': course,
        'staff': staff,
        'rows': rows, 'timed': any(p.slot for p in rows), 'noted': any(p.note for p in rows),
        'columns': [e.pk for e in columns],
        'panel': course.kind in Course.PANEL,
        'office': office,
        'decide': decide,
        'history': Course.visible_to(person.roles),
        'slots': slots,
        'modules': credited(course),
        'bookings': course.bookings.select_related('room'),
        'missing': sum(1 for p in rows if p.passed is None),
        'handed': handed_in(rows[0]) if course.kind == Course.Kind.THESIS and rows else None,
        'adding': AddForm(),
        'enrolled': enrolled() if office else [],
    })


@require('manage_exams')
def edit(request, pk=None):
    exams = request.user.within(Course.objects.filter(kind__in=[Course.Kind.EXAM, Course.Kind.THESIS]), 'manage_exams')
    course = get_object_or_404(exams, pk=pk) if pk else Course(term=Term.pick(request.GET.get('semester')), kind=Course.Kind.EXAM)
    thesis = course.kind == Course.Kind.THESIS
    form = (ThesisForm if thesis else ExamForm)(request.POST or None, instance=course)
    if form.is_valid() and (saved := kept(form, request.user, 'manage_exams')):
        course = saved
        if thesis and 'deadline' in form.changed_data:
            study = course.participations.select_related('study__person', 'study__program', 'study__subject', 'study__start_term').get().study
            study.notify('verlaengerung', reverse('exams:index'), thema=course.title, frist=f'{course.deadline:%d.%m.%Y}')
        messages.success(request, 'Gespeichert.' if pk else 'Gespeichert. Kandidat:innen melden sich bis zum Anmeldeschluss selbst an.')
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
    form = SlotForm(request.POST, person=request.user)
    rows = list(course.participations.filter(slot=None).select_related('study__person').order_by('created'))
    if not form.is_valid() or not rows:
        messages.error(request, 'Bitte Beginn, Dauer und Raum angeben; alle Teilnehmenden haben schon Termine.' if rows else 'Alle Teilnehmenden haben schon Termine.')
        return redirect(course)
    try:
        panel.schedule(course, rows, form.cleaned_data['start'], form.cleaned_data['minutes'], form.cleaned_data['room'], request.user)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f'{len(rows)} Termine vergeben und Einladungen versandt.')
    return redirect(course)


@require_POST
def release(request, pk):
    """Release results to the candidates and lock them; only whoever decides results can reopen."""
    course = get_object_or_404(Course, pk=pk, kind__in=EXAMS)
    office = request.user.may('decide_results', course)
    if not (office or course.staff.filter(pk=request.user.pk).exists()):
        raise PermissionDenied
    if course.released:
        if not office:
            raise PermissionDenied
        course.released = None
        messages.success(request, 'Ergebnisse wieder zur Bearbeitung geöffnet.')
    else:
        if course.participations.filter(passed=None).exists():
            messages.error(request, 'Erst freigeben, wenn für alle Teilnehmenden ein Ergebnis eingetragen ist.')
            return redirect(course)
        course.released = timezone.now()
        for p in course.participations.select_related('study__person'):
            texts.send('ergebnis', p.study.person, reverse('exams:index'), pruefung=course.title)
        messages.success(request, 'Ergebnisse freigegeben und gesperrt.')
    course.save(update_fields=['released'])
    return redirect(course)


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM)
    study = get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED)
    module = course.modules.filter(program=study.program).first()
    if not course.is_open:
        messages.error(request, 'Die Anmeldefrist ist abgelaufen.')
    elif module is None:
        messages.error(request, 'Diese Prüfung gehört nicht zu Ihrem Studiengang.')
    elif course.subject_id not in (None, study.subject_id):
        messages.error(request, f'Diese Prüfung ist für das Hauptfach {course.subject}.')
    elif (module.semester or 0) > study.semester:
        messages.error(request, f'{module.code} ist erst ab dem {module.semester}. Fachsemester vorgesehen.')
    elif attempts(request.user, module) >= study.program.attempts:
        messages.error(request, f'Sie haben alle {study.program.attempts} Versuche für {module.code} ausgeschöpft.')
    else:
        Participation.objects.get_or_create(course=course, study=study, module=module)
        texts.send('pruefungsanmeldung', request.user, reverse('exams:index'), pruefung=course.title, modul=module)
        messages.success(request, 'Angemeldet. Die Bestätigung liegt in Ihrem Postfach.')
    return redirect('exams:index')


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation, pk=pk, study__person=request.user, course__kind=Course.Kind.EXAM)
    if participation.course.is_open and participation.passed is None:
        participation.delete()
        messages.success(request, 'Abgemeldet.')
    else:
        messages.error(request, 'Rücktritt ist nur bis zum Ende der Anmeldefrist möglich.')
    return redirect('exams:index')


class RecognitionForm(forms.Form):
    who = forms.CharField(label='Studierende:r', widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}))
    module = forms.CharField(label='Modul', help_text='Kürzel oder Titel, z. B. „MT-01 Klausur“ oder „Musiktheorie I“.')
    grade = forms.DecimalField(label='Note', required=False, min_value=1, max_value=4, decimal_places=1)
    note = forms.CharField(label='Herkunft', widget=forms.Textarea(attrs={'rows': 3}), help_text='Hochschule, Veranstaltung, Semester, ECTS.')

    def clean(self):
        data = super().clean()
        found = Study.find(data.get('who', ''))
        if len(found) != 1:
            names = ', '.join(str(s.person) for s in found)
            raise forms.ValidationError(f'Mehrere passen: {names}.' if found else 'Niemand Immatrikuliertes passt dazu.')
        study = data['study'] = found[0]
        modules = matching(Module.objects.filter(program=study.program, children=None), data.get('module', ''), 'code', 'title')
        if len(modules) != 1:
            raise forms.ValidationError(f'Kein eindeutiges Modul in {study.program} für „{data.get("module")}“.')
        data['module'] = modules[0]
        return data


@require('recognise')
def recognition(request):
    term = Term.current()
    if term is None:
        return without_term(request, 'ein Semester, in dem Anerkennungen verbucht werden')
    course, _ = Course.objects.get_or_create(term=term, kind=Course.Kind.RECOGNITION, defaults={'title': f'Anerkennungen {term}'})
    form = RecognitionForm(request.POST or None)
    if form.is_valid() and not request.user.may('recognise', form.cleaned_data['study']):
        form.add_error(None, 'Diese Person studiert außerhalb der Institute und Studiengänge, für die Ihr Amt zuständig ist.')
    if form.is_valid():
        data = form.cleaned_data
        Participation.objects.update_or_create(
            course=course, study=data['study'], module=data['module'],
            defaults={'grade': data['grade'], 'passed': True, 'note': data['note']},
        )
        texts.send('anerkennung', data['study'].person, modul=data['module'], herkunft=data['note'])
        messages.success(request, f'{data["module"]} für {data["study"].person} anerkannt.')
        return redirect('exams:recognition')
    return render(request, 'exams/recognition.html', {
        'form': form, 'course': course, 'enrolled': enrolled(),
        'recent': course.participations.select_related('study__person', 'module').order_by('-created')[:20],
    })


def achievements(study):
    """What a Leistungsübersicht, a Zeugnis and a Diploma Supplement show: the modules with their credits, and the grade so far."""
    tree, average = study.progress()
    earned, total, honours = sum(m.earned for m in tree), sum(m.credits for m in tree), study.program.honours
    rows = [r for r in study_plan(tree, study.program.semesters) if r['kind'] != 'part' and r['node'].earned or r['kind'] == 'part' and r['exam'] and any(i.done for i in r['items'])]
    return {
        'study': study, 'rows': rows, 'earned': earned, 'total': total, 'finished': earned >= total, 'average': average,
        'honours': None not in (average, honours) and average <= honours, 'today': timezone.localdate(), 'signature': texts.block('unterschrift_zeugnisse'),
    }


def transcript(request, pk):
    """Leistungsübersicht or, once all credits are earned, Zeugnis."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk)
    if not (study.person == request.user or request.user.may('view_files', study)):
        raise PermissionDenied
    found, office = achievements(study), request.user.may('decide_results', study)  # a Zeugnis comes from the office
    copy = not office and study.person == request.user and study.status == Study.Status.GRADUATED and found['finished']  # graduates print their own
    return render(request, 'exams/transcript.html', found | {
        'complete': found['finished'] and (office or copy), 'copy': copy, 'supplement': (office or copy) and study.program.level,
        'verbal': verbal(found['average']) if found['average'] else '', 'sender': texts.block('absender_leistungen'),
    })


def supplement(request, pk):
    """The Diploma Supplement to the Zeugnis: in English, in the sections of the European model. For the office a draft until
    every credit is earned; for graduates a copy of their own."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk)
    office = request.user.may('decide_results', study)
    if not (office or study.person == request.user and study.status == Study.Status.GRADUATED):
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
