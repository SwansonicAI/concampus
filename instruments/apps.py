from datetime import timedelta

from django.apps import AppConfig
from django.urls import reverse
from django.utils import timezone

from core.apps import Tile, feature, plural, task


class InstrumentsConfig(Tile, AppConfig):
    name = 'instruments'
    verbose_name = 'Instrumente'
    order = 7
    icon = 'M21 16V8l-9-5-9 5v8l9 5 9-5zM3.3 7 12 12l8.7-5M12 22V12'
    url = 'instruments:index'

    def title(self, roles):
        if 'manage_instruments' in roles:
            return 'Instrumente'

    def figure(self, person):
        from .models import Loan

        out = person.within(Loan.objects.filter(returned=None), 'manage_instruments')
        late = out.filter(due__lt=timezone.localdate()).count()
        return f'{out.count()} verliehen' + (f' · {late} überfällig' if late else '')

    def features(self, person):
        found = []
        if 'manage_instruments' in person.roles:
            found.append(feature('Leihinstrumente', reverse('instruments:index'), 'instrumentenpool verleihen ausleihe rückgabe inventar'))
        if person.loans.exists():
            found.append(feature('Meine Leihinstrumente', reverse('instruments:mine'), 'instrument ausgeliehen rückgabe'))
        return found

    def tasks(self, person):
        from .models import Loan

        today, found = timezone.localdate(), []
        if 'manage_instruments' in person.roles:
            late = person.within(Loan.objects.filter(returned=None, due__lt=today), 'manage_instruments').count()
            if late:
                found.append(task(f'{plural(late, "Leihinstrument", "Leihinstrumente")} überfällig', f"{reverse('instruments:index')}?stand=ueberfaellig"))
        for loan in person.loans.filter(returned=None, due__lte=today + timedelta(days=14)).select_related('instrument'):
            found.append(task(f'{loan.instrument.name} zurückgeben', reverse('instruments:mine'), loan.due))
        return found
