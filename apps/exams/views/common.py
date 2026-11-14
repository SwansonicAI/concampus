"""Helpers several exam pages share: the kinds of exams, theses handed in, exams one may register for."""
from django.db.models import Min, Q
from django.utils import timezone

from core.models import Term
from courses.models import Course, Participation
from curriculum.models import Module
from students.models import Study
from ..results import exhausted

EXAMS =[Course.Kind.EXAM, Course.Kind.RECOGNITION, Course.Kind.THESIS]
STATES = [('offen', 'offen'), ('bewertung', 'in Bewertung'), ('freigegeben', 'freigegeben')]


def handed_in(participation):
    """The file handed in for a thesis: the latest since it was registered."""
    return participation.study.documents.filter(kind='abschlussarbeit', created__gte=participation.created).last()


def offered(person):
    """Exams of this term the person may still register for: their programme's modules, due by now, not yet passed,
    none under a Schwerpunkt other than the one chosen; artistic exams held per Hauptfach only in their own."""
    studies = person.studies.filter(status=Study.Status.ENROLLED)
    studies = studies.exclude(pk__in=[s.pk for s in studies if exhausted(s)])  # the Prüfungsanspruch lost: nothing new to take
    passed = Participation.objects.filter(study__in=studies, passed=True, module__isnull=False).values('module')  # NOT IN with a NULL matches nothing
    semester = max((s.semester for s in studies), default=0)
    elsewhere = {pk for s in studies.select_related('program') if s.focus_id for pk, option in s.program.chosen_by().items() if option.pk != s.focus_id}
    return (  # with those whose registration opens later in the term
        Course.objects.filter(term=Term.current(), kind=Course.Kind.EXAM, deadline__gte=timezone.localdate())
        .filter(modules__in=Module.objects.filter(program__studies__in=studies).exclude(pk__in=elsewhere))
        .filter(Q(subject=None) | Q(subject__in=studies.values('subject')))
        .filter(modules__semester__lte=semester).exclude(participations__study__in=studies).exclude(modules__in=passed)
        .annotate(date=Min('bookings__start')).distinct()
    )
