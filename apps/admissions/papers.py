"""What an application has to hold, as the document types say, and what of it is still missing."""
from collections import defaultdict

from django.db.models import F, Prefetch
from django.utils import timezone

from core.models import once
from courses.models import Course, Participation
from instruments.models import Loan
from students.models import DocumentType

Condition = DocumentType.Condition


def stages():
    """Audition participations in stage order: video round, Hauptfach, shared parts."""
    return (
        Participation.objects.filter(course__kind=Course.Kind.AUDITION).select_related('course')
        .order_by(F('course__deadline').asc(nulls_last=True), F('course__subject').asc(nulls_last=True), 'course__title')
    )


def document_types():
    """The kinds of documents an application can ask for, each with its Hauptfächer; fetch once for many studies."""
    return once('document_types', lambda: list(DocumentType.objects.exclude(condition=Condition.NEVER).prefetch_related('subjects')))


def papers(studies):
    """The studies with what Study.missing() and checklist() read, fetched at once for many: loans still out, documents,
    the person's other studies, the registrations, the audition stages."""
    return studies.select_related('person', 'program').prefetch_related(
        Prefetch('person__loans', Loan.objects.filter(returned=None).select_related('instrument')), 'person__documents', 'person__studies', 'terms',
        Prefetch('participations', stages(), to_attr='stages'),
    )


def checklist(study, types=None):
    """What a complete application holds, as the document types say: [{kind, label, note, required, documents}]. What
    holds for all of a person's applications, like a CV, counts once handed in with any of them. Reads
    `study.person.documents.all()` and `study.person.studies.all()`; prefetch them for many studies."""
    person, handed, borrowed, today = study.person, defaultdict(list), defaultdict(list), timezone.localdate()
    same = {s.pk for s in person.studies.all() if s.subject_id == study.subject_id and s.pk != study.pk}  # the same Hauptfach elsewhere
    types = document_types() if types is None else types
    shared = {t.key for t in types if t.shared}
    for d in person.documents.all():
        if d.study_id == study.pk:
            handed[d.kind_id].append(d)
        elif d.study_id and d.kind_id in shared:
            borrowed[d.kind_id].append(d)
    steps = getattr(study, 'stages', None)
    video = any(p.course.video for p in steps) if steps is not None else Participation.objects.filter(study=study, course__video=True).exists()
    applies = {
        Condition.ALWAYS: True,
        Condition.VIDEO: video,
        Condition.DEGREE: study.program.postgraduate,
        Condition.MINOR: bool(person.birth_date) and (today - person.birth_date).days < 18 * 365.25,
        Condition.FOREIGN: person.needs_german,
        Condition.APS: person.needs_aps,
    }
    return [
        {
            'kind': t, 'label': t.name, 'note': t.note, 'required': t.required, 'documents': handed[t.key] or borrowed[t.key],
            'enrolment': t.by_enrolment,  # due by the Immatrikulation, not with the application
            'reusable': [d for d in person.documents.all() if d.kind_id == t.key and d.study_id in same] if t.condition == Condition.VIDEO and not handed[t.key] else [],
        }
        for t in types if applies.get(t.condition) or t.condition == Condition.SUBJECTS and study.subject_id in {s.pk for s in t.subjects.all()}
    ]


def incomplete(study, types=None):
    return [item['label'] for item in checklist(study, types) if item['required'] and not item['documents']]


def owed(items):
    """Of a checklist, what can still be handed in once uploads are closed, until the application is decided: required
    documents still missing, but not a video, judged by then."""
    return [item for item in items if (item['required'] or item['enrolment']) and not item['documents'] and item['kind'].condition != Condition.VIDEO]
