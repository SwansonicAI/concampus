"""What a study has achieved: the modules whose Prüfungsanspruch is lost, and what a Leistungsübersicht or Zeugnis shows."""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Max, OuterRef, Subquery, Sum
from django.utils import timezone

from core import texts
from core.dates import months_before, working
from courses.models import Course, Participation
from curriculum.models import Module, coverage
from curriculum.plans import plan


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
        released = module.failures[-1].told or module.failures[-1].course.released
        notice = study.documents.filter(kind='notice', created__gte=released).select_related('message').order_by('created').first()
        opened = study.person.electronic and notice and notice.message and notice.message.read
        known = timezone.localtime(opened).date() + timedelta(days=1) if opened else timezone.localtime(notice.created if notice else released).date() + timedelta(days=4)
        module.appeal = working(months_before(known, -1))  # a Frist ending on a weekend or holiday runs on (§ 31 Abs. 3 VwVfG)
    return lost


def achievements(study):
    """What a Leistungsübersicht, a Zeugnis and a Diploma Supplement show: the modules with their credits, and the grade so far."""
    tree, average = study.progress()
    earned, total, honours = sum(m.earned for m in tree), sum(m.credits for m in tree), study.program.honours
    last = study.participations.filter(passed=True, course__released__isnull=False).aggregate(last=Max('course__released'))['last']
    rows = []
    for r in plan(tree, study.program.semesters):
        if r['kind'] != 'part' and r['node'].earned or r['kind'] == 'part' and r['exam'] and any(i.done and not i.excused or i.failures for i in r['items']):  # an exam waived is no result
            above = rows[-1] if rows else None
            if above and above['kind'] == 'group' and r['kind'] != 'part' and r['node'].parent_id == above['node'].pk and above['node'].offered > above['node'].credits:
                rows[-1] = {**r, 'choice': above['node']}  # a choice and what was chosen, on one line with the choice's weight
            else:
                rows.append({**r, 'choice': None})
    thesis = study.participations.filter(course__kind=Course.Kind.THESIS, passed=True).select_related('course').first()
    return {
        'study': study, 'rows': rows, 'earned': earned, 'total': total, 'finished': earned >= total, 'average': average,
        'covered': coverage(tree, set(study.program.foci()) - {study.focus_id} if study.focus_id else ())[0],
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
