from django import forms
from django.forms.renderers import DjangoTemplates

from .models import Person


class Renderer(DjangoTemplates):
    """Every form's fields: labels without Django's trailing colon, a check box in front of its sentence."""

    field_template_name = 'core/field.html'


class ContactForm(forms.ModelForm):
    """What everyone keeps up themselves: where they live and how to reach them."""

    class Meta:
        model = Person
        fields = ['address', 'phone']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}
