"""Students registering for courses, switching, withdrawing, and answering requests."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from core import texts
from exams.results import exhausted
from students.models import Study
from ..models import Course, Participation, Signup
from .common import TEACHING, admit, back, taking, teaches
from .timetable import fits


def join(course, study, module, group, note=''):
    """A place in a course taught in groups, or why not: one place a person in a course, none past a group's last; without
    a group where the Institut gives them out. The course is locked meanwhile, so neither slips through, however many
    register in the same moment."""
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        if mine := course.participations.filter(study__person=study.person_id).select_related('group').first():
            return f'Sie haben schon einen Platz{f" in Gruppe {mine.group.number}" if mine.group else ""}.' + (' Die Gruppe wechseln Sie oben unter „Meine Anmeldung“.' if group else '')
        if group and taking(group) >= group.capacity:
            return f'Gruppe {group.number} ist inzwischen voll. Bitte eine andere wählen.'
        Participation.objects.create(course=course, study=study, module=module, group=group, note=note)
    return ''


def moved(course, group, participation):
    """Into another group of the course, or why not: only where a place is free, while the course is locked; the old
    place is given up in the same step."""
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=course.pk)
        if taking(group) >= group.capacity:
            return f'Gruppe {group.number} ist voll.'
        course.participations.filter(study=participation.study_id).update(group=group)  # each module it counts for
    return ''


@require_POST
def register(request, pk):
    course = get_object_or_404(Course, pk=pk, kind__in=TEACHING)
    study = fits(course, get_object_or_404(Study, pk=request.POST.get('study'), person=request.user, status=Study.Status.ENROLLED))
    module, reason = study.owed, request.POST.get('grund', '').strip()[:200]
    late = not course.is_open and not course.upcoming  # the teachers may still take someone while the term runs
    besides = besides_focus(course, study)
    choosing = course.grouping == Course.Grouping.CHOICE
    group = course.groups.filter(pk=request.POST.get('gruppe') or None).first() if choosing else None
    full = choosing and not group and course.groups.exists()  # every group full: the teachers may open another
    if course.cancelled or course.term.planning:
        messages.error(request, 'Diese Veranstaltung entfällt.' if course.cancelled else 'Das Semester ist noch nicht veröffentlicht.')
    elif course.allocation == Course.Allocation.STAFF:
        messages.error(request, 'Die Lehrenden teilen die Plätze ein.')
    elif course.allocation == Course.Allocation.ENSEMBLE:
        messages.error(request, 'Bitte als Ensemble anmelden.')
    elif course.upcoming:
        messages.error(request, f'Die Anmeldung beginnt am {date_format(course.opens, "j.n.Y")}.')
    elif late and course.term.end < timezone.localdate():
        messages.error(request, 'Der Anmeldeschluss ist vorbei.')
    elif study.concern and not reason:
        messages.error(request, 'Bitte kurz angeben, warum Sie sich dennoch anmelden; die Lehrenden sehen es.')
    elif lost(study):
        messages.error(request, 'Der Prüfungsanspruch ist erloschen; neue Anmeldungen sind nicht möglich.')
    elif course.allocation == Course.Allocation.CHOICE or late or full:
        if Signup.objects.get_or_create(course=course, study=study, defaults={'module': module, 'note': reason or request.POST.get('note', '').strip()})[1]:
            texts.send('angefragt', request.user, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
        messages.success(request, f'{"Nachmeldung angefragt" if late else "Angefragt"}. Die Lehrenden entscheiden; die Antwort kommt in Ihr Postfach.{besides}')
    elif group or course.groups.exists():
        if refused := join(course, study, module, group, reason):
            messages.error(request, refused)
        else:
            messages.success(request, f'Sie sind angemeldet, in Gruppe {group.number}.{besides}' if group else f'Sie sind angemeldet. Ihre Gruppe teilt das Institut zu; Sie erfahren sie per Nachricht.{besides}')
    elif course.participations.filter(study__person=request.user).exists():
        messages.error(request, 'Sie sind bereits angemeldet.')
    else:
        participation, created = Participation.objects.get_or_create(course=course, study=study, module=module, defaults={'note': reason})
        if place := course.waitlist().get(participation.pk):
            if created:
                texts.send('warteliste', request.user, course.get_absolute_url(), veranstaltung=course.title, semester=course.term, platz=place)
            messages.success(request, f'Sie stehen auf Platz {place} der Warteliste. Wird ein Platz frei, rücken Sie nach und erhalten eine Nachricht.{besides}')
        else:
            messages.success(request, f'Sie sind angemeldet.{besides}')
    return back(request, course)


def lost(study):
    """Whether the study's Prüfungsanspruch is lost: nothing new to take then."""
    return bool(exhausted(study))


def besides_focus(course, study):
    """What to tell a student who registers for a course that counts only for a Schwerpunkt other than theirs, or for
    one while none is chosen; '' otherwise."""
    chosen_by = study.program.chosen_by()
    options = {chosen_by.get(m) for m in course.modules.filter(program=study.program).values_list('pk', flat=True)}
    if not options or None in options or study.focus_id in {o.pk for o in options}:
        return ''
    names = ' oder '.join(sorted(o.title for o in options))
    if study.focus_id:
        return f' Sie zählt für {names}, nicht für Ihren Schwerpunkt {study.focus.title}.'
    return f' Sie zählt für {names}; Ihren Schwerpunkt haben Sie noch nicht gewählt.'


@require_POST
def choose(request, pk):
    """Accept a wish for a place, or decline it; either way the student hears."""
    signup = get_object_or_404(Signup.objects.select_related('course__term', 'study__person'), pk=pk)
    course, person = signup.course, signup.study.person
    if not teaches(request.user, course):
        raise PermissionDenied
    if request.POST.get('decision') == 'yes':
        group = course.groups.filter(pk=request.POST.get('gruppe') or None).first()
        Participation.objects.get_or_create(course=course, study=signup.study, module=signup.module, defaults={'group': group})
        texts.send('eingetragen', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    else:
        texts.send('kein_platz', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)
    signup.delete()
    messages.success(request, f'{person}: {"zugelassen" if request.POST.get("decision") == "yes" else "abgesagt"}. Nachricht versandt.')
    return redirect(course)


@require_POST
def retract(request, pk):
    signup = get_object_or_404(Signup, pk=pk, study__person=request.user)
    signup.delete()
    messages.success(request, f'Anfrage für „{signup.course.title}“ zurückgezogen.')
    return back(request, signup.course)


@require_POST
def withdraw(request, pk):
    participation = get_object_or_404(Participation.objects.select_related('course__term'), pk=pk, study__person=request.user, course__kind__in=TEACHING, passed=None)
    if participation.course.leavable:
        before = participation.course.waitlist()
        participation.course.participations.filter(study=participation.study_id, passed=None).delete()  # each module it counts for
        admit(participation.course, before)
        messages.success(request, f'Von „{participation.course.title}“ abgemeldet.')
    elif participation.course.is_open:
        messages.error(request, 'Abmelden geht hier über die Lehrenden.')
    else:
        messages.error(request, 'Der Abmeldeschluss ist vorbei.')
    return back(request, participation.course)


@require_POST
def switch(request, pk):
    """A participant takes a free place in another group of the course while registration runs, giving up their own in
    the same step."""
    participation = get_object_or_404(Participation.objects.select_related('course__term', 'group'), pk=pk, study__person=request.user, group__isnull=False, passed=None)
    course = participation.course
    group = get_object_or_404(course.groups, pk=request.POST.get('gruppe') or None)
    if not course.is_open or course.grouping != Course.Grouping.CHOICE:
        messages.error(request, 'Die Gruppe wechseln geht bis zum Anmeldeschluss, wo Sie sie selbst wählen; sonst über die Lehrenden.')
    elif refused := moved(course, group, participation):
        messages.error(request, refused)
    else:
        messages.success(request, f'Gewechselt: jetzt in Gruppe {group.number}.')
    return back(request, course)
