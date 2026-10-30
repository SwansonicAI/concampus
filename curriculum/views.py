from collections import defaultdict

from django.contrib import messages
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.access import require
from core.models import Office, Term
from core.transfer import FORMAT, load, plan as written
from courses.models import Course, Format
from students.models import Study
from .apps import OVERSIGHT
from .models import Module, Program


def plan(tree, semesters, choices=(), taking=(), late=()):
    """Rows of the Studienverlaufsplan as the SVPP prints it: every module, and under it its course work and exams
    with their credits per semester. Groups and choices become headings; each Schwerpunkt of a choice (Program.choices())
    is a block of its own (`within`, from where it `opens`), „oder“ between them. For a student each cell has its
    `state`: passed, taken this term (`taking`), an exam overdue (`late`), or waived for the Hauptfach („off“)."""
    rows, area = [], {o.pk: o.choice.pk for o in choices}
    begun = set()  # the choices whose first Schwerpunkt is listed

    def state(items):
        for i in items:
            if i.excused:
                return 'off'
            if i.done or i.pk in taking or i.pk in late:
                return 'done' if i.done else 'now' if i.pk in taking else 'late'
        return ''

    def visit(node, depth, within=False):
        leaves = [n for n in node.nodes if not n.nodes]
        if opens := node.pk in area:
            if area[node.pk] in begun:
                rows.append({'kind': 'or', 'depth': depth})
            begun.add(area[node.pk])
        within = within or opens
        rows.append({
            'kind': 'module' if leaves else 'group', 'node': node, 'depth': depth, 'choosing': node.pk in area or node.pk in area.values(),
            'options': list(area.values()).count(node.pk), 'within': within, 'opens': opens,  # a choice: how many Schwerpunkte, one to be studied
        })
        parts = {}
        for leaf in leaves:
            parts.setdefault(leaf.title, []).append(leaf)
        for title, items in sorted(parts.items(), key=lambda part: part[1][0].exam):  # course work first, then exams
            cells = [[i for i in items if i.semester == s] for s in range(1, semesters + 1)]
            rows.append({
                'kind': 'part', 'title': title, 'items': items, 'depth': depth + 1, 'exam': items[0].exam, 'within': within,
                'form': items[0].description, 'weight': items[0].weight, 'off': all(i.excused for i in items),
                'cells': [{'items': cell, 'state': state(cell)} for cell in cells],
                'grades': [i.grade for i in items if getattr(i, 'grade', None)],
                'open': [i for i in items if i.semester is None], 'open_state': state([i for i in items if i.semester is None]),
            })
        for child in node.nodes:
            if child.nodes:
                visit(child, depth + 1, within)

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
    rows = plan(tree, program.semesters, program.choices())
    for row in rows:
        if row['kind'] == 'part':
            row['offers'] = sorted({c for i in row['items'] for c in offers[i.pk]}, key=lambda c: c.title)
    figures = bool(request.user.roles & OVERSIGHT)
    return render(request, 'curriculum/program.html', {
        'program': program, 'rows': rows, 'formats': Format.objects.exclude(short=''), 'semesters': range(1, program.semesters + 1), 'term': term, 'edit': request.user.may('edit_curriculum', program),
        'reachable': Course.reachable(request.user, Course.objects.filter(pk__in=[c.pk for found in offers.values() for c in found])),
        'total': sum(m.credits for m in tree), 'figures': figures, 'students': figures and program.studies.filter(status='enrolled').count(),
        'coming': Term.objects.filter(start__gt=timezone.localdate()).order_by('start'),
    })


@require('edit_curriculum')
@require_POST
def copy(request, pk):
    """The Studienplan as a new PO-Version that applies from a semester to come: whoever studies in it keeps theirs, whoever
    starts from that semester starts in the new one, to be changed until then."""
    program = get_object_or_404(request.user.within(Program.objects.all(), 'edit_curriculum'), pk=pk)
    starts = Term.objects.filter(pk=request.POST.get('gilt_ab') or None, start__gt=timezone.localdate()).first()
    if not starts:
        messages.error(request, 'Bitte das Semester wählen, ab dem die neue PO-Version gilt.')
        return redirect(program)
    version = Program.objects.filter(degree=program.degree, name=program.name).aggregate(Max('version'))['version__max'] + 1
    load({'format': FORMAT, 'programs': [written(program)[0] | {'version': version}]})
    new = Program.objects.get(degree=program.degree, name=program.name, version=version)
    new.starts = starts
    new.save(update_fields=['starts'])
    messages.success(request, f'PO-Version {version} angelegt, gilt ab dem {starts}; bis dahin beginnt man in Version {program.version}.')
    return redirect(new)
