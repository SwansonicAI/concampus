from django.conf import settings
from django.core.management.base import BaseCommand

from courses.moodle import sync


class Command(BaseCommand):
    help = "Bring the Hochschule's Moodle up to Campus: courses, teachers and participants (docs/moodle.md). Run nightly."

    def handle(self, *args, **options):
        if not settings.MOODLE['url']:
            self.stdout.write('Kein Moodle eingerichtet (MOODLE_URL, MOODLE_TOKEN).')
            return
        self.stdout.write(', '.join(f'{n} {what}' for what, n in sync(accounts=bool(settings.DEMO)).items()) + '.')
