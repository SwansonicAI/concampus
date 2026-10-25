"""What becomes of applications that came to nothing: once the Hochschule's period is over, their documents are deleted and
whoever only applied is made anonymous. The statistics keep counting them; nobody can be named any more."""
from calendar import monthrange

from django.db.models import CharField, Q, Value
from django.db.models.functions import Cast, Concat

from core.models import Change, Message, Person
from courses.models import Participation
from students.models import Document, Study


def months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, monthrange(year, month + 1)[1]))


def ended(cutoff):
    """Applications that never led to studying here and ended before `cutoff`: turned down, or the place declined or not
    taken up, when decided; left on the Nachrückliste, when its semester began, as places go down the list until then."""
    return Study.objects.filter(
        Q(status=Study.Status.REJECTED, since__lt=cutoff) | Q(status=Study.Status.LEFT, terms=None, since__lt=cutoff)
        | Q(status=Study.Status.WAITLISTED, start_term__start__lt=cutoff)
    )


def forget(cutoff):
    """Delete the documents of applications that ended before `cutoff`, and make anonymous everyone who has nothing else
    here: no other study, no teaching, no office. Returns (documents deleted, people made anonymous)."""
    gone = ended(cutoff)
    documents = Document.objects.filter(study__in=gone)
    for d in documents.exclude(file=''):
        d.file.delete(save=False)
    deleted = documents.delete()[0]
    people = (
        Person.objects.filter(studies__in=gone).exclude(studies__in=Study.objects.exclude(pk__in=gone))
        .filter(groups=None, teaching=None, institute=None, is_superuser=False).exclude(username__startswith='anonym-').distinct()
    )
    ids = list(people.values_list('pk', flat=True))
    Message.objects.filter(person__in=ids).delete()
    Change.objects.filter(person__in=ids).delete()  # what the history kept about them
    Participation.objects.filter(study__person__in=ids).update(note='')
    Person.objects.filter(pk__in=ids).update(  # gender and nationality stay for the statistics
        username=Concat(Value('anonym-'), Cast('pk', CharField())), first_name='', last_name='Anonym', title='', given_names='', birth_name='',
        email='', birth_date=None, birth_place='', second_nationality='', address='', entrance_date=None, entrance_place='', insurer='', insurance='',
        insurance_confirmed=None, hold='', password='!', is_active=False,
    )
    return deleted, len(ids)
