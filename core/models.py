from datetime import timedelta

from django.apps import apps
from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import RegexValidator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.text import slugify

from . import destatis
from .access import CAPABILITIES, CODENAMES


class Tracked:
    """Mixin for models whose every change is kept as a Change (see core.history)."""

    owner = ''  # path to the id of the person the record is about, e.g. 'study.person_id'; their history goes with them
    capabilities = ()  # who sees its history and may restore it
    keep = None  # how long its changes are kept (timedelta); None: as long as the person they are about
    scope = None  # (paths to its Institute, paths to its Studiengänge), for offices limited to some; None: only offices for the whole Hochschule

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

    @classmethod
    def open_to(cls, person, pk):
        """Whether the record is within the person's reach for its history: by any of their capabilities that sees a history,
        as its history spans what several offices look after (a person's); a deleted record only for offices of the whole
        Hochschule."""
        record = cls.objects.filter(pk=pk).first()
        seeing = {c for model in apps.get_models() if issubclass(model, Tracked) for c in model.capabilities}
        return any(person.may(c, record) if record else person.everywhere(c) for c in person.reach if c in seeing)


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
    university = models.CharField(
        'Hochschulnummer', max_length=4, choices=destatis.german, blank=True, help_text='Für die amtliche Statistik, aus dem Schlüsselverzeichnis.',
    )
    region = models.CharField('Kreis', max_length=5, choices=destatis.districts, blank=True, help_text='Sitz der Hochschule, für die amtliche Statistik.')
    report_id = models.CharField('BerichtseinheitID', max_length=8, blank=True, help_text='Vom Statistischen Landesamt vergeben, für die Lieferung.')
    accept_days = models.PositiveSmallIntegerField('Frist zur Annahme eines Studienplatzes (Tage)', default=14)
    late_fee = models.DecimalField('Säumnisgebühr', max_digits=6, decimal_places=2, default=15, help_text='Zum Semesterbeitrag, wer sich in der Nachfrist zurückmeldet.')
    payee = models.CharField('Zahlungsempfänger', max_length=120, blank=True, help_text='Für den Semesterbeitrag; leer: der Name der Hochschule.')
    iban = models.CharField('IBAN', max_length=34, blank=True, help_text='Studierende überweisen dorthin, mit ihrer Matrikelnummer im Verwendungszweck.')
    bic = models.CharField('BIC', max_length=11, blank=True)
    withdraw_weeks = models.PositiveSmallIntegerField(
        'Abmeldung von Prüfungen (Wochen)', default=3, help_text='So viele Wochen vor Beginn des Prüfungszeitraums endet die Abmeldung.',
    )
    retention_months = models.PositiveSmallIntegerField(
        'Bewerbungen ohne Studium aufbewahren (Monate)', default=6,
        help_text='Danach werden ihre Unterlagen gelöscht und wer nur beworben war, wird anonymisiert; die Statistik zählt weiter. 0: nie.',
    )
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
    scope = (['institute', 'studies__subject__institute'], ['studies__program'])

    class Gender(models.TextChoices):  # as the official student statistics count it
        FEMALE = 'w', 'weiblich'
        MALE = 'm', 'männlich'
        DIVERSE = 'd', 'divers'
        UNSTATED = 'x', 'ohne Angabe'

    class Insurance(models.TextChoices):
        STATUTORY = 'gesetzlich', 'gesetzlich versichert'
        EXEMPT = 'befreit', 'von der Versicherungspflicht befreit'

    title = models.CharField('Titel', max_length=40, blank=True)
    given_names = models.CharField('Alle Vornamen', max_length=120, blank=True, help_text='Wie im Ausweis, für Urkunden; leer heißt nur der Rufname.')
    birth_name = models.CharField('Geburtsname', max_length=80, blank=True)
    gender = models.CharField('Geschlecht', max_length=1, choices=Gender, blank=True)
    birth_date = models.DateField('Geburtsdatum', null=True, blank=True)
    birth_place = models.CharField('Geburtsort', max_length=80, blank=True)
    nationality = models.CharField('Staatsangehörigkeit', max_length=60, choices=destatis.countries, blank=True)
    second_nationality = models.CharField('Weitere Staatsangehörigkeit', max_length=60, choices=destatis.countries, blank=True)
    address = models.TextField('Anschrift', blank=True)
    phone = models.CharField(
        'Telefon', max_length=40, blank=True, validators=[RegexValidator(r'^\+?[\d ()/.-]{5,}$', 'Bitte nur Ziffern, Leerzeichen und + ( ) / - eingeben.')],
    )
    home =models.CharField('Heimatwohnsitz', max_length=5, choices=destatis.places, blank=True, help_text='Kreis, im Ausland der Staat.')
    residence = models.CharField('Semesterwohnsitz', max_length=5, choices=destatis.places, blank=True, help_text='Während der Unterrichtszeit; leer: wie der Heimatwohnsitz.')
    entrance = models.CharField(
        'Hochschulzugangsberechtigung', max_length=10, choices=destatis.ENTRANCE, blank=True,
        help_text='Die erste, mit der ein Studium in Deutschland möglich war.',
    )
    entrance_date = models.DateField('Erworben am', null=True, blank=True)
    entrance_region = models.CharField('Erworben in', max_length=5, choices=destatis.places, blank=True, help_text='Kreis, im Ausland der Staat.')
    first_university = models.CharField(
        'Hochschule der Ersteinschreibung', max_length=7, choices=destatis.universities, blank=True, help_text='Leer: diese Hochschule.',
    )
    first_term = models.CharField(
        'Semester der Ersteinschreibung', max_length=12, blank=True, help_text='Z. B. WiSe 2019/20 oder SoSe 2020; leer: das erste Semester hier.',
        validators=[RegexValidator(r'^(WiSe \d{4}/\d{2}|SoSe \d{4})$', 'Z. B. WiSe 2019/20 oder SoSe 2020.')],
    )
    external_terms = models.PositiveSmallIntegerField('Semester an anderen deutschen Hochschulen', default=0, help_text='Zählen zu den Hochschulsemestern.')
    degree_university = models.CharField(
        'Hochschule des letzten Abschlusses', max_length=7, choices=destatis.universities, blank=True, help_text='Nur für einen Abschluss anderswo.',
    )
    degree_exam = models.CharField('Letzter Abschluss', max_length=2, choices=destatis.degrees, blank=True)
    degree_field = models.CharField('Im Studienfach', max_length=3, choices=destatis.fields, blank=True)
    degree_date = models.DateField('Abgeschlossen am', null=True, blank=True)
    degree_grade = models.CharField('Gesamtnote', max_length=1, choices=destatis.GRADES, blank=True)
    vocational = models.BooleanField('Berufsausbildung mit Abschluss vor dem Studium', default=False)
    internship = models.BooleanField('Praktikum oder Volontariat für dieses Studium', default=False)
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

    class Employment(models.TextChoices):
        PROFESSUR = 'professur', 'Professur'
        MITARBEIT = 'mitarbeit', 'Künstlerische oder wissenschaftliche Mitarbeit'
        LEHRAUFTRAG = 'lehrauftrag', 'Lehrauftrag'

    employment = models.CharField('Beschäftigung', max_length=12, choices=Employment, blank=True, help_text='Für Lehrende.')
    obligation = models.DecimalField(
        'Lehrverpflichtung (LVS)', max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Was die Lehrverpflichtungsverordnung verlangt, nach Ermäßigungen; bei einem Lehrauftrag die vereinbarten SWS.',
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
        """What blocks enrolment and Rückmeldung whatever has been paid: a Sperre, no confirmation from the health insurer,
        an instrument kept past its day. Reads `loans.all()`; prefetch them, with their instruments, for many people."""
        reasons = [f'Sperre: {self.hold}'] if self.hold else []
        if not self.insurance_confirmed:
            reasons.append('Versicherungsmeldung der Krankenkasse fehlt')
        return reasons + [f'Leihinstrument zurückgeben: {loan.instrument.name}' for loan in self.loans.all() if loan.overdue]

    @classmethod
    def history(cls, pk):
        """Everything about the person: their own record, studies, terms, documents, results, bookings."""
        return models.Q(person=pk)

    @classmethod
    def teachers(cls):
        """Who can teach or examine: whoever belongs to an Institut or already teaches."""
        return cls.objects.filter(models.Q(institute__isnull=False) | models.Q(teaching__isnull=False)).distinct()

    @cached_property
    def reach(self):
        """Where each capability of the person's offices holds: None for the whole Hochschule, else the ids of the
        Institute and the Studiengänge it is limited to. Superusers hold all, everywhere."""
        if self.is_superuser:
            return dict.fromkeys(CODENAMES)
        reach = {}
        for office in Office.objects.filter(user=self).prefetch_related('permissions', 'institutes', 'programs'):
            institutes, programs = {i.pk for i in office.institutes.all()}, {p.pk for p in office.programs.all()}
            for capability in (p.codename for p in office.permissions.all() if p.codename in CODENAMES):
                if not (institutes or programs) or reach.get(capability, ()) is None:
                    reach[capability] = None
                else:
                    held = reach.setdefault(capability, (set(), set()))
                    held[0].update(institutes)
                    held[1].update(programs)
        return reach

    def everywhere(self, capability):
        return capability in self.reach and self.reach[capability] is None

    def within(self, queryset, *capabilities):
        """The records of `queryset` the person may act on with any of these capabilities: all, or those of the Institute
        and Studiengänge their offices are limited to, reached by the model's `scope`."""
        reaches = [self.reach[c] for c in capabilities if c in self.reach]
        if None in reaches:
            return queryset
        model = queryset.model
        if not reaches or model.scope is None:
            return queryset.none()
        ids = [{pk for reach in reaches for pk in reach[n]} for n in (0, 1)]
        found = models.Q(pk__in=[])
        for paths, pks in zip(model.scope, ids):
            for path in paths:
                found |= models.Q(**{f'{path}__in': pks})
        return queryset.filter(pk__in=model.objects.filter(found).values('pk'))

    def may(self, capability, record):
        """Whether the person may act on this record with the capability."""
        return self.everywhere(capability) or capability in self.reach and self.within(type(record).objects.filter(pk=record.pk), capability).exists()

    def ours(self, capability):
        """Ids of the Institute a person's lists open on for this capability: those their office is limited to, else
        their own; none for the whole Hochschule."""
        reach = self.reach.get(capability, ())
        if reach is None:
            return set()
        return set(reach[0]) if reach and reach[0] else {self.institute_id} - {None}

    def reached(self, model, capability):
        """Ids of the records of `model` the person may act on with the capability, kept for the request: for long lists of rooms."""
        found = self.__dict__.setdefault('_reached', {})
        if (model, capability) not in found:
            found[model, capability] = set(self.within(model.objects.all(), capability).values_list('pk', flat=True))
        return found[model, capability]

    @cached_property
    def roles(self):
        """The capabilities of the person's offices, plus 'student', 'applicant', 'alumni' (studied here, graduated or not),
        'teacher' and 'juror' (on the jury of an Eignungsprüfung) as derived from studies and teaching."""
        roles = set(self.reach)
        studies = list(self.studies.annotate(registered=models.Count('terms')).values_list('status', 'registered'))
        statuses = {status for status, _ in studies}
        if 'enrolled' in statuses:
            roles.add('student')
        if statuses & {'applied', 'admitted', 'waitlisted'}:
            roles.add('applicant')
        if any(status in ('graduated', 'left') and registered for status, registered in studies):
            roles.add('alumni')
        kinds = set(self.teaching.values_list('kind', flat=True).distinct())
        if kinds:
            roles.add('teacher')
        if 'audition' in kinds:
            roles.add('juror')
        return roles


class Office(Tracked, Group):
    """An office of the Hochschule, named as it likes: the people in it and what they may do (core.access.CAPABILITIES),
    for the whole Hochschule or only for some Institute and Studiengänge."""

    capabilities = ('manage_accounts',)

    institutes = models.ManyToManyField(Institute, blank=True, related_name='offices', verbose_name='Nur für die Institute')
    programs = models.ManyToManyField('curriculum.Program', blank=True, related_name='offices', verbose_name='Nur für die Studiengänge')

    class Meta:
        ordering = ['name']
        verbose_name = 'Amt'
        verbose_name_plural = 'Ämter'
        default_permissions = ()
        permissions = [capability for _, group in CAPABILITIES for capability in group]

    @property
    def remit(self):
        """Whom the office acts for, as its list shows it."""
        return ', '.join(map(str, [*self.institutes.all(), *self.programs.all()])) or 'ganze Hochschule'

    @classmethod
    def holding(cls, capability, program=None):
        """The offices that hold a capability, for sentences like 'Zentral vergeben: …'; with a programme, those for it."""
        offices = cls.objects.filter(permissions__codename=capability)
        if program:
            offices = offices.filter(models.Q(programs=None) | models.Q(programs=program), institutes=None)
        *rest, last = offices.values_list('name', flat=True) or ['']
        return f'{", ".join(rest)} oder {last}' if rest else last


class Term(Tracked, models.Model):
    capabilities = ('manage_terms',)

    name = models.CharField('Name', max_length=40, unique=True)
    start = models.DateField('Beginn')
    end = models.DateField('Ende')
    lectures_start = models.DateField('Beginn des Unterrichts')
    lectures_end = models.DateField('Ende des Unterrichts')
    reregistration_start = models.DateField('Rückmeldung ab')
    deadline = models.DateField('Rückmeldefrist')
    late_deadline = models.DateField('Nachfrist bis', null=True, blank=True, help_text='Rückmeldung mit Säumnisgebühr; leer: keine Nachfrist.')
    fee = models.DecimalField('Semesterbeitrag', max_digits=7, decimal_places=2)
    exam_signup_start = models.DateField('Prüfungsanmeldung ab', null=True, blank=True, help_text='Neue Prüfungen des Semesters übernehmen den Zeitraum.')
    exam_signup_end = models.DateField('Prüfungsanmeldung bis', null=True, blank=True)
    exams_start = models.DateField('Beginn des Prüfungszeitraums', null=True, blank=True, help_text='Bis einige Wochen davor melden sich Studierende von Prüfungen ab.')
    exams_end = models.DateField('Ende des Prüfungszeitraums', null=True, blank=True)

    class Meta:
        ordering = ['start']
        verbose_name = 'Semester'
        verbose_name_plural = 'Semester'

    def __str__(self):
        return self.name

    @property
    def reregistration_open(self):
        return self.reregistration_start <= timezone.localdate()

    @property
    def closes(self):
        """The last day of the Rückmeldung: the end of the Nachfrist, else the Rückmeldefrist."""
        return self.late_deadline or self.deadline

    def due(self, day, late_fee):
        """The Semesterbeitrag paid on `day`: with the Säumnisgebühr in the Nachfrist."""
        return self.fee + (late_fee if self.late_deadline and day > self.deadline else 0)

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
            raise ValidationError({'lectures_end': 'Der Unterricht endet nach seinem Beginn.'})
        if self.reregistration_start and self.deadline and self.reregistration_start > self.deadline:
            raise ValidationError({'deadline': 'Die Rückmeldefrist endet nach dem Beginn der Rückmeldung.'})
        if self.late_deadline and self.deadline and self.late_deadline <= self.deadline:
            raise ValidationError({'late_deadline': 'Die Nachfrist endet nach der Rückmeldefrist.'})
        if self.exam_signup_start and self.exam_signup_end and self.exam_signup_start > self.exam_signup_end:
            raise ValidationError({'exam_signup_end': 'Die Anmeldung endet nach ihrem Beginn.'})
        if self.exams_start and self.exams_end and self.exams_start > self.exams_end:
            raise ValidationError({'exams_end': 'Der Prüfungszeitraum endet nach seinem Beginn.'})

    @classmethod
    def pick(cls, pk):
        """The term with this pk (as given in a query string), else the current one."""
        return (pk and pk.isdigit() and cls.objects.filter(pk=pk).first()) or cls.current()


class Holiday(Tracked, models.Model):
    """Unterrichtsfreie Tage: public holidays, Christmas, the Dies Academicus. Weekly course dates leave them out."""

    capabilities = ('manage_terms',)

    name = models.CharField('Bezeichnung', max_length=80)
    first = models.DateField('Von')
    last = models.DateField('Bis', null=True, blank=True, help_text='Leer: nur ein Tag.')

    class Meta:
        ordering = ['first']
        verbose_name = 'Unterrichtsfreier Tag'
        verbose_name_plural = 'Unterrichtsfreie Tage'

    def __str__(self):
        return self.name

    def clean(self):
        if self.last and self.first and self.last < self.first:
            raise ValidationError({'last': 'Endet nicht vor dem ersten Tag.'})

    @classmethod
    def days(cls, start, end):
        """The lecture-free days from `start` to `end`."""
        found = set()
        for holiday in cls.objects.filter(first__lte=end).filter(models.Q(last__gte=start) | models.Q(last=None, first__gte=start)):
            day = max(holiday.first, start)
            while day <= min(holiday.last or holiday.first, end):
                found.add(day)
                day += timedelta(days=1)
        return found


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
        if person.email:  # the Postfach has it either way
            cls.mail(person, subject, body)
        return message

    @staticmethod
    def mail(person, subject, body):
        """By e-mail only, once the transaction commits, so no mail goes out for what was rolled back."""
        sender = Institution.get().email or None
        transaction.on_commit(lambda: send_mail(subject, body, sender, [person.email], fail_silently=True))


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
