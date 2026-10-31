import re
from collections import defaultdict
from datetime import timedelta

from django import forms
from django.db.models import F, Max, Min, Q
from django.utils import timezone

from core.dates import ClockField, DayField, MomentField, working
from core.models import Institution, Person, Term
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

    def __init__(self, *args, person, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = bookable(person)
        self.fields['room'].need = course and needed(course)
        if course and course.hours:  # an SWS is 45 minutes a week
            self.fields['minutes'].initial = int(course.hours * 45)
        if course and course.allocation == Course.Allocation.ENSEMBLE:
            for_ensemble(self, person, course)
        elif course and course.pk and course.groups.exists():
            for_group(self, person, course)


def needed(course):
    """The seats a course's dates take: its participants, or its places while nobody is in yet; in a course taught in
    groups, those of its biggest group."""
    if course.pk and (biggest := course.groups.aggregate(n=Max('capacity'))['n']):
        return biggest
    return course.participations.count() or course.capacity or 0


def for_ensemble(form, person, course):
    """'Für': whose dates in an ensemble course, a coach's own ensembles, the office's any; a coach of one has it chosen."""
    office = person.may('manage_courses', course)
    ensembles = (course.ensembles.all() if office else course.ensembles.filter(coach=person)).select_related('coach').prefetch_related('members__person')
    field = form.fields['ensemble'] = EnsembleField(label='Für', queryset=ensembles, required=False, empty_label='Alle')
    own = [e for e in ensembles if e.coach_id == person.pk]
    if office:
        field.choices = [('', 'Alle'), *(
            (label, [(e.pk, field.label_from_instance(e)) for e in group]) for label, group in
            [('Meine Ensembles', own), ('Weitere', [e for e in ensembles if e not in own])] if group
        )]
    if len(own) == 1:
        field.initial = own[0].pk
    form.order_fields(['ensemble'])


def for_group(form, person, course):
    """'Für': whose dates in a course taught in groups, a teacher's own groups, the office's any; empty, all of them
    together, as for a joint lecture."""
    office = person.may('manage_courses', course)
    groups = course.groups.all() if office else course.groups.filter(staff=person)
    form.fields['group'] = forms.ModelChoiceField(label='Für', queryset=groups, required=False, empty_label='Alle Gruppen')
    form.fields['group'].label_from_instance = lambda g: f'Gruppe {g.number}'
    if not office and len(groups) == 1:
        form.fields['group'].initial = groups[0].pk
    form.order_fields(['group'])


class GroupForm(PlanForm):
    """A group of a course: its teachers and places, and its weekly time across the lecture period."""

    staff = forms.ModelMultipleChoiceField(label='Lehrende', queryset=None, widget=forms.CheckboxSelectMultiple)
    capacity = forms.IntegerField(label='Plätze', min_value=1, max_value=999, help_text='Höchstens so viele, wie der Raum fasst.')
    layout = [('Wann und wo, jede Woche', [('weekday', 'time', 'minutes', 'room')]), (None, ['capacity', 'staff'])]

    def __init__(self, *args, person, course, **kwargs):
        super().__init__(*args, person=person, course=course, **kwargs)
        self.fields.pop('group', None)
        self.fields['room'].need = self.initial.get('capacity')
        self.fields['staff'].queryset = Person.teachers()
        self.fields['staff'].choices = teachers(course.subject, person.ours('manage_courses'), course.modules.values('parent'))

    def clean(self):
        data = super().clean()
        if data.get('room') and data.get('capacity') and data['capacity'] > data['room'].capacity:
            self.add_error('capacity', f'{data["room"]} fasst {data["room"].capacity} Plätze.')
        return data


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
        if course and course.kind == Course.Kind.COURSE:  # a lesson's date is for one
            self.fields['room'].need = needed(course)
        if course and course.kind == Course.Kind.LESSON:
            self.fields['student'] = forms.ModelChoiceField(
                label='Für', queryset=Person.objects.filter(studies__participations__course=course).distinct(), empty_label='Bitte wählen',
            )
            self.order_fields(['student'])
        elif course and course.allocation == Course.Allocation.ENSEMBLE:  # a coaching session of one's own ensembles; the office books for any
            for_ensemble(self, person, course)
        elif course and course.groups.exists():
            for_group(self, person, course)

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
    """The course work of the plans a course can count for, or their exams, as choices grouped by programme, the
    programmes as lists show them (Bachelor first), each in study-plan order."""
    groups, shared = defaultdict(list), defaultdict(set)
    rank = {pk: n for n, pk in enumerate(Program.objects.ordered().values_list('pk', flat=True))}
    leaves = sorted(
        Module.objects.filter(children=None, exam=exams).select_related('program').order_by(F('semester').asc(nulls_last=True), 'code'),
        key=lambda m: rank[m.program_id],
    )
    for m in leaves:
        shared[m.code, m.title].add(m.program.name)
    for m in leaves:
        others = sorted(shared[m.code, m.title] - {m.program.name}) if m.code else []
        groups[str(m.program)].append((m.pk, f'{m.code} {m.title}' + (f' · auch in {", ".join(others)}' if others else '')))
    return list(groups.items())


class CourseForm(forms.ModelForm):
    programme = forms.ModelChoiceField(
        label='Module zeigen für', queryset=Program.planned(), required=False, empty_label='alle Studiengänge',
        help_text='Nur ein Filter für die Liste darunter: eine Veranstaltung kann für Module mehrerer Studiengänge zählen.',
        widget=forms.Select(attrs={'data-leads': 'modules'}),
    )
    opens = DayField(label='Anmeldung ab', required=False, help_text='Erster Tag der Anmeldung; leer: sofort.')
    deadline = DayField(label='Anmeldeschluss', required=False, help_text='Letzter Tag der Anmeldung; abmelden geht bis zum Ende der Vorlesungszeit.')

    class Meta:
        model = Course
        fields = [
            'term', 'title', 'kind', 'area', 'format', 'subject', 'rhythm', 'staff', 'modules', 'hours', 'capacity', 'allocation', 'grouping', 'opens', 'deadline',
            'clash_dates', 'description',
        ]
        labels = {'staff': 'Lehrende'}
        localized_fields = ['hours', 'pass_mark']  # a decimal comma, in a text box
        widgets = {
            'staff': forms.CheckboxSelectMultiple,
            'modules': forms.CheckboxSelectMultiple(attrs={'data-twins': ''}),  # a module several Studiengänge share: ticked once for all
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    layout = [
        (None, ['correction', 'term', 'title', ('kind', 'allocation'), ('area', 'format'), ('subject', 'rhythm')]),
        ('Umfang und Anmeldung', [('hours', 'capacity', 'opens', 'deadline'), ('grouping', 'clash_dates')]),
        ('Lehrende und Module', ['staff', 'programme', 'modules']),
        (None, ['description']),
    ]
    planned = ('kind', 'format', 'hours', 'modules', 'programme')  # what counts in the Deputat and the Studienpläne
    moves = False  # a course stays in its semester; an audition may move

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
        grouped = self.instance.pk and self.instance.groups.exists()
        if 'capacity' in self.fields and grouped:  # the places are the groups'
            del self.fields['capacity']
        if 'grouping' in self.fields and not grouped:  # how groups are given out, once there are some
            del self.fields['grouping']
        if {'staff', 'modules'} <= set(self.fields):  # who taught these modules lately, to come first once they are ticked
            taught = defaultdict(set)
            for module, teacher in Course.objects.filter(kind=Course.Kind.COURSE, term__start__gte=timezone.localdate() - timedelta(days=730)).values_list('modules', 'staff').distinct():
                if module and teacher:
                    taught[module].add(teacher)
            self.taught = {m: sorted(t) for m, t in taught.items()}
        if 'clash_dates' in self.fields:
            self.fields['clash_dates'].help_text = f'Überschneidungen mit dem Stundenplan; leer: wie in den Hochschuldaten, ab {Institution.get().clash_dates}. Verhindert wird die Anmeldung nie.'
        if 'program' in self.fields:
            self.fields['program'].queryset = Program.planned()
        if 'term' in self.fields and self.instance.pk and not self.moves:  # its dates and people are that semester's
            self.fields.pop('term')
        elif 'term' in self.fields:
            self.fields['term'].queryset = self.fields['term'].queryset.order_by('-start') if self.moves else Term.objects.filter(end__gte=timezone.localdate()).order_by('start')
        for name in ('area', 'format'):  # by them the Verzeichnis, the Deputat and Moodle order it: never left empty
            if name in self.fields:
                self.fields[name].required, self.fields[name].empty_label = True, 'Bitte wählen'


    def clean_modules(self):
        """A module several Studiengänge share, the same code and title in each, counts for all of them."""
        chosen = self.cleaned_data['modules']
        pairs = {(m.code, m.title) for m in chosen if m.code}
        twins = Module.objects.filter(children=None, code__in={c for c, _ in pairs}, title__in={t for _, t in pairs}) if pairs else Module.objects.none()
        return Module.objects.filter(pk__in={m.pk for m in chosen} | {m.pk for m in twins if (m.code, m.title) in pairs})


class TeachingForm(CourseForm):
    """What a course's teachers decide themselves. Its hours count in their Deputat and its modules are the Studienpläne's:
    those, its staff and its kind are for the office."""

    class Meta(CourseForm.Meta):
        fields = ['capacity', 'allocation', 'opens', 'deadline', 'clash_dates', 'description']


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

    moves = True
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
        program, term = data.get('program'), data.get('term')
        if program and program.starts and term and term.start < program.starts.start:
            self.add_error('program', f'Diese PO-Version gilt erst ab dem {program.starts}; wer im {term} beginnt, beginnt in der vorigen.')
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
        widget=forms.TextInput(attrs={'list': 'enrolled', 'placeholder': 'Name oder Matrikelnummer', 'autocomplete': 'off', 'size': 32}),
    )

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course and course.groups.exists():  # into a group, its places notwithstanding: the teachers decide
            self.fields['group'] = forms.ModelChoiceField(label='In Gruppe', queryset=course.groups.all(), empty_label=None)
            self.fields['group'].label_from_instance = lambda g: f'Gruppe {g.number}'
