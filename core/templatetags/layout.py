from urllib.parse import urlsplit

from django import template
from django.urls import resolve

register = template.Library()


@register.filter
def arranged(form):
    """A form's fields as its `layout` groups them: [(heading, rows)], each row the fields side by side, a name alone a
    row of its own. Fields the form dropped are skipped; those the layout leaves out follow, each on its own row."""
    fields = {f.name: f for f in form.visible_fields()}
    groups = []
    for heading, rows in getattr(form, 'layout', None) or ():
        rows = [[fields.pop(name) for name in (row if isinstance(row, tuple) else (row,)) if name in fields] for row in rows]
        groups.append((heading, [row for row in rows if row]))
    groups.append((None, [[f] for f in fields.values()]))
    return [(heading, rows) for heading, rows in groups if rows]


@register.filter
def resolved(url):
    """Where a link of the site goes, its app and page: a page's trail starts there, so the bars mark it."""
    return resolve(urlsplit(url).path) if url else None
