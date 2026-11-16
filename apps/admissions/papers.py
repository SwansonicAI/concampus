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
    """Audition participations in stage order: video round, Hauptfach, shared parts; each course with what follows it."""
    return (
        Participation.objects.filter(course__kind=Course.Kind.AUDITION).select_related('course__subject')  # its name, for Course.stage
        .prefetch_related(Prefetch('course__following', Course.objects.filter(kind=Course.Kind.AUDITION))).order_by(F('course__deadline').asc(nulls_last=True), F('course__subject').asc(nulls_last=True), 'course__title')
    )


def document_types():
    """The kinds of documents an application can ask for, each with its Hauptfächer and semesters; fetch once for many studies."""
    return once('document_types', lambda: list(DocumentType.objects.exclude(condition=Condition.NEVER).prefetch_related('subjects', 'terms')))


def kinds():
    """Every document type by its key, for what was handed in under one no longer asked."""
    return once('document_kinds', lambda: {t.key: t for t in DocumentType.objects.all()})


def first(key, every):
    """The type a line of semester changes began with (DocumentType.follows)."""
    while every[key].follows_id:
        key = every[key].follows_id
    return key


def papers(studies):
    """The studies with what Study.missing() and checklist() read, fetched at once for many: loans still out, documents,
    the person's other studies, the registrations, the audition stages."""
    return studies.select_related('person', 'program').prefetch_related(
        Prefetch('person__loans', Loan.objects.filter(returned=None).select_related('instrument')), 'person__documents', 'person__studies', 'terms',
        Prefetch('participations', stages(), to_attr='stages'),
    )


def checklist(study, types=None):
    """What a complete application holds, as the document types of its Studienbeginn say: [{kind, label, note,
    required, documents}]; after them, what was handed in under a type no longer asked (`old`), still to read. What
    holds for all of a person's applications, like a CV, counts once handed in with any of them, also under another
    semester's version of the type. Reads `study.person.documents.all()` and `study.person.studies.all()`; prefetch
    them for many studies."""
    person, handed, borrowed, today = study.person, defaultdict(list), defaultdict(list), timezone.localdate()
    same = {s.pk for s in person.studies.all() if s.subject_id == study.subject_id and s.pk != study.pk}  # the same Hauptfach elsewhere
    types, every = [t for t in (document_types() if types is None else types) if any(x.pk == study.start_term_id for x in t.terms.all())], kinds()
    own, shared = {first(t.key, every): t.key for t in types}, {t.key for t in types if t.shared}
    for d in person.documents.all():
        key = own.get(first(d.kind_id, every), d.kind_id)
        if d.study_id == study.pk:
            handed[key].append(d)
        elif d.study_id and key in shared:
            borrowed[key].append(d)
    steps = getattr(study, 'stages', None)
    online = any(p.course.video for p in steps) if steps is not None else Participation.objects.filter(study=study, course__video=True).exists()
    mappe = any(t.condition == Condition.SUBJECTS and t.jury and study.subject_id in {s.pk for s in t.subjects.all()} for t in types)
    applies = {
        Condition.ALWAYS: True,
        Condition.VIDEO: online and not mappe,  # a Mappe asked for the Hauptfach, as in composition, stands in for the video
        Condition.DEGREE: study.program.postgraduate,
        Condition.MINOR: bool(person.birth_date) and (today - person.birth_date).days < 18 * 365.25,
        Condition.FOREIGN: person.needs_german,
        Condition.APS: person.needs_aps,
    }
    items = [
        {
            'kind': t, 'label': t.name, 'note': t.note, 'required': t.required, 'documents': handed[t.key] or borrowed[t.key],
            'enrolment': t.by_enrolment,  # due by the Immatrikulation, not with the application
            'reusable': [d for d in person.documents.all() if d.kind_id == t.key and d.study_id in same] if t.condition == Condition.VIDEO and not handed[t.key] else [],
        }
        for t in types if applies.get(t.condition) or t.condition == Condition.SUBJECTS and study.subject_id in {s.pk for s in t.subjects.all()}
    ]
    asked = {item['kind'].key for item in items}
    return items + [  # handed in, but no longer asked: kept to read, never to hand in again
        {'kind': every[key], 'label': every[key].name, 'note': '', 'required': False, 'documents': documents, 'enrolment': False, 'reusable': [], 'old': True}
        for key, documents in handed.items() if key not in asked and key in every and key not in DocumentType.SYSTEM
    ]


def judged(participations):
    """Of an online stage's participations, those with something handed in for the commission to judge: a video, or a
    Mappe; a CV, handed in once for every application, does not count."""
    return participations.filter(study__documents__kind__jury=True, study__documents__kind__shared=False).distinct()


def shown(items, playable=False):
    """Of a checklist, what the jury sees: the papers it reads beside the playing, such as a CV; with `playable`, the
    videos it watches."""
    return [d for item in items if item['kind'].jury for d in item['documents'] if d.playable == playable]


def incomplete(study, types=None):
    """What an application still lacks: the papers it must hand in, and the fee where one is owed."""
    return ['Gebühr für die Eignungsprüfung'] * study.fee_owed + [item['label'] for item in checklist(study, types) if item['required'] and not item['documents']]


def owed(items):
    """Of a checklist, what can still be handed in once uploads are closed, until the application is decided: required
    documents still missing, but not a video, judged by then."""
    return [item for item in items if (item['required'] or item['enrolment']) and not item['documents'] and item['kind'].condition != Condition.VIDEO]
