from django.contrib.auth.models import AbstractUser, Group, Permission
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify

from .access import CAPABILITIES, CODENAMES


class Tracked:
    """Mixin for models whose every change is kept as a Change (see core.history)."""

    owner = ''  # path to the id of the person the record is about, e.g. 'study.person_id'; their history goes with them
    capabilities = ()  # who sees its history and may restore it
    keep = None  # how long its changes are kept (timedelta); None: as long as the person they are about

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded = dict(zip(field_names, values))  # what a save is compared with
        return instance

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using, fields, from_queryset)
        names = {f.attname for f in self._meta.concrete_fields if fields is None or f.name in fields or f.attname in fields}
        self._loaded = getattr(self, '_loaded', {}) | {name: value for name, value in self.__dict__.items() if name in names}

    @classmethod
    def history(cls, pk):
        """The changes shown as a record's history: its own."""
        return models.Q(table=cls._meta.label_lower, row=pk)

    @classmethod
    def visible_to(cls, roles):
        return bool(roles & set(cls.capabilities))


class Keyed(models.Model):
    """A list entry that code, links and other records refer to by a short key, made once from its name."""

    key = models.SlugField('Schlüssel', max_length=40, unique=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.key:
            base = slugify(self.name)[:36] or 'eintrag'
            self.key, n = base, 1
            while type(self).objects.filter(key=self.key).exists():
                n += 1
                self.key = f'{base}-{n}'
        super().save(*args, **kwargs)


class Institution(Tracked, models.Model):
    """The Hochschule itself: its name and address for letters, and the rules it sets. One record."""

    capabilities = ('manage_institution',)

    name = models.CharField('Name', max_length=120, default='Hochschule')
    short_name = models.CharField('Kurzname', max_length=40, blank=True, help_text='Im Fenstertitel und in kurzen Sätzen.')
    address = models.TextField('Anschrift', blank=True)
    place = models.CharField('Ort', max_length=60, blank=True, help_text='Für Datumszeilen in Briefen.')
    email = models.EmailField('E-Mail', blank=True)
    website = models.URLField('Website', blank=True)
    imprint = models.URLField('Impressum', blank=True)
    privacy = models.URLField('Datenschutz', blank=True)
    accessibility = models.URLField('Barrierefreiheit', blank=True)
    matriculation_start = models.PositiveIntegerField('Erste Matrikelnummer', default=100001, help_text='Danach fortlaufend.')
    accept_days = models.PositiveSmallIntegerField('Frist zur Annahme eines Studienplatzes (Tage)', default=14)
    signup = models.BooleanField('Neue Bewerbungskonten', default=True, help_text='Ohne Haken legt niemand ein neues Bewerbungskonto an.')
    insurers = models.TextField('Krankenkassen', blank=True, help_text='Vorschläge beim Annehmen des Studienplatzes, eine je Zeile.')
    student_daily_hours = models.PositiveSmallIntegerField('Übezeit am Tag (Std.)', default=4, help_text='So viele Stunden buchen Studierende am Tag.')
    student_ahead_hours = models.PositiveSmallIntegerField('Vorlauf für Studierende (Std.)', default=24, help_text='So weit im Voraus buchen Studierende.')
    last_minute_hours = models.PositiveSmallIntegerField('Kurzfristig ohne Kontingent (Std.)', default=3, help_text='Was so bald beginnt, zählt nicht zum Tageskontingent.')
    teacher_ahead_days = models.PositiveSmallIntegerField('Vorlauf für Lehrende (Tage)', default=183)

    class Meta:
        verbose_name = 'Hochschule'
        verbose_name_plural = 'Hochschule'

    def __str__(self):
        return self.name

    @classmethod
    def get(cls):
        """The record, or its defaults until someone fills it in."""
        return cls.objects.first() or cls()


class Institute(Tracked, models.Model):
    """An Institut of the Hochschule: its teachers, its Hauptfächer and the rooms both may book."""

    capabilities = ('manage_institution',)

    name = models.CharField('Name', max_length=80, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Institut'
        verbose_name_plural = 'Institute'

    def __str__(self):
        return self.name


class Person(Tracked, AbstractUser):
    """Everyone: applicants, students, teachers, staff.

    What they may do comes from their offices; everything else follows from what they do (see `roles`).
    """

    owner = 'pk'
    capabilities = ('manage_students', 'manage_accounts')

    class Gender(models.TextChoices):  # as the official student statistics count it
        FEMALE = 'w', 'weiblich'
        MALE = 'm', 'männlich'
        DIVERSE = 'd', 'divers'
        UNSTATED = 'x', 'ohne Angabe'

    class Entrance(models.TextChoices):
        GENERAL = 'ahr', 'Allgemeine Hochschulreife'
        SUBJECT = 'fgh', 'Fachgebundene Hochschulreife'
        FOREIGN = 'ausland', 'Ausländische Hochschulzugangsberechtigung'
        TALENT = 'begabung', 'Besondere künstlerische Begabung, ohne Hochschulreife'
        VOCATIONAL = 'beruf', 'Beruflich qualifiziert'

    class Insurance(models.TextChoices):
        STATUTORY = 'gesetzlich', 'gesetzlich versichert'
        EXEMPT = 'befreit', 'von der Versicherungspflicht befreit'

    title = models.CharField('Titel', max_length=40, blank=True)
    given_names = models.CharField('Alle Vornamen', max_length=120, blank=True, help_text='Wie im Ausweis, für Urkunden; leer heißt nur der Rufname.')
    birth_name = models.CharField('Geburtsname', max_length=80, blank=True)
    gender = models.CharField('Geschlecht', max_length=1, choices=Gender, blank=True)
    birth_date = models.DateField('Geburtsdatum', null=True, blank=True)
    birth_place = models.CharField('Geburtsort', max_length=80, blank=True)
    nationality = models.CharField('Staatsangehörigkeit', max_length=60, blank=True)
    second_nationality = models.CharField('Weitere Staatsangehörigkeit', max_length=60, blank=True)
    address = models.TextField('Anschrift', blank=True)
    entrance = models.CharField('Hochschulzugangsberechtigung', max_length=10, choices=Entrance, blank=True)
    entrance_date = models.DateField('Erworben am', null=True, blank=True)
    entrance_place = models.CharField('Erworben in', max_length=80, blank=True, help_text='Kreis und Land, im Ausland der Staat.')
    external_terms = models.PositiveSmallIntegerField('Semester an anderen Hochschulen', default=0, help_text='Zählen zu den Hochschulsemestern.')
    insurer = models.CharField('Krankenkasse', max_length=80, blank=True)
    insurance = models.CharField('Versicherungsstatus', max_length=10, choices=Insurance, blank=True)
    insurance_confirmed = models.DateField(
        'Versicherungsmeldung erhalten am', null=True, blank=True,
        help_text='Elektronische Meldung der Krankenkasse im Studierendenmeldeverfahren; Voraussetzung für Immatrikulation und Rückmeldung.',
    )
    matriculation = models.CharField('Matrikelnummer', max_length=12, unique=True, null=True, blank=True)
    hold = models.CharField('Sperre', max_length=200, blank=True, help_text='Grund der Sperre; leer heißt keine Sperre.')
    institute = models.ForeignKey(
        Institute, models.SET_NULL, null=True, blank=True, related_name='staff', verbose_name='Institut',
        help_text='Für Lehrende. Studierende gehören zum Institut ihres Hauptfachs.',
    )

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Person'
        verbose_name_plural = 'Personen'

    def __str__(self):
        return ' '.join(filter(None, [self.title, self.first_name, self.last_name])) or self.username

    @property
    def name(self):
        return f'{self.last_name}, {self.first_name}'

    @property
    def legal_name(self):
        """The name on certificates: all given names, as in the passport."""
        return f'{self.given_names or self.first_name} {self.last_name}'

    def institutes(self):
        """Their own Institut as teachers, and those of the Hauptfächer they are enrolled in."""
        return Institute.objects.filter(
            models.Q(staff=self) | models.Q(subjects__studies__person=self, subjects__studies__status='enrolled'),
        ).distinct()

    def missing(self):
        """What blocks enrolment and Rückmeldung whatever has been paid: a Sperre, no confirmation from the health insurer."""
        reasons = [f'Sperre: {self.hold}'] if self.hold else []
        if not self.insurance_confirmed:
            reasons.append('Versicherungsmeldung der Krankenkasse fehlt')
        return reasons

    @classmethod
    def history(cls, pk):
        """Everything about the person: their own record, studies, terms, documents, results, bookings."""
        return models.Q(person=pk)

    @classmethod
    def teachers(cls):
        """Who can teach or examine: whoever belongs to an Institut or already teaches."""
        return cls.objects.filter(models.Q(institute__isnull=False) | models.Q(teaching__isnull=False)).distinct()

    @cached_property
    def roles(self):
        """The capabilities of the person's offices (all for superusers), plus 'student', 'applicant' and 'teacher' as
        derived from studies and teaching."""
        if self.is_superuser:
            roles = set(CODENAMES)
        else:
            roles = set(Permission.objects.filter(group__user=self, codename__in=CODENAMES).values_list('codename', flat=True))
        statuses = set(self.studies.values_list('status', flat=True))
        if 'enrolled' in statuses:
            roles.add('student')
        if statuses & {'applied', 'admitted', 'waitlisted'}:
            roles.add('applicant')
        if self.teaching.exists():
            roles.add('teacher')
        return roles


class Office(Tracked, Group):
    """An office of the Hochschule, named as it likes: the people in it and what they may do (core.access.CAPABILITIES)."""

    capabilities = ('manage_accounts',)

    class Meta:
        proxy = True
        ordering = ['name']
        verbose_name = 'Amt'
        verbose_name_plural = 'Ämter'
        default_permissions = ()
        permissions = [capability for _, group in CAPABILITIES for capability in group]

    @classmethod
    def holding(cls, capability):
        """The offices that hold a capability, for sentences like 'Zentral vergeben: …'."""
        return ' oder '.join(cls.objects.filter(permissions__codename=capability).values_list('name', flat=True))


class Term(Tracked, models.Model):
    capabilities = ('manage_terms',)

    name = models.CharField('Name', max_length=40, unique=True)
    start = models.DateField('Beginn')
    end = models.DateField('Ende')
    lectures_start = models.DateField('Vorlesungsbeginn')
    lectures_end = models.DateField('Vorlesungsende')
    reregistration_start = models.DateField('Rückmeldung ab')
    deadline = models.DateField('Rückmeldefrist')
    fee = models.DecimalField('Semesterbeitrag', max_digits=7, decimal_places=2)

    class Meta:
        ordering = ['start']
        verbose_name = 'Semester'
        verbose_name_plural = 'Semester'

    def __str__(self):
        return self.name

    @property
    def reregistration_open(self):
        return self.reregistration_start <= timezone.localdate()

    @classmethod
    def current(cls):
        """The term under way; between terms the last one, before the first term the first."""
        return cls.objects.filter(start__lte=timezone.localdate()).order_by('-start').first() or cls.objects.first()

    @classmethod
    def upcoming(cls):
        return cls.objects.filter(start__gt=timezone.localdate()).first()

    def clean(self):
        if self.start and self.end and self.start >= self.end:
            raise ValidationError({'end': 'Das Semester endet nach seinem Beginn.'})
        if self.lectures_start and self.lectures_end and self.lectures_start >= self.lectures_end:
            raise ValidationError({'lectures_end': 'Die Vorlesungszeit endet nach ihrem Beginn.'})
        if self.reregistration_start and self.deadline and self.reregistration_start > self.deadline:
            raise ValidationError({'deadline': 'Die Rückmeldefrist endet nach dem Beginn der Rückmeldung.'})

    @classmethod
    def pick(cls, pk):
        """The term with this pk (as given in a query string), else the current one."""
        return (pk and pk.isdigit() and cls.objects.filter(pk=pk).first()) or cls.current()


class Text(Tracked, models.Model):
    """A letter, message or text block as the Hochschule words it (core.texts); without one, the default applies."""

    capabilities = ('manage_institution',)

    key = models.CharField('Text', max_length=40, unique=True)
    subject = models.CharField('Betreff', max_length=200, blank=True)
    body = models.TextField('Text')

    class Meta:
        ordering = ['key']
        verbose_name = 'Text'
        verbose_name_plural = 'Texte'

    def __str__(self):
        from .texts import TEXTS

        return TEXTS[self.key].title if self.key in TEXTS else self.key

    def clean(self):
        from .texts import TEXTS, check

        for field in ('subject', 'body'):
            try:
                check(getattr(self, field), TEXTS[self.key].allowed)
            except ValidationError as error:
                raise ValidationError({field: error.messages})


class Message(models.Model):
    """The portal Postfach. Everything sent to a person lands here and goes out by e-mail."""

    person = models.ForeignKey(Person, models.CASCADE, related_name='messages', verbose_name='Empfänger:in')
    subject = models.CharField('Betreff', max_length=200)
    body = models.TextField('Text')
    link = models.CharField('Link', max_length=200, blank=True)
    created = models.DateTimeField('Gesendet', default=timezone.now)
    read = models.DateTimeField('Gelesen', null=True, blank=True)
    sender = models.CharField('Absender', max_length=200, blank=True, help_text='Bei Bescheiden: wer sie erlassen hat, wie es im Brief steht.')

    class Meta:
        ordering = ['-created']
        verbose_name = 'Nachricht'
        verbose_name_plural = 'Nachrichten'

    def __str__(self):
        return self.subject

    def get_absolute_url(self):
        return reverse('message', args=[self.pk])

    @classmethod
    def send(cls, person, subject, body, link='', sender=''):
        message = cls.objects.create(person=person, subject=subject, body=body, link=link, sender=sender)
        if person.email:  # once saved, so no mail goes out for what was rolled back; the Postfach has it either way
            sender = Institution.get().email or None
            transaction.on_commit(lambda: send_mail(subject, body, sender, [person.email], fail_silently=True))
        return message


class Change(models.Model):
    """One change to a tracked record: the fields that changed as {name: [before, after]}, on deletion all of them so the
    record can be restored. Never edited; restoring is a change of its own."""

    class Action(models.TextChoices):
        CREATED = 'created', 'angelegt'
        CHANGED = 'changed', 'geändert'
        DELETED = 'deleted', 'gelöscht'
        RESTORED = 'restored', 'zurückgesetzt'

    at = models.DateTimeField('Zeitpunkt', default=timezone.now)
    by = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Von')
    person = models.ForeignKey(Person, models.CASCADE, null=True, blank=True, related_name='+', verbose_name='Betrifft')
    table = models.CharField('Tabelle', max_length=40)
    row = models.PositiveIntegerField('Datensatz')
    action = models.CharField('Art', max_length=8, choices=Action)
    fields = models.JSONField('Felder', encoder=DjangoJSONEncoder, default=dict)

    class Meta:
        ordering = ['-at', '-pk']
        verbose_name = 'Änderung'
        verbose_name_plural = 'Änderungen'
        indexes = [models.Index(fields=['table', 'row'])]

    def __str__(self):
        return f'{self.table} {self.row} {self.get_action_display()}'
