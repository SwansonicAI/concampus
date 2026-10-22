"""What a query is, and a table as the page shows it."""
import re
from dataclasses import dataclass, field, replace
from functools import cached_property

from django.urls import reverse
from django.utils.formats import number_format
from django.utils.http import urlencode

from core.models import Term
from .figures import MEASURES, OTHERS, semesters, table, window

SPANS = [('j5', 'letzte 5 Jahre'), ('j10', 'letzte 10 Jahre'), ('alle', 'alle Semester')]


@dataclass
class Query:
    """What to count, split by what, in which semesters and narrowed to which values; read from and written to links."""
    measure: object
    keys: list
    where: dict = field(default_factory=dict)
    zeit: str = ''
    share: bool = False

    @classmethod
    def read(cls, params):
        measure = MEASURES.get(params.get('was')) or MEASURES['studierende']
        keys = [k for k in dict.fromkeys([params.get('nach') or measure.default, params.get('und', '')]) if k in measure.dimensions] or [measure.default]
        where = {k[2:]: v for k, v in params.items() if k.startswith('f_') and k[2:] in measure.dimensions and v}
        return cls(measure, keys, where, params.get('zeit', ''), params.get('anteil') == '1')

    @property
    def timed(self):
        """Across semesters: a span of years rather than one semester."""
        return any(self.measure.dimensions[k].time for k in [*self.keys, *self.where])

    @cached_property
    def terms(self):
        if self.timed:
            return window(self.measure, None if self.zeit == 'alle' else int(self.zeit[1:]) if self.zeit in ('j5', 'j10') else 10)
        found, now = semesters(self.measure), Term.current()
        until = [t for t in found if t.start <= now.start] or found
        return [next((t for t in found if str(t.pk) == self.zeit), until[-1])] if found else []

    def params(self, **changes):
        q = replace(self, **changes)
        found = {'was': q.measure.key, 'nach': q.keys[0], 'und': q.keys[1] if len(q.keys) > 1 else '', 'zeit': q.zeit, 'anteil': '1' if q.share else ''}
        found.update({f'f_{k}': v for k, v in q.where.items()})
        return {k: v for k, v in found.items() if v}

    def url(self, view='analytics:query', **changes):
        return f'{reverse(view)}?{urlencode(self.params(**changes))}'

    def drill(self, key, value):
        """Where a label leads: a semester opens the default split for it; anything else its course over time."""
        if value is None or value == OTHERS:
            return None
        where = {**self.where, key: str(value)}
        then = self.measure.default if self.measure.dimensions[key].time else self.measure.drill
        if then == key or then in where:
            return None
        return self.url(keys=[then], where=where, zeit='', share=False)

    def records(self, **values):
        if None in values.values() or OTHERS in values.values():
            return None
        return self.url('analytics:records', where={**self.where, **{k: str(v) for k, v in values.items()}}, keys=[self.keys[0]], share=False)


def figure(value, measure, share=False):
    if share:
        return f'{number_format(value * 100, 0)} %'
    return number_format(value, measure.decimals, force_grouping=True) + (f' {measure.unit}' if measure.unit else '')


def short(label):
    """'WiSe 2017/18' → 'W 17/18', so twenty semesters fit side by side."""
    return re.sub(r'(WiSe|SoSe) 20', lambda m: f'{m[1][0]} ', str(label))


def chart(query, person, title=''):
    """A table as the page shows it: bars or columns for one dimension, a shaded grid for two; numbers open their records."""
    m, t = query.measure, table(query.measure, query.keys, query.terms, query.where)
    open_ = m.capability in person.roles
    found = {'title': title, 'query': query, 'table': t, 'total': figure(t.total, m)}
    if len(query.keys) == 1:
        dim, key = t.dims[0], query.keys[0]
        values = {v: t.row_totals.get(v, 0) for v in t.rows}
        peak = max(values.values(), default=0) or 1
        last = list(t.rows)[-1] if t.rows and dim.time else None
        found['columns'], found['timed'], found['dense'] = dim.time or dim.columns, dim.time, len(t.rows) > 12
        found['bars'] = [{
            'label': label, 'tick': short(label), 'shown': figure(n / t.total if query.share and t.total else n, m, query.share),
            'width': round(100 * n / peak, 1), 'now': v == last, 'drill': query.drill(key, v),
            'records': open_ and query.records(**{key: v}),
        } for v, label in t.rows.items() for n in [values[v]]]
        return found
    (rk, ck) = query.keys
    peak = t.peak
    found['head'] = list(t.cols.values())
    found['lines'] = [{
        'label': label, 'drill': query.drill(rk, r),
        'cells': [{
            'shown': figure(n / t.row_totals[r] if query.share and t.row_totals.get(r) else n, m, query.share) if n else '',
            'shade': round(100 * (n / t.row_totals[r] if query.share and t.row_totals.get(r) else n / peak)),
            'records': n and open_ and query.records(**{rk: r, ck: c}),
        } for c in t.cols for n in [t.cells.get((r, c), 0)]],
        'total': figure(t.row_totals.get(r, 0), m),
    } for r, label in t.rows.items()]
    found['foot'] = [figure(t.col_totals.get(c, 0), m) for c in t.cols]
    return found


def label(dim, value):
    return next((text for k, text in dim.labels([value]).items() if str(k) == value), value)
