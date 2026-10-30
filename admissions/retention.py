"""What becomes of applications that came to nothing: once the Hochschule's period is over, their documents are deleted and
whoever only applied is made anonymous. The statistics keep counting them; nobody can be named any more."""
from django.db.models import Q

from core.dates import months_before
from core.models import Change, Consent, Message, Person, SignIn
from courses.models import Participation
from students.models import Document, InsuranceReport, Study


def ended(cutoff):
    """Applications that never led to studying here and ended before `cutoff`: turned down, or the place declined or not
    taken up, when decided; left on the Nachrückliste, when its semester began, as places go down the list until then."""
    return Study.objects.filter(
        Q(status=Study.Status.REJECTED, since__lt=cutoff) | Q(status=Study.Status.LEFT, terms=None, since__lt=cutoff)
        | Q(status=Study.Status.WAITLISTED, start_term__start__lt=cutoff)
    )


def forget(cutoff):
    """Delete health data once its application is decided, the documents of applications that ended before `cutoff`, and
    make anonymous everyone who has nothing else here: no other study, no teaching, no office. Returns (documents deleted,
    people made anonymous)."""
    gone = ended(cutoff)
    documents = Document.objects.filter(Q(study__in=gone) | Q(kind__health=True) & ~Q(study__status=Study.Status.APPLIED))
    deleted = Document.purge(documents)
    people = (
        Person.objects.filter(studies__in=gone).exclude(studies__in=Study.objects.exclude(pk__in=gone))
        .filter(groups=None, teaching=None, institute=None, is_superuser=False).exclude(username__startswith='anonym-').distinct()
    )
    ids = list(people.values_list('pk', flat=True))
    Message.objects.filter(person__in=ids).delete()
    Change.objects.filter(person__in=ids).delete()  # what the history kept about them
    Consent.objects.filter(person__in=ids).delete()
    SignIn.objects.filter(person__in=ids).delete()
    InsuranceReport.objects.filter(person__in=ids).delete()
    Participation.objects.filter(study__person__in=ids).update(note='')
    Person.anonymise(ids)
    return deleted, len(ids)
