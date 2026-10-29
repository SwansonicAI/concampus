import re
from collections import defaultdict

from django import forms
from django.db.models import F, Q
from django.utils import timezone

from core.dates import ClockField, DayField, MomentField
from core.models import Institution, Person
from curriculum.models import Module, Program
from rooms.forms import RoomField, bookable
from rooms.models import Room
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


class EnsembleField(forms.ModelChoiceField):
    def label_from_instance(self, ensemble):  # many a Streichquartett: who plays in it tells them apart
        return f'{ensemble.name}: {", ".join(s.person.last_name for s in ensemble.members.all())}'


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
        elif course and course.allocation == Course.Allocation.ENSEMBLE:  # a coaching session of one's own ensembles; the office books for any
            office = person.may('manage_courses', course)
            ensembles = (course.ensembles.all() if office else course.ensembles.filter(coach=person)).select_related('coach').prefetch_related('members__person')
            field = self.fields['ensemble'] = EnsembleField(label='Für', queryset=ensembles, required=False, empty_label='Alle')
            if office:
                own = [e for e in ensembles if e.coach_id == person.pk]
                field.choices = [('', 'Alle'), *(
                    (label, [(e.pk, field.label_from_instance(e)) for e in group]) for label, group in
                    [('Meine Ensembles', own), ('Weitere', [e for e in ensembles if e not in own])] if group
                )]
            self.order_fields(['ensemble'])

    def clean(self):
        data = super().clean()
        if data.get('begin') and data.get('until') and data['until'] <= data['begin']:
            raise forms.ValidationError('„Bis“ muss nach „Von“ liegen.')
        return data


class SlotForm(forms.Form):
    """A block of consecutive exam or audition slots, in the room the exam is booked in unless another is chosen."""

    start = MomentField(label='Beginn')
    minutes = forms.IntegerField(label='Minuten je Person', min_value=5, max_value=120, initial=20)
    pause = forms.IntegerField(label='Wechsel dazwischen', min_value=0, max_value=60, initial=5, help_text='Minuten')
    room = RoomField(label='Raum', queryset=None, empty_label='Bitte wählen')

    def __init__(self, *args, person, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = Room.objects.filter(Q(pk__in=bookable(person).values('pk')) | Q(bookings__course=course)).distinct()
        booked = course.bookings.order_by('start').first()
        if booked:  # the block starts where the exam is booked
            self.fields['room'].initial, self.fields['start'].initial = booked.room_id, timezone.localtime(booked.start)
        if length := re.findall(r'(\d+) Min\.', ' '.join([course.title, *course.modules.values_list('title', flat=True)])):  # e.g. '(15–25 Min.)': the longest
            self.fields['minutes'].initial = max(int(n) for n in length)


def teachers(subject=None, institutes=(), module=()):
    """Teachers as choices, the likely ones first: those teaching the Hauptfach, then those teaching the courses of the
    modules in question, then those of the Institute in question, then every other Institut's."""
    hauptfach = set(Course.objects.filter(kind=Course.Kind.LESSON, subject=subject).values_list('staff', flat=True)) if subject else set()
    teaching = set(Course.objects.filter(kind=Course.Kind.COURSE, modules__parent__in=module).values_list('staff', flat=True)) if module else set()
    groups = defaultdict(list)
    for t in Person.teachers().select_related('institute'):
        rank = 0 if t.pk in hauptfach else 1 if t.pk in teaching else 2 if t.institute_id in institutes else 3
        label = f'Hauptfach {subject}' if rank == 0 else 'Lehrende des Moduls' if rank == 1 else str(t.institute or 'Ohne Institut')
        groups[rank, label].append((t.pk, str(t)))
    return [(label, people) for (_, label), people in sorted(groups.items())]


def substitutes(course):
    """Who may mark in place of an examiner who stands back: teachers off the panel, those of the subject first."""
    staff = set(course.staff.values_list('pk', flat=True))
    return [(label, [(pk, name) for pk, name in people if pk not in staff]) for label, people in teachers(course.subject)]


def leaf_modules(exams=False):
    """Modules a course can count for, or only the exams of the plans, as choices grouped by programme in study-plan order."""
    groups = defaultdict(list)
    leaves = Module.objects.filter(children=None, **({'exam': True} if exams else {})).select_related('program').order_by('program', F('semester').asc(nulls_last=True), 'code')
    for m in leaves:
        groups[str(m.program)].append((m.pk, f'{m.code} {m.title}'))
    return list(groups.items())


class CourseForm(forms.ModelForm):
    programme = forms.ModelChoiceField(
        label='Module zeigen für', queryset=Program.planned(), required=False, empty_label='alle Studiengänge',
        help_text='Nur ein Filter für die Liste darunter: eine Veranstaltung kann für Module mehrerer Studiengänge zählen.',
        widget=forms.Select(attrs={'data-leads': 'modules'}),
    )
    opens = DayField(label='Anmeldung ab', required=False, help_text='Erster Tag der Anmeldung; leer: sofort.')
    deadline = DayField(label='Anmeldeschluss', required=False, help_text='Letzter Tag für An- und Abmeldung.')

    class Meta:
        model = Course
        fields = ['title', 'kind', 'area', 'format', 'subject', 'staff', 'modules', 'hours', 'capacity', 'allocation', 'opens', 'deadline', 'description']
        labels = {'staff': 'Lehrende'}
        localized_fields = ['hours', 'pass_mark']  # a decimal comma, in a text box
        widgets = {
            'staff': forms.CheckboxSelectMultiple,
            'modules': forms.CheckboxSelectMultiple,
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        if 'kind' in self.fields:  # the Lehrform says how it is taught; this, how it is organised
            self.fields['kind'].choices = [(Course.Kind.COURSE.value, 'Lehrveranstaltung: gemeinsame Termine'), (Course.Kind.LESSON.value, 'Klasse: je Person eine eigene Unterrichtszeit')]
            self.fields['kind'].label = 'Organisation'
        if 'staff' in self.fields:
            self.fields['staff'].queryset = Person.teachers()
            self.fields['staff'].choices = teachers(self.instance.subject, person.ours('manage_courses') | person.ours('manage_exams') if person else ())
        if person and not self.instance.pk and 'teacher' in person.roles and not self.initial.get('staff'):  # who creates a course mostly teaches it
            self.initial['staff'] = [person.pk]
        if 'accompaniment' in self.fields:
            self.fields['accompaniment'].queryset = Course.objects.filter(kind=Course.Kind.LESSON, term=self.instance.term_id).order_by('title')
        if 'modules' in self.fields:
            self.fields['modules'].choices = leaf_modules()
            self.fields['programme'].initial = self.instance.pk and self.instance.modules.values_list('program', flat=True).first()
            names = [name for name in self.fields if name != 'programme']
            names.insert(names.index('modules'), 'programme')
            self.order_fields(names)
        else:
            del self.fields['programme']
        if 'subject' in self.fields:
            self.fields['subject'].empty_label = 'Keines'
        if 'program' in self.fields:
            self.fields['program'].queryset = Program.planned()
        if 'term' in self.fields:
            self.fields['term'].queryset = self.fields['term'].queryset.order_by('-start')
        if 'area' in self.fields:
            self.fields['area'].empty_label = 'Keines'


class TeachingForm(CourseForm):
    """What a course's teachers decide themselves. Its hours count in their Deputat and its modules are the Studienpläne's:
    those, its staff and its kind are for the office."""

    class Meta(CourseForm.Meta):
        fields = ['capacity', 'allocation', 'opens', 'deadline', 'description']


class ExamForm(CourseForm):
    class Meta(CourseForm.Meta):
        fields = ['title', 'staff', 'oral', 'assessor', 'modules', 'opens', 'deadline', 'pass_mark', 'max_points', 'description']
        labels = {'staff': 'Kommission', 'modules': 'Prüft das Modul'}

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, person=person, **kwargs)
        self.fields['deadline'].help_text = f'Letzter Tag der Anmeldung. Abmelden geht bis {Institution.get().withdraw_weeks} Wochen vor dem Prüfungszeitraum.'
        modules = self.instance.modules.values('parent') if self.instance.pk else ()
        self.fields['staff'].choices = teachers(self.instance.subject, person.ours('manage_exams') if person else (), modules)
        self.fields['assessor'].queryset = Person.teachers()
        self.fields['assessor'].choices = [('', 'Keiner'), *teachers(self.instance.subject, person.ours('manage_exams') if person else (), modules)]
        self.fields['modules'].choices = leaf_modules(exams=True)


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
        if study.teacher_id:  # the Hauptfachlehrer:in mostly reviews it
            self.initial['staff'] = [study.teacher_id]


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
