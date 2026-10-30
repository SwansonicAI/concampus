from collections import defaultdict

from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from core.models import Office, Term
from courses.models import Course
from students.models import Study
from .apps import OVERSIGHT
from .models import Module, Program


def plan(tree, semesters):
    """Rows of the Studienverlaufsplan as the SVPP prints it: every module, and under it its course work and exams
    with their credits per semester. Groups and choices become headings."""
    rows = []

    def visit(node, depth):
        leaves = [n for n in node.nodes if not n.nodes]
        rows.append({'kind': 'module' if leaves else 'group', 'node': node, 'depth': depth})
        parts = {}
        for leaf in leaves:
            parts.setdefault(leaf.title, []).append(leaf)
        for title, items in sorted(parts.items(), key=lambda part: part[1][0].exam):  # course work first, then exams
            rows.append({
                'kind': 'part', 'title': title, 'items': items, 'depth': depth + 1, 'exam': items[0].exam,
                'form': items[0].description, 'weight': items[0].weight,
                'cells': [[i for i in items if i.semester == s] for s in range(1, semesters + 1)],
                'grades': [i.grade for i in items if getattr(i, 'grade', None)],
                'open': [i for i in items if i.semester is None],
            })
        for child in node.nodes:
            if child.nodes:
                visit(child, depth + 1)

    for root in tree:
        visit(root, 0)
    return rows


def index(request):
    sizes = dict(Module.counted().values('program').annotate(n=Count('pk')).values_list('program', 'n'))
    programs = list(Program.objects.ordered().prefetch_related('subjects'))
    for p in programs:
        p.size = sizes.get(p.pk, 0)
    figures = bool(request.user.roles & OVERSIGHT)
    if figures:
        counts = {(row['program'], row['status']): row['n'] for row in Study.objects.values('program', 'status').annotate(n=Count('pk'))}
        for p in programs:  # counted apart: joining studies and modules in one query multiplies them
            p.students, p.applicants = counts.get((p.pk, Study.Status.ENROLLED), 0), counts.get((p.pk, Study.Status.APPLIED), 0)
    return render(request, 'curriculum/index.html', {'programs': programs, 'figures': figures, 'keepers': Office.holding('edit_curriculum', linked=True)})


def program(request, pk):
    program = get_object_or_404(Program.objects.prefetch_related('subjects'), pk=pk)
    term = Term.current()
    offers = defaultdict(set)
    for link in Course.modules.through.objects.filter(module__program=program, course__term=term).select_related('course'):
        offers[link.module_id].add(link.course)
    tree = program.curriculum()
    rows = plan(tree, program.semesters)
    for row in rows:
        if row['kind'] == 'part':
            row['offers'] = sorted({c for i in row['items'] for c in offers[i.pk]}, key=lambda c: c.title)
    figures = bool(request.user.roles & OVERSIGHT)
    return render(request, 'curriculum/program.html', {
        'program': program, 'rows': rows, 'semesters': range(1, program.semesters + 1), 'term': term, 'edit': request.user.may('edit_curriculum', program),
        'reachable': Course.reachable(request.user, Course.objects.filter(pk__in=[c.pk for found in offers.values() for c in found])),
        'total': sum(m.credits for m in tree), 'figures': figures, 'students': figures and program.studies.filter(status='enrolled').count(),
    })
