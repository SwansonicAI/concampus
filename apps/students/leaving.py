from django.utils import timezone

from core.models import Office
from exams.results import achievements


def leaving(study):
    """The Laufzettel: what is still open before someone leaves, each with the offices it is for."""
    found = []
    if study.issued is None and achievements(study)['finished']:
        found.append(('Zeugnis noch nicht ausgestellt', Office.holding('decide_results', study.program)))
    for loan in study.person.loans.filter(returned=None).select_related('instrument'):
        found.append((f'Leihinstrument zurückgeben: {loan.instrument.name}', Office.holding('manage_instruments')))
    if unpaid := study.terms.filter(paid=None, term__start__lte=timezone.localdate()).exclude(leave=True).select_related('term').first():
        found.append((f'Semesterbeitrag {unpaid.term} offen', Office.holding('manage_reregistration')))
    if study.person.hold:
        found.append((f'Sperre: {study.person.hold}', Office.holding('manage_students')))
    return found
