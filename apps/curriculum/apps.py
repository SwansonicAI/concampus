from django.apps import AppConfig
from django.urls import reverse

from core.apps import Tile, feature

OVERSIGHT = {'view_curriculum', 'teacher'}  # the tile, and how many study and apply; every Studienplan is open to all


class CurriculumConfig(Tile, AppConfig):
    name = 'curriculum'
    verbose_name = 'Studiengänge'
    order = 6
    icon = 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5'
    url = 'curriculum:index'

    def title(self, roles):
        if roles & OVERSIGHT:
            return 'Studiengänge'

    def features(self, person):
        found = [feature('Studiengänge', reverse('curriculum:index'), 'studienplan studienordnung svpp studienverlaufsplan modulhandbuch module ects fpso')]
        for study in person.studies.filter(status='enrolled').select_related('program', 'subject'):
            found.append(feature(f'Mein Studienplan {study.subject}', study.program.get_absolute_url(), 'studienverlaufsplan svpp module ects'))
        return found

    def figure(self, person):
        from .models import Module, Program

        return f'{Program.objects.count()} Studiengänge · {Module.counted().count()} Module'
