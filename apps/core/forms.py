from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.db import models
from django.db.models import Count
from django.forms.renderers import DjangoTemplates
from django.http import QueryDict
from django.shortcuts import redirect

from .backends import TRIES, held
from .models import Feedback, Message, Person


class ComposeForm(forms.Form):
    subject = forms.CharField(label='Betreff', max_length=200)
    body = forms.CharField(label='Text', widget=forms.Textarea, help_text='{vorname} und {nachname} werden für jede Person eingesetzt.')

    def send(self, people, sender, link=''):
        """The message to each person, by name, into their Postfach and by e-mail."""
        for person in people:
            subject, body = self.cleaned_data['subject'], self.cleaned_data['body']
            for placeholder, value in {'{vorname}': person.first_name, '{nachname}': person.last_name}.items():
                subject, body = subject.replace(placeholder, value), body.replace(placeholder, value)
            Message.send(person, subject, body, link, sender=sender)


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
    """Every form's fields: labels without Django's trailing colon, a check box in front of its sentence; grouped and side
    by side as the form's `layout` says (core.templatetags.layout)."""

    field_template_name = 'core/field.html'
    form_template_name = 'core/form.html'


class SignInForm(AuthenticationForm):
    """Signing in, held back for a while after too many failures (core.backends.held)."""

    def clean(self):
        if held(self.data.get('username', ''), self.request):
            raise forms.ValidationError(
                f'Nach {TRIES} Fehlversuchen ist die Anmeldung von hier eine Viertelstunde gesperrt. Ein neues Passwort setzen Sie über „Passwort vergessen?“.',
            )
        return super().clean()


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ['name', 'email', 'page', 'text']
        widgets = {'text': forms.Textarea(attrs={'rows': 5})}

    layout = [(None, [('name', 'email'), 'page', 'text'])]


class EmailForm(forms.Form):
    """A new sign-in address, taken on once confirmed from it."""

    email = forms.EmailField(label='Neue E-Mail-Adresse')

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person

    def clean_email(self):
        email = self.cleaned_data['email']
        if Person.objects.filter(models.Q(email__iexact=email) | models.Q(username__iexact=email)).exclude(pk=self.person.pk).exists():
            raise forms.ValidationError('Diese Adresse gehört schon zu einem anderen Konto.')
        return email


class ContactForm(forms.ModelForm):
    """What everyone keeps up themselves: where they live and how to reach them."""

    class Meta:
        model = Person
        fields = ['address', 'residence', 'residence_town', 'phone', 'email', 'private_email']
        widgets = {'address': forms.Textarea(attrs={'rows': 3})}
        labels = {'email': 'E-Mail, privat'}
        help_texts = {'email': 'Nach dem Studium gilt die Hochschuladresse nicht mehr; Nachrichten gehen hierhin.'}

    layout = [(None, ['address', 'residence', ('phone', 'email'), 'private_email'])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        roles = self.instance.roles
        if not ('alumni' in roles and not roles & {'student', 'applicant', 'teacher'} and not self.instance.groups.exists()):  # only who has left
            del self.fields['email']
        else:
            self.fields['email'].required = True
        if 'student' not in roles:  # while studying: kept for after, and where one lives in term, for the official statistics
            del self.fields['private_email'], self.fields['residence'], self.fields['residence_town']
