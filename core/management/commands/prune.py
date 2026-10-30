from django.core.management.base import BaseCommand
from django.utils import timezone

from admissions.retention import forget
from core.dates import months_before
from core.history import tracked
from core.models import Change, Feedback, Institution, SignIn
from students.disposal import expire, reduce


class Command(BaseCommand):
    help = 'Delete what is kept longer than allowed: changes past Tracked.keep, sign-ins past SignIn.KEEP, applications that came to nothing past the Hochschule\'s period, and student files the archive has assessed. Run nightly.'

    def handle(self, *args, **options):
        now = timezone.now()
        deleted = sum(
            Change.objects.filter(table=model._meta.label_lower, at__lt=now - model.keep).delete()[0]
            for model in tracked() if model.keep
        )
        self.stdout.write(f'{deleted} Änderungen gelöscht.')
        self.stdout.write(f'{SignIn.objects.filter(at__lt=now - SignIn.KEEP).delete()[0]} Anmeldungen gelöscht.')
        self.stdout.write(f'{Feedback.objects.filter(answered__lt=timezone.localdate() - Feedback.KEEP).delete()[0]} Rückmeldungen zur Barrierefreiheit gelöscht.')
        if months := Institution.get().retention_months:
            documents, people = forget(months_before(timezone.localdate(), months))
            self.stdout.write(f'{documents} Unterlagen gelöscht, {people} Personen anonymisiert.')
        self.stdout.write(f'{reduce()} Studierendenakten auf die Grunddaten gekürzt, {expire()} Grunddaten anonymisiert.')
