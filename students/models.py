import re
import uuid
from pathlib import Path

from django.db import models
from django.utils.formats import number_format
from django.urls import reverse
from django.utils import timezone

from core import texts
from core.models import Keyed, Message, Person, Term, Tracked
from core.text import matching
from curriculum.models import Program, Subject, weighted

class Study(Tracked, models.Model):
    """One person's path through one programme, from application to graduation."""

    owner = 'person_id'
    capabilities = ('manage_students',)

    class Status(models.TextChoices):
        APPLIED = 'applied', 'Beworben'
        ADMITTED = 'admitted', 'Zugelassen'
        WAITLISTED = 'waitlisted', 'Nachrückliste'
        REJECTED = 'rejected', 'Abgelehnt'
        ENROLLED = 'enrolled', 'Immatrikuliert'
        GUEST = 'guest', 'Gasthörer:in'
        GRADUATED = 'graduated', 'Abgeschlossen'
        LEFT = 'left', 'Ausgeschieden'

    APPLICATION = [Status.APPLIED, Status.ADMITTED, Status.WAITLISTED, Status.REJECTED]

    person = models.ForeignKey(Person, models.CASCADE, related_name='studies', verbose_name='Person')
    program = models.ForeignKey(Program, models.PROTECT, related_name='studies', verbose_name='Studiengang')
    subject = models.ForeignKey(Subject, models.PROTECT, related_name='studies', verbose_name='Hauptfach')
    focus = models.ForeignKey(
        'curriculum.Module', models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Schwerpunkt',
        help_text='Der gewählte Bereich des Studienplans, wo die Ordnung Schwerpunkte vorsieht.',
    )
    start_term = models.ForeignKey(Term, models.PROTECT, related_name='intake', verbose_name='Studienbeginn')
    status = models.CharField('Status', max_length=10, choices=Status, default=Status.APPLIED)
    since = models.DateField('Status seit', default=timezone.localdate)
    teacher = models.ForeignKey(
        Person, models.SET_NULL, null=True, blank=True, related_name='class_studies',
        verbose_name='Hauptfachlehrer:in', help_text='Bis zur Zulassung der Wunsch aus der Bewerbung.',
    )
    prior_terms = models.PositiveSmallIntegerField('Angerechnete Semester', default=0)

    class Meta:
        ordering = ['-start_term__start', 'person__last_name']
        verbose_name = 'Studium'
        verbose_name_plural = 'Studien'
        constraints = [models.UniqueConstraint(fields=['person', 'program', 'subject', 'start_term'], name='unique_study')]

    def __str__(self):
        return f'{self.person} · {self.subject} · {self.program}'

    def get_absolute_url(self):
        if self.status in self.APPLICATION:
            return reverse('admissions:application', args=[self.pk])
        return reverse('students:study', args=[self.pk])

    @classmethod
    def find(cls, text):
        """Enrolled studies and guest auditors meant by a name, e-mail or Matrikelnummer, however it is typed; at most five."""
        studies = cls.objects.filter(status__in=[cls.Status.ENROLLED, cls.Status.GUEST]).select_related('person', 'program')
        if number := re.search(r'\d{5,}', text):
            if exact := list(studies.filter(person__matriculation=number.group())):
                return exact
        return list(matching(studies, text, 'person__first_name', 'person__last_name', 'person__birth_name', 'person__email')[:5])

    def set_status(self, status):
        self.status, self.since = status, timezone.localdate()
        self.save(update_fields=['status', 'since'])

    def wording(self, key, **values):
        """(subject, body) of a text about this study, its placeholders filled in (core.texts)."""
        return texts.render(
            key, vorname=self.person.first_name, nachname=self.person.last_name, studiengang=f'{self.program.degree} {self.program.name}',
            hauptfach=self.subject, semester=self.start_term, matrikelnummer=self.person.matriculation or '', **values,
        )

    def notify(self, key, link='', **values):
        """Issue a Bescheid: it goes to the Postfach and stays in the student file as a printable letter."""
        subject, body = self.wording(key, **values)
        message = Message.send(self.person, subject, body, link, sender=texts.block('absender_bescheide'))
        Document.objects.create(person=self.person, study=self, kind=DocumentType.system('notice'), title=subject, message=message)

    def missing(self):
        """What still blocks the enrolment of an admitted applicant."""
        reasons = self.person.missing()
        if not self.terms.filter(term=self.start_term).exists():
            reasons.insert(0, 'Studienplatz noch nicht angenommen')
        if not self.person.entrance:
            reasons.append('Hochschulzugangsberechtigung nicht erfasst')
        return reasons

    @property
    def semester(self):
        """Fachsemester: credited prior terms plus registered terms so far, leave excluded."""
        return self.prior_terms + self.terms.filter(leave=False, term__start__lte=timezone.localdate()).count()

    @property
    def university_semester(self):
        """Hochschulsemester: every term the person has been registered here, leave included, plus semesters elsewhere."""
        here = StudyTerm.objects.filter(study__person=self.person_id, term__start__lte=timezone.localdate()).values('term').distinct().count()
        return self.person.external_terms + here

    def record(self):
        """Credited participations, one per module, a graded one where there is one. Exams count once released."""
        best = {}
        rows = (
            self.participations.filter(passed=True, module__isnull=False)
            .exclude(course__kind='exam', course__released=None)
            .select_related('module', 'course__term')
        )
        for p in rows:
            if p.grade is not None or p.module_id not in best:
                best[p.module_id] = p
        return sorted(best.values(), key=lambda p: p.module.code)

    def progress(self):
        """(curriculum tree with earned credits and grades, final grade so far)."""
        tree = self.program.curriculum({p.module_id: p.grade for p in self.record()})
        return tree, weighted(tree, self.program.rounding)


class StudyTerm(Tracked, models.Model):
    """A study registered for one term (Rückmeldung), possibly as leave (Urlaubssemester).

    The fee is paid per person: one paid registration covers all of a person's studies that term.
    """

    owner = 'study.person_id'
    capabilities = ('manage_students',)

    study = models.ForeignKey(Study, models.CASCADE, related_name='terms', verbose_name='Studium')
    term = models.ForeignKey(Term, models.PROTECT, related_name='registrations', verbose_name='Semester')
    leave = models.BooleanField('Urlaubssemester', default=False)
    paid = models.DateField('Beitrag bezahlt am', null=True, blank=True)

    class Meta:
        ordering = ['term__start']
        verbose_name = 'Rückmeldung'
        verbose_name_plural = 'Rückmeldungen'
        constraints = [models.UniqueConstraint(fields=['study', 'term'], name='unique_study_term')]

    def __str__(self):
        return f'{self.study} · {self.term}'


class DocumentType(Tracked, Keyed):
    """A kind of document: what applicants hand in, and the Bescheide the Hochschule issues. When the application
    checklist asks for it and what an upload may be are its settings."""

    class Condition(models.TextChoices):
        ALWAYS = 'always', 'immer'
        VIDEO = 'video', 'wenn es eine Videostufe gibt; die Kommission sichtet es'
        DEGREE = 'degree', 'wenn der Studiengang einen Abschluss voraussetzt'
        MINOR = 'minor', 'bei Minderjährigen'
        SUBJECTS = 'subjects', 'bei den gewählten Hauptfächern'
        FOREIGN = 'foreign', 'bei ausländischer Staatsangehörigkeit'
        NEVER = 'never', 'nicht in der Checkliste'

    SYSTEM = {'notice': {'name': 'Bescheid', 'condition': Condition.NEVER}}  # what the code files itself
    capabilities = ('run_auditions',)

    name = models.CharField('Name', max_length=80, unique=True)
    condition = models.CharField('In der Checkliste', max_length=10, choices=Condition, default=Condition.ALWAYS)
    subjects = models.ManyToManyField(Subject, blank=True, related_name='+', verbose_name='Hauptfächer', help_text='Nur für „bei den gewählten Hauptfächern“.')
    required = models.BooleanField('Pflicht', default=True, help_text='Ohne Haken freiwillig: die Bewerbung ist auch ohne vollständig.')
    note = models.TextField('Hinweis für Bewerbende', blank=True)
    file_types = models.CharField('Dateitypen', max_length=120, blank=True, help_text='Endungen, z. B. „.pdf, .jpg“; leer: PDF und Bilder.')
    max_mb = models.PositiveIntegerField('Höchstens (MB)', null=True, blank=True)
    single = models.BooleanField('Nur eine Datei', default=False, help_text='Eine neue ersetzt die alte.')
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Unterlagenart'
        verbose_name_plural = 'Unterlagenarten'

    def __str__(self):
        return self.name

    @classmethod
    def system(cls, key):
        return cls.objects.get_or_create(key=key, defaults=cls.SYSTEM[key])[0]

    @property
    def extensions(self):
        return tuple(e.strip().lower() for e in self.file_types.replace(';', ',').split(',') if e.strip())

    @property
    def limit(self):
        return f'{number_format(self.max_mb / 1024, 1)} GB' if self.max_mb >= 1024 else f'{self.max_mb} MB'


def stored(document, filename):
    """A random name: nobody finds a file by guessing its address. The title keeps what it was called."""
    return f'dokumente/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class Document(Tracked, models.Model):
    """A file or link a person handed in or received: CV, certificates, audition video, attests, Bescheide."""

    owner = 'person_id'
    capabilities = ('manage_students',)

    VIDEO_FILES = ('.mp4', '.m4v', '.mov', '.webm', '.ogv', '.ogg')  # what browsers play

    person = models.ForeignKey(Person, models.CASCADE, related_name='documents', verbose_name='Person')
    study = models.ForeignKey(Study, models.CASCADE, null=True, blank=True, related_name='documents', verbose_name='Studium')
    kind = models.ForeignKey(DocumentType, models.PROTECT, to_field='key', related_name='documents', verbose_name='Art')
    title = models.CharField('Titel', max_length=120)
    file = models.FileField('Datei', upload_to=stored, blank=True)
    url = models.URLField('Link', blank=True)
    message = models.ForeignKey(Message, models.CASCADE, null=True, blank=True, related_name='+', verbose_name='Bescheid')
    created = models.DateTimeField('Eingegangen', default=timezone.now)

    class Meta:
        ordering = ['created']
        verbose_name = 'Dokument'
        verbose_name_plural = 'Dokumente'

    def __str__(self):
        return self.title

    @property
    def href(self):
        if self.message_id:
            return reverse('letter', args=[self.message_id])
        return reverse('admissions:document', args=[self.pk]) if self.file else self.url

    @property
    def playable(self):
        """A video the browser plays right here: an upload, or a link straight to a video file."""
        return (self.file.name if self.file else self.url).lower().split('?')[0].endswith(self.VIDEO_FILES)
