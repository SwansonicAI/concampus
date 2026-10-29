from django import forms
from django.db.models import Count
from django.forms.renderers import DjangoTemplates
from django.http import QueryDict
from django.shortcuts import redirect

from .models import Person


def frequent(field, choices, n=8):
    """A person field's choices with the answers given most often first, under 'Häufig'; the rest as they were."""
    from .models import Person

    flat = dict(c for group in choices for c in (group[1] if isinstance(group[1], (list, tuple)) else [group]))
    common = Person.objects.exclude(**{field: ''}).values(field).annotate(n=Count('pk')).order_by('-n').values_list(field, flat=True)[:n]
    return [('Häufig', [(value, flat[value]) for value in common if value in flat]), *(c for c in choices if c[0])]


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
        fields = ['address', 'phone', 'email', 'private_email']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}
        labels = {'email': 'E-Mail, privat'}
        help_texts = {'email': 'Nach dem Studium gilt die Hochschuladresse nicht mehr; Nachrichten gehen hierhin.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        roles = self.instance.roles
        if not ('alumni' in roles and not roles & {'student', 'applicant', 'teacher'} and not self.instance.groups.exists()):  # only who has left
            del self.fields['email']
        else:
            self.fields['email'].required = True
        if 'student' not in roles:  # while studying: kept for after
            del self.fields['private_email']
