from django.apps import AppConfig
from django.urls import reverse
from django.utils.formats import number_format

from core.apps import Tile, feature


class RoomsConfig(Tile, AppConfig):
    name = 'rooms'
    verbose_name = 'Räume'
    order = 5
    icon = 'M3 4h18v18H3zM16 2v4M8 2v4M3 10h18'
    url = 'rooms:index'

    def title(self, roles):
        if roles - {'applicant'}:
            return 'Räume'

    def features(self, person):
        if not self.title(person.roles):
            return []
        return [
            feature('Raumplan', reverse('rooms:index'), 'belegung räume heute kalender tagesplan institut gebäude'),
            feature('Raum finden', reverse('rooms:find'), 'frei freier raum suchen verfügbar'),
            feature('Raum buchen', reverse('rooms:new'), 'buchung reservieren üben überaum kammermusik probe'),
            feature('Meine Buchungen', reverse('rooms:mine'), 'stornieren übezeit kontingent'),
            feature('Veranstaltungen', reverse('rooms:events'), 'konzert klassenabend meisterkurs vortrag programm'),
            feature('Kalender abonnieren', f"{reverse('rooms:mine')}#kalender", 'ical ics outlook google apple handy feed'),
        ]

    def figure(self, person):
        from django.utils import timezone

        from .forms import STUDENT_DAILY, quota
        from .models import Booking
        from .views import agenda

        today = timezone.localdate()
        if 'Veranstaltungsbüro' in person.roles:
            return f'{Booking.objects.filter(start__date=today).count()} Buchungen heute'
        if 'student' in person.roles:
            return f'Heute noch {number_format(STUDENT_DAILY - quota(person, today))} von {STUDENT_DAILY} Std. Übezeit'
        return f'{agenda(person).filter(start__date=today).count()} Termine heute'
