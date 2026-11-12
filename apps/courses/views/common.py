"""Helpers several course pages share: who runs a course, who sees it, who is queued."""
from django.db.models import Count, Min, OuterRef, Q, Subquery
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from core import texts
from core.access import CODENAMES
from students.models import Study
from ..models import Course, Participation

TEACHING = [Course.Kind.COURSE, Course.Kind.LESSON]
MANAGERS = {Course.Kind.EXAM: 'manage_exams', Course.Kind.RECOGNITION: 'recognise', Course.Kind.AUDITION: 'run_auditions'}


def teaches(person, course):
    """Who runs a course: its staff, and whoever manages courses of its kind where it belongs."""
    return course.staff.filter(pk=person.pk).exists() or person.may(MANAGERS.get(course.kind, 'manage_courses'), course)


def ahead():
    """Subquery: the other students of the same course who registered earlier, or at the same moment and were entered
    earlier."""
    earlier = Participation.objects.filter(
        Q(created__lt=OuterRef('created')) | Q(created=OuterRef('created'), pk__lt=OuterRef('pk')), course=OuterRef('course'),
    ).exclude(study=OuterRef('study'))
    return Subquery(earlier.values('course').annotate(n=Count('study', distinct=True)).values('n')[:1])


def queued(participation):
    """Where a participation annotated with ahead() stands on its course's waitlist, from 1; None with a place."""
    course, before = participation.course, participation.ahead or 0
    if course.allocation == Course.Allocation.ORDER and course.capacity is not None and before >= course.capacity:
        return before - course.capacity + 1
    return None


def admit(course, before):
    """Tell who has moved up from the waitlist, `before` being Course.waitlist() then, that they have a place."""
    for person in {p.study.person for p in course.participations.filter(pk__in=set(before) - set(course.waitlist())).select_related('study__person')}:
        texts.send('nachgerueckt', person, course.get_absolute_url(), veranstaltung=course.title, semester=course.term)


def seen(course, person, rows):
    """The participations of a course its teacher sees: a coach of an ensemble course, not its office, those of their own
    ensembles' members; a teacher of groups of a course, those in them."""
    if person.may('manage_courses', course):
        return rows
    if course.allocation == Course.Allocation.ENSEMBLE:
        coached = set(Study.objects.filter(ensembles__course=course, ensembles__coach=person).values_list('pk', flat=True))
        return [p for p in rows if p.study_id in coached]
    taught = set(course.groups.filter(staff=person).values_list('pk', flat=True))
    return [p for p in rows if p.group_id in taught] if taught else rows


def taking(group):
    """How many students a group's places are taken by."""
    return group.participations.values('study').distinct().count()


def back(request, course):
    """Where a form that came with `next` leads back to, else the course."""
    to = request.POST.get('next', '')
    return redirect(to if url_has_allowed_host_and_scheme(to, {request.get_host()}) else course.get_absolute_url())


def since(person):
    """For a student and nobody else: terms from their first, as filter arguments."""
    if person.roles & {'teacher', *CODENAMES} or 'student' not in person.roles:
        return {}
    return {'start__gte': person.studies.aggregate(first=Min('start_term__start'))['first']}
