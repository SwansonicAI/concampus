from collections import defaultdict

from django import forms
from django.contrib.auth.forms import BaseUserCreationForm
from django.db import models
from django.db.models import Case, Exists, OuterRef, Q, When
from django.utils import timezone
from django.utils.formats import date_format

from core import destatis
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
        self.fields['password1'].help_text = 'Mindestens 8 Zeichen, nicht nur Ziffern und nicht Ihrem Namen ähnlich.'
        self.fields['password2'].help_text = ''

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if Person.objects.filter(models.Q(username=email) | models.Q(email__iexact=email)).exists():
            raise forms.ValidationError('Mit dieser Adresse gibt es schon ein Konto. Bitte melden Sie sich an.')
        return email

    def save(self, commit=True):
        self.instance.username = self.cleaned_data['email']
        return super().save(commit)


class IntakeField(forms.ModelChoiceField):
    def label_from_instance(self, course):
        stage = 'mit Videostufe' if course.video else 'ohne Videostufe'
        return f'{course.program.title} · {course.subject} · ab {course.term}, {stage} (Frist {date_format(course.deadline, "j.n.Y")})'


class Hauptfach(forms.Select):
    """Each option names its Hauptfach, so the teachers below narrow to that class."""

    def create_option(self, *args, **kwargs):
        option = super().create_option(*args, **kwargs)
        if option['value']:
            option['attrs']['data-group'] = str(option['value'].instance.subject)
        return option


class ApplicationForm(forms.Form):
    audition = IntakeField(label='Studiengang und Hauptfach', queryset=None, empty_label=None, widget=Hauptfach(attrs={'data-leads': 'teacher'}))
    teacher = forms.ModelChoiceField(label='Wunschlehrer:in', queryset=None, required=False, empty_label='Keine Angabe')
    repertoire = forms.CharField(
        label='Prüfungsprogramm', widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Werke mit Komponist:in, Satz und ungefährer Dauer.',
    )
    entrance = forms.ChoiceField(
        label='Hochschulzugangsberechtigung', choices=[('', 'Noch nicht erworben')] + destatis.ENTRANCE, required=False,
        help_text='Die erste, mit der ein Studium in Deutschland möglich war.',
    )
    entrance_date = DayField(label='Erworben am', required=False, widget=forms.DateInput(format='%d.%m.%Y', attrs={'data-day': '', 'placeholder': 'TT.MM.JJJJ'}))
    entrance_region = forms.ChoiceField(label='Erworben in', choices=[('', '–')] + destatis.places(), required=False, help_text='Kreis, im Ausland der Staat.')
    accompanist = forms.BooleanField(label='Ich wünsche Korrepetition für die Prüfung.', required=False)
    declaration = forms.BooleanField(
        label='Ich versichere, dass meine Angaben vollständig und richtig sind.', help_text='Ersetzt den unterschriebenen Bewerbungsantrag.',
    )

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        studies = person.studies.all()  # who applied before: their instrument first, then its Institut's, at their level
        applied = studies.filter(program=OuterRef('program'), subject=OuterRef('subject'), start_term=OuterRef('term'))
        level = Q(program__postgraduate__in=studies.values('program__postgraduate'))
        auditions = self.fields['audition'].queryset = (
            Course.objects.filter(kind=Course.Kind.AUDITION, program__isnull=False, deadline__gte=timezone.localdate()).exclude(Exists(applied))
            .select_related('program', 'subject', 'term')
            .annotate(near=Case(
                When(level & Q(subject__in=studies.values('subject')), then=0), When(level & Q(subject__institute__in=studies.values('subject__institute')), then=1), default=2,
            ))
            .order_by('near', 'program__degree', 'program__name', 'subject__name')
        )
        if person.home:  # where the HZB was most likely gained
            self.fields['entrance_region'].choices = [('', '–'), ('Ihr Heimatwohnsitz', [(person.home, person.get_home_display())])] + destatis.places()
        classes = defaultdict(set)  # who teaches each Hauptfach one can apply for, grouped so applicants find their instrument
        for subject, pk in Course.objects.filter(kind=Course.Kind.LESSON, subject__in=auditions.values('subject'), staff__isnull=False).values_list('subject__name', 'staff'):
            classes[subject].add(pk)
        teachers = self.fields['teacher'].queryset = Person.objects.filter(pk__in={pk for pks in classes.values() for pk in pks})
        people = teachers.in_bulk()
        self.fields['teacher'].choices = [('', 'Keine Angabe')] + [
            (subject, [(p.pk, str(p)) for p in sorted((people[pk] for pk in pks), key=lambda p: p.name)]) for subject, pks in sorted(classes.items())
        ]




class AcceptForm(forms.ModelForm):
    """Immatrikulationsantrag: the health insurance, and what the official statistics ask about where the person lives and
    studied before."""

    degree_date = DayField(label='Abgeschlossen am', required=False, widget=forms.DateInput(format='%d.%m.%Y', attrs={'data-day': '', 'placeholder': 'TT.MM.JJJJ'}))

    class Meta:
        model = Person
        fields = [
            'insurer', 'insurance', 'home', 'residence', 'first_university', 'first_term', 'external_terms',
            'degree_university', 'degree_exam', 'degree_field', 'degree_date', 'degree_grade', 'vocational', 'internship',
        ]
        widgets = {'insurer': forms.TextInput(attrs={'list': 'insurers'})}
        help_texts = {
            'first_university': 'Wo Sie zum ersten Mal studiert haben; leer, wenn Sie jetzt zum ersten Mal studieren.',
            'first_term': 'Z. B. WiSe 2019/20; leer, wenn Sie jetzt zum ersten Mal studieren.',
            'external_terms': 'An anderen deutschen Hochschulen, Urlaubssemester eingeschlossen.',
            'degree_university': 'Nur wenn Sie schon einen Hochschulabschluss haben.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ['insurer', 'insurance', 'home']:
            self.fields[field].required = True
