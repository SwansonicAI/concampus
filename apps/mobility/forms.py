from django import forms

from core.dates import DayField
from curriculum.models import Module
from students.models import Study
from .models import Agreement, Component, Mobility


def academic(day):
    """The Studienjahr a day falls in, by the year it begins: a stay from September on counts for that year's."""
    return day.year if day.month >= 9 else day.year - 1


class MobilityForm(forms.ModelForm):
    who = forms.CharField(label='Studierende:r', widget=forms.TextInput(attrs={'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}))
    start, end = DayField(label='Von'), DayField(label='Bis')
    layout = [(None, ['who', 'partner', ('kind', 'start', 'end')])]

    class Meta:
        model = Mobility
        fields = ['partner', 'kind', 'start', 'end']

    def clean_who(self):
        found = Study.find(self.cleaned_data['who'])
        if len(found) != 1:
            raise forms.ValidationError(f'Mehrere passen: {", ".join(str(s.person) for s in found)}. Bitte genauer.' if found else 'Niemand Immatrikuliertes passt dazu.')
        return found[0]

    def clean(self):
        data = super().clean()
        if (partner := data.get('partner')) and (start := data.get('start')):
            self.instance.study = data.get('who')
            self.instance.agreement = Agreement.objects.filter(partner=partner, first_year__lte=academic(start), last_year__gte=academic(start)).first()
            if self.instance.agreement is None:
                self.add_error('partner', f'Mit {partner} gibt es für {academic(start)}/{(academic(start) + 1) % 100:02d} kein Abkommen.')
        return data


class LineForm(forms.ModelForm):
    """A line of the Learning Agreement: a course there with its credits, or a module here."""

    class Meta:
        model = Component
        fields = ['table', 'title', 'code', 'credits', 'module']
        widgets = {'table': forms.HiddenInput}

    def __init__(self, *args, study, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['module'].queryset = Module.objects.filter(program=study.program, children=None).order_by('code')
        self.fields['credits'].localize, self.fields['credits'].widget.is_localized = True, True

    def clean(self):
        data = super().clean()
        if data.get('table') == Component.Table.ABROAD and not data.get('title'):
            self.add_error('title', 'Bitte die Veranstaltung dort nennen.')
        if data.get('table') == Component.Table.HOME and not data.get('module'):
            self.add_error('module', 'Bitte das Modul hier wählen.')
        return data
