"""The Studienverlaufsplan as rows and columns, for a Studiengang and for one student's progress through it."""


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


def columns(rows, program, graded):
    """What a plan shows: CP and Gewicht only where its Studiengang has them (a Konzertexamen may have neither), Note for
    whoever sees grades, `span` how many columns there are; and whether its forms count `minutes` or SWS."""
    nodes = [r['node'] for r in rows if 'node' in r] + [i for r in rows for i in r.get('items', ())]
    credited, weighted = any(n.credits for n in nodes), any(n.weight for n in nodes)
    return {'credited': credited, 'weighted': weighted, 'span': 2 + program.semesters + credited + weighted + bool(graded), 'minutes': program.weekly == 'minuten'}
