from collections import defaultdict

from django import forms
from django.db.models import F

from core.dates import ClockField, DayField, MomentField
from core.models import Institution, Person
from curriculum.models import Module, Program
from rooms.forms import RoomField, bookable
from students.models import Study
from .models import Course


class PlanForm(forms.Form):
    """A weekly time for a course across the lecture period."""

    weekday = forms.TypedChoiceField(
        label='Wochentag', coerce=int,
        choices=list(enumerate(['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag'])),
    )
    time = ClockField(label='Beginn')
    minutes = forms.IntegerField(label='Dauer (Min.)', min_value=15, max_value=480, initial=90)
    room = RoomField(label='Raum', queryset=None, empty_label='Bitte wählen')

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)


class LessonForm(PlanForm):
    """One student's weekly lesson, a row of the class timetable; left without a time, the student has none."""

    person = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['time'].required = self.fields['room'].required = False

    def clean(self):
        data = super().clean()
        if bool(data.get('time')) != bool(data.get('room')):
            raise forms.ValidationError('Bitte Beginn und Raum angeben oder beides leer lassen.')
        return data


Lessons = forms.formset_factory(LessonForm, extra=0)


class DateForm(forms.Form):
    """One date of a course, such as a day of a block seminar; in one-to-one lessons a make-up lesson for one student."""

    day = DayField(label='Tag')
    begin = ClockField(label='Von')
    until = ClockField(label='Bis')
    room = RoomField(label='Raum', queryset=None, empty_label='Bitte wählen')

    def __init__(self, *args, person, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)
        if course and course.kind == Course.Kind.LESSON:
            self.fields['student'] = forms.ModelChoiceField(
                label='Für', queryset=Person.objects.filter(studies__participations__course=course).distinct(), empty_label='Bitte wählen',
            )
            self.order_fields(['student'])

    def clean(self):
        data = super().clean()
        if data.get('begin') and data.get('until') and data['until'] <= data['begin']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data


class SlotForm(forms.Form):
    """A block of consecutive exam or audition slots."""

    start = MomentField(label='Beginn')
    minutes = forms.IntegerField(label='Minuten je Person', min_value=5, max_value=120, initial=20)
    room = RoomField(label='Raum', queryset=None, empty_label='Bitte wählen')

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
    opens = DayField(label='Anmeldung ab', required=False, help_text='Erster Tag der Anmeldung; leer: sofort.')
    deadline = DayField(label='Anmeldeschluss', required=False, help_text='Letzter Tag für An- und Abmeldung.')

    class Meta:
        model = Course
        fields = ['title', 'kind', 'area', 'format', 'subject', 'staff', 'modules', 'hours', 'capacity', 'allocation', 'opens', 'deadline', 'description']
        labels = {'staff': 'Lehrende'}
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
        if 'program' in self.fields:
            self.fields['program'].queryset = Program.planned()
        if 'term' in self.fields:
            self.fields['term'].queryset = self.fields['term'].queryset.order_by('-start')
        if 'area' in self.fields:
            self.fields['area'].empty_label = 'Keines'


class ExamForm(CourseForm):
    class Meta(CourseForm.Meta):
        fields = ['title', 'staff', 'modules', 'opens', 'deadline', 'pass_mark', 'max_points', 'description']
        labels = {'staff': 'Kommission', 'modules': 'Prüft das Modul'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['deadline'].help_text = f'Letzter Tag der Anmeldung. Abmelden geht bis {Institution.get().withdraw_weeks} Wochen vor dem Prüfungszeitraum.'


class ThesisForm(CourseForm):
    """An Abschlussarbeit. Registering it, the office picks the module of the candidate's plan it is written for."""

    module = forms.ModelChoiceField(label='Modul', queryset=Module.objects.none(), empty_label=None)
    deadline = DayField(label='Abgabe bis', help_text='Bei einer Verlängerung das neue Datum; die Person erhält einen Bescheid.')
    field_order = ['module', 'title', 'staff', 'deadline', 'description']

    class Meta(CourseForm.Meta):
        fields = ['title', 'staff', 'deadline', 'description']
        labels = {'title': 'Thema', 'staff': 'Gutachter:innen', 'description': 'Notiz'}

    def __init__(self, *args, study=None, **kwargs):
        super().__init__(*args, **kwargs)
        if study is None:  # registered already: the module stays
            del self.fields['module']
            return
        exams = study.program.modules.filter(children=None, exam=True).order_by(F('semester').desc(nulls_last=True), 'code')
        self.fields['module'].queryset = exams
        self.fields['module'].initial = exams.filter(title__icontains='arbeit').first()  # Bachelor-, Master-, Diplomarbeit come last in the plan


class AuditionForm(CourseForm):
    """One stage of an Eignungsprüfung. The first stage, with its programme, is what applicants apply for."""

    deadline = DayField(label='Bewerbungsschluss', required=False, help_text='Bis dahin laden Bewerbende Video und Unterlagen hoch.')

    class Meta(CourseForm.Meta):
        fields = ['title', 'term', 'program', 'subject', 'video', 'staff', 'deadline', 'capacity', 'pass_mark', 'max_points', 'accompaniment', 'description']
        labels = {'staff': 'Kommission', 'capacity': 'Studienplätze'}
        help_texts = {
            'program': 'Nur bei der ersten Stufe: der Studiengang, für den man sich hier bewirbt.',
            'video': 'Bewerbende reichen ein Video ein, die Kommission wertet online.',
        }


class EnsembleForm(forms.Form):
    """Register an ensemble: the person registering and the others who play with them."""

    name = forms.CharField(label='Besetzung', max_length=80, help_text='Z. B. „Streichquartett“ oder „Duo Violine/Klavier“.')
    members = forms.CharField(label='Weitere Mitglieder', widget=forms.Textarea(attrs={'rows': 3}), help_text='Eine Person je Zeile: Name oder E-Mail-Adresse.')
    repertoire = forms.CharField(label='Repertoire', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    wish = forms.ModelChoiceField(label='Wunsch-Betreuung', queryset=None, required=False, empty_label='Keine Angabe')

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.fields['wish'].queryset = course.staff.all()

    def clean_members(self):
        """Each line one enrolled person for whose Studiengang the course counts."""
        found = []
        for line in filter(None, (line.strip() for line in self.cleaned_data['members'].splitlines())):
            studies = [s for s in Study.find(line) if self.course.modules.filter(program=s.program_id).exists()]
            if len(studies) != 1:
                names = ', '.join(str(s.person) for s in studies)
                raise forms.ValidationError(f'„{line}“: mehrere passen ({names}). Bitte genauer.' if studies else f'„{line}“: niemand, für den die Veranstaltung anrechenbar ist.')
            found.append(studies[0])
        return found


class AddForm(forms.Form):
    who = forms.CharField(
        label='Teilnehmende eintragen', max_length=120,
        widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}),
    )
