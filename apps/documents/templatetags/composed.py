from django import template

from ..compose import compose

register = template.Library()


@register.simple_tag(takes_context=True)
def composed(context, doc):
    """A document's content as the Hochschule writes it (documents.compose), from what its view gave the template; in the
    editor's preview, its draft (`draft`)."""
    return compose(doc, context.flatten(), context.get('draft'))
