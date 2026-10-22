from collections import defaultdict

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from core.access import require
from core.models import Term
from courses.models import Course
from .models import Program


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
                'open': [i for i in items if i.semester is None],
            })
        for child in node.nodes:
            if child.nodes:
                visit(child, depth + 1)

    for root in tree:
        visit(root, 0)
    return rows


@require('view_curriculum', 'teacher')
def index(request):
    programs = Program.objects.annotate(
        students=Count('studies', filter=Q(studies__status='enrolled'), distinct=True),
        applicants=Count('studies', filter=Q(studies__status='applied'), distinct=True),
        size=Count('modules', filter=Q(modules__parent=None), distinct=True),
    ).prefetch_related('subjects')
    return render(request, 'curriculum/index.html', {'programs': programs})


@require('view_curriculum', 'teacher')
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
    return render(request, 'curriculum/program.html', {
        'program': program, 'rows': rows, 'semesters': range(1, program.semesters + 1), 'term': term,
        'total': sum(m.credits for m in tree), 'students': program.studies.filter(status='enrolled').count(),
    })
