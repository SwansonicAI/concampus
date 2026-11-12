"""The teaching load (Deputat): SWS per course and teacher, and the page that sums them."""
import re
from collections import Counter, defaultdict
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Count, F, Max, Min, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from core.access import require
from core.models import Institute, Institution, Person, Term
from core.shortcuts import without_term
from core.text import fold
from curriculum.models import Module
from students.models import Study
from ..models import Course, Ensemble, Group, Participation, Reduction
from .common import TEACHING


def deputat(courses, person):
    """A teacher's load in SWS: each course's hours; per participant in one-to-one teaching, per ensemble they coach in
    ensemble courses, per group they teach in courses taught in groups."""
    return sum(sws(c, c.coached) for c in coaching(courses.filter(cancelled=None), person).select_related('format', 'term').prefetch_related(
        'participations__module__program', 'origin__participations__module__program'))


def counted(rows, by, field='pk'):
    """How many distinct `field` of `rows`, filtered by OuterRef and grouped `by` it, as a subquery: counts side by
    side, which joins in one query would multiply."""
    return Coalesce(Subquery(rows.order_by().values(by).annotate(n=Count(field, distinct=True)).values('n')), 0)


def coaching(courses, person):
    """The courses with `size`, `grouped` (how many groups) and `coached`: the ensembles the person coaches, or the
    groups they teach, as share() counts them."""
    here = OuterRef('pk')
    return courses.annotate(
        size=counted(Participation.objects.filter(course=here), 'course', 'study'), grouped=counted(Group.objects.filter(course=here), 'course'),
        coached=counted(Ensemble.objects.filter(course=here, coach=person), 'course') + counted(Group.objects.filter(course=here, staff=person), 'course'),
    )


def sws(course, coached):
    """A course's SWS for one of its teachers: a lesson class each participation by the weekly minutes its plan gives,
    as many to an SWS as the Hochschule counts (Institution.lesson_minutes), else the course's hours; a class of a coming term not yet filled as the one it was
    taken over from. Otherwise the hours as often as `share` says. Reads `participations.all()` with their modules, and
    the origin's; prefetch them for many courses."""
    if course.kind == Course.Kind.LESSON:
        taking = course.participations.all() or (course.origin.participations.all() if course.origin_id and course.term.start > timezone.localdate() else [])
        return sum(minutes(p.module) or course.hours or 0 for p in taking)
    return (course.hours or 0) * share(course, coached)


def minutes(module):
    """The SWS of one lesson in a plan whose form says its weekly extent, 'E 90' in minutes or 'E 2' in SWS as its
    Studiengang reads it (Program.weekly); None where it does not."""
    found = module and re.search(r'(\d+)\s*$', module.description)
    return Decimal(found[1]) / (Institution.get().lesson_minutes if module.program.weekly == 'minuten' else 1) if found else None


def share(course, coached):
    """How many times a course's hours count for one of its teachers: per participant in one-to-one teaching, whether a
    lesson class or a course whose Lehrform counts so; per ensemble coached in ensemble courses, per group taught in a
    course taught in groups (`grouped`); else once."""
    if course.kind == Course.Kind.LESSON or course.format_id and course.format.per_head:
        return course.size
    return coached if course.allocation == Course.Allocation.ENSEMBLE or course.grouped else 1


def taught(person, term):
    """The person's class, each study with `lesson`, the person's own lesson class it is in in `term`, and else `until`,
    the last semester its Studienplan has Hauptfach lessons in: past it, none is due; before it, one is `missing`."""
    today = timezone.localdate()
    studies = list(
        Study.objects.filter(teacher=person, status=Study.Status.ENROLLED).select_related('person', 'program', 'subject', 'start_term')
        .annotate(fs=F('prior_terms') + Count('terms', filter=Q(terms__leave=False, terms__term__start__lte=today)))
    )
    lessons = dict(Participation.objects.filter(  # Hauptfach classes only: Orchesterstudien do not stand in for them
        study__in=studies, course__term=term, course__kind=Course.Kind.LESSON, course__subject__isnull=False, course__staff=person,
    ).values_list('study', 'course__title'))
    until = dict(  # the Hauptfach's lessons: classes of a Hauptfach, not Pflichtfach Klavier or Orchesterstudien
        Module.objects.filter(program__in={s.program_id for s in studies}, courses__kind=Course.Kind.LESSON, courses__subject__isnull=False, semester__isnull=False)
        .values('program').annotate(last=Max('semester')).values_list('program', 'last')
    )
    passed = set(Participation.objects.filter(study__in=studies, passed=True).values_list('study', 'module'))
    failed = Counter(
        Participation.objects.filter(study__in=studies, passed=False, course__kind__in=[Course.Kind.EXAM, Course.Kind.THESIS], course__released__isnull=False)
        .values_list('study', 'module')
    )
    choices = {}
    for s in studies:  # a plan without lessons, such as a doctorate's, owes none
        s.lesson, s.until = lessons.get(s.pk), until.get(s.program_id)
        s.missing = not s.lesson and s.until is not None and s.fs <= s.until
        tries = [n for (study, module), n in failed.items() if study == s.pk and (study, module) not in passed]
        s.lost, s.last = any(n >= s.program.attempts for n in tries), any(n == s.program.attempts - 1 for n in tries)
        s.over = s.fs > s.program.semesters
        options = choices.setdefault(s.program_id, s.program.choices())
        s.open = options[0].choice.title if options and not s.focus_id and s.fs >= min(o.first or 99 for o in options) - 1 else ''
    return studies


def hours(term, seen):
    """{teacher: [(course, SWS)]} of a term's teaching, for the teachers `seen` (pks; None: all)."""
    courses = (
        term.courses.filter(kind__in=TEACHING, cancelled=None).select_related('format', 'term')
        .prefetch_related('staff', 'ensembles', 'groups__staff', *(Prefetch(path, Participation.objects.filter(course__kind=Course.Kind.LESSON).select_related('module__program')) for path in ('participations', 'origin__participations')))  # sws() counts those of lessons only
        .annotate(size=Count('participations__study', distinct=True))
    )
    if seen is not None:
        courses = courses.filter(pk__in=Course.objects.filter(staff__in=seen).values('pk'))
    found = defaultdict(list)
    for c in courses:
        coached = Counter(e.coach_id for e in c.ensembles.all()) + Counter(t.pk for g in c.groups.all() for t in g.staff.all())
        c.grouped = len(c.groups.all())
        for s in c.staff.all():
            if seen is None or s.pk in seen:
                found[s].append((c, sws(c, coached[s.pk])))
    return found


def loads(term, person, institute='', words=()):
    """The Deputat of the teachers in the person's view: their own, and that of the Institute in reach. Each row has
    `teacher`, `courses` [(course, SWS)], `total`, the term's `reductions`, `due` (the Lehrverpflichtung less them),
    `balance` (total less due), `low` (under the share of it the Hochschule asks in every term) and `account`: the
    balances of the Studienjahre of its Ausgleichszeitraum up to this term, to be evened out within them, counted from
    when the Hochschule began to keep it. A Lehrauftrag has no account: its hours are agreed each term."""
    teaching = Person.objects.filter(teaching__term=term, teaching__kind__in=TEACHING)
    seen = None if person.everywhere('view_teaching_load') else {person.pk, *person.within(teaching, 'view_teaching_load').values_list('pk', flat=True)}
    now = {t: cs for t, cs in hours(term, seen).items() if institute in ('', str(t.institute_id)) and all(w in fold(t.name) for w in words)}
    institution = Institution.get()
    since, floor = institution.load_since, institution.load_floor
    terms = [term, *(Term.objects.filter(start__lt=term.start, start__gte=since).order_by('-start')[:2 * institution.load_years - 1] if since else [])]
    reduced = defaultdict(list)
    for r in Reduction.objects.filter(person__in=now, term__in=terms):
        reduced[r.person_id, r.term_id].append(r)

    def due(teacher, on):
        return None if teacher.obligation is None else teacher.obligation - sum(r.hours for r in reduced[teacher.pk, on.pk])

    account, first = defaultdict(Decimal), dict(Course.objects.filter(staff__in=now, kind__in=TEACHING).values('staff').annotate(first=Min('term__start')).values_list('staff', 'first'))
    for earlier in terms[1:]:
        taught = hours(earlier, {t.pk for t in now})
        for t in now:
            if first.get(t.pk) and first[t.pk] <= earlier.start and (owed := due(t, earlier)) is not None:
                account[t.pk] += sum(h for _, h in taught.get(t, [])) - owed
    rows = []
    for t, cs in now.items():
        total, owed = sum(h for _, h in cs), due(t, term)
        rows.append(SimpleNamespace(
            teacher=t, courses=cs, total=total, reductions=reduced[t.pk, term.pk], due=owed, balance=None if owed is None else total - owed,
            low=bool(floor) and owed is not None and total * 100 < owed * floor,
            account=None if owed is None or t.employment == Person.Employment.LEHRAUFTRAG or not since or term.start < since else account[t.pk] + total - owed,
        ))
    return sorted(rows, key=lambda row: row.teacher.last_name)


@require('view_teaching_load', 'teacher')
def load(request):
    person = request.user
    term = Term.pick(request.GET.get('semester'))
    if term is None:
        return without_term(request, 'ein Semester mit Veranstaltungen')
    ours = person.ours('view_teaching_load')  # an Institut's office sees its own; the list says so
    institute, q, stand = request.GET.get('institut', str(min(ours)) if len(ours) == 1 else ''), request.GET.get('q', '').strip(), request.GET.get('stand', '')
    rows = loads(term, person, institute, fold(q).split())
    if stand:
        rows = [row for row in rows if row.balance is not None and (row.balance > 0 if stand == 'ueber' else row.balance < 0)]
    return render(request, 'courses/load.html', {
        'term': term, 'terms': Term.objects.order_by('-start'), 'rows': rows, 'total': sum(row.total for row in rows), 'everyone': 'view_teaching_load' in person.roles,
        'since': Institution.get().load_since and Term.objects.filter(start__gte=Institution.get().load_since).order_by('start').first(),
        'institutes': Institute.objects.filter(pk__in=ours) if not person.everywhere('view_teaching_load') and ours else Institute.objects.all(),
        'institute': institute, 'q': q, 'stand': stand,
    })
