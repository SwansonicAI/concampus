from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import Institution, Person, Term, Tracked
from curriculum.models import Module, Program, Subject
from students.models import Study


class Area(Tracked, models.Model):
    """A Fachgebiet: how the Vorlesungsverzeichnis groups courses, e.g. Musiktheorie or Kammermusik."""

    capabilities = ('manage_courses',)

    name = models.CharField('Name', max_length=80, unique=True)
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Fachgebiet'
        verbose_name_plural = 'Fachgebiete'

    def __str__(self):
        return self.name


class Format(Tracked, models.Model):
    """A Lehrform, as the Studienpläne abbreviate it: Vorlesung (V), Seminar (S), Einzelunterricht (E) …"""

    capabilities = ('manage_courses',)

    name = models.CharField('Name', max_length=60, unique=True)
    short = models.CharField('Kürzel', max_length=8, blank=True, help_text='Wie in den Studienplänen, z. B. „S“ für Seminar.')
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Lehrform'
        verbose_name_plural = 'Lehrformen'

    def __str__(self):
        return self.name


class Course(Tracked, models.Model):
    """Anything a term offers that people take part in: seminars, lessons, exams, auditions, recognitions, theses.

    They share staff, participations, rooms and results; the kind decides which app shows them and how.
    """

    capabilities = ('manage_courses', 'manage_exams', 'run_auditions')
    scope = (['subject__institute', 'staff__institute'], ['program', 'modules__program'])

    class Kind(models.TextChoices):
        COURSE = 'course', 'Lehrveranstaltung'
        LESSON = 'lesson', 'Einzelunterricht'
        EXAM = 'exam', 'Prüfung'
        AUDITION = 'audition', 'Eignungsprüfung'
        RECOGNITION = 'recognition', 'Anerkennung'
        THESIS = 'thesis', 'Abschlussarbeit'  # one candidate: the title is the topic, the staff its examiners, the deadline when it is due

    class Allocation(models.TextChoices):
        ORDER = 'order', 'in der Reihenfolge der Anmeldung'
        CHOICE = 'choice', 'die Lehrenden wählen aus den Anmeldungen'
        STAFF = 'staff', 'die Lehrenden teilen ein, ohne Anmeldung'
        ENSEMBLE = 'ensemble', 'als Ensemble; die Lehrenden teilen die Betreuung zu'

    PANEL = [Kind.EXAM, Kind.AUDITION, Kind.THESIS]

    term = models.ForeignKey(Term, models.PROTECT, related_name='courses', verbose_name='Semester')
    kind = models.CharField('Art', max_length=12, choices=Kind, default=Kind.COURSE)
    title = models.CharField('Titel', max_length=200)
    area = models.ForeignKey(Area, models.SET_NULL, null=True, blank=True, related_name='courses', verbose_name='Fachgebiet')
    format = models.ForeignKey(Format, models.SET_NULL, null=True, blank=True, related_name='courses', verbose_name='Lehrform')
    program = models.ForeignKey(
        Program, models.PROTECT, null=True, blank=True, related_name='auditions', verbose_name='Studiengang',
        help_text='Nur bei Eignungsprüfungen: Studiengang, für den sie zulässt.',
    )
    subject = models.ForeignKey(Subject, models.PROTECT, null=True, blank=True, related_name='courses', verbose_name='Hauptfach')
    modules = models.ManyToManyField(Module, blank=True, related_name='courses', verbose_name='Anrechenbar für')
    staff = models.ManyToManyField(Person, blank=True, related_name='teaching', verbose_name='Lehrende / Kommission')
    hours = models.DecimalField('SWS', max_digits=4, decimal_places=1, null=True, blank=True, help_text='Bei Einzelunterricht je Teilnahme.')
    capacity = models.PositiveSmallIntegerField('Plätze', null=True, blank=True)
    allocation = models.CharField('Platzvergabe', max_length=8, choices=Allocation, default=Allocation.ORDER)
    opens = models.DateField('Anmeldung ab', null=True, blank=True, help_text='Erster Tag der Anmeldung; leer: sofort.')
    deadline = models.DateField('Anmeldeschluss', null=True, blank=True, help_text='Letzter Tag für An- und Abmeldung.')
    pass_mark = models.DecimalField(
        'Bestehensgrenze', max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Gesetzt: Punkte, bestanden ab dieser Grenze. Leer: Noten, bestanden bis 4,0.',
    )
    max_points = models.PositiveSmallIntegerField('Höchstpunktzahl', null=True, blank=True, help_text='Bei Punkten: so viele gibt jedes Mitglied höchstens.')
    accompaniment = models.ForeignKey(
        'self', models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Korrepetition',
        help_text='Bei Eignungsprüfungen: die Veranstaltung, in die eingetragen wird, wer eine Begleitung wünscht.',
    )
    released = models.DateTimeField('Ergebnisse freigegeben', null=True, blank=True)
    video = models.BooleanField('Videostufe', default=False, help_text='Nur bei Eignungsprüfungen: Bewerbende reichen ein Video ein, die Kommission wertet online.')
    description = models.TextField('Beschreibung', blank=True)

    class Meta:
        ordering = ['title']
        verbose_name = 'Veranstaltung'
        verbose_name_plural = 'Veranstaltungen'

    def __str__(self):
        return f'{self.title} ({self.term})'

    @classmethod
    def history(cls, pk):
        """The course's own changes and those to its results."""
        return (
            super().history(pk)
            | models.Q(table='courses.participation', row__in=Participation.objects.filter(course=pk).values('pk'))
            | models.Q(table='courses.score', row__in=Score.objects.filter(participation__course=pk).values('pk'))
        )

    @classmethod
    def reachable(cls, person, courses):
        """The pks of `courses` whose page (get_absolute_url) the person may open: any course or lesson; an exam,
        recognition or thesis for the offices that manage or decide them and its examiners (exams:exam); an audition
        for the offices that run them and its jury (admissions:audition)."""
        base = cls.objects.filter(pk__in=courses.values('pk'))
        found = (
            base.filter(kind__in=[cls.Kind.COURSE, cls.Kind.LESSON]) | base.filter(staff=person)
            | person.within(base.filter(kind__in=[cls.Kind.EXAM, cls.Kind.RECOGNITION, cls.Kind.THESIS]), 'manage_exams', 'decide_results')
            | person.within(base.filter(kind=cls.Kind.AUDITION), 'run_auditions')
        )
        return set(found.values_list('pk', flat=True))

    def get_absolute_url(self):
        app = {self.Kind.EXAM: 'exams:exam', self.Kind.RECOGNITION: 'exams:exam', self.Kind.THESIS: 'exams:exam', self.Kind.AUDITION: 'admissions:audition'}
        return reverse(app.get(self.kind, 'courses:course'), args=[self.pk])

    @property
    def is_open(self):
        today = timezone.localdate()
        return (self.opens is None or self.opens <= today) and (self.deadline is None or today <= self.deadline)

    @property
    def leavable(self):
        """Whether participants may withdraw themselves: from what they registered for alone, while registration is open.
        A class the teachers give out, or an ensemble, they leave through the teachers."""
        return self.is_open and self.allocation in (self.Allocation.ORDER, self.Allocation.CHOICE)

    @property
    def upcoming(self):
        """Registration has yet to open."""
        return self.opens is not None and timezone.localdate() < self.opens

    @property
    def withdraw_by(self):
        """The last day to withdraw from an exam: some weeks before the term's Prüfungszeitraum, else the Anmeldeschluss."""
        if self.term.exams_start:
            return self.term.exams_start - timedelta(weeks=Institution.get().withdraw_weeks)
        return self.deadline

    @property
    def withdrawable(self):
        return self.withdraw_by is None or timezone.localdate() <= self.withdraw_by

    @property
    def points(self):
        return self.pass_mark is not None

    @property
    def stage(self):
        """Short name of an audition stage: Video, Hauptfach, or the shared part's own name."""
        if self.video:
            return 'Video'
        if self.subject_id:
            return 'Hauptfach'
        return self.title.removeprefix('Eignungsprüfung ').split(' und ')[0]

    def clean(self):
        if self.pass_mark is not None and self.max_points is None:
            raise ValidationError({'max_points': 'Bei einer Bestehensgrenze in Punkten bitte die Höchstpunktzahl angeben.'})
        if self.pass_mark is not None and self.pass_mark > self.max_points:
            raise ValidationError({'pass_mark': 'Die Bestehensgrenze liegt über der Höchstpunktzahl.'})

    def passes(self, grade):
        return grade >= self.pass_mark if self.points else grade <= 4


class Participation(Tracked, models.Model):
    """A study taking part in a course. Passed, it credits `module`, one of the course's modules."""

    owner = 'study.person_id'
    capabilities = ('decide_results', 'run_auditions')
    scope = (['study__subject__institute', 'course__subject__institute'], ['study__program'])

    class Remark(models.TextChoices):  # as Prüfungsämter note them
        EXCUSED = 'rt', 'RT · Rücktritt mit triftigem Grund'
        ABSENT = 'ne', 'NE · nicht erschienen'
        CHEATED = 'tv', 'TV · Täuschungsversuch'

    course = models.ForeignKey(Course, models.CASCADE, related_name='participations', verbose_name='Veranstaltung')
    study = models.ForeignKey(Study, models.CASCADE, related_name='participations', verbose_name='Studium')
    module = models.ForeignKey(Module, models.PROTECT, null=True, blank=True, related_name='participations', verbose_name='Modul')
    note = models.TextField('Notiz', blank=True, help_text='Repertoire bei Prüfungen, Herkunft bei Anerkennungen.')
    slot = models.DateTimeField('Termin', null=True, blank=True)
    grade = models.DecimalField('Note / Punkte', max_digits=4, decimal_places=2, null=True, blank=True)
    passed = models.BooleanField('Bestanden', null=True, blank=True)
    remark = models.CharField(
        'Vermerk', max_length=2, choices=Remark, blank=True,
        help_text='Nicht erschienen und Täuschung gelten als nicht bestanden; ein Rücktritt mit triftigem Grund zählt nicht als Versuch.',
    )
    created = models.DateTimeField('Angemeldet', default=timezone.now, help_text='Reihenfolge für die Warteliste.')

    class Meta:
        ordering = ['slot', 'created']
        verbose_name = 'Teilnahme'
        verbose_name_plural = 'Teilnahmen'
        constraints = [
            models.UniqueConstraint(fields=['course', 'study', 'module'], name='unique_participation'),
            models.UniqueConstraint(fields=['course', 'study'], condition=models.Q(module=None), name='unique_participation_without_module'),
        ]

    def __str__(self):
        return f'{self.study.person} · {self.course}'


class Signup(Tracked, models.Model):
    """A wish for a place in a course whose staff choose; accepted, it becomes a participation."""

    owner = 'study.person_id'
    capabilities = ('manage_courses',)
    scope = (['course__subject__institute', 'course__staff__institute'], ['study__program'])

    course = models.ForeignKey(Course, models.CASCADE, related_name='signups', verbose_name='Veranstaltung')
    study = models.ForeignKey(Study, models.CASCADE, related_name='signups', verbose_name='Studium')
    module = models.ForeignKey(Module, models.PROTECT, null=True, blank=True, related_name='+', verbose_name='Modul')
    note = models.TextField('Nachricht an die Lehrenden', blank=True)
    created = models.DateTimeField('Angefragt', default=timezone.now)

    class Meta:
        ordering = ['created']
        verbose_name = 'Platzanfrage'
        verbose_name_plural = 'Platzanfragen'
        constraints = [models.UniqueConstraint(fields=['course', 'study'], name='unique_signup')]

    def __str__(self):
        return f'{self.study.person} · {self.course}'


class Ensemble(Tracked, models.Model):
    """Students who play together in a course such as Kammermusik: registered as a group, coached by one of its teachers."""

    capabilities = ('manage_courses',)
    scope = (['course__subject__institute', 'course__staff__institute'], ['members__program'])

    course = models.ForeignKey(Course, models.CASCADE, related_name='ensembles', verbose_name='Veranstaltung')
    name = models.CharField('Besetzung', max_length=80)
    members = models.ManyToManyField(Study, related_name='ensembles', verbose_name='Mitglieder')
    repertoire = models.TextField('Repertoire', blank=True)
    wish = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Wunsch-Betreuung')
    coach = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='coaching', verbose_name='Betreuung')
    created = models.DateTimeField('Angemeldet', default=timezone.now)

    class Meta:
        ordering = ['created']
        verbose_name = 'Ensemble'
        verbose_name_plural = 'Ensembles'

    def __str__(self):
        return f'{self.name} · {self.course}'


class Score(Tracked, models.Model):
    """One examiner's mark. The participation's grade is the panel's decision, usually the mean."""

    owner = 'participation.study.person_id'
    capabilities = ('decide_results', 'run_auditions')
    scope = (['participation__study__subject__institute', 'participation__course__subject__institute'], ['participation__study__program'])

    participation = models.ForeignKey(Participation, models.CASCADE, related_name='scores', verbose_name='Teilnahme')
    examiner = models.ForeignKey(Person, models.CASCADE, related_name='scores', verbose_name='Prüfer:in')
    value = models.DecimalField('Wertung', max_digits=4, decimal_places=2)

    class Meta:
        verbose_name = 'Wertung'
        verbose_name_plural = 'Wertungen'
        constraints = [models.UniqueConstraint(fields=['participation', 'examiner'], name='unique_score')]

    def __str__(self):
        return f'{self.participation} · {self.examiner}'
