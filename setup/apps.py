from django.apps import AppConfig
from django.urls import reverse

from core.apps import Tile, feature, plural, task


class SetupConfig(Tile, AppConfig):
    name = 'setup'
    verbose_name = 'Einstellungen'
    order = 9
    icon = 'M4 6h16M4 12h16M4 18h16M8 4v4M16 10v4M10 16v4'
    url = 'setup:index'

    def title(self, roles):
        from .editors import EDITORS

        if 'manage_institution' in roles or any(e.section and e.capability in roles for e in EDITORS.values()):
            return 'Einstellungen'

    def figure(self, person):
        from .views import usable

        return f'{plural(len(usable(person)), "Liste", "Listen")} zu pflegen'

    def features(self, person):
        from .views import usable

        found = [feature(e.title, reverse('setup:list', args=[e.key]), f'einstellungen pflegen anlegen {e.words}') for e in usable(person)]
        if 'manage_institution' in person.roles:
            found.append(feature('Texte', reverse('setup:texts'), 'einstellungen bescheide nachrichten briefe vorlagen formulierung rechtsbehelfsbelehrung'))
        return found

    def tasks(self, person):
        from core.models import Term

        if 'manage_terms' in person.roles and not Term.upcoming():
            return [task('Nächstes Semester anlegen', reverse('setup:new', args=['semester']))]
        return []
