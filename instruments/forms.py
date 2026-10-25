from django import forms

from core.dates import DayField
from core.models import Person
from core.text import matching
from students.models import Study


class LendForm(forms.Form):
    who = forms.CharField(
        label='An', max_length=120,
        widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}),
    )
    due = DayField(label='Rückgabe bis')
    note = forms.CharField(label='Notiz', max_length=200, required=False, help_text='Z. B. mitgegebenes Zubehör.')

    def clean_who(self):
        """One student or teacher, found by name, e-mail or Matrikelnummer."""
        text = self.cleaned_data['who']
        found = {s.person for s in Study.find(text)} | set(matching(Person.teachers(), text, 'first_name', 'last_name', 'email')[:5])
        if len(found) != 1:
            names = ', '.join(sorted(str(p) for p in found))
            raise forms.ValidationError(f'Mehrere passen: {names}. Bitte genauer.' if found else 'Niemand passt dazu.')
        return found.pop()
