from django.apps import AppConfig
from django.urls import reverse

from core.apps import Tile, feature


class CurriculumConfig(Tile, AppConfig):
    name = 'curriculum'
    verbose_name = 'Studiengänge'
    order = 6
    icon = 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5'
    url = 'curriculum:index'

    def title(self, roles):
        if roles & {'view_curriculum', 'teacher'}:
            return 'Studiengänge'

    def features(self, person):
        if not self.title(person.roles):
            return []
        return [feature('Studiengänge', reverse('curriculum:index'), 'studienordnung svpp studienverlaufsplan modulhandbuch module ects fpso')]

    def figure(self, person):
        from .models import Module, Program

        return f'{Program.objects.count()} Studiengänge · {Module.objects.count()} Module'
