from urllib.parse import urlsplit

from django import template
from django.template.loader_tags import BLOCK_CONTEXT_KEY
from django.urls import resolve, reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

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
    """Where a link of the site goes, its app and page: a page's trail starts there, so the menu marks it."""
    return resolve(urlsplit(url).path) if url else None


@register.inclusion_tag('core/_side.html', takes_context=True)
def side(context):
    """The side menu: the person's areas, the one this page belongs to open with its pages. A page belongs where its trail
    starts, else where it is: to the area whose menu lists it, else to its app's. Below the item it belongs to, the way
    down from there: the trail's further steps, then the page itself, so one two pages deep still sees where."""
    trail, menu, path = context.get('trail') or [], context['menu'], context['request'].path
    match = resolved(trail[0][1]) if trail else context['request'].resolver_match
    here = match.view_name if match else ''
    listing = next(((app, url) for app, _, pages in menu for _, url, under in pages if here in under), None)
    area = listing[0].label if listing else match and match.app_name
    top = (listing[1] if listing else reverse('home') if here in ('home', 'day') else reverse('directory') if here == 'directory'
           else next((reverse(app.url) for app, *_ in menu if app.label == area), None))
    return {
        'menu': [(app, title, pages if app.label == area else []) for app, title, pages in menu], 'area': area, 'top': top,
        'way': [*trail[1:], (titled(context, trail[-1][0] if len(trail) > 1 else ''), path)] if top and path != top else [],
    }


def titled(context, above):
    """The page's own {% block title %}, which names it as the last step of the way down; without the step above's name,
    which it often repeats: „Sichtung Videostufe Violine“ under „Videostufe Violine“ is „Sichtung“."""
    blocks = context.render_context.get(BLOCK_CONTEXT_KEY)
    block = blocks and blocks.get_block('title')
    title = block.nodelist.render(context).strip() if block else ''
    return mark_safe(above and title.replace(str(escape(above)), '').strip(' ·') or title)  # rendered, so escaped already
