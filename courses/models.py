from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import Person, Term, Tracked
from curriculum.models import Module, Program, Subject
from students.models import Study


class Course(Tracked, models.Model):
    """Anything a term offers that people take part in: seminars, lessons, exams, auditions, recognitions.

    They share staff, participations, rooms and results; the kind decides which app shows them and how.
    """

    offices = ('ASA', 'Prüfungsamt', 'Dekanat')

    class Kind(models.TextChoices):
        COURSE = 'course', 'Lehrveranstaltung'
        LESSON = 'lesson', 'Einzelunterricht'
        EXAM = 'exam', 'Prüfung'
        AUDITION = 'audition', 'Eignungsprüfung'
        RECOGNITION = 'recognition', 'Anerkennung'

    PANEL = [Kind.EXAM, Kind.AUDITION]

    term = models.ForeignKey(Term, models.PROTECT, related_name='courses', verbose_name='Semester')
    kind = models.CharField('Art', max_length=12, choices=Kind, default=Kind.COURSE)
    title = models.CharField('Titel', max_length=200)
    program = models.ForeignKey(
        Program, models.PROTECT, null=True, blank=True, related_name='auditions', verbose_name='Studiengang',
        help_text='Nur bei Eignungsprüfungen: Studiengang, für den sie zulässt.',
    )
    subject = models.ForeignKey(Subject, models.PROTECT, null=True, blank=True, related_name='courses', verbose_name='Hauptfach')
    modules = models.ManyToManyField(Module, blank=True, related_name='courses', verbose_name='Anrechenbar für')
    staff = models.ManyToManyField(Person, blank=True, related_name='teaching', verbose_name='Lehrende / Kommission')
    hours = models.DecimalField('SWS', max_digits=4, decimal_places=1, null=True, blank=True, help_text='Bei Einzelunterricht je Teilnahme.')
    capacity = models.PositiveSmallIntegerField('Plätze', null=True, blank=True)
    deadline = models.DateField('Anmeldeschluss', null=True, blank=True, help_text='Letzter Tag für An- und Abmeldung.')
    pass_mark = models.DecimalField(
        'Bestehensgrenze', max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Gesetzt: Punkte, bestanden ab dieser Grenze. Leer: Noten, bestanden bis 4,0.',
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

    def get_absolute_url(self):
        app = {self.Kind.EXAM: 'exams:exam', self.Kind.RECOGNITION: 'exams:exam', self.Kind.AUDITION: 'admissions:audition'}
        return reverse(app.get(self.kind, 'courses:course'), args=[self.pk])

    @property
    def is_open(self):
        return self.deadline is None or timezone.localdate() <= self.deadline

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

    def passes(self, grade):
        return grade >= self.pass_mark if self.points else grade <= 4


class Participation(Tracked, models.Model):
    """A study taking part in a course. Passed, it credits `module`, one of the course's modules."""

    owner = 'study.person_id'
    offices = ('ASA', 'Prüfungsamt')

    course = models.ForeignKey(Course, models.CASCADE, related_name='participations', verbose_name='Veranstaltung')
    study = models.ForeignKey(Study, models.CASCADE, related_name='participations', verbose_name='Studium')
    module = models.ForeignKey(Module, models.PROTECT, null=True, blank=True, related_name='participations', verbose_name='Modul')
    note = models.TextField('Notiz', blank=True, help_text='Repertoire bei Prüfungen, Herkunft bei Anerkennungen.')
    slot = models.DateTimeField('Termin', null=True, blank=True)
    grade = models.DecimalField('Note / Punkte', max_digits=4, decimal_places=2, null=True, blank=True)
    passed = models.BooleanField('Bestanden', null=True, blank=True)
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


class Score(Tracked, models.Model):
    """One examiner's mark. The participation's grade is the panel's decision, usually the mean."""

    owner = 'participation.study.person_id'
    offices = ('ASA', 'Prüfungsamt')

    participation = models.ForeignKey(Participation, models.CASCADE, related_name='scores', verbose_name='Teilnahme')
    examiner = models.ForeignKey(Person, models.CASCADE, related_name='scores', verbose_name='Prüfer:in')
    value = models.DecimalField('Wertung', max_digits=4, decimal_places=2)

    class Meta:
        verbose_name = 'Wertung'
        verbose_name_plural = 'Wertungen'
        constraints = [models.UniqueConstraint(fields=['participation', 'examiner'], name='unique_score')]

    def __str__(self):
        return f'{self.participation} · {self.examiner}'
