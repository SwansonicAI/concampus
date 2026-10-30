from django.conf import settings
from django.core.management.base import BaseCommand

from courses.moodle import sync


class Command(BaseCommand):
    help = "Bring the Hochschule's Moodle up to ConCampus: courses, teachers and participants (docs/moodle.md). Run each day; with --changed each minute."

    def add_arguments(self, parser):
        parser.add_argument('--changed', action='store_true', help='Only the courses changed since they were last sent; run each minute.')

    def handle(self, *args, **options):
        if not settings.MOODLE['url']:
            if not options['changed']:
                self.stdout.write('Kein Moodle eingerichtet (MOODLE_URL, MOODLE_TOKEN).')
            return
        counts = sync(accounts=bool(settings.DEMO), changed=options['changed'])
        if counts['Kurse'] or not options['changed']:  # each minute, quiet while nothing changed
            self.stdout.write(', '.join(f'{n} {what}' for what, n in counts.items()) + '.')
