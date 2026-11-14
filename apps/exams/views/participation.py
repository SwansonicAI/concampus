"""A student's part in an exam: registering, withdrawing, handing in, attests, Nachteilsausgleich."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import dates, texts
from core.access import require
from core.models import Consent, Office
from courses.models import Course, Participation
from students.models import Document, DocumentType, Study
from ..results import exhausted


def filed(study, request, what):
    """The attest an office files for `what`, with the person's signed consent: the scan, or the day the paper arrived;
    (document, '') or (None, why not)."""
    kind, file, text = DocumentType.system('attest'), request.FILES.get('file'), request.POST.get('received', '').strip()
    try:
        received = text and dates.received(text)
    except forms.ValidationError as error:
        return None, f'Attest eingegangen am: {error.messages[0]}'
    if not (file or received):
        return None, 'Bitte das Attest hochladen oder angeben, wann es auf Papier eingegangen ist.'
    if file and (refusal := kind.refuses(file)):
        return None, refusal
    if not request.POST.get('consent'):
        return None, 'Ein Attest lässt sich nur mit der unterschriebenen Einwilligung verbuchen.'
    consent = Consent.give(study.person, what, request.user)
    if file:
        return Document.hand_in(study, kind, file, consent), ''
    title = f'Attest {what}, auf Papier eingegangen am {date_format(received, "j.n.Y")}'
    return Document.objects.create(person=study.person, study=study, kind=kind, title=title, consent=consent), ''


@require_POST
def attest(request, pk):
    """Rücktritt mit triftigem Grund: the office files the attest and marks RT, which is no attempt."""
    course = get_object_or_404(Course, pk=pk, kind=Course.Kind.EXAM, released=None)
    if not request.user.may('decide_results', course):
        raise PermissionDenied('Einen Rücktritt verbucht das Amt, das die Ergebnisse einträgt.')
    p = get_object_or_404(course.participations.select_related('study__person'), pk=request.POST.get('participation'), passed=None)
    attest, refusal = filed(p.study, request, f'zum Rücktritt von {course.title}')
    if refusal:
        messages.error(request, refusal)
    else:
        p.remark, p.grade, p.passed = Participation.Remark.EXCUSED, None, None
        p.save(update_fields=['remark', 'grade', 'passed'])
        messages.success(request, f'Rücktritt von {p.study.person.first_name} {p.study.person.last_name} verbucht; das Attest liegt in der Akte.')
    return redirect(course)


@require('grant_compensation')
@require_POST
def compensate(request, pk):
    """Nachteilsausgleich: the Prüfungsausschuss grants a measure on an Antrag with an attest, by Bescheid;
    examiners see the measure, never the attest. Empty, it ends."""
    study = get_object_or_404(request.user.within(Study.objects.select_related('person'), 'grant_compensation'), pk=pk, status=Study.Status.ENROLLED)
    measure = request.POST.get('compensation', '').strip()[:200]
    attest, refusal = filed(study, request, 'zum Nachteilsausgleich') if measure and measure != study.compensation else (None, '')
    if refusal:
        messages.error(request, refusal)
    else:
        study.compensation = measure
        study.save(update_fields=['compensation'])
        if measure:
            study.notify('nachteilsausgleich', study.get_absolute_url(), massnahme=measure)
        messages.success(request, 'Nachteilsausgleich gewährt, Bescheid versandt.' if measure else 'Nachteilsausgleich beendet.')
    return redirect(study)


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
    elif not request.POST.get('declared'):
        messages.error(request, 'Zur Abgabe gehört die Selbstständigkeitserklärung.')
    elif refusal := kind.refuses(file):
        messages.error(request, refusal)
    else:
        handed = Document.hand_in(p.study, kind, file)
        handed.declared = timezone.now()
        handed.save(update_fields=['declared'])
        messages.success(request, 'Abschlussarbeit abgegeben. Die Gutachter:innen können sie jetzt öffnen.')
    return redirect(p.course if office else 'exams:index')


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
    elif exhausted(study):  # as for courses: with one module failed for good, nothing new is taken
        messages.error(request, 'Der Prüfungsanspruch ist erloschen; neue Anmeldungen sind nicht möglich.')
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
    if participation.withdrawable and participation.passed is None:
        participation.delete()
        messages.success(request, 'Abgemeldet.')
    else:
        until = 'bis zur Prüfung' if participation.course.withdrawable else f'bis zum {date_format(participation.course.withdraw_by, "j.n.Y")}'
        messages.error(request, f'Abmelden ging nur {until}. Bei triftigem Grund ist ein Rücktritt mit Nachweis möglich; zuständig: {Office.holding("decide_results", participation.study.program) or "das zuständige Amt"}.')
    return redirect('exams:index')
