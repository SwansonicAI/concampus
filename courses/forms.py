import re
from collections import defaultdict

from django import forms
from django.db.models import F, Min, Q
from django.utils import timezone

from core.dates import ClockField, DayField, MomentField, working
from core.models import Institution, Person
from curriculum.models import Module, Program
from rooms.forms import Participants, People, RoomField, bookable
from rooms.models import Booking, Room
from students.models import Study
from .models import Appointment, Course


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
        for field in self.fields.values():  # a table row: the column heading is the only label in sight
            field.widget.attrs.setdefault('aria-label', field.label)

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
        lately = Booking.objects.filter(course__kind=course.kind).order_by('-created').values_list('room', flat=True)[:40]  # the halls such exams and auditions are held in
        self.fields['room'].recent = list(dict.fromkeys(lately))[:5]
        booked = course.bookings.order_by('start').first()
        if booked:  # the block starts where the exam is booked
            self.fields['room'].initial, self.fields['start'].initial = booked.room_id, timezone.localtime(booked.start)
        if length := re.findall(r'(\d+) Min\.', ' '.join([course.title, *course.modules.values_list('title', flat=True)])):  # e.g. '(15–25 Min.)': the longest
            self.fields['minutes'].initial = max(int(n) for n in length)


def teachers(subject=None, institutes=(), module=(), appointed=None):
    """Teachers as choices, the likely ones first: those teaching the Hauptfach, then those teaching the courses of the
    modules in question, then those of the Institute in question, then every other Institut's. For a panel, those
    `appointed` as examiners come first and the others say they are not."""
    hauptfach = set(Course.objects.filter(kind=Course.Kind.LESSON, subject=subject).values_list('staff', flat=True)) if subject else set()
    teaching = set(Course.objects.filter(kind=Course.Kind.COURSE, modules__parent__in=module).values_list('staff', flat=True)) if module else set()
    groups = defaultdict(list)
    for t in Person.teachers().select_related('institute'):
        rank = 0 if t.pk in hauptfach else 1 if t.pk in teaching else 2 if t.institute_id in institutes else 3
        label = f'Hauptfach {subject}' if rank == 0 else 'Lehrende des Moduls' if rank == 1 else str(t.institute or 'Ohne Institut')
        if appointed is not None:
            rank, label = (-1, 'Als Prüfende bestellt') if t.pk in appointed else (rank, f'{label}, nicht bestellt')
        groups[rank, label].append((t.pk, str(t)))
    return [(label, people) for (_, label), people in sorted(groups.items())]


def appointed(course):
    """The pks of those appointed to examine on the course's panel, or None where it has no panel or no term yet."""
    if course.kind not in Course.PANEL or not course.term_id:
        return None
    return {pk for pk, subject in Appointment.examiners({course.year})[course.year] if subject == course.subject_id}


def substitutes(course):
    """Who may mark in place of an examiner who stands back: teachers off the panel, the appointed first."""
    staff = set(course.staff.values_list('pk', flat=True))
    return [(label, [(pk, name) for pk, name in people if pk not in staff]) for label, people in teachers(course.subject, appointed=appointed(course))]


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
        fields = ['title', 'kind', 'area', 'format', 'subject', 'rhythm', 'staff', 'modules', 'hours', 'capacity', 'allocation', 'opens', 'deadline', 'description']
        labels = {'staff': 'Lehrende'}
        localized_fields = ['hours', 'pass_mark']  # a decimal comma, in a text box
        widgets = {
            'staff': forms.CheckboxSelectMultiple,
            'modules': forms.CheckboxSelectMultiple,
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    layout = [
        (None, ['correction', 'title', ('kind', 'allocation'), ('area', 'format', 'subject', 'rhythm')]),
        ('Umfang und Anmeldung', [('hours', 'capacity', 'opens', 'deadline')]),
        ('Lehrende und Module', ['staff', 'programme', 'modules']),
        (None, ['description']),
    ]
    planned = ('kind', 'format', 'hours', 'modules', 'programme')  # what counts in the Deputat and the Studienpläne

    def __init__(self, *args, person=None, planning=True, over=False, **kwargs):
        super().__init__(*args, **kwargs)
        for name in () if planning else self.planned:
            self.fields.pop(name, None)
        if over:  # the semester is over: a correction, kept with its reason in the Verlauf
            self.fields['correction'] = forms.CharField(label='Grund der Korrektur', max_length=200, help_text='Das Semester ist vorbei; der Verlauf hält den Grund fest.')
        if 'kind' in self.fields:  # the Lehrform says how it is taught; this, how it is organised
            self.fields['kind'].choices = [(Course.Kind.COURSE.value, 'Lehrveranstaltung: gemeinsame Termine'), (Course.Kind.LESSON.value, 'Klasse: je Person eine eigene Unterrichtszeit')]
            self.fields['kind'].label = 'Organisation'
        if 'staff' in self.fields:
            self.fields['staff'].queryset = Person.teachers()
            self.fields['staff'].choices = teachers(
                self.instance.subject, person.ours('manage_courses') | person.ours('manage_exams') if person else (), appointed=appointed(self.instance),
            )
        if person and not self.instance.pk and 'teacher' in person.roles and not self.initial.get('staff'):  # who creates a course mostly teaches it
            self.initial['staff'] = [person.pk]
        if 'accompaniment' in self.fields:
            self.fields['accompaniment'].queryset = Course.objects.filter(kind=Course.Kind.LESSON, term=self.instance.term_id).order_by('title')
        if 'after' in self.fields:  # the stages of the same intake
            self.fields['after'].queryset = Course.objects.filter(kind=Course.Kind.AUDITION, term=self.instance.term_id).exclude(pk=self.instance.pk).order_by('title')
        if 'modules' in self.fields:
            self.fields['modules'].choices = leaf_modules()
            self.fields['programme'].initial = self.instance.pk and self.instance.modules.values_list('program', flat=True).first()
            names = [name for name in self.fields if name != 'programme']
            names.insert(names.index('modules'), 'programme')
            self.order_fields(names)
        else:
            self.fields.pop('programme', None)
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

    layout = [
        (None, ['title', 'modules']),
        ('Kommission', ['staff', ('oral', 'assessor')]),
        ('Anmeldung und Bewertung', [('opens', 'deadline', 'pass_mark', 'max_points')]),
        (None, ['description']),
    ]

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, person=person, **kwargs)
        self.fields['deadline'].help_text = f'Letzter Tag der Anmeldung. Abmelden geht bis {Institution.get().withdraw_weeks} Wochen vor dem Prüfungszeitraum.'
        modules = self.instance.modules.values('parent') if self.instance.pk else ()
        self.fields['staff'].choices = teachers(self.instance.subject, person.ours('manage_exams') if person else (), modules, appointed(self.instance))
        self.fields['assessor'].queryset = Person.teachers()
        self.fields['assessor'].choices = [('', 'Keiner'), *teachers(self.instance.subject, person.ours('manage_exams') if person else (), modules)]
        self.fields['modules'].choices = leaf_modules(exams=True)


WRITTEN = re.compile(r'arbeit|schriftlich|programmheft|dokumentation|dissertation', re.I)  # what of a final project is written


class ThesisForm(CourseForm):
    """An Abschlussarbeit. Registering it, the office picks the module of the candidate's plan it is written for."""

    module = forms.ModelChoiceField(label='Modul', queryset=Module.objects.none(), empty_label=None)
    deadline = DayField(
        label='Abgabe bis', help_text='Bei einer Verlängerung das neue Datum; die Person erhält einen Bescheid. Ein Samstag, Sonntag oder Feiertag wird zum nächsten Werktag.',
    )
    field_order = ['module', 'title', 'staff', 'deadline', 'description']
    layout = ()  # one after another

    class Meta(CourseForm.Meta):
        fields = ['title', 'staff', 'deadline', 'description']
        labels = {'title': 'Thema', 'staff': 'Gutachter:innen', 'description': 'Notiz'}

    def clean_deadline(self):
        return working(self.cleaned_data['deadline'])

    def __init__(self, *args, study=None, **kwargs):
        super().__init__(*args, **kwargs)
        if study is None:  # registered already: the module stays
            del self.fields['module']
            return
        exams = study.program.modules.filter(children=None, exam=True)
        finals = exams.filter(Q(final=True) | Q(parent__final=True))  # theses and the written parts of final projects
        if study.focus_id:  # not under a Schwerpunkt chosen against
            finals = finals.exclude(pk__in=[pk for pk, option in study.program.chosen_by().items() if option.pk != study.focus_id])
        exams = (finals if finals.exists() else exams).order_by(F('semester').desc(nulls_last=True), 'code')
        self.fields['module'].queryset = exams
        self.fields['module'].initial = next((m for m in exams if WRITTEN.search(m.title)), None) or exams.first()
        if study.teacher_id:  # the Hauptfachlehrer:in mostly reviews it
            self.initial['staff'] = [study.teacher_id]


class AuditionForm(CourseForm):
    """One stage of an Eignungsprüfung. The first stage, with its programme, is what applicants apply for."""

    deadline = DayField(label='Bewerbungsschluss', required=False, help_text='Bis dahin laden Bewerbende Video und Unterlagen hoch.')

    class Meta(CourseForm.Meta):
        fields = ['title', 'term', 'program', 'subject', 'video', 'after', 'staff', 'deadline', 'marks_by', 'capacity', 'pass_mark', 'max_points', 'accompaniment', 'description']
        labels = {'staff': 'Kommission', 'capacity': 'Studienplätze'}
        help_texts = {
            'program': 'Nur bei der ersten Stufe: der Studiengang, für den man sich hier bewirbt.',
            'video': 'Bewerbende reichen ein Video ein, die Kommission wertet online.',
        }

    layout = [
        (None, ['title', ('term', 'program', 'subject'), 'video', 'after']),
        ('Kommission und Wertung', ['staff', ('deadline', 'marks_by'), ('capacity', 'pass_mark', 'max_points')]),
        (None, ['accompaniment', 'description']),
    ]

    def clean(self):
        data = super().clean()
        if data.get('marks_by') and data.get('deadline') and data['marks_by'] <= data['deadline']:
            self.add_error('marks_by', 'Die Kommission wertet nach dem Bewerbungsschluss, sonst bleibt wer zuletzt einreicht ungewertet.')
        return data


class EnsembleForm(forms.Form):
    """Register an ensemble: the person registering and the others who play with them."""

    name = forms.CharField(label='Besetzung', max_length=80, help_text='Z. B. „Streichquartett“ oder „Duo Violine/Klavier“.')
    members = Participants(
        label='Weitere Mitglieder', queryset=None, widget=People,
        help_text='Ihre Klasse und wer schon mit Ihnen spielt steht hier; alle anderen finden Sie, wenn Sie oben einen Namen tippen.',
    )
    repertoire = forms.CharField(label='Repertoire', widget=forms.Textarea(attrs={'rows': 3}), required=False)
    wish = forms.ModelChoiceField(label='Wunsch-Betreuung', queryset=None, required=False, empty_label='Keine Angabe')

    def __init__(self, *args, course, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.fields['wish'].queryset = course.staff.all()
        enrolled, field = Q(studies__status='enrolled'), self.fields['members']
        field.queryset = Person.objects.filter(enrolled).annotate(subject=Min('studies__subject__name', filter=enrolled)).exclude(pk=person.pk)
        field.near = Q(studies__teacher__in=person.studies.values('teacher')) | Q(studies__ensembles__members__person=person)
        field.picked = self.data.getlist('members') if self.is_bound else []

    def clean_members(self):
        """Each person picked, by the study the course counts for."""
        found = []
        for person in self.cleaned_data['members']:
            if not (study := Study.objects.filter(person=person, status=Study.Status.ENROLLED, program__modules__courses=self.course).first()):
                raise forms.ValidationError(f'{person}: für dessen Studium ist die Veranstaltung nicht anrechenbar.')
            found.append(study)
        return found


class AddForm(forms.Form):
    who = forms.CharField(
        label='Teilnehmende eintragen', max_length=120,
        widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off'}),
    )
