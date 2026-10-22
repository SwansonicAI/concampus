from django import forms

from core.models import Person
from .models import Study


class StudyForm(forms.ModelForm):
    class Meta:
        model = Study
        fields = ['teacher', 'focus', 'prior_terms']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['teacher'].queryset = Person.teachers()
        focus = self.fields['focus']
        focus.queryset = self.instance.program.modules.filter(children__isnull=False).distinct()
        focus.label_from_instance = lambda m: f'{m.code} {m.title}'.strip()
