import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from core.models import Export
from core.transfer import dump, load


class Command(BaseCommand):
    help = 'Load a ConCampus document (docs/transfer.md) into the database, all or nothing; with --export write one of everything.'

    def add_arguments(self, parser):
        parser.add_argument('file')
        parser.add_argument('--export', action='store_true', help='Write every Semester, Hauptfach, Studiengang and person to the file.')

    def handle(self, file, export, **options):
        if export:
            document = dump()
            with open(file, 'w', encoding='utf-8', newline='\n') as out:
                json.dump(document, out, cls=DjangoJSONEncoder, ensure_ascii=False, indent=1)
            Export.log(None, f'Gesamtexport in {Path(file).name}', len(document['people']))
            self.stdout.write(f'{len(document["people"])} Personen, {len(document["programs"])} Studiengänge nach {file} geschrieben.')
            return
        try:
            counts = load(json.loads(Path(file).read_text(encoding='utf-8')))
        except ValueError as error:
            raise CommandError(f'Nichts geladen:\n{error}')
        self.stdout.write(', '.join(f'{n} {what}' for what, n in counts.items()) + ' geladen.')
