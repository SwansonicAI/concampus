from django import template
from django.utils.formats import number_format
from django.utils.html import format_html

register = template.Library()


@register.filter
def mark(value):
    """An examiner's mark as Prüfungsämter write it: one decimal, a second only where given (2,0 · 17,5 · 16,25); what was
    typed into a refused form, as it was typed."""
    if value is None or isinstance(value, str):
        return value or ''
    return number_format(value, 2 if value * 10 % 1 else 1)


@register.filter
def programme(note):
    """A programme as a sheet lists it, without the times in the video."""
    from core.text import untimed

    return untimed(note)


@register.filter
def coded(module):
    """A module as the plan names it: its code small and muted, then its title."""
    if not module:
        return ''
    return format_html('<span class="code">{}</span> {}', module.code, module.title) if module.code else module.title


@register.filter
def of(mapping, key):
    """mapping[key] in a template, None if missing: a row's Gutachten or reason by its examiner."""
    return mapping.get(key)


@register.simple_tag
def scale():
    """The grades one examiner may give, for a dropdown."""
    from courses.panel import SCALE

    return SCALE
