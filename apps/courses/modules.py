from collections import defaultdict


def credited(course, own=()):
    """The modules a course counts for, by title: [(title, [(program, codes)])], so a course in many plans reads in a few lines;
    the Studiengänge in `own` first."""
    found = defaultdict(lambda: defaultdict(list))
    for m in course.modules.select_related('program').order_by('title', 'program__degree', 'program__name', 'code'):
        found[m.title][m.program].append(m.code)
    rows = [(title, sorted(programs.items(), key=lambda p: p[0].pk not in own)) for title, programs in found.items()]
    return sorted(rows, key=lambda row: row[1][0][0].pk not in own)
