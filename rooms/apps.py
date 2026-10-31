from datetime import timedelta

from django.apps import AppConfig
from django.urls import reverse
from django.utils.formats import number_format

from core.apps import Tile, feature, plural, task


class RoomsConfig(Tile, AppConfig):
    name = 'rooms'
    verbose_name = 'Räume'
    order = 5
    icon = 'M3 4h18v18H3zM16 2v4M8 2v4M3 10h18'
    url = 'rooms:index'

    def title(self, roles):
        if roles - {'applicant', 'alumni'}:
            return 'Räume'

    def features(self, person):
        if not self.title(person.roles):
            return []
        return [
            feature('Raumplan', reverse('rooms:index'), 'belegung räume heute kalender tagesplan institut gebäude'),
            feature('Raum buchen', reverse('rooms:find'), 'frei freier raum finden suchen verfügbar buchung reservieren üben überaum kammermusik probe'),
            feature('Meine Buchungen', reverse('rooms:mine'), 'stornieren übezeit kontingent'),
            feature('Veranstaltungen', reverse('rooms:events'), 'konzert klassenabend meisterkurs vortrag programm'),
            feature('Kalender abonnieren', f"{reverse('rooms:mine')}#kalender", 'ical ics outlook google apple handy feed'),
        ]

    def figure(self, person):
        from django.utils import timezone

        from core.dates import starting
        from core.models import Institution
        from .forms import quota
        from .models import Booking
        from .views import agenda

        today = timezone.localdate()
        if 'manage_bookings' in person.roles:
            return f'{person.within(Booking.objects.filter(**starting(today)), "manage_bookings").count()} Buchungen heute'
        if 'student' in person.roles:
            daily = Institution.get().student_daily_hours
            return f'Übezeit heute: noch {number_format(daily - quota(person, today))} von {daily} Std.'
        return f'Ihre Termine heute: {agenda(person).filter(**starting(today)).count()}'

    def tasks(self, person):
        from django.utils import timezone

        from .models import Booking

        if 'manage_bookings' not in person.roles:
            return []
        soon = Booking.objects.filter(category__public=True, internal=False, requested=False, programme='', start__gte=timezone.now(), start__lte=timezone.now() + timedelta(days=14))
        found = [
            task(f'Programm fehlt: {b.label}', reverse('rooms:booking', args=[b.pk]), timezone.localdate(b.start))
            for b in person.within(soon, 'manage_bookings').select_related('category', 'course')
        ]
        asked = person.within(Booking.objects.filter(requested=True, end__gte=timezone.now()), 'manage_bookings').order_by('start')
        if first := asked.first():
            found.append(task(f'{plural(asked.count(), "Raumanfrage", "Raumanfragen")} entscheiden', reverse('rooms:requests'), timezone.localdate(first.start)))
        return found
