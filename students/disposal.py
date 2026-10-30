"""What becomes of a former student's file (ThürArchivG, Thüringer Aufbewahrungsrichtlinie): kept `file_years` after the
last Exmatrikulation, then offered to the archive; once the archive has assessed it, and taken over what it keeps, cut
down to the Grunddaten, which answer Studienzeitbescheinigungen and Zweitschriften; after `core_years` made anonymous.
The statistics keep counting them."""
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from core.dates import months_before
from core.history import quietly
from core.models import Change, Consent, Institution, Message, Person, SignIn
from courses.models import Ensemble, Participation, Recusal, Score
from instruments.models import Loan
from rooms.models import Booking
from .models import Disposal, Document, Event, InsuranceReport, Study, StudyTerm

CORE = {  # the Grunddaten: who it was, and through the studies kept with them, when and what they studied and passed
    'username', 'title', 'first_name', 'last_name', 'given_names', 'birth_name', 'birth_date', 'birth_place', 'matriculation', 'date_joined',
} | Person.COUNTED


def ended(years):
    """People whose every study here ended at least `years` ago, with nothing else tying them here: no study open, no
    office, no teaching."""
    cutoff = months_before(timezone.localdate(), years * 12)
    later = Study.objects.filter(person=OuterRef('pk')).filter(~Q(status__in=[*Study.FORMER, Study.Status.REJECTED]) | Q(since__gte=cutoff))
    return (
        Person.objects.filter(studies__status__in=Study.FORMER, studies__terms__isnull=False, groups=None, teaching=None, institute=None, is_superuser=False)
        .exclude(Exists(later)).distinct()
    )


def due():
    """Files kept long enough and not yet offered to the archive."""
    return ended(Institution.get().file_years).filter(disposal=None)


def offer(people, by):
    """Offer these files to the archive; returns how many."""
    return len(Disposal.objects.bulk_create(Disposal(person=p, offered_by=by) for p in people))


def assess(people, archival, by):
    """The archive's assessment of files offered to it: archival ones it has taken over as a copy."""
    return Disposal.objects.filter(person__in=people, assessed=None).update(archival=archival, assessed=timezone.localdate(), assessed_by=by)


def reduce():
    """Cut assessed files down to the Grunddaten; everything else goes: documents, messages, the Verlauf, consents,
    sign-ins, loans and bookings, stays abroad, notes and Gutachten, grounds of leave, a Nachteilsausgleich. Returns how many."""
    from mobility.models import Mobility  # an app that builds on this one

    ids = list(Person.objects.filter(disposal__assessed__isnull=False, disposal__reduced=None).values_list('pk', flat=True))
    with quietly():
        Mobility.objects.filter(study__person__in=ids).delete()  # the results recognised from a stay abroad stay
        Document.purge(Document.objects.filter(person__in=ids))
        for model in (Message, Consent, SignIn, Loan, Booking, Event, InsuranceReport):
            model.objects.filter(person__in=ids).delete()
        for through in (Booking.participants.through, Booking.absent.through):
            through.objects.filter(person__in=ids).delete()
        Recusal.objects.filter(participation__study__person__in=ids).delete()
        Ensemble.objects.filter(wish__in=ids).update(wish=None)
        Score.objects.filter(participation__study__person__in=ids).update(report='')
        Participation.objects.filter(study__person__in=ids).update(note='')
        StudyTerm.objects.filter(study__person__in=ids).update(leave_reason='')
        Study.objects.filter(person__in=ids).update(compensation='')
        Change.objects.filter(person__in=ids).delete()
        Person.clear(ids, CORE)
    Disposal.objects.filter(person__in=ids).update(reduced=timezone.localdate())
    return len(ids)


def expire():
    """Make anonymous whoever's Grunddaten are kept long enough; returns how many."""
    ids = list(ended(Institution.get().core_years).filter(disposal__reduced__isnull=False).exclude(username__startswith='anonym-').values_list('pk', flat=True))
    Person.anonymise(ids)
    return len(ids)
