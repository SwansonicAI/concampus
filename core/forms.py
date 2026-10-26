from django import forms
from django.forms.renderers import DjangoTemplates
from django.http import QueryDict
from django.shortcuts import redirect

from .models import Person


def again(request, to, name):
    """Back to the page holding the form `name` when it was refused, with what was typed kept for sent(): kept in the
    session, not the address, since a note may say why someone was ill."""
    request.session['sent'] = [name, {key: request.POST.getlist(key) for key in request.POST if key != 'csrfmiddlewaretoken'}]
    return redirect(to)


def sent(request, name):
    """What again() kept for the form `name`, once, to fill it in as it was sent; else None."""
    if request.session.get('sent', [None])[0] != name:
        return None
    data = QueryDict(mutable=True)
    for key, values in request.session.pop('sent')[1].items():
        data.setlist(key, values)
    return data


class Renderer(DjangoTemplates):
    """Every form's fields: labels without Django's trailing colon, a check box in front of its sentence."""

    field_template_name = 'core/field.html'


class ContactForm(forms.ModelForm):
    """What everyone keeps up themselves: where they live and how to reach them."""

    class Meta:
        model = Person
        fields = ['address', 'phone']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}
