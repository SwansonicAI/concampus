"""A person's file as one copy: what Campus keeps about them, in readable values, with the files they handed in. For the
person's own request (Art. 15 DSGVO) and for the archive taking a file over. Others' data stays out: a booking or a
course shows without who else took part."""
import json
import zipfile
from tempfile import TemporaryFile

from django.db.models import Q
from django.utils.text import capfirst

from courses.models import Course, Reduction
from instruments.models import Loan
from rooms.models import Booking
from .history import describe, show
from .models import Change, Export

SKIPPED = {'id', 'password', 'is_superuser', 'is_staff', 'person', 'study', 'file'}


def values(record):
    """{label: value as people read it} of a record's own fields."""
    shown = {}
    for f in record._meta.concrete_fields:
        if f.name in SKIPPED:
            continue
        if display := getattr(record, f'get_{f.name}_display', None):
            value = display() or '–'
        elif f.is_relation:
            value = str(getattr(record, f.name) or '–')
        else:
            value = str(show(f, f.value_from_object(record), {}))
        shown[str(capfirst(f.verbose_name))] = value
    return shown


def contents(person):
    """The file as data, and its documents with the names they take in the copy."""
    documents = [(d, f'unterlagen/{d.pk}-{d.file.name.rsplit("/", 1)[-1]}' if d.file else '') for d in person.documents.select_related('kind', 'study')]
    data = {
        'Angaben': values(person),
        'Ämter': [str(o) for o in person.groups.all()],
        'Studien': [
            values(s) | {
                'Semester': [values(t) for t in s.terms.select_related('term')],
                'Leistungen': [values(p) for p in s.participations.select_related('course', 'module')],
            }
            for s in person.studies.select_related('program', 'subject', 'start_term', 'teacher')
        ],
        'Unterlagen': [values(d) | ({'Datei': name} if name else {}) for d, name in documents],
        'Postfach': [values(m) for m in person.messages.all()],
        'Verlauf': [
            {'Am': show(Change._meta.get_field('at'), c.at, {}), 'Von': str(c.by or '–'), 'Was': f'{c.record} {c.get_action_display()}'} | {str(label): f'{before} → {after}' for label, before, after in c.lines}
            for c in describe(Change.objects.filter(person=person).select_related('by'), person)
        ],
        'Einwilligungen': [values(c) for c in person.consents.all()],
        'Meldungen im Studierendenmeldeverfahren': [values(r) for r in person.insurance_reports.select_related('term')],
        'Anmeldungen': [values(s) for s in person.sign_ins.all()],
        'Leihinstrumente': [values(loan) for loan in Loan.objects.filter(person=person).select_related('instrument')],
        'Raumbuchungen': [values(b) for b in Booking.objects.filter(Q(person=person) | Q(participants=person)).select_related('room', 'category', 'course').distinct()],
        'Lehre': [values(c) for c in Course.objects.filter(staff=person).select_related('term', 'subject')],
        'Ermäßigungen': [values(r) for r in Reduction.objects.filter(person=person)],
        'Exporte': [values(e) for e in Export.objects.filter(person=person)],
    }
    return {title: part for title, part in data.items() if part}, documents


def dossier(person):
    """The copy as a ZIP file, opened for reading: akte.json and the uploaded files under unterlagen/."""
    data, documents = contents(person)
    out = TemporaryFile()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as copy:
        copy.writestr('akte.json', json.dumps(data, ensure_ascii=False, indent=2))
        for d, name in documents:
            if name and d.file.storage.exists(d.file.name):
                copy.write(d.file.path, name, zipfile.ZIP_STORED)  # videos and scans compress no further
    out.seek(0)
    return out
