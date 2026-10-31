from contextvars import ContextVar
from datetime import timedelta

from django.apps import apps
from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import FileExtensionValidator, MaxValueValidator, RegexValidator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models.functions import Cast, Concat
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.html import format_html, format_html_join
from django.utils.text import slugify

from . import destatis
from .access import CAPABILITIES, CODENAMES
from .keys import COUNTRIES, DEGREES, DISTRICTS, FIELDS, GERMAN, PLACES, TOWNS, UNIVERSITIES, Edition, Key, KeyField  # noqa: F401, the key lists' models

looked_up = ContextVar('looked_up', default=None)  # what a GET request has looked up: core.middleware.lookups


def once(key, fetch):
    """`fetch()`, within a GET request once per `key`: what does not change while a page is shown, such as the current
    semester, asked for in many places. Elsewhere, as after a change, every time."""
    found = looked_up.get()
    if found is None:
        return fetch()
    if key not in found:
        found[key] = fetch()
    return found[key]


class Tracked:
    """Mixin for models whose every change is kept as a Change (see core.history)."""

    owner = ''  # path to the id of the person the record is about, e.g. 'study.person_id'; their history goes with them
    capabilities = ()  # who sees its history and may restore it
    keep = None  # how long its changes are kept (timedelta); None: as long as the person they are about
    scope = None  # (paths to its Institute, paths to its Studiengänge), for offices limited to some; None: only offices for the whole Hochschule
    settled = ()  # fields a Bescheid changes: only another Bescheid changes them back, not the Verlauf

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
    def keepers(cls, record):
        """The capabilities that look after a record, see its history and undo its changes: the model's own; models whose
        records belong to different offices by their kind narrow them (courses.Course). `record` may be None, or an
        unsaved copy of a deleted one."""
        return cls.capabilities

    @classmethod
    def undoers(cls, record):
        """The capabilities that undo a record's changes: those that look after it; results narrow them."""
        return cls.keepers(record)

    @classmethod
    def held(cls, person, record, deleted=False, undo=False):
        """Of the capabilities that look after `record` (with `undo`, that undo its changes), those the person holds for
        it; for a record no longer there, only those of offices for the whole Hochschule."""
        found = cls.undoers(record) if undo else cls.keepers(record)
        return {c for c in found if (person.everywhere(c) if deleted else c in person.reach and person.may(c, record))}

    @classmethod
    def seen_by(cls, person, pk):
        """The person's capabilities by which they see a record's history; empty: it is closed to them."""
        record = cls.objects.filter(pk=pk).first()
        return cls.held(person, record, deleted=record is None)


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


def contrast(colour):
    """How white text reads on `colour` (#rrggbb): the contrast ratio after WCAG 2.1."""
    def linear(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(int(colour[i:i + 2], 16)) for i in (1, 3, 5))
    return 1.05 / (0.2126 * r + 0.7152 * g + 0.0722 * b + 0.05)


class Institution(Tracked, models.Model):
    """The Hochschule itself: its name and address for letters, its look, and the rules it sets. One record."""

    capabilities = ('manage_institution',)

    name = models.CharField('Name', max_length=120, default='Hochschule')
    short_name = models.CharField('Kurzname', max_length=40, blank=True, help_text='Im Fenstertitel und in kurzen Sätzen.')
    wordmark = models.TextField(
        'Schriftzug', blank=True,
        help_text='Im Kopf jeder Seite und im Briefkopf, eine Zeile je Zeile; die weiteren in der Akzentfarbe. Leer: der Name.',
    )
    logo = models.FileField(
        'Logo', upload_to='hochschule/', blank=True, validators=[FileExtensionValidator(['svg', 'png'])],
        help_text='Im Briefkopf statt des Schriftzugs, als SVG oder PNG.',
    )
    accent = models.CharField(
        'Akzentfarbe', max_length=7, blank=True, validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Als #rrggbb, z. B. #008a00.')],
        help_text='Für Balken, Schaltflächen und Hervorhebungen; leer: Grün. Weiße Schrift darauf muss lesbar bleiben.',
    )
    radius = models.PositiveSmallIntegerField(
        'Eckenradius (Pixel)', default=4, validators=[MaxValueValidator(12)], help_text='Für Schaltflächen, Felder und kleine Kästen; 0: eckig.',
    )
    address = models.TextField('Anschrift', blank=True)
    place = models.CharField('Ort', max_length=60, blank=True, help_text='Für Datumszeilen in Briefen.')
    email = models.EmailField('E-Mail', blank=True)
    student_mail = models.CharField(
        'Domain der Studierendenadressen', max_length=80, blank=True,
        help_text='Wer immatrikuliert wird, erhält hier eine Adresse, z. B. „stud.hochschule.de“; leer: keine.',
    )
    website = models.URLField('Website', blank=True)
    imprint = models.URLField('Impressum', blank=True)
    privacy = models.URLField('Datenschutz', blank=True)
    accessibility = models.URLField('Barrierefreiheit der Website', blank=True, help_text='Die Erklärung der Website; ConCampus hat eine eigene, unter Texte.')
    matriculation_start = models.PositiveIntegerField('Erste Matrikelnummer', default=100001, help_text='Danach fortlaufend.')
    university = KeyField('Hochschulnummer', max_length=4, keys=GERMAN, blank=True, help_text='Für die amtliche Statistik, aus dem Schlüsselverzeichnis.')
    region = KeyField('Kreis', max_length=5, keys=DISTRICTS, blank=True, help_text='Sitz der Hochschule, für die amtliche Statistik.')
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
    file_years = models.PositiveSmallIntegerField(
        'Studierendenakten aufbewahren (Jahre)', default=5,
        help_text='Nach der letzten Exmatrikulation; dann dem Archiv angeboten und, sobald es bewertet hat, bis auf die Grunddaten gelöscht.',
    )
    core_years = models.PositiveSmallIntegerField(
        'Grunddaten aufbewahren (Jahre)', default=50,
        help_text='Name, Geburtsdaten, Matrikelnummer, Studienzeiten und Leistungen, für Studienzeitbescheinigungen und Zweitschriften; danach anonymisiert. Ebenso der Name früherer Beschäftigter.',
    )
    staff_years = models.PositiveSmallIntegerField(
        'Daten früherer Beschäftigter aufbewahren (Jahre)', default=5,
        help_text='Nach dem Deaktivieren des Kontos; dann bis auf den Namen gelöscht, der an Veranstaltungen und Prüfungen stehen bleibt.',
    )
    signup = models.BooleanField('Neue Bewerbungskonten', default=True, help_text='Ohne Haken legt niemand ein neues Bewerbungskonto an.')
    sender_number = models.CharField(
        'Absendernummer', max_length=8, blank=True, validators=[RegexValidator(r'^H\d{7}$', '„H“ und sieben Ziffern, z. B. H0001281.')],
        help_text='Im Studierendenmeldeverfahren, vom GKV-Spitzenverband vergeben; wer einen Studienplatz annimmt, nennt sie der Krankenkasse.',
    )
    insurers = models.TextField('Krankenkassen', blank=True, help_text='Vorschläge beim Annehmen des Studienplatzes, eine je Zeile.')
    student_daily_hours = models.PositiveSmallIntegerField('Übezeit am Tag (Std.)', default=4, help_text='So viele Stunden buchen Studierende am Tag.')
    student_ahead_hours = models.PositiveSmallIntegerField('Vorlauf für Studierende (Std.)', default=24, help_text='So weit im Voraus buchen Studierende.')
    last_minute_hours = models.PositiveSmallIntegerField('Kurzfristig ohne Kontingent (Std.)', default=3, help_text='Was so bald beginnt, zählt nicht zum Tageskontingent.')
    teacher_ahead_days = models.PositiveSmallIntegerField('Vorlauf für Lehrende (Tage)', default=183)
    credit_hours = models.PositiveSmallIntegerField(
        'Arbeitsstunden je CP', null=True, blank=True, help_text='Wie die Prüfungsordnung einen Leistungspunkt rechnet, meist 30; leer: im Studienplan nicht genannt.',
    )
    clash_dates = models.PositiveSmallIntegerField(
        'Rote Warnung ab', default=3,
        help_text='So vielen Terminen einer Veranstaltung, die sich mit dem Stundenplan überschneiden. Weniger, etwa eine Probe, warnt nur gelb. '
                  'Verhindert wird die Anmeldung nie; Übezeiten zählen nicht.',
    )
    load_since = models.DateField(
        'Deputatskonto ab', null=True, blank=True,
        help_text='Ab dem Semester, das an diesem Tag beginnt, rechnet das Deputat Mehr- und Minderleistungen über drei Studienjahre auf (ThürLVVO § 7); leer: nicht.',
    )

    class Meta:
        verbose_name = 'Hochschule'
        verbose_name_plural = 'Hochschule'

    def __str__(self):
        return self.name

    def clean(self):
        if self.accent and (ratio := contrast(self.accent)) < 4.5:
            raise ValidationError({'accent': f'Zu hell: weiße Schrift darauf erreicht einen Kontrast von {ratio:.1f} : 1, nötig sind 4,5 : 1.'.replace('.', ',')})

    @classmethod
    def get(cls):
        """The record, or its defaults until someone fills it in."""
        return once('institution', lambda: cls.objects.first() or cls())

    @property
    def lines(self):
        """The wordmark, line by line."""
        return (self.wordmark or self.name).splitlines()

    @property
    def look(self):
        """The stylesheet's variables (campus.css :root) where the Hochschule has its own: {name: value}."""
        found = {'radius': f'{self.radius}px'} if self.radius != 4 else {}
        if self.accent:
            rgb, hexed = [int(self.accent[i:i + 2], 16) for i in (1, 3, 5)], '#{:02x}{:02x}{:02x}'.format
            found |= {
                'accent': self.accent,
                'accent-dark': hexed(*(round(v * 0.78) for v in rgb)),
                'accent-light': hexed(*(round((v + 255) / 2) for v in rgb)),
                'accent-pale': hexed(*(round(v + (255 - v) * 0.9) for v in rgb)),
            }
        return found


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
    nationality = KeyField('Staatsangehörigkeit', max_length=60, keys=COUNTRIES, blank=True)
    second_nationality = KeyField('Weitere Staatsangehörigkeit', max_length=60, keys=COUNTRIES, blank=True)
    address = models.TextField('Anschrift', blank=True)
    phone = models.CharField(
        'Telefon', max_length=40, blank=True, validators=[RegexValidator(r'^\+?[\d ()/.-]{5,}$', 'Bitte nur Ziffern, Leerzeichen und + ( ) / - eingeben.')],
    )
    private_email = models.EmailField('E-Mail, privat', blank=True, help_text='Aus der Bewerbung; nach dem Studium gehen Nachrichten hierhin.')
    home = KeyField('Heimatwohnsitz', max_length=5, keys=PLACES, town='home_town', blank=True, help_text='Kreis, im Ausland der Staat.')
    home_town = KeyField('Gemeinde des Heimatwohnsitzes', max_length=8, keys=TOWNS, blank=True, help_text='Wechselt sie den Kreis, folgt der Wohnsitz ihr.')
    residence = KeyField(
        'Semesterwohnsitz', max_length=5, keys=PLACES, town='residence_town', blank=True, help_text='Während der Unterrichtszeit; leer: wie der Heimatwohnsitz.',
    )
    residence_town = KeyField('Gemeinde des Semesterwohnsitzes', max_length=8, keys=TOWNS, blank=True)
    entrance = models.CharField(
        'Hochschulzugangsberechtigung', max_length=10, choices=destatis.ENTRANCE, blank=True,
        help_text='Die erste, mit der ein Studium in Deutschland möglich war.',
    )
    entrance_date = models.DateField('Erworben am', null=True, blank=True)
    entrance_region = KeyField('Erworben in', max_length=5, keys=PLACES, town='entrance_town', blank=True, help_text='Kreis, im Ausland der Staat.')
    entrance_town = KeyField('Gemeinde der Hochschulzugangsberechtigung', max_length=8, keys=TOWNS, blank=True, help_text='Wie sie damals hieß.')
    first_university = KeyField('Hochschule der Ersteinschreibung', max_length=7, keys=UNIVERSITIES, blank=True, help_text='Leer: diese Hochschule.')
    first_term = models.CharField(
        'Semester der Ersteinschreibung', max_length=12, blank=True, help_text='Z. B. WiSe 2019/20 oder SoSe 2020; leer: das erste Semester hier.',
        validators=[RegexValidator(r'^(WiSe \d{4}/\d{2}|SoSe \d{4})$', 'Z. B. WiSe 2019/20 oder SoSe 2020.')],
    )
    external_terms = models.PositiveSmallIntegerField('Semester an anderen deutschen Hochschulen', default=0, help_text='Zählen zu den Hochschulsemestern.')
    degree_university = KeyField('Hochschule des letzten Abschlusses', max_length=7, keys=UNIVERSITIES, blank=True, help_text='Nur für einen Abschluss anderswo.')
    degree_exam = KeyField('Letzter Abschluss', max_length=2, keys=DEGREES, blank=True)
    degree_field = KeyField('Im Studienfach', max_length=3, keys=FIELDS, blank=True)
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
    insurer_number = models.CharField('Betriebsnummer der Krankenkasse', max_length=8, blank=True, help_text='Aus ihrer Meldung; an sie gehen die Meldungen der Hochschule.')
    insurance_arrears = models.DateField(
        'Beitragsrückstand gemeldet am', null=True, blank=True, help_text='Von der Krankenkasse (M12); hält die Rückmeldung auf, bis sie die Zahlung meldet (M13).',
    )
    electronic = models.DateTimeField(
        'Bescheide elektronisch seit', null=True, blank=True,
        help_text='Einwilligung, dass Bescheide am Tag nach dem Öffnen im Postfach bekannt gegeben sind (§ 41 Abs. 2a VwVfG); ohne sie gehen Bescheide per Post.',
    )
    entrance_checked = models.DateField(
        'Hochschulzugang geprüft am', null=True, blank=True,
        help_text='Bei einer Hochschulzugangsberechtigung aus dem Ausland: bewertet nach anabin oder durch die Zeugnisanerkennungsstelle.',
    )
    matriculation = models.CharField('Matrikelnummer', max_length=12, unique=True, null=True, blank=True)
    sso_id = models.CharField(
        'Kennung im Hochschulkonto', max_length=255, unique=True, null=True, blank=True,
        help_text='Wie der Login der Hochschule die Person nennt (eduPersonPrincipalName); bei der ersten Anmeldung über ihn gesetzt.',
    )
    hold = models.CharField('Sperre', max_length=200, blank=True, help_text='Grund der Sperre; leer heißt keine Sperre.')

    class Restriction(models.TextChoices):  # the grounds of Art. 18 Abs. 1 DSGVO
        ACCURACY = 'richtigkeit', 'Richtigkeit bestritten, bis sie geprüft ist (lit. a)'
        UNLAWFUL = 'statt_loeschung', 'Unrechtmäßig verarbeitet, die Person will Einschränkung statt Löschung (lit. b)'
        CLAIMS = 'rechtsanspruch', 'Nicht mehr gebraucht, die Person braucht sie für Rechtsansprüche (lit. c)'
        OBJECTION = 'widerspruch', 'Widerspruch eingelegt, bis er geprüft ist (lit. d)'

    restriction = models.CharField(
        'Verarbeitung eingeschränkt', max_length=16, choices=Restriction, blank=True,
        help_text='Art. 18 DSGVO: die Daten nur noch speichern, anderes nur mit Einwilligung, für Rechtsansprüche oder aus wichtigem '
                  'öffentlichem Interesse; ConCampus löscht nichts von der Person, solange sie gilt. Vor dem Aufheben die Person unterrichten.',
    )
    restricted = models.DateField('Eingeschränkt seit', null=True, blank=True)
    left = models.DateField('Ausgeschieden am', null=True, blank=True, help_text='Für Beschäftigte: der Tag, an dem ihr Konto deaktiviert wurde.')
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
        help_text='Was die Lehrverpflichtungsverordnung verlangt, vor Ermäßigungen; bei einem Lehrauftrag die vereinbarten SWS.',
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

    @property
    def abroad(self):
        """Whether the HZB comes from a school abroad that is not a German one: it is assessed before the Immatrikulation."""
        return self.entrance in destatis.SCHOOL_ABROAD

    @property
    def needs_german(self):
        """Whether German must be proved: an HZB from abroad, not in German."""
        return self.abroad and self.entrance_region[2:] not in destatis.GERMAN_SPEAKING

    @property
    def needs_aps(self):
        return self.abroad and self.entrance_region[2:] in destatis.APS

    @property
    def permit(self):
        """Whether studying here takes a residence permit: no citizenship of the EU, the EEA or Switzerland."""
        return bool(self.nationality) and not {self.nationality, self.second_nationality} & destatis.FREE_MOVEMENT

    def missing(self):
        """What blocks enrolment and Rückmeldung whatever has been paid: a Sperre, no confirmation from the health insurer,
        an instrument kept past its day. Reads `loans.all()`; prefetch them, with their instruments, for many people."""
        reasons = [f'Sperre: {self.hold}'] if self.hold else []
        if not self.insurance_confirmed:
            reasons.append('Versicherungsmeldung der Krankenkasse fehlt')
        if self.insurance_arrears:
            reasons.append('Beitragsrückstand bei der Krankenkasse')
        return reasons + [f'Leihinstrument zurückgeben: {loan.instrument.name}' for loan in self.loans.all() if loan.overdue]

    COUNTED = {'gender', 'nationality', 'entrance'}  # what the figures count people by (analytics/figures.py), kept when made anonymous

    @classmethod
    def clear(cls, pks, kept=(), **values):
        """Empty every field of these people but `kept` and set `values`, leaving no trace in the Verlauf; they can no
        longer sign in."""
        empty = {f.name: f.get_default() for f in cls._meta.concrete_fields if not f.primary_key and f.name not in kept}
        cls.objects.filter(pk__in=pks).update(**empty | {'password': '!', 'is_active': False} | values)

    @classmethod
    def anonymise(cls, pks):
        cls.clear(pks, cls.COUNTED, username=Concat(models.Value('anonym-'), Cast('pk', models.CharField())), last_name='Anonym')

    @classmethod
    def history(cls, pk):
        """Everything about the person: their own record, studies, terms, documents, results, bookings."""
        return models.Q(person=pk)

    @classmethod
    def seen_by(cls, person, pk):
        """A person's history spans what several offices look after: it opens to every capability that sees a history and
        holds for them, each for its own tables. Undoing a change stays with the office of the changed record."""
        record = cls.objects.filter(pk=pk).first()
        seeing = {c for model in apps.get_models() if issubclass(model, Tracked) for c in model.capabilities}
        return {c for c in seeing & set(person.reach) if (person.may(c, record) if record else person.everywhere(c))}

    @classmethod
    def teachers(cls):
        """Who can teach or examine: whoever belongs to an Institut or already teaches."""
        return cls.objects.filter(models.Q(institute__isnull=False) | models.Q(teaching__isnull=False)).distinct()

    @cached_property
    def reach(self):
        """Where each capability of the person's offices holds: None for the whole Hochschule, else the ids of the
        Institute and the Studiengänge it is limited to; offices held until a day count until then, once confirmed.
        Superusers hold all, everywhere."""
        if self.is_superuser:
            return dict.fromkeys(CODENAMES)
        reach = {}
        held = models.Q(user=self) | models.Q(memberships__person=self, memberships__confirmed_by__isnull=False, memberships__until__gte=timezone.localdate())
        for office in Office.objects.filter(held).distinct().prefetch_related('permissions', 'institutes', 'programs'):
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
        studies, courses, me = self.studies.model.objects, self.teaching.model.objects, models.OuterRef('pk')
        found = Person.objects.filter(pk=self.pk).values(  # in one query, asked on every page
            student=models.Exists(studies.filter(person=me, status='enrolled')),
            applicant=models.Exists(studies.filter(person=me, status__in=['applied', 'admitted', 'waitlisted'])),
            alumni=models.Exists(studies.filter(person=me, status__in=['graduated', 'left'], terms__isnull=False)),
            teacher=models.Exists(courses.filter(staff=me)),
            juror=models.Exists(courses.filter(staff=me, kind='audition')),
            substitute=models.Exists(self.substituting.model.objects.filter(substitute=me, participation__course__kind='audition')),  # on the jury too
        ).get()
        found['juror'] |= found.pop('substitute')
        return set(self.reach) | {role for role, held in found.items() if held}


class Office(Tracked, Group):
    """An office of the Hochschule, named as it likes: the people in it and what they may do (core.access.CAPABILITIES),
    for the whole Hochschule or only for some Institute and Studiengänge."""

    capabilities = ('manage_accounts',)

    institutes = models.ManyToManyField(Institute, blank=True, related_name='offices', verbose_name='Nur für die Institute')
    programs = models.ManyToManyField('curriculum.Program', blank=True, related_name='offices', verbose_name='Nur für die Studiengänge')
    email = models.EmailField('E-Mail', blank=True, help_text='Das gemeinsame Postfach des Amts, etwa pruefungsamt@…; leer: die Adressen seiner Mitglieder.')

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
    def holding(cls, capability, program=None, remit=False, linked=False, institute=None):
        """The offices that hold a capability (or any of several), for sentences like 'Zentral vergeben: …'; with a
        programme, those for it; with `remit`, each limited one says for what; `linked` for a page, each a link to its
        shared address so the person can write at once."""
        offices = cls.objects.filter(permissions__codename__in=[capability] if isinstance(capability, str) else capability).distinct()
        if program:
            offices = offices.filter(models.Q(programs=None) | models.Q(programs=program), institutes=None)
        if institute:
            offices = offices.filter(models.Q(institutes=None) | models.Q(institutes=institute), programs=None)
        names = [
            format_html('<a href="mailto:{}">{}</a>', o.email, name) if linked and o.email else name
            for o in offices.prefetch_related('institutes', 'programs') for name in [o.name + (limited(o) if remit else '')]
        ]
        *rest, last = names or ['']
        return format_html_join(', ', '{}', ((n,) for n in rest)) + format_html(' oder {}', last) if rest else format_html('{}', last)


def limited(office):
    """', nur für …' after an office limited to some Institute or Studiengänge; long lists counted."""
    reach = [*office.institutes.all(), *office.programs.all()]
    if not reach:
        return ''
    return f' (nur für {", ".join(map(str, reach))})' if len(reach) <= 2 else f' (nur für {len(reach)} Institute und Studiengänge)'


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
    fee_union = models.DecimalField('davon Studierendenwerk', max_digits=7, decimal_places=2, null=True, blank=True)
    fee_ticket = models.DecimalField('davon Semesterticket', max_digits=7, decimal_places=2, null=True, blank=True)
    fee_council = models.DecimalField(
        'davon Studierendenschaft', max_digits=7, decimal_places=2, null=True, blank=True,
        help_text='Die Teile, wie sie Bescheide nennen und wie Erstattungen sie brauchen; zusammen der Semesterbeitrag.',
    )
    exam_signup_start = models.DateField('Prüfungsanmeldung ab', null=True, blank=True, help_text='Neue Prüfungen des Semesters übernehmen den Zeitraum.')
    exam_signup_end = models.DateField('Prüfungsanmeldung bis', null=True, blank=True)
    exams_start = models.DateField('Beginn des Prüfungszeitraums', null=True, blank=True, help_text='Bis einige Wochen davor melden sich Studierende von Prüfungen ab.')
    exams_end = models.DateField('Ende des Prüfungszeitraums', null=True, blank=True)
    planning = models.BooleanField(
        'In Planung', default=False, help_text='Seine Lehrveranstaltungen sehen nur die Ämter und die Lehrenden, bis das Semester veröffentlicht ist.',
    )
    ready_by = models.DateField('Vorlesungsverzeichnis fertig bis', null=True, blank=True, help_text='Bis dahin ist das Semester geplant und veröffentlicht.')
    replies_by = models.DateField('Rückmeldung der Lehrenden bis', null=True, blank=True, help_text='Bis dahin bestätigen die Lehrenden ihre Veranstaltungen.')
    course_signup_start = models.DateField(
        'Anmeldung zu Lehrveranstaltungen ab', null=True, blank=True, help_text='Neue und übernommene Lehrveranstaltungen übernehmen den Zeitraum.',
    )
    course_signup_end = models.DateField('Anmeldung zu Lehrveranstaltungen bis', null=True, blank=True)

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

    @property
    def parts(self):
        """What the Semesterbeitrag is made of: [(whom it goes to, amount)]."""
        return [(name, amount) for name, amount in [('Studierendenwerk', self.fee_union), ('Semesterticket', self.fee_ticket), ('Studierendenschaft', self.fee_council)] if amount]

    def due(self, day, late_fee):
        """The Semesterbeitrag paid on `day`: with the Säumnisgebühr in the Nachfrist."""
        return self.fee + (late_fee if self.late_deadline and day > self.deadline else 0)

    @classmethod
    def current(cls):
        """The term under way; between terms the last one, before the first term the first."""
        return once('current', lambda: cls.objects.filter(start__lte=timezone.localdate()).order_by('-start').first() or cls.objects.first())

    @classmethod
    def upcoming(cls):
        return once('upcoming', lambda: cls.objects.filter(start__gt=timezone.localdate()).first())

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
        if self.parts and self.fee is not None and sum(amount for _, amount in self.parts) != self.fee:
            raise ValidationError({'fee_council': 'Die Teile ergeben zusammen nicht den Semesterbeitrag.'})
        if self.start and self.end and (other := Term.objects.exclude(pk=self.pk).filter(start__lte=self.end, end__gte=self.start).first()):
            raise ValidationError({'start': f'Überschneidet sich mit dem {other} ({other.start:%d.%m.%Y}–{other.end:%d.%m.%Y}).'})
        if self.start and self.end and self.lectures_start and self.lectures_end and not self.start <= self.lectures_start < self.lectures_end <= self.end:
            raise ValidationError({'lectures_start': 'Der Unterricht liegt im Semester.'})
        if self.deadline and self.start and self.deadline >= self.start:
            raise ValidationError({'deadline': 'Zurückgemeldet wird vor Beginn des Semesters.'})

    @classmethod
    def shown(cls, person):
        """The terms whose Lehrveranstaltungen the person sees: one in planning only the offices and the teachers."""
        return cls.objects.all() if person.roles - {'student', 'applicant', 'alumni'} else cls.objects.filter(planning=False)

    @property
    def over(self):
        return self.end < timezone.localdate()

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
    public = models.BooleanField('Gesetzlicher Feiertag', default=False, help_text='Eine Frist, die an ihm endet, endet erst am nächsten Werktag.')

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
    def days(cls, start, end, public=False):
        """The lecture-free days from `start` to `end`; with `public`, only public holidays."""
        found = set()
        holidays = cls.objects.filter(public=True) if public else cls.objects.all()
        for holiday in holidays.filter(first__lte=end).filter(models.Q(last__gte=start) | models.Q(last=None, first__gte=start)):
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

    @property
    def reference(self):
        """A Bescheid's Aktenzeichen, to quote in a Widerspruch; other messages have none."""
        return f'{self.created:%Y}-{self.pk}' if self.sender else ''

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
    reason = models.CharField('Grund', max_length=200, blank=True, help_text='Bei einer Korrektur nach Ende des Semesters.')

    class Meta:
        ordering = ['-at', '-pk']
        verbose_name = 'Änderung'
        verbose_name_plural = 'Änderungen'
        indexes = [models.Index(fields=['table', 'row'])]

    def __str__(self):
        return f'{self.table} {self.row} {self.get_action_display()}'


class Membership(Tracked, models.Model):
    """Someone joining an office, waiting until a second person who keeps accounts confirms it (Vier-Augen-Prinzip): for
    good, then they become one of its members; or until a day, as a stand-in or for a term of office, which ends by
    itself."""

    owner = 'person_id'
    capabilities = ('manage_accounts',)

    person = models.ForeignKey(Person, models.CASCADE, related_name='memberships', verbose_name='Person')
    office = models.ForeignKey(Office, models.CASCADE, related_name='memberships', verbose_name='Amt')
    until = models.DateField('Bis einschließlich', null=True, blank=True, help_text='Für eine Vertretung oder Amtszeit; leer: auf Dauer.')
    note = models.CharField('Anlass', max_length=200, blank=True, help_text='Z. B. „Vertretung während der Elternzeit von …“.')
    by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Eingetragen von')
    confirmed_by = models.ForeignKey(Person, models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Bestätigt von')
    created = models.DateTimeField('Eingetragen am', default=timezone.now)

    class Meta:
        ordering = ['-created']
        verbose_name = 'Mitgliedschaft in einem Amt'
        verbose_name_plural = 'Mitgliedschaften in Ämtern'

    def __str__(self):
        return f'{self.person} · {self.office}' + (f' bis {self.until:%d.%m.%Y}' if self.until else '')

    def confirm(self, by):
        """Take effect: a member for good, or until the day."""
        if self.until is None:
            self.person.groups.add(self.office)
            self.delete()
        else:
            self.confirmed_by = by
            self.save(update_fields=['confirmed_by'])


class Export(models.Model):
    """A list with personal data taken out as a file: who, when, what and how many rows (docs/privacy.md); kept for
    `KEEP`, long enough to trace where a list went."""

    KEEP = timedelta(days=730)

    person = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Von')
    what = models.CharField('Was', max_length=300)
    rows = models.PositiveIntegerField('Zeilen')
    created = models.DateTimeField('Am', default=timezone.now)

    class Meta:
        ordering = ['-created']
        verbose_name = 'Export'
        verbose_name_plural = 'Exporte'

    @classmethod
    def log(cls, person, what, rows):
        cls.objects.create(person=person, what=what[:300], rows=rows)


class SignIn(models.Model):
    """A sign-in or a failed try, kept for security alone (Art. 32 DSGVO) and only for `KEEP`: the person sees their own,
    `manage_accounts` all (docs/privacy.md)."""

    KEEP = timedelta(days=90)

    username = models.CharField('Angegeben', max_length=150)
    person = models.ForeignKey(Person, models.CASCADE, null=True, related_name='sign_ins', verbose_name='Person')
    ip = models.CharField('Adresse', max_length=45, blank=True)
    at = models.DateTimeField('Am', default=timezone.now)
    success = models.BooleanField('Angemeldet')

    class Meta:
        ordering = ['-at']
        verbose_name = 'Anmeldung'
        verbose_name_plural = 'Anmeldungen'


class Feedback(models.Model):
    """A barrier in ConCampus reported through the form under its accessibility statement (ThürBarrWebG): answered by
    `manage_institution` within `ANSWER`, deleted `KEEP` after the answer."""

    ANSWER, KEEP = timedelta(days=30), timedelta(days=365)

    name = models.CharField('Name', max_length=120, blank=True)
    email = models.EmailField('E-Mail', help_text='Für die Antwort.')
    page = models.CharField('Seite', max_length=300, blank=True, help_text='Wo die Barriere ist, wenn auf einer bestimmten Seite.')
    text = models.TextField('Was ist nicht zugänglich?')
    received = models.DateTimeField('Eingegangen', default=timezone.now)
    answered = models.DateField('Beantwortet am', null=True, blank=True)
    answered_by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Beantwortet von')

    class Meta:
        ordering = ['-received']
        verbose_name = 'Rückmeldung zur Barrierefreiheit'
        verbose_name_plural = 'Rückmeldungen zur Barrierefreiheit'

    @property
    def due(self):
        return timezone.localdate(self.received + self.ANSWER)


class Consent(models.Model):
    """An Einwilligung to health data in a document (Art. 9 Abs. 2 lit. a, Art. 7 DSGVO): the words the person agreed to,
    when, who recorded it (they themselves, or an office from paper), and when they took it back. Kept after that, to show
    it was given."""

    person = models.ForeignKey(Person, models.CASCADE, related_name='consents', verbose_name='Person')
    purpose = models.CharField('Wofür', max_length=200, help_text='Wie im Wortlaut, z. B. „zum Nachteilsausgleich“.')
    wording = models.TextField('Wortlaut')
    by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Verbucht von')
    given = models.DateTimeField('Erteilt', default=timezone.now)
    revoked = models.DateTimeField('Widerrufen', null=True, blank=True)

    class Meta:
        ordering = ['-given']
        verbose_name = 'Einwilligung'
        verbose_name_plural = 'Einwilligungen'

    def __str__(self):
        return f'Nachweis {self.purpose}'

    @classmethod
    def give(cls, person, purpose, by):
        from .texts import block
        return cls.objects.create(person=person, purpose=purpose, wording=block('einwilligung_gesundheit', zweck=purpose), by=by)


class Grant(Tracked, models.Model):
    """Rights given to an office, waiting until a second person who keeps accounts confirms them."""

    capabilities = ('manage_accounts',)

    office = models.ForeignKey(Office, models.CASCADE, related_name='grants', verbose_name='Amt')
    rights = models.JSONField('Rechte', default=list)
    by = models.ForeignKey(Person, models.SET_NULL, null=True, related_name='+', verbose_name='Eingetragen von')
    created = models.DateTimeField('Eingetragen am', default=timezone.now)

    class Meta:
        ordering = ['-created']
        verbose_name = 'Rechte für ein Amt'
        verbose_name_plural = 'Rechte für Ämter'

    def __str__(self):
        return f'{self.office}: {", ".join(self.labels)}'

    @property
    def labels(self):
        names = dict(c for _, group in CAPABILITIES for c in group)
        return [names.get(r, r) for r in self.rights]

    def confirm(self, by):
        from django.contrib.auth.models import Permission

        self.office.permissions.add(*Permission.objects.filter(content_type__model='office', codename__in=self.rights))
        self.delete()
