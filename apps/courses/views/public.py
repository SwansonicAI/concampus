"""The Vorlesungsverzeichnis for everyone, without signing in, where the Hochschule wants it (Institution.public): each
published semester's Lehrveranstaltungen with teachers, Lehrform, time, room and what they count for; never who takes
part."""
from collections import defaultdict

from django.contrib.auth.decorators import login_not_required
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.models import Institution, Term
from core.text import matching
from curriculum.models import Program
from ..models import Area, Course
from .catalogue import SEARCHED
from .rollover import described, usual


def opened():
    if not Institution.get().public:
        raise Http404


@login_not_required
def catalogue(request):
    opened()
    terms = Term.objects.filter(planning=False, courses__kind=Course.Kind.COURSE).distinct().order_by('-start')
    term = Term.pick(request.GET.get('semester'))
    if term not in terms:
        term = terms.first()
    courses = term.courses.filter(kind=Course.Kind.COURSE) if term else Course.objects.none()
    offered = courses
    program, area, q = request.GET.get('studiengang', ''), request.GET.get('fachgebiet', ''), request.GET.get('q', '').strip()
    if program.isdigit():
        courses = courses.filter(modules__program=program)
    if area.isdigit():
        courses = courses.filter(area=area)
    if q:
        courses = matching(courses, q, *SEARCHED)
    courses = list(
        courses.select_related('area', 'format').prefetch_related('staff').distinct()
        .order_by(F('area__order').asc(nulls_last=True), 'area__name', 'title')
    )
    slots = usual(courses)
    for c in courses:
        c.slot = described(slots.get(c.pk))
    return render(request, 'courses/public.html', {
        'term': term, 'terms': terms, 'courses': courses, 'q': q, 'program': program, 'area': area,
        'programs': Program.planned(), 'areas': Area.objects.filter(courses__in=offered.values('pk')).distinct(),
    })


@login_not_required
def course(request, pk):
    opened()
    course = get_object_or_404(Course.objects.select_related('term', 'area', 'format'), pk=pk, kind=Course.Kind.COURSE, term__planning=False)
    counts = defaultdict(list)  # Studiengang: its modules
    for m in course.modules.select_related('program').order_by('program__name', 'code'):
        counts[m.program].append(m)
    groups = list(course.groups.order_by('number'))
    slots = usual(groups, by='group') if groups else usual([course])
    return render(request, 'courses/public_course.html', {
        'course': course, 'staff': course.staff.all(), 'counts': dict(counts),
        'slot': described(slots.get(course.pk)), 'groups': [(g, described(slots.get(g.pk))) for g in groups],
        'dates': course.bookings.filter(start__gte=timezone.now(), participants=None, ensemble=None, category__blocks=True).select_related('room', 'group')[:10],
    })
