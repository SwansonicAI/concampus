from django import template

from ..compose import compose

register = template.Library()


@register.simple_tag(takes_context=True)
def composed(context, doc):
    """A document's content as the Hochschule composes it (documents.compose), from what its view gave the template; in a
    preview, the draft (`draft`) with a block marked (`marked`)."""
    return compose(doc, context.flatten(), context.get('draft'), context.get('marked'))
