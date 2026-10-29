from django import forms

from core.models import Person
from courses.models import Course
from .models import Study


class StudyForm(forms.ModelForm):
    class Meta:
        model = Study
        fields = ['teacher', 'prior_terms', 'exit_reason', 'structured', 'employed', 'cumulative']  # the Schwerpunkt is recorded on the study page, by the office deciding results

    layout = [(None, [('teacher', 'prior_terms'), 'exit_reason', ('structured', 'employed', 'cumulative')])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        study = self.instance
        teaching = Person.objects.filter(teaching__kind=Course.Kind.LESSON, teaching__subject=study.subject_id)  # Hauptfach classes
        self.fields['teacher'].queryset = (teaching | Person.objects.filter(pk=study.teacher_id)).distinct() if teaching.exists() else Person.teachers()
        if study.status not in Study.FORMER:  # the ground comes with the Exmatrikulation
            del self.fields['exit_reason']
        if not (study.program_id and study.program.kind == '5'):  # only a Promotion
            for field in ['structured', 'employed', 'cumulative']:
                del self.fields[field]
