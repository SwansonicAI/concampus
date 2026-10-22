from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Institute, Person, Tracked
from courses.models import Course


def later(moment, weeks):
    """The same local wall-clock time `weeks` later, so weekly series keep their hour across DST changes."""
    return timezone.make_aware(timezone.localtime(moment).replace(tzinfo=None) + timedelta(weeks=weeks))


class Room(Tracked, models.Model):
    """A room of the Hochschule or a venue it uses. Institutes book their own rooms; event and meeting rooms go
    through the Veranstaltungsbüro."""

    offices = ('Veranstaltungsbüro',)

    class Function(models.TextChoices):
        PRACTICE = 'uebung', 'Übungsraum'
        TEACHING = 'unterricht', 'Unterrichtsraum'
        CHAMBER = 'kammermusik', 'Kammermusik'
        ORGAN = 'orgel', 'Orgel'
        SEMINAR = 'seminar', 'Hörsaal und Seminarraum'
        STUDIO = 'studio', 'Studio und Labor'
        MEETING = 'sitzung', 'Sitzungsraum'
        EVENT = 'veranstaltung', 'Veranstaltungsraum'
        WORKSHOP = 'werkstatt', 'Werkstatt'

    OFFICE_ONLY = [Function.EVENT, Function.MEETING]

    name = models.CharField('Name', max_length=60, unique=True, help_text='Raumnummer oder Name, z. B. „FH 214“ oder „Festsaal“.')
    purpose = models.CharField('Bezeichnung', max_length=80, blank=True, help_text='Wie an der Tür, z. B. „Unterrichtsraum Klavier“.')
    building = models.CharField('Gebäude', max_length=40)
    function = models.CharField('Funktion', max_length=16, choices=Function, default=Function.TEACHING)
    institutes = models.ManyToManyField(Institute, blank=True, related_name='rooms', verbose_name='Institute')
    capacity = models.PositiveSmallIntegerField('Plätze')
    equipment = models.CharField('Ausstattung', max_length=200, blank=True)

    class Meta:
        ordering = ['building', 'name']
        verbose_name = 'Raum'
        verbose_name_plural = 'Räume'

    def __str__(self):
        return self.name


class Booking(Tracked, models.Model):
    """A room taken for a time. Lessons, exams, rehearsals, concerts, practice and closures are all bookings,
    told apart by their category."""

    owner = 'person_id'
    offices = ('Veranstaltungsbüro',)
    keep = timedelta(days=730)

    class Category(models.TextChoices):  # as the HfM's Asimut has them
        PRACTICE = 'uebung', 'Übebuchung'
        CHAMBER = 'kammermusik', 'Kammermusik-Probe'
        LESSON = 'unterricht', 'Unterricht'
        REHEARSAL = 'probe', 'Probe'
        ORCHESTRA = 'orchesterprobe', 'Orchesterprobe'
        SECTION = 'registerprobe', 'Registerprobe'
        EXAM = 'pruefung', 'Prüfung'
        AUDITION = 'eignungspruefung', 'Eignungsprüfung'
        RECITAL = 'vorspiel', 'Vorspiel'
        CLASS_CONCERT = 'klassenabend', 'Klassenabend'
        CONCERT = 'konzert', 'Konzert'
        EVENT = 'veranstaltung', 'Veranstaltung'
        MASTERCLASS = 'meisterkurs', 'Meisterkurs'
        WEIMAR_MASTERCLASSES = 'weimarer-meisterkurse', 'Weimarer Meisterkurse'
        COMPETITION = 'wettbewerb', 'Wettbewerb'
        WORKSHOP = 'workshop', 'Workshop'
        LECTURE = 'vortrag', 'Vortrag'
        CONFERENCE = 'tagung', 'Tagung'
        MEETING = 'sitzung', 'Sitzung'
        INTERVIEW = 'vorstellung', 'Vorstellungsgespräche'
        RECORDING = 'aufnahme', 'Aufnahme'
        STUDENT_PROJECT = 'projekt', 'Studentisches Projekt'
        RENTAL = 'vermietung', 'Vermietung'
        OPTION = 'option', 'freie Konzert-Option'
        CONCERT_SUPPORT = 'konzertbetreuung', 'Konzertbetreuung'
        ORGANISATION = 'organisation', 'Organisation VB'
        TUNING = 'stimmung', 'Stimmung'
        PIANO_REPAIR = 'klavierstimmer', 'Reparaturarbeiten Klavierstimmer'
        CARETAKER = 'hausmeister', 'Hausmeister'
        TECHNICS = 'technik', 'Technik'
        TRANSPORT = 'transport', 'Transport'
        PERSONAL = 'persoenlich', 'Persönliche Buchung'
        GENERAL = 'allgemein', 'Allgemeine Buchung'
        BLOCKED = 'blockiert', 'Blockiert'
        CANCELLED = 'abgesagt', 'Abgesagt'

    C = Category
    TONES = {  # colour family on the plans
        'teach': [C.LESSON, C.REHEARSAL, C.ORCHESTRA, C.SECTION, C.RECORDING, C.STUDENT_PROJECT],
        'exam': [C.EXAM, C.AUDITION, C.RECITAL, C.COMPETITION, C.INTERVIEW],
        'event': [C.CLASS_CONCERT, C.CONCERT, C.EVENT, C.MASTERCLASS, C.WEIMAR_MASTERCLASSES, C.WORKSHOP, C.LECTURE, C.CONFERENCE,
                  C.MEETING, C.RENTAL, C.OPTION, C.CONCERT_SUPPORT, C.ORGANISATION],
        'service': [C.TUNING, C.PIANO_REPAIR, C.CARETAKER, C.TECHNICS, C.TRANSPORT],
        'blocked': [C.BLOCKED],
        'cancelled': [C.CANCELLED],
    }
    TONE = {category: tone for tone, categories in TONES.items() for category in categories}
    PUBLIC = [C.CLASS_CONCERT, C.CONCERT, C.EVENT, C.MASTERCLASS, C.WEIMAR_MASTERCLASSES, C.WORKSHOP, C.LECTURE, C.CONFERENCE, C.RECITAL, C.COMPETITION]
    STUDENT = [C.PRACTICE, C.CHAMBER]  # what students book themselves
    TEACHER = [C.LESSON, C.REHEARSAL, C.SECTION, C.EXAM, C.RECITAL, C.CLASS_CONCERT, C.MASTERCLASS, C.WORKSHOP, C.LECTURE, C.MEETING, C.RECORDING, C.PERSONAL, C.PRACTICE]

    room = models.ForeignKey(Room, models.CASCADE, related_name='bookings', verbose_name='Raum')
    start = models.DateTimeField('Beginn')
    end = models.DateTimeField('Ende')
    category = models.CharField('Kategorie', max_length=24, choices=Category, default=Category.GENERAL)
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
        return self.title or (self.course.title if self.course_id else self.get_category_display())

    @property
    def tone(self):
        return self.TONE.get(self.category, 'practice')

    def clashes(self):
        """Bookings that overlap in the same room. Cancelled ones stay visible but no longer block it."""
        return (
            Booking.objects.filter(room=self.room_id, start__lt=self.end, end__gt=self.start)
            .exclude(pk=self.pk).exclude(category=self.Category.CANCELLED)
        )

    def clean(self):
        if self.room_id and self.start and self.end and self.category != self.Category.CANCELLED and self.clashes().exists():
            raise ValidationError('Der Raum ist in diesem Zeitraum bereits belegt.')
