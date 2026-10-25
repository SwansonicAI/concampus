"""Auswertungen: how the Hochschule is doing at a glance, a free query over everything counted, and the records
behind any number for whoever may see them."""
import csv

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.formats import number_format

from core.access import require
from core.models import Term
from core.views import SHOWN, without_term
from .charts import SPANS, Query, chart, label, lines
from .figures import MEASURES, narrowed, semesters, table
from .overview import overview


@require('view_statistics')
def index(request):
    if Term.current() is None:
        return without_term(request, 'mindestens ein Semester')
    return render(request, 'analytics/overview.html', overview(request.user))


@require('view_statistics')
def query(request):
    q = Query.read(request.GET)
    if request.GET.get('format') == 'csv':
        return export(q)
    m = q.measure
    return render(request, 'analytics/query.html', {
        'q': q, 'chart': lines(q) if q.as_lines and q.timed else chart(q, request.user),
        'total': number_format(table(m, q.keys[:1], q.terms, q.where).total, m.decimals, force_grouping=True),
        'measures': MEASURES.values(), 'dimensions': m.dimensions.items(),
        'spans': SPANS if q.timed else [(str(t.pk), t.name) for t in reversed(semesters(m))],
        'zeit': q.zeit or ('j10' if q.timed else str(q.terms[0].pk) if q.terms else ''),
        'filters': [(m.dimensions[k].title, label(m.dimensions[k], v), q.url(where={x: y for x, y in q.where.items() if x != k})) for k, v in q.where.items()],
        'terms': ', '.join(t.name for t in q.terms) if not q.timed else '',
        'span': dict(SPANS).get(q.zeit, SPANS[1][1]), 'split': m.dimensions[q.keys[0]].title,
    })


def export(q):
    t = table(q.measure, q.keys, q.terms, q.where)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="auswertung-{q.measure.key}.csv"'
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    if len(q.keys) == 1:
        writer.writerow([t.dims[0].title, q.measure.title])
        writer.writerows([label, t.row_totals.get(v, 0)] for v, label in t.rows.items())
    else:
        writer.writerow([t.dims[0].title, *t.cols.values(), 'Gesamt'])
        writer.writerows([label, *(t.cells.get((r, c), 0) for c in t.cols), t.row_totals.get(r, 0)] for r, label in t.rows.items())
    writer.writerow(['Gesamt', *(t.col_totals.get(c, 0) for c in t.cols), t.total] if len(q.keys) > 1 else ['Gesamt', t.total])
    return response


@require('view_statistics')
def records(request):
    q = Query.read(request.GET)
    if not request.user.everywhere(q.measure.capability):  # names of the whole Hochschule
        raise PermissionDenied
    found = q.measure.objects(narrowed(q.measure, q.terms, q.where))
    return render(request, 'analytics/records.html', {
        'q': q, 'records': [q.measure.describe(o) for o in found[:SHOWN]], 'count': found.count(),
        'filters': [f'{q.measure.dimensions[k].title}: {label(q.measure.dimensions[k], v)}' for k, v in q.where.items()],
        'terms': ', '.join(t.name for t in q.terms) if not q.timed else '',
    })
