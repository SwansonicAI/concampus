from django import forms
from django.db.models import Q

from core.dates import PastField
from core.models import Person
from courses.models import Course
from curriculum.models import Program, Subject
from .models import Study


class GuestForm(forms.Form):
    """Taking on a Gasthörer:in, who attends courses, or a Zweithörer:in, enrolled elsewhere, who attends and takes exams
    here (Immatrikulationsordnung): on paper application, entered by the office."""

    status = forms.ChoiceField(label='Als', choices=[(s.value, s.label) for s in (Study.Status.GUEST, Study.Status.SECOND)], widget=forms.RadioSelect)
    first_name = forms.CharField(label='Vorname', max_length=150)
    last_name = forms.CharField(label='Nachname', max_length=150)
    email = forms.EmailField(label='E-Mail')
    birth_date = PastField(label='Geburtsdatum')
    program = forms.ModelChoiceField(label='Studiengang', queryset=Program.objects.ordered(), help_text='Für Gasthörende die Gasthörerschaft; für Zweithörende der Studiengang, in dem sie hier hören.')
    subject = forms.ModelChoiceField(label='Fach', queryset=Subject.objects.order_by('name'))
    home_university = forms.CharField(label='Heimathochschule', max_length=120, required=False)
    home_matriculation = forms.CharField(label='Matrikelnummer dort', max_length=20, required=False)

    layout = [(None, ['status', ('first_name', 'last_name'), ('email', 'birth_date'), ('program', 'subject'), ('home_university', 'home_matriculation')])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('home_university', 'home_matriculation'):
            self.fields[name].widget.attrs['data-for'] = 'status:second'

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if Person.objects.filter(Q(username=email) | Q(email__iexact=email)).exists():
            raise forms.ValidationError('Mit dieser Adresse gibt es schon ein Konto; ein weiteres Studium legt die Studierendenverwaltung an der Person an.')
        return email

    def clean(self):
        data = super().clean()
        if data.get('status') == Study.Status.SECOND and not data.get('home_university'):
            self.add_error('home_university', 'Bei Zweithörenden: wo sie immatrikuliert sind.')
        if (program := data.get('program')) and data.get('subject') and program.subjects.exists() and not program.subjects.filter(pk=data['subject'].pk).exists():
            self.add_error('subject', f'{program} bietet das Hauptfach {data["subject"]} nicht an.')
        return data


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
