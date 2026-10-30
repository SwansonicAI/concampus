from django.apps import AppConfig
from django.urls import reverse

from core.apps import Tile, feature, plural, task


class MobilityConfig(Tile, AppConfig):
    name = 'mobility'
    verbose_name = 'Internationales'
    order = 8
    icon = 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20'
    url = 'mobility:index'

    def title(self, roles):
        if 'manage_mobility' in roles:
            return 'Internationales'

    def figure(self, person):
        from django.utils import timezone

        from .models import Mobility

        today = timezone.localdate()
        abroad = Mobility.objects.filter(status=Mobility.Status.ACCEPTED, start__lte=today, end__gte=today).count()
        return f'{abroad} im Ausland'

    def features(self, person):
        from .models import Mobility

        found = []
        if person.everywhere('manage_mobility'):
            found += [
                feature('Auslandsaufenthalte', reverse('mobility:index'), 'erasmus outgoing learning agreement auslandssemester mobilität'),
                feature('Partnerhochschulen und Abkommen', reverse('mobility:partners'), 'erasmus partner abkommen iia inter-institutional agreement plätze'),
            ]
        if Mobility.objects.filter(study__person=person).exists():
            found.append(feature('Mein Auslandsaufenthalt', reverse('mobility:mine'), 'erasmus learning agreement auslandssemester partnerhochschule'))
        return found

    def tasks(self, person):
        from .models import Mobility
        from .views import unrecognised, waiting

        found = []
        if n := waiting(person).count():
            found.append(task(f'{plural(n, "Learning Agreement", "Learning Agreements")} genehmigen', f"{reverse('mobility:index')}?stand=nominiert"))
        for m in unrecognised(person):
            found.append(task(f'Anerkennen: {m.study.person}, zurück von der {m.partner}', m.get_absolute_url()))
        for m in Mobility.objects.filter(study__person=person, signed_student=None, status__in=[Mobility.Status.NOMINATED, Mobility.Status.ACCEPTED]).select_related('partner'):
            found.append(task(f'Learning Agreement für die {m.partner} ausfüllen und unterschreiben', m.get_absolute_url(), m.start))
        return found
