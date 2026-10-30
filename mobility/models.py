"""Erasmus+ stays abroad (docs/mobility.md): the partner Hochschulen, the agreements with them, and students' stays there
with their Learning Agreements. Shaped after Erasmus Without Paper, so that its network or the EWP Dashboard can carry
them: a Partner is its HEI, an Agreement its IIA, a Mobility with its Components its outgoing mobility and Online
Learning Agreement."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import COUNTRIES, KeyField, Person, Tracked
from curriculum.models import Module, Subject
from students.models import Study


class Partner(Tracked, models.Model):
    """A Hochschule abroad that exchanges students with this one."""

    capabilities = ('manage_mobility',)

    name = models.CharField('Name', max_length=200, unique=True)
    city = models.CharField('Ort', max_length=80, blank=True)
    country = KeyField('Staat', max_length=60, keys=COUNTRIES)
    erasmus = models.CharField('Erasmus-Code', max_length=20, blank=True, help_text='Wie die Erasmus-Charta sie nennt, z. B. „A WIEN05“.')
    hei = models.CharField('Kennung in Erasmus Without Paper', max_length=100, blank=True, help_text='Ihr SCHAC-Code, z. B. „mdw.ac.at“.')
    website = models.URLField('Website', blank=True)
    email = models.EmailField('International Office', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Partnerhochschule'
        verbose_name_plural = 'Partnerhochschulen'

    def __str__(self):
        return self.name


def year(first):
    return f'{first}/{(first + 1) % 100:02d}'


class Agreement(Tracked, models.Model):
    """An Erasmus+ agreement with a partner (Inter-Institutional Agreement): for which Studienjahre, Fachgebiet and levels,
    how many students go each way for how many months a year, and who has signed it."""

    capabilities = ('manage_mobility',)

    partner = models.ForeignKey(Partner, models.CASCADE, related_name='agreements', verbose_name='Partnerhochschule')
    first_year = models.PositiveSmallIntegerField('Ab Studienjahr', help_text='Das Jahr, in dem es beginnt: 2021 für 2021/22.')
    last_year = models.PositiveSmallIntegerField('Bis Studienjahr')
    isced = models.CharField('Fachgebiet (ISCED-F)', max_length=4, default='0215', help_text='0215: Musik und darstellende Künste.')
    subjects = models.ManyToManyField(Subject, blank=True, related_name='+', verbose_name='Hauptfächer', help_text='Leer: alle.')
    levels = models.CharField('Stufen (EQR)', max_length=20, default='6, 7', help_text='6 Bachelor, 7 Master.')
    places_out = models.PositiveSmallIntegerField('Plätze für Studierende von hier', default=2)
    months_out = models.PositiveSmallIntegerField('Monate je Platz', default=5)
    places_in = models.PositiveSmallIntegerField('Plätze für Studierende von dort', default=2)
    months_in = models.PositiveSmallIntegerField('Monate je Platz, von dort', default=5)
    teaching = models.PositiveSmallIntegerField('Lehraufenthalte je Richtung', default=0)
    language = models.CharField('Empfohlene Sprache', max_length=40, blank=True, help_text='Mit Niveau, z. B. „Englisch B2“.')
    signed = models.DateField('Von hier unterzeichnet am', null=True, blank=True)
    partner_signed = models.DateField('Von der Partnerhochschule unterzeichnet am', null=True, blank=True)
    note = models.TextField('Notiz', blank=True)

    class Meta:
        ordering = ['partner__name', '-last_year']
        verbose_name = 'Abkommen'
        verbose_name_plural = 'Abkommen'

    def __str__(self):
        return f'{self.partner}, {self.years}'

    def clean(self):
        if self.first_year and self.last_year and self.last_year < self.first_year:
            raise ValidationError({'last_year': 'Das letzte Studienjahr liegt vor dem ersten.'})

    @property
    def years(self):
        return f'{year(self.first_year)} bis {year(self.last_year)}'

    @property
    def approved(self):
        return bool(self.signed and self.partner_signed)


class Mobility(Tracked, models.Model):
    """A student's stay at a partner Hochschule, with its Learning Agreement: what they take there (A) and what it counts
    for here (B), signed by them, by the Hochschule here and by the partner. A line changed after they have signed asks
    for every signature anew."""

    owner = 'study.person_id'
    capabilities = ('manage_mobility',)

    class Kind(models.TextChoices):
        STUDY = 'studium', 'Studium'
        TRAINEESHIP = 'praktikum', 'Praktikum'

    class Status(models.TextChoices):
        NOMINATED = 'nominiert', 'nominiert'
        ACCEPTED = 'angenommen', 'von der Partnerhochschule angenommen'
        DONE = 'zurueck', 'zurück'
        CANCELLED = 'abgesagt', 'abgesagt'

    study = models.ForeignKey(Study, models.CASCADE, related_name='mobilities', verbose_name='Studium')
    partner = models.ForeignKey(Partner, models.PROTECT, related_name='mobilities', verbose_name='Partnerhochschule')
    agreement = models.ForeignKey(Agreement, models.SET_NULL, null=True, blank=True, related_name='mobilities', verbose_name='Abkommen')
    kind = models.CharField('Art', max_length=10, choices=Kind, default=Kind.STUDY)
    status = models.CharField('Stand', max_length=10, choices=Status, default=Status.NOMINATED)
    start = models.DateField('Von')
    end = models.DateField('Bis')
    signed_student = models.DateTimeField('Von der oder dem Studierenden unterschrieben', null=True, blank=True)
    signed_here = models.DateTimeField('Von der Hochschule genehmigt', null=True, blank=True)
    signed_here_by = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Genehmigt von')
    signed_partner = models.DateField('Von der Partnerhochschule genehmigt', null=True, blank=True)
    changes = models.PositiveSmallIntegerField('Änderungen nach der Genehmigung', default=0)

    class Meta:
        ordering = ['-start']
        verbose_name = 'Auslandsaufenthalt'
        verbose_name_plural = 'Auslandsaufenthalte'

    def __str__(self):
        return f'{self.study.person}, {self.partner}'

    def clean(self):
        if self.start and self.end and self.end <= self.start:
            raise ValidationError({'end': 'Das Ende liegt nicht nach dem Beginn.'})

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse('mobility:mobility', args=[self.pk])

    @property
    def abroad(self):
        return self.status == self.Status.ACCEPTED and self.start <= timezone.localdate() <= self.end

    @property
    def signed(self):
        return bool(self.signed_student and self.signed_here and self.signed_partner)

    @property
    def open(self):
        """Whether its Learning Agreement can still change."""
        return self.status in (self.Status.NOMINATED, self.Status.ACCEPTED)

    def changed(self):
        """A line was added or taken out: every signature anew, counted as a change once all three had signed."""
        self.changes += self.signed
        self.signed_student = self.signed_here = self.signed_here_by = self.signed_partner = None
        self.save(update_fields=['changes', 'signed_student', 'signed_here', 'signed_here_by', 'signed_partner'])


class Component(Tracked, models.Model):
    """A line of the Learning Agreement: a course at the partner (A), or a module here it counts for (B)."""

    owner = 'mobility.study.person_id'
    capabilities = ('manage_mobility',)

    class Table(models.TextChoices):
        ABROAD = 'A', 'An der Partnerhochschule'
        HOME = 'B', 'Anerkennung hier'

    mobility = models.ForeignKey(Mobility, models.CASCADE, related_name='components', verbose_name='Auslandsaufenthalt')
    table = models.CharField('Tabelle', max_length=1, choices=Table)
    title = models.CharField('Veranstaltung', max_length=200, blank=True)
    code = models.CharField('Kürzel dort', max_length=40, blank=True)
    credits = models.DecimalField('ECTS', max_digits=4, decimal_places=1)
    module = models.ForeignKey(Module, models.PROTECT, null=True, blank=True, related_name='+', verbose_name='Modul')

    class Meta:
        ordering = ['table', 'pk']
        verbose_name = 'Zeile des Learning Agreement'
        verbose_name_plural = 'Zeilen des Learning Agreement'

    def __str__(self):
        return self.title or str(self.module)
