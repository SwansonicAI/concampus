from decimal import InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import texts
from core.access import require
from core.models import Term
from core.text import matching
from core.views import without_term
from courses import panel
from courses.forms import AddForm, ExamForm, SlotForm
from courses.views import enrolled
from courses.models import Course, Participation
from curriculum.models import Module
from curriculum.views import plan as study_plan
from students.models import Study

EXAMS = [Course.Kind.EXAM, Course.Kind.RECOGNITION]


def verbal(grade):
    """German grade in words, as on a Zeugnis (RPSO § 15)."""
    for limit, word in [(1.5, 'sehr gut'), (2.5, 'gut'), (3.5, 'befriedigend'), (4.0, 'ausreichend')]:
        if grade <= limit:
            return word
    return 'nicht ausreichend'


def attempts(person, module):
    return Participation.objects.filter(study__person=person, module=module, passed=False, course__released__isnull=False).count()


def offered(person):
    """Exams of this term the person may still register for: their programme's modules, due by now, not yet passed."""
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    passed = Participation.objects.filter(study__in=studies, passed=True).values('module')
    semester = max((s.semester for s in studies), default=0)
    return (
        Course.objects.filter(term=Term.current(), kind=Course.Kind.EXAM, modules__program__studies__in=studies, deadline__gte=timezone.localdate())
        .filter(modules__semester__lte=semester).exclude(participations__study__in=studies).exclude(modules__in=passed)
        .annotate(date=Min('bookings__start')).distinct()
    )


def index(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    context = {'term': term, 'terms': Term.objects.filter(courses__kind=Course.Kind.EXAM).distinct()}
    exams = (
        Course.objects.filter(term=term, kind__in=EXAMS).select_related('subject').prefetch_related('staff')
        .annotate(date=Min('bookings__start'), size=Count('participations', distinct=True))
        .annotate(graded=Count('participations', filter=Q(participations__passed__isnull=False), distinct=True))
        .order_by('date', 'title')
    )
    if person.roles & {'manage_exams', 'decide_results'}:
        context['exams'] = exams
    elif 'teacher' in person.roles:
        context['exams'] = exams.filter(staff=person)
    if 'student' in person.roles:
        studies = person.studies.filter(status=Study.Status.ENROLLED)
        mine = Participation.objects.filter(study__in=studies, course__kind=Course.Kind.EXAM).select_related('course__term', 'module')
        context.update(mine=mine.order_by('-course__term__start'), offered=offered(person), studies=studies)
    return render(request, 'exams/index.html', context)


def exam(request, pk):
    person = request.user
    course = get_object_or_404(Course.objects.select_related('term', 'subject'), pk=pk, kind__in=EXAMS)
    office, decide = 'manage_exams' in person.roles, 'decide_results' in person.roles
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
        'rows': rows,
        'columns': [e.pk for e in columns],
        'panel': course.kind in Course.PANEL,
        'office': office,
        'decide': decide,
        'history': Course.visible_to(person.roles),
        'slots': slots,
        'modules': course.modules.select_related('program'),
        'bookings': course.bookings.select_related('room'),
        'missing': sum(1 for p in rows if p.passed is None),
        'adding': AddForm(),
        'enrolled': enrolled() if office else [],
    })


@require('manage_exams')
def edit(request, pk=None):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM) if pk else Course(term=Term.pick(request.GET.get('semester')), kind=Course.Kind.EXAM)
    form = ExamForm(request.POST or None, instance=course)
    if form.is_valid():
        course = form.save()
        messages.success(request, 'Gespeichert. Kandidat:innen melden sich bis zum Anmeldeschluss selbst an.')
        return redirect(course)
    back = course.get_absolute_url() if pk else reverse('exams:index')
    return render(request, 'courses/form.html', {'form': form, 'course': course, 'menu': 'exams/_nav.html', 'heading': 'Neue Prüfung', 'back': back})


@require('manage_exams')
@require_POST
def plan(request, pk):
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM)
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
    office = 'decide_results' in request.user.roles
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


def transcript(request, pk):
    """Leistungsübersicht or, once all credits are earned, Zeugnis."""
    study = get_object_or_404(Study.objects.select_related('person', 'program', 'subject'), pk=pk)
    if study.person != request.user and 'view_files' not in request.user.roles:
        raise PermissionDenied
    tree, average = study.progress()
    earned, total = sum(m.earned for m in tree), sum(m.credits for m in tree)
    rows = [r for r in study_plan(tree, study.program.semesters) if r['kind'] != 'part' and r['node'].earned or r['kind'] == 'part' and r['exam'] and any(i.done for i in r['items'])]
    return render(request, 'exams/transcript.html', {
        'study': study, 'rows': rows, 'earned': earned, 'total': total, 'average': average, 'honours': None not in (average, study.program.honours) and average <= study.program.honours,
        'complete': earned >= total and 'decide_results' in request.user.roles,  # a Zeugnis comes from the office
        'verbal': verbal(average) if average else '', 'today': timezone.localdate(),
        'sender': texts.block('absender_leistungen'), 'signature': texts.block('unterschrift_zeugnisse'),
    })
