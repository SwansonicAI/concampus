"""What a person owes at the Rückmeldung beyond the Semesterbeitrag: the fees the Hochschule's Gebührenordnung sets per
status and Studiengang (Fee), and the Langzeitstudiengebühr where the Land charges one (Institution.long_fee), once the
Regelstudienzeit is exceeded by more than the semesters it allows. Each, as a Hochschule likes, in its settings; none
by default. An exemption granted on application (StudyTerm.waived) lifts them for a semester."""
from core.models import Institution
from .models import Fee, Study, StudyTerm


def counted(person, term):
    """The semesters that count towards the Langzeitstudiengebühr before `term`: those elsewhere, and those here without
    leave, a semester in Teilzeit in every study as half."""
    halves = {}
    for t, part in StudyTerm.objects.filter(study__person=person, withdrawn=False, leave=False, term__start__lt=term.start).values_list('term', 'part_time'):
        halves[t] = min(halves.get(t, 2), 1 if part else 2)  # full time in any study: a whole semester
    return person.external_terms + sum(halves.values()) / 2


def extras(person, term):
    """[(name, amount)] that `person` owes for `term` beyond the Semesterbeitrag."""
    if StudyTerm.objects.filter(study__person=person, term=term, waived=True).exists():
        return []
    studies = list(person.studies.filter(status__in=Study.ATTENDING).select_related('program'))
    found = [
        (f.name, f.amount) for f in Fee.objects.filter(who__in={s.status for s in studies}).prefetch_related('programs')
        if any(f.who == s.status and (not f.programs.all() or s.program in f.programs.all()) for s in studies)
    ]
    institution, enrolled = Institution.get(), [s for s in studies if s.status == Study.Status.ENROLLED]
    if institution.long_fee and enrolled and counted(person, term) >= max(s.program.semesters for s in enrolled) + institution.long_fee_grace:
        found.append(('Langzeitstudiengebühr', institution.long_fee))
    return found


def owed(person, term, day, late_fee):
    """All `person` owes for `term`, paid on `day`: the Semesterbeitrag, the Säumnisgebühr where late, and the rest."""
    return term.due(day, late_fee) + sum(amount for _, amount in extras(person, term))
