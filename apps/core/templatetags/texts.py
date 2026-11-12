from django import template

from core.texts import block

register = template.Library()


@register.simple_tag
def text(key, **values):
    """A text block as the Hochschule words it (core.texts)."""
    return block(key, **values)
