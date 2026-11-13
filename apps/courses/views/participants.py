"""Who takes part in a course, as its staff see and change it."""
import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import texts
from core.apps import plural
from core.forms import ComposeForm
from core.models import Export, Person
from students.models import Study
from .. import panel
from ..forms import AddForm
from ..models import Course, Participation, Recusal, marked, owed_module
from .common import TEACHING, admit, ahead, queued, seen, teaches
from .timetable import clashes, fits, weight


@require_POST
def add(request, pk):
    """Enter an enrolled student into any course, exam included, by name or Matrikelnummer."""
    course = get_object_or_404(Course, pk=pk)
    if not teaches(request.user, course) or course.allocation == Course.Allocation.ENSEMBLE and not request.user.may('manage_courses', course):
        raise PermissionDenied
    form = AddForm(request.POST, course=course)
    found = Study.find(form.cleaned_data['who']) if form.is_valid() else []
    if len(found) != 1:
        names = ', '.join(str(s.person) for s in found)
        messages.error(request, f'Mehrere passen: {names}. Bitte genauer.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return redirect(course)
    study, exam = found[0], course.kind == Course.Kind.EXAM
    module = owed_module(course, study) or (course.modules.filter(program=study.program_id).first() if exam else None)  # the next module due, as the class filling does
    if exam and (refused := course.refuses(study, module)):
        messages.error(request, f'{study.person} nicht eingetragen: {refused}')
        return redirect(course)
    group = form.cleaned_data.get('group')
    if not exam and (there := course.participations.filter(study__person=study.person_id).select_related('group').first()):
        messages.success(request, f'{study.person} ist bereits eingetragen{f", in Gruppe {there.group.number}" if there.group else ""}.')
        return redirect(course)
    participation, created = Participation.objects.get_or_create(course=course, study=study, module=module, defaults={'group': group})
    if not created:
        messages.success(request, f'{study.person} ist bereits eingetragen.')
    elif exam:  # the office registers whoever the regulations allow, in the registration period or not
        texts.send('pruefungsanmeldung', study.person, reverse('exams:index'), pruefung=course.title, modul=module)
        attempt = next(p.attempt for p in panel.sheet(course)[1] if p.pk == participation.pk)
        messages.success(request, f'{study.person} eingetragen, {attempt}. Versuch{"" if course.is_open else ", außerhalb der Anmeldefrist"}.')
    else:
        texts.send('eingetragen', study.person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
        fits(course, study)
        dates = (group.bookings if group else course.bookings.filter(group=None, participants=None)).filter(start__gte=timezone.now()).select_related('room')
        _, clashing = weight(clashes(study.person, dates), dates)
        notes = ' '.join(filter(None, [study.concern, clashing and f'An {plural(clashing, "Termin", "Terminen")} überschneidet sie sich mit dem Stundenplan.']))
        done = f'{study.person} eingetragen' + (f', für {module.code}' if module else '') + (f', in Gruppe {group.number}' if group else '') + '.'
        (messages.warning if notes else messages.success)(request, f'{done} {notes}'.strip())
    return redirect(course)


@require_POST
def drop(request, pk):
    """Take someone out while no mark is given: of an exam the office entered them in by mistake, of a course its
    teachers, say someone who never came. They hear of it, the waitlist moves up, the Verlauf keeps it."""
    course = get_object_or_404(Course.objects.exclude(allocation=Course.Allocation.ENSEMBLE), pk=pk, kind__in=[Course.Kind.EXAM, Course.Kind.COURSE], released=None)
    exam = course.kind == Course.Kind.EXAM
    if not (request.user.may('manage_exams', course) if exam else teaches(request.user, course)):
        raise PermissionDenied
    rows = course.participations.select_related('study__person').filter(scores=None, grade=None)
    if exam:
        chosen = [get_object_or_404(rows, pk=request.POST.get('participation'))]
    else:
        chosen = [p for p in seen(course, request.user, list(rows.filter(passed=None, study__in=request.POST.getlist('study'))))]
        if not chosen:
            messages.error(request, 'Niemand ausgetragen. Bitte ankreuzen, wen; wer ein Ergebnis hat, bleibt.')
            return redirect(course)
    before = course.waitlist()
    for participation in chosen:
        participation.delete()
        texts.send('ausgetragen', participation.study.person, reverse('exams:index') if exam else course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    admit(course, before)
    messages.success(request, f'{", ".join(str(p.study.person) for p in chosen)} ausgetragen.')
    return redirect(course)


def roster(request, pk):
    """A course's participants as CSV, to print or keep, as its teachers see them."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    rows = seen(course, request.user, list(course.participations.select_related('study__person', 'study__program', 'study__subject', 'module', 'group').annotate(ahead=ahead())))
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="teilnehmende-{course.pk}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Matrikelnummer', 'Nachname', 'Vorname', 'Studiengang', 'Hauptfach', 'Modul', 'Gruppe', 'Angemeldet', 'Warteliste', 'Note', 'Bestanden'])
    Export.log(request.user, f'Teilnehmende {course.title}, {course.term}', len(rows))
    for p in sorted(rows, key=lambda p: (p.study.person.last_name, p.study.person.first_name)):
        writer.writerow([
            p.study.person.matriculation, p.study.person.last_name, p.study.person.first_name, p.study.program.title, p.study.subject,
            p.module.code if p.module else '', p.group.number if p.group else '', f'{timezone.localtime(p.created):%d.%m.%Y %H:%M}', queued(p) or '',
            str(p.grade if p.grade is not None else '').replace('.', ','), {True: 'ja', False: 'nein'}.get(p.passed, ''),
        ])
    return response


def write(request, pk):
    """A message from a course's teachers to its participants, the waitlist included: the ticked, else all."""
    course = get_object_or_404(Course.objects.select_related('term'), pk=pk, kind__in=TEACHING)
    if not teaches(request.user, course):
        raise PermissionDenied
    chosen = set(request.POST.getlist('study'))
    rows = seen(course, request.user, list(course.participations.select_related('study__person')))
    studies = list({p.study_id: p.study for p in rows if not chosen or str(p.study_id) in chosen}.values())
    people = sorted({s.person for s in studies}, key=lambda p: p.name)
    form = ComposeForm(request.POST if 'subject' in request.POST else None)
    if people and form.is_valid():
        form.send(people, str(request.user), course.get_absolute_url())
        messages.success(request, f'Nachricht an {plural(len(people), "Person", "Personen")} gesendet.')
        return redirect(course)
    return render(request, 'students/compose.html', {
        'form': form, 'people': people, 'studies': studies, 'back': course.get_absolute_url(), 'sender': request.user, 'course': course,
    })


@require_POST
def recuse(request, pk):
    """An examiner stands back from a candidate before marking them, giving the reason. The office that makes up the
    panel appoints a substitute in their place, or lifts it again. The result waits for every place to be marked."""
    course = get_object_or_404(Course, pk=pk, kind__in=Course.PANEL, released=None)
    person = request.user
    office = course.composes(person)
    overrides = {p.pk: p.override for p in panel.sheet(course)[1]}  # a grade set by hand stays; one that was the mean follows the marks
    if lifted := request.POST.get('aufheben', ''):
        participation, _, examiner = lifted.partition('-')
        if not office:
            raise PermissionDenied('Eine Befangenheit hebt nur das Amt auf, das die Kommission zusammenstellt.')
        Recusal.objects.filter(participation__course=course, participation=participation, examiner=examiner).delete()
        participation, done = int(participation), 'Befangenheit aufgehoben.'
    elif replaced := request.POST.get('ersetzen', ''):
        if not office:
            raise PermissionDenied('Einen Ersatz bestellt nur das Amt, das die Kommission zusammenstellt.')
        recusal = get_object_or_404(Recusal.objects.select_related('participation__study__person', 'examiner'), pk=replaced, participation__course=course)
        substitute = Person.teachers().exclude(pk__in=course.staff.values('pk')).filter(pk=request.POST.get(f'ersatz-{recusal.pk}') or None).first()
        if substitute is None:
            messages.error(request, 'Bitte einen Ersatz wählen.')
            return redirect(course)
        recusal.substitute = substitute
        recusal.save(update_fields=['substitute'])
        texts.send(
            'ersatz', substitute, course.get_absolute_url(), pruefung=course.title, kandidat=recusal.participation.study.person,
            fuer=recusal.examiner, von=person,
        )
        participation, done = recusal.participation_id, f'{substitute} wertet {recusal.participation.study.person} an Stelle von {recusal.examiner}.'
    else:
        participation = get_object_or_404(course.participations.exclude(scores__in=marked(person)), pk=request.POST.get('participation'))
        reason = request.POST.get('reason', '').strip()[:200]
        if not (reason and course.staff.filter(pk=person.pk).exists()):
            raise PermissionDenied
        Recusal.objects.get_or_create(participation=participation, examiner=person, defaults={'reason': reason})
        participation, done = participation.pk, f'Befangenheit angezeigt. Sie werten {participation.study.person} nicht.'
    rows = [p for p in panel.sheet(course)[1] if p.pk == participation]
    for p in rows:
        p.override = overrides[p.pk]
    panel.save({}, rows, [], decide=False)  # the result as the others' marks make it
    messages.success(request, done)
    back = request.POST.get('next', '')
    return redirect(back if url_has_allowed_host_and_scheme(back, request.get_host()) else course.get_absolute_url())
