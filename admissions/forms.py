from django import forms
from django.contrib.auth.forms import BaseUserCreationForm
from django.utils import timezone

from core.dates import DayField
from core.models import Person
from courses.models import Course


class SignupForm(BaseUserCreationForm):
    birth_date = DayField(label='Geburtsdatum', widget=forms.DateInput(format='%d.%m.%Y', attrs={'data-day': '', 'placeholder': 'TT.MM.JJJJ'}))

    class Meta:
        model = Person
        fields = ['first_name', 'last_name', 'email', 'gender', 'birth_date', 'birth_place', 'nationality']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ['first_name', 'last_name', 'email', 'gender', 'birth_date']:
            self.fields[field].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if Person.objects.filter(username=email).exists():
            raise forms.ValidationError('Mit dieser Adresse gibt es schon ein Konto. Bitte melden Sie sich an.')
        return email

    def save(self, commit=True):
        self.instance.username = self.cleaned_data['email']
        return super().save(commit)


class IntakeField(forms.ModelChoiceField):
    def label_from_instance(self, course):
        stage = 'mit Videostufe' if course.video else 'ohne Videostufe'
        return f'{course.program.degree} {course.program.name} · {course.subject} · ab {course.term}, {stage} (Frist {course.deadline:%d.%m.%Y})'


class ApplicationForm(forms.Form):
    audition = IntakeField(label='Studiengang und Hauptfach', queryset=None, empty_label=None)
    teacher = forms.ModelChoiceField(label='Wunschlehrer:in', queryset=None, required=False, empty_label='Keine Angabe')
    repertoire = forms.CharField(
        label='Prüfungsprogramm', widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Werke mit Komponist:in, Satz und ungefährer Dauer.',
    )
    entrance = forms.ChoiceField(label='Hochschulzugangsberechtigung', choices=[('', 'Noch nicht erworben')] + Person.Entrance.choices, required=False)
    entrance_date = DayField(label='Erworben am', required=False, widget=forms.DateInput(format='%d.%m.%Y', attrs={'data-day': '', 'placeholder': 'TT.MM.JJJJ'}))
    entrance_place = forms.CharField(label='Erworben in', required=False, help_text='Kreis und Land, im Ausland der Staat.')
    accompanist = forms.BooleanField(label='Ich wünsche Korrepetition für die Prüfung.', required=False)
    declaration = forms.BooleanField(
        label='Ich versichere, dass meine Angaben vollständig und richtig sind.', help_text='Ersetzt den unterschriebenen Bewerbungsantrag.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['audition'].queryset = (
            Course.objects.filter(kind=Course.Kind.AUDITION, program__isnull=False, deadline__gte=timezone.localdate())
            .select_related('program', 'subject', 'term').order_by('program__degree', 'program__name', 'subject__name')
        )
        self.fields['teacher'].queryset = Person.objects.filter(teaching__kind=Course.Kind.LESSON, teaching__subject__isnull=False).distinct()




class AcceptForm(forms.Form):
    insurer = forms.CharField(label='Krankenkasse', max_length=80, widget=forms.TextInput(attrs={'list': 'insurers'}))
    insurance = forms.ChoiceField(label='Versicherungsstatus', choices=Person.Insurance.choices)
