from django.apps import AppConfig
from django.urls import reverse
from django.utils.formats import number_format

from core.apps import Tile, feature


class AnalyticsConfig(Tile, AppConfig):
    name = 'analytics'
    verbose_name = 'Auswertungen'
    order = 8
    icon = 'M3 21h18M6 17V11M11 17V5M16 17v-8M21 17V8'
    url = 'analytics:index'

    def title(self, roles):
        if 'view_statistics' in roles:
            return 'Auswertungen'

    def figure(self, person):
        from core.models import Term
        from .figures import MEASURES, semesters, tally

        now = Term.current()
        if now is None:
            return ''
        found = f'{number_format(tally(MEASURES["studierende"], (), [now]), 0, force_grouping=True)} Studierende'
        rounds = [t for t in semesters(MEASURES['bewerbungen']) if t.start <= now.start]
        if len(rounds) > 2 and (before := tally(MEASURES['bewerbungen'], (), [rounds[-3]])):
            change = round(100 * (tally(MEASURES['bewerbungen'], (), [rounds[-1]]) - before) / before)
            found += f' · Bewerbungen {change:+d} % zum Vorjahr'
        return found

    def features(self, person):
        found = [
            feature('Amtliche Statistik', reverse('analytics:official'), 'statistisches landesamt destatis studierendenstatistik prüfungsstatistik lieferung estatistik'),
        ] if 'report_statistics' in person.roles else []
        if not self.title(person.roles):
            return found
        from .charts import Query
        from .figures import MEASURES

        return found + [
            feature('Auswertungen', reverse('analytics:index'), 'statistik kennzahlen überblick zahlen entwicklung trend diagramm bericht'),
            feature('Abfrage', reverse('analytics:query'), 'auswertung statistik zählen kreuztabelle pivot export csv'),
            *(feature(f'{m.title} auswerten', Query(m, [m.default]).url(), f'statistik zahlen {m.title.lower()}') for m in MEASURES.values()),
        ]
