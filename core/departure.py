"""What becomes of the record of someone who worked here (docs/privacy.md): the day their account is deactivated is kept
(`Person.left`); `staff_years` after it the record is cut down to the name, which stays on the courses they taught and
the exams they examined; after `core_years`, as with former students' Grunddaten, it is made anonymous. Nothing of
someone whose processing is restricted (Art. 18 DSGVO)."""
from django.db.models import Exists, OuterRef
from django.utils import timezone

from courses.models import Reduction
from instruments.models import Loan
from rooms.models import Booking
from students.models import Study
from .dates import months_before
from .history import quietly
from .models import Change, Consent, Institution, Membership, Message, Person, SignIn

KEPT = {'username', 'title', 'first_name', 'last_name', 'left', 'date_joined'} | Person.COUNTED


def gone(years):
    """Former staff whose account was deactivated at least `years` ago, with no study open here."""
    open_study = Study.objects.filter(person=OuterRef('pk')).exclude(status__in=[*Study.FORMER, Study.Status.REJECTED])
    return Person.objects.filter(is_active=False, left__lte=months_before(timezone.localdate(), years * 12), restriction='', is_superuser=False).exclude(Exists(open_study))


def reduce():
    """Cut their records down to the name; their Postfach, consents, sign-ins, bookings, loans, offices, Ermäßigungen and
    Verlauf go. Returns how many."""
    ids = list(gone(Institution.get().staff_years).exclude(password='!').values_list('pk', flat=True))  # '!': cut down already (Person.clear)
    with quietly():
        for model in (Message, Consent, SignIn, Booking, Loan, Membership, Reduction, Change):
            model.objects.filter(person__in=ids).delete()
        for through in (Booking.participants.through, Booking.absent.through, Person.groups.through):
            through.objects.filter(person__in=ids).delete()
        Person.clear(ids, KEPT)
    return len(ids)


def expire():
    """Make anonymous whoever's name is kept long enough; returns how many."""
    ids = list(gone(Institution.get().core_years).exclude(username__startswith='anonym-').values_list('pk', flat=True))
    Person.anonymise(ids)
    return len(ids)
