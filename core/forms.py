from django.forms.renderers import DjangoTemplates


class Renderer(DjangoTemplates):
    """Every form's fields: labels without Django's trailing colon, a check box in front of its sentence."""

    field_template_name = 'core/field.html'
