from django import template
from django.utils.formats import number_format

register = template.Library()


@register.filter
def mark(value):
    """An examiner's mark as Prüfungsämter write it: one decimal, a second only where given (2,0 · 17,5 · 16,25)."""
    if value is None:
        return ''
    return number_format(value, 2 if value * 10 % 1 else 1)
