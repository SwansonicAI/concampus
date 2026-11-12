from django import forms
from django.db.models import Q

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
        own = set(Person.objects.filter(teaching__kind=Course.Kind.LESSON, teaching__subject=study.subject_id).values_list('pk', flat=True))  # Hauptfach classes
        everyone = list(Person.objects.filter(Q(pk__in=Person.teachers().values('pk')) | Q(pk=study.teacher_id)).order_by('last_name', 'first_name'))
        self.fields['teacher'].queryset = Person.objects.filter(pk__in=[p.pk for p in everyone])
        self.fields['teacher'].choices = [('', '–')] + [  # who teaches the Hauptfach first; anyone else, as someone new, under „Weitere“
            (group, [(p.pk, str(p)) for p in everyone if (p.pk in own) == mine]) for group, mine in [(f'Hauptfach {study.subject}', True), ('Weitere', False)]
        ]
        if study.status not in Study.FORMER:  # the ground comes with the Exmatrikulation
            del self.fields['exit_reason']
        if not (study.program_id and study.program.kind == '5'):  # only a Promotion
            for field in ['structured', 'employed', 'cumulative']:
                del self.fields[field]
