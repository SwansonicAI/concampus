from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Institute, Keyed, Person, Tracked
from courses.models import Course


def later(moment, weeks):
    """The same local wall-clock time `weeks` later, so weekly series keep their hour across DST changes."""
    return timezone.make_aware(timezone.localtime(moment).replace(tzinfo=None) + timedelta(weeks=weeks))


def span(hours):
    return f'{hours // 24} Tage' if hours > 24 and hours % 24 == 0 else f'{hours} Stunden'


class RoomFunction(Tracked, Keyed):
    """What a room is for: Übungsraum, Unterrichtsraum, Veranstaltungsraum."""

    capabilities = ('manage_rooms',)

    name = models.CharField('Name', max_length=60, unique=True)
    central = models.BooleanField(
        'Zentral vergeben', default=False,
        help_text='Nur bucht, wer alle Räume buchen darf, z. B. Veranstaltungs- und Sitzungsräume. Sonst buchen Lehrende und Studierende die Räume ihres Instituts.',
    )
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Raumfunktion'
        verbose_name_plural = 'Raumfunktionen'

    def __str__(self):
        return self.name


class Room(Tracked, models.Model):
    """A room of the Hochschule or a venue it uses. Institutes book their own rooms; others are given out centrally."""

    capabilities = ('manage_rooms',)
    scope = (['institutes'], [])

    name = models.CharField('Name', max_length=60, unique=True, help_text='Raumnummer oder Name, z. B. „FH 214“ oder „Festsaal“.')
    purpose = models.CharField('Bezeichnung', max_length=80, blank=True, help_text='Wie an der Tür, z. B. „Unterrichtsraum Klavier“.')
    building = models.CharField('Gebäude', max_length=40)
    function = models.ForeignKey(RoomFunction, models.PROTECT, to_field='key', related_name='rooms', verbose_name='Funktion')
    institutes = models.ManyToManyField(Institute, blank=True, related_name='rooms', verbose_name='Institute')
    capacity = models.PositiveSmallIntegerField('Plätze')
    equipment = models.CharField('Ausstattung', max_length=200, blank=True)

    class Meta:
        ordering = ['building', 'name']
        verbose_name = 'Raum'
        verbose_name_plural = 'Räume'

    def __str__(self):
        return self.name


class BookingCategory(Tracked, Keyed):
    """What a booking is for. Its settings decide who books it, how it shows on the plans and which rules apply."""

    class Tone(models.TextChoices):  # colour family on the plans
        TEACH = 'teach', 'Unterricht und Probe'
        PRACTICE = 'practice', 'Üben'
        EXAM = 'exam', 'Prüfung'
        EVENT = 'event', 'Veranstaltung'
        SERVICE = 'service', 'Service'
        BLOCKED = 'blocked', 'Blockiert'
        CANCELLED = 'cancelled', 'Abgesagt'

    SYSTEM = {  # what the code books itself, created on first use
        'unterricht': {'name': 'Unterricht', 'tone': Tone.TEACH, 'teachers': True},
        'pruefung': {'name': 'Prüfung', 'tone': Tone.EXAM, 'teachers': True},
        'eignungspruefung': {'name': 'Eignungsprüfung', 'tone': Tone.EXAM},
        'abgesagt': {'name': 'Abgesagt', 'tone': Tone.CANCELLED, 'blocks': False},
    }

    capabilities = ('manage_rooms',)

    name = models.CharField('Name', max_length=60, unique=True)
    tone = models.CharField('Farbe', max_length=10, choices=Tone, default=Tone.PRACTICE)
    students = models.BooleanField('Studierende buchen sie', default=False)
    teachers = models.BooleanField('Lehrende buchen sie', default=False)
    quota = models.BooleanField('Zählt zum Tageskontingent', default=False, help_text='Die Stunden, die Studierende am Tag buchen dürfen.')
    ahead_hours = models.PositiveSmallIntegerField(
        'Eigener Vorlauf (Std.)', null=True, blank=True,
        help_text='Wie weit im Voraus Studierende sie buchen; leer: wie in den Hochschuldaten.',
    )
    participants = models.PositiveSmallIntegerField('Mindestens weitere Teilnehmende', default=0, help_text='Für Studierende, z. B. 1 bei Kammermusik.')
    public = models.BooleanField('Öffentlich', default=False, help_text='Steht unter Veranstaltungen; alle sehen die Einzelheiten.')
    blocks = models.BooleanField('Belegt den Raum', default=True, help_text='Ohne Haken, wie bei „Abgesagt“: der Raum bleibt frei, der Termin fällt aus den Kalendern.')
    order = models.PositiveSmallIntegerField('Reihenfolge', default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Buchungskategorie'
        verbose_name_plural = 'Buchungskategorien'

    def __str__(self):
        return self.name

    @classmethod
    def system(cls, key):
        return cls.objects.get_or_create(key=key, defaults=cls.SYSTEM[key])[0]

    @property
    def ahead(self):
        return span(self.ahead_hours) if self.ahead_hours else ''


class Booking(Tracked, models.Model):
    """A room taken for a time. Lessons, exams, rehearsals, concerts, practice and closures are all bookings,
    told apart by their category."""

    owner = 'person_id'
    capabilities = ('manage_bookings',)
    scope = (['room__institutes'], [])
    keep = timedelta(days=730)

    room = models.ForeignKey(Room, models.CASCADE, related_name='bookings', verbose_name='Raum')
    start = models.DateTimeField('Beginn')
    end = models.DateTimeField('Ende')
    category = models.ForeignKey(BookingCategory, models.PROTECT, to_field='key', related_name='bookings', verbose_name='Kategorie')
    person = models.ForeignKey(Person, models.CASCADE, related_name='bookings', verbose_name='Gebucht für')
    participants = models.ManyToManyField(Person, blank=True, related_name='attending', verbose_name='Teilnehmende')
    course = models.ForeignKey(Course, models.CASCADE, null=True, blank=True, related_name='bookings', verbose_name='Veranstaltung')
    title = models.CharField('Titel', max_length=120, blank=True)
    description = models.TextField('Beschreibung', blank=True)
    series = models.UUIDField('Serie', null=True, blank=True, db_index=True)
    created = models.DateTimeField('Gebucht am', default=timezone.now)

    class Meta:
        ordering = ['start']
        verbose_name = 'Buchung'
        verbose_name_plural = 'Buchungen'
        constraints = [models.CheckConstraint(condition=models.Q(end__gt=models.F('start')), name='booking_ends_after_start')]

    def __str__(self):
        return f'{self.label} · {self.room}'

    @property
    def label(self):
        return self.title or (self.course.title if self.course_id else self.category.name)

    @property
    def tone(self):
        return self.category.tone

    def clashes(self):
        """Bookings that overlap in the same room. Cancelled ones stay visible but no longer block it."""
        return (
            Booking.objects.filter(room=self.room_id, start__lt=self.end, end__gt=self.start, category__blocks=True)
            .exclude(pk=self.pk)
        )

    def clean(self):
        if self.room_id and self.start and self.end and self.category_id and self.category.blocks and self.clashes().exists():
            raise ValidationError('Der Raum ist in diesem Zeitraum bereits belegt.')
