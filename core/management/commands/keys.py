import tempfile
import urllib.request

from django.core.management.base import BaseCommand

from core import keys


class Command(BaseCommand):
    help = (
        'Bring the official key lists up to the editions shipped with this version (core/data), or to an edition of the '
        'Gemeindeverzeichnis given as a file or the address Destatis publishes it at. Run with every deploy; what is in already stays.'
    )

    def add_arguments(self, parser):
        parser.add_argument('gemeindeverzeichnis', nargs='?', help='GV-ISys, „Alle politisch selbständigen Gemeinden“ (xlsx): a file or its address')

    def handle(self, gemeindeverzeichnis=None, **options):
        if gemeindeverzeichnis is None:
            editions = keys.bundled()
        elif gemeindeverzeichnis.startswith('https://'):
            with tempfile.NamedTemporaryFile(suffix='.xlsx') as file:
                file.write(urllib.request.urlopen(gemeindeverzeichnis, timeout=60).read())
                file.flush()
                editions = keys.gemeindeverzeichnis(file.name, source=gemeindeverzeichnis)
        else:
            editions = keys.gemeindeverzeichnis(gemeindeverzeichnis)
        for e in editions:
            self.stdout.write(f'{e}: {e.added} neu, {e.renamed} geändert, {e.ended} beendet.')
            if e.notes:
                self.stdout.write(e.notes)
        if not editions:
            self.stdout.write('Alle Verzeichnisse sind auf dem Stand.')
