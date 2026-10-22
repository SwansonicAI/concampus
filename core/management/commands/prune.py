from django.core.management.base import BaseCommand
from django.utils import timezone

from core.history import tracked
from core.models import Change


class Command(BaseCommand):
    help = 'Delete changes kept longer than their records ask (Tracked.keep). Run nightly.'

    def handle(self, *args, **options):
        now = timezone.now()
        deleted = sum(
            Change.objects.filter(table=model._meta.label_lower, at__lt=now - model.keep).delete()[0]
            for model in tracked() if model.keep
        )
        self.stdout.write(f'{deleted} Änderungen gelöscht.')
