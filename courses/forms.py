from collections import defaultdict

from django import forms
from django.db.models import F

from core.dates import ClockField, DayField, MomentField
from core.models import Person
from curriculum.models import Module
from rooms.forms import bookable
from .models import Course


class PlanForm(forms.Form):
    """A weekly time for a course across the lecture period."""

    weekday = forms.TypedChoiceField(
        label='Wochentag', coerce=int,
        choices=list(enumerate(['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag'])),
    )
    time = ClockField(label='Beginn')
    minutes = forms.IntegerField(label='Dauer (Min.)', min_value=15, max_value=480, initial=90)
    room = forms.ModelChoiceField(label='Raum', queryset=None, empty_label='Bitte wählen')

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)


class SlotForm(forms.Form):
    """A block of consecutive exam or audition slots."""

    start = MomentField(label='Beginn')
    minutes = forms.IntegerField(label='Minuten je Person', min_value=5, max_value=120, initial=20)
    room = forms.ModelChoiceField(label='Raum', queryset=None, empty_label='Bitte wählen')

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)


def leaf_modules():
    """Modules a course can count for, as choices grouped by programme in study-plan order."""
    groups = defaultdict(list)
    leaves = Module.objects.filter(children=None).select_related('program').order_by('program', F('semester').asc(nulls_last=True), 'code')
    for m in leaves:
        groups[str(m.program)].append((m.pk, f'{m.code} {m.title}'))
    return list(groups.items())


class CourseForm(forms.ModelForm):
    deadline = DayField(label='Anmeldeschluss', required=False, help_text='Letzter Tag für An- und Abmeldung.')

    class Meta:
        model = Course
        fields = ['title', 'kind', 'subject', 'staff', 'modules', 'hours', 'capacity', 'deadline', 'description']
        widgets = {
            'staff': forms.CheckboxSelectMultiple,
            'modules': forms.CheckboxSelectMultiple,
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'kind' in self.fields:
            self.fields['kind'].choices = [(k.value, k.label) for k in (Course.Kind.COURSE, Course.Kind.LESSON)]
        self.fields['staff'].queryset = Person.teachers()
        if 'accompaniment' in self.fields:
            self.fields['accompaniment'].queryset = Course.objects.filter(kind=Course.Kind.LESSON, term=self.instance.term_id).order_by('title')
        if 'modules' in self.fields:
            self.fields['modules'].choices = leaf_modules()
        if 'subject' in self.fields:
            self.fields['subject'].empty_label = 'Keines'


class ExamForm(CourseForm):
    class Meta(CourseForm.Meta):
        fields = ['title', 'staff', 'modules', 'deadline', 'pass_mark', 'max_points', 'description']
        labels = {'staff': 'Kommission', 'modules': 'Prüft das Modul'}


class AuditionForm(CourseForm):
    """One stage of an Eignungsprüfung. The first stage, with its programme, is what applicants apply for."""

    deadline = DayField(label='Bewerbungsschluss', required=False, help_text='Bis dahin laden Bewerbende Video und Unterlagen hoch.')

    class Meta(CourseForm.Meta):
        fields = ['title', 'term', 'program', 'subject', 'video', 'staff', 'deadline', 'capacity', 'pass_mark', 'max_points', 'accompaniment', 'description']
        labels = {'staff': 'Kommission', 'capacity': 'Studienplätze'}
        help_texts = {'program': 'Nur bei der ersten Stufe: der Studiengang, für den man sich hier bewirbt.'}


class AddForm(forms.Form):
    who = forms.CharField(
        label='Teilnehmende eintragen', max_length=120,
        widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}),
    )
