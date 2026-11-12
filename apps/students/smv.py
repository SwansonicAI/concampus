"""The Studierendenmeldeverfahren inside ConCampus (§ 199a SGB V, docs/smv.md): the insurers' reports booked to the people
they are about, the Hochschule's own made from the list of status changes. Sending and fetching them through dakota
comes with the GKV's XML schemata; until then the Hochschule's wait as open."""
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from core import history
from core.models import Person, Term
from core.text import fold
from .models import Event, InsuranceReport, Study

Reason, State = InsuranceReport.Reason, InsuranceReport.State
OURS = [Study.Status.APPLIED, Study.Status.WAITLISTED, Study.Status.ADMITTED, Study.Status.ENROLLED]  # whom an insurer may report on


def first(name):
    return fold(name).split()[:1]


def surnames(name):
    return set(fold(name).replace('-', ' ').split())


def candidates(report):
    """Who an insurer's report may be about: people applying or studying here born that day, with its first name or a
    part of its surname."""
    people = Person.objects.filter(birth_date=report.birth_date, studies__status__in=OURS).distinct()
    return [p for p in people if first(p.first_name) == first(report.first_name) or surnames(report.last_name) & surnames(f'{p.last_name} {p.birth_name}')]


def match(report):
    """The one person an insurer's report is about: born that day, with its first name and its surname or birth name."""
    found = [
        p for p in Person.objects.filter(birth_date=report.birth_date, studies__status__in=OURS).distinct()
        if first(p.first_name) == first(report.first_name) and fold(report.last_name) in {fold(p.last_name), fold(p.birth_name)}
    ]
    return found[0] if len(found) == 1 else None


def receive(**fields):
    """An insurer's report as dakota hands it over: booked to its person when found, else left for the student office."""
    report = InsuranceReport.objects.create(**fields)
    if person := match(report):
        book(report, person)
    return report


def book(report, person, by=None):
    """Book an insurer's report to `person`: their insurer and status (M10, M11), arrears (M12) and their payment (M13).
    After a change of insurer the new one hears that they study here, from the day it insures them."""
    with transaction.atomic(), history.because(f'{report.get_reason_display()} der {report.insurer}'):
        report.person, report.state, report.done, report.by = person, State.DONE, timezone.now(), by
        report.save(update_fields=['person', 'state', 'done', 'by'])
        if report.reason in (Reason.STATUS, Reason.CHANGE):
            insured = report.insurance != InsuranceReport.UNINSURED
            person.insurer, person.insurer_number = report.insurer, report.insurer_number
            person.insurance = report.insurance if insured else ''
            person.insurance_confirmed = timezone.localdate(report.created) if insured else None
        else:
            person.insurance_arrears = timezone.localdate(report.created) if report.reason == Reason.ARREARS else None
        person.save(update_fields=['insurer', 'insurer_number', 'insurance', 'insurance_confirmed', 'insurance_arrears'])
        studying = person.studies.filter(status=Study.Status.ENROLLED)
        if report.reason == Reason.CHANGE and person.insurance == Person.Insurance.STATUTORY and studying.exists():
            InsuranceReport.objects.create(
                reason=Reason.START, person=person, insurer=person.insurer, insurer_number=person.insurer_number,
                day=studying.aggregate(Min('since'))['since__min'], term=Term.objects.filter(start__lte=report.day).order_by('-start').first(), due=report.day,
            )


def tell(event):
    """The Hochschule's report of a status change to the person's insurer: M20 on Immatrikulation, M30 on Exmatrikulation.
    None for someone exempt from the student insurance, or without an insurer's report to address it to."""
    person = event.person
    if person.insurance != Person.Insurance.STATUTORY or not person.insurer_number:
        return None
    return InsuranceReport.objects.create(
        reason=Reason.START if event.kind == Event.Kind.ENROLLED else Reason.END, person=person, event=event,
        insurer=person.insurer, insurer_number=person.insurer_number, day=event.on, term=event.term, due=timezone.localdate(),
    )


def again(report, by):
    """One of the Hochschule's reports that the insurer turned down, made anew once the student office has put right
    what was wrong, to the insurer the person's record now names."""
    person = report.person
    report.state = State.DROPPED
    report.save(update_fields=['state'])
    return InsuranceReport.objects.create(
        reason=report.reason, person=person, event=report.event, insurer=person.insurer, insurer_number=person.insurer_number,
        day=report.day, term=report.term, due=timezone.localdate(), by=by,
    )


def cancel(report, by):
    """Withdraw one of the Hochschule's reports: unsent it is dropped, sent a cancellation (Storno) follows it."""
    if report.state == State.OPEN:
        report.state, report.by = State.DROPPED, by
        report.save(update_fields=['state', 'by'])
        return report
    return InsuranceReport.objects.create(
        reason=report.reason, cancels=report, person=report.person, insurer=report.insurer, insurer_number=report.insurer_number,
        day=report.day, term=report.term, due=timezone.localdate(), by=by,
    )
