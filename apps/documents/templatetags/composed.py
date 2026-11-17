from django import template

from ..compose import compose

register = template.Library()


@register.simple_tag(takes_context=True)
def composed(context, occasion):
    """A document's content as the Hochschule writes it (documents.compose), for the case its view gave the template; in
    the editor's preview, the document worked on (`shown`) with its draft (`draft`)."""
    return compose(occasion, context.flatten(), context.get('draft'), context.get('shown'))
