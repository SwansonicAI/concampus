from django.core.management.base import BaseCommand
from django.utils import timezone

from admissions.retention import forget
from core.dates import months_before
from core.history import tracked
from core.models import Change, Institution


class Command(BaseCommand):
    help = 'Delete what is kept longer than allowed: changes past Tracked.keep, and applications that came to nothing past the Hochschule\'s period. Run nightly.'

    def handle(self, *args, **options):
        now = timezone.now()
        deleted = sum(
            Change.objects.filter(table=model._meta.label_lower, at__lt=now - model.keep).delete()[0]
            for model in tracked() if model.keep
        )
        self.stdout.write(f'{deleted} Änderungen gelöscht.')
        if months := Institution.get().retention_months:
            documents, people = forget(months_before(timezone.localdate(), months))
            self.stdout.write(f'{documents} Unterlagen gelöscht, {people} Personen anonymisiert.')
