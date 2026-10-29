from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def mark(value):
    """An examiner's mark as Prüfungsämter write it: one decimal, a second only where given (2,0 · 17,5 · 16,25); what was
    typed into a refused form, as it was typed."""
    if value is None or isinstance(value, str):
        return value or ''
    return number_format(value, 2 if value * 10 % 1 else 1)


@register.filter
def of(mapping, key):
    """mapping[key] in a template, None if missing: a row's Gutachten or reason by its examiner."""
    return mapping.get(key)


@register.simple_tag
def scale():
    """The grades one examiner may give, for a dropdown."""
    from courses.panel import SCALE

    return SCALE
