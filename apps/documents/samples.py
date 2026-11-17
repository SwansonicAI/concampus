"""Each Bescheinigung and Urkunde as it is printed, for one made-up person: the very template its page renders, given
what its view gives it, so the preview cannot drift from the document. Erika Mustermann, Bachelor Musik, Violine,
eight semesters, her studies done; nobody's real records."""
from collections import defaultdict
from datetime import date
from types import SimpleNamespace as Made

from django.template.loader import render_to_string

from core.models import Institution
from core.texts import block


class Named(str):
    """A value printed by its name that has more to it, as a Semester or a Hauptfach."""

    def __new__(cls, name, **more):
        named = super().__new__(cls, name)
        named.__dict__.update(name=name, **more)
        return named


def semesters():
    found = []
    for year in range(2022, 2026):
        found += [
            Named(f'WiSe {year}/{year - 1999}', start=date(year, 10, 1), end=date(year + 1, 3, 31), lectures_start=date(year, 10, 15)),
            Named(f'SoSe {year + 1}', start=date(year + 1, 4, 1), end=date(year + 1, 9, 30), lectures_start=date(year + 1, 4, 10)),
        ]
    return found


def modules():
    """The rows of a Zeugnis: Module, their credits and grades."""
    found = [
        ('Hauptfach Violine 1', 30, 2, '1,3'), ('Hauptfach Violine 2', 30, 2, '1,0'), ('Kammermusik', 12, 1, '1,7'), ('Orchesterstudien', 12, 1, '2,0'),
        ('Musiktheorie', 8, 1, '2,3'), ('Gehörbildung', 6, 1, '1,7'), ('Musikgeschichte', 6, 1, '1,3'), ('Bachelorprüfung', 20, 3, '1,0'),
    ]
    return [
        Made(kind='module', choice=None, node=Made(code=f'BA-{n:02}', title=title, earned=credits, credits=credits, weight=weight, done=True, grade=grade))
        for n, (title, credits, weight, grade) in enumerate(found, 1)
    ]


def letter(key):
    """A Bescheid, by its text's key, as the made-up person gets it: its words, with what follows it where it applies."""
    from core.models import Message, Person
    from core.texts import Values, examples, render

    from .catalogue import FOLLOWING, WITHIN

    values = Values(examples(key))
    for name, part in WITHIN.get(key, {}).items():
        values[name] = block(part, **values)
    subject, body = render(key, *FOLLOWING.get(key, ()), **values)
    person = Person(first_name=values['vorname'], last_name=values['nachname'], address='Musterstraße 1\n12345 Musterstadt', matriculation=values['matrikelnummer'])
    return Message(pk=1234, person=person, subject=subject, body=body, sender=block('absender_bescheide'), template=key)


def made_up(request):
    """What the documents' views give their templates, for the made-up person: ({document: (template, context)}, what
    they all share, the Zeugnis's results, the person)."""
    terms = semesters()
    person = Made(
        first_name='Erika', last_name='Mustermann', given_names='Erika Maria', name='Erika Mustermann', legal_name='Erika Maria Mustermann',
        birth_name='', birth_date=date(2001, 4, 1), birth_place='Musterstadt', matriculation='100123', address='Musterstraße 1\n12345 Musterstadt', external_terms=0,
    )
    for n, term in enumerate(terms, 1):
        term.pk = n
    study = Made(
        pk=0, person=person, subject=Named('Violine', english='Violin'), start_term=terms[0], start_term_id=1, since=terms[-1].end, status='graduated',
        issued=date(2026, 3, 20), focus=None, studienart='Erststudium', exit_reason='', get_exit_reason_display=lambda: '', get_absolute_url='#',
        program=Made(
            name='Musik', title='Bachelor Musik', english='Music', qualification='Bachelor of Music', degree='B.Mus.', semesters=8, version='2023',
            rounding='cut', level='bachelor', postgraduate=False, honours=1.3, outcomes='Graduates perform at a professional level, as soloists and in ensembles.',
        ),
    )
    finished = {'rows': modules(), 'completed': date(2026, 2, 27), 'earned': 124, 'total': 124, 'honours': False}
    staff = [Named('Prof. Anna Berger', last_name='Berger'), Named('Prof. Jonas Wendt', last_name='Wendt')]
    pages = {
        'immatrikulation': ('students/certificate.html', {
            'term': terms[-1], 'registration': Made(leave=False, part_time=False), 'fs': 8, 'hs': 8, 'bafoeg': False, 'first': terms[0],
            'code': 'K7Q2-9XW4', 'check': request.build_absolute_uri('/pruefen/'),
        }),
        'studienzeiten': ('students/periods.html', {
            'person': person, 'studies': [study], 'rows': [(term, study, Made(leave=False), n) for n, term in enumerate(terms, 1)], 'count': 8, 'leave': 0,
        }),
        'exmatrikulation': ('students/exmatriculation.html', {'reason': False}),
        'leistungen': ('exams/transcript.html', finished | {
            'complete': True, 'average': '1,3', 'verbal': 'sehr gut', 'thesis': 'Bachs Sonaten und Partiten in historischer Aufführungspraxis', 'covered': 100,
        }),
        **{doc: ('exams/supplement.html', finished | {  # in English, and in German
            'average': 1.3, 'finished': True, 'distribution': None, 'reference': 4, 'dated': finished['completed'], 'lang': lang, 'de': lang == 'de',
            'verbal': 'sehr gut' if lang == 'de' else 'very good',
            'grades': [(1.5, 'sehr gut', 'very good'), (2.5, 'gut', 'good'), (3.5, 'befriedigend', 'satisfactory'), (4.0, 'ausreichend', 'sufficient')],
        }) for doc, lang in (('supplement', 'en'), ('supplement_de', 'de'))},
        'brief': ('core/_letter.html', {'letter': letter('zulassung'), 'institution': Institution.get()}),
        'urkunde': ('exams/certificate.html', {'completed': finished['completed'], 'signature': block('unterschrift_urkunden')}),
        'notenliste': ('exams/roster.html', {
            'course': Made(title='Hauptfachprüfung Violine', term=terms[-1], points=False), 'notice': False, 'bookings': [], 'staff': staff,
            'rows': [
                Made(study=Made(person=Made(matriculation=f'10012{n}', name=name), subject='Violine', program=Made(degree='B.Mus.')), attempt=1, marks=[(e, grade) for e in staff], grade=grade, passed=True, remark='')
                for n, (name, grade) in enumerate([('Erika Mustermann', 1.0), ('Max Muster', 1.7), ('Lena Beispiel', 2.3)], 3)
            ],
        }),
        'leihvertrag': ('instruments/agreement.html', {
            'loan': Made(
                instrument=Made(name='Viola Nr. 12', number='INV-0412', maker='Werkstatt Mittenwald, 1987'), instrument_id=0, person=person,
                start=date(2025, 10, 1), due=date(2026, 3, 31), condition='gut, Bogen neu behaart', note='Etui, Bogen, Kolophonium', returned=None, returned_condition='',
            ),
        }),
    }
    return pages, {'today': date.today(), 'study': study}, finished, person


def sample(doc, request, **more):
    """The page of the document `doc` (core.paper.DOCUMENTS) for the made-up person; `more`: what else its template is
    given, a composition's draft and the block marked in it (templatetags.composed)."""
    pages, common, finished, person = made_up(request)
    study, common = common['study'], {'base': 'core/panel.html'} | common | more
    if doc == 'vordruck':  # its wording is the Unterlagenart's: the first that has one
        from students.models import DocumentType

        kind = DocumentType.objects.exclude(template='').order_by('name').first()
        values = {'name': person.legal_name, 'geburtsdatum': '1. April 2001', 'studiengang': study.program.title, 'hauptfach': study.subject, 'hochschule': Institution.get().name}
        if kind is None:
            return '<div class="document" data-paper="vordruck"><h1>Vordruck</h1><p>Noch hat keine Unterlagenart einen Vordruck.</p></div>'
        return render_to_string('admissions/blank.html', {'base': 'core/panel.html', 'kind': kind, 'body': kind.template.format_map(defaultdict(str, values))}, request)
    template, context = pages[doc]
    return render_to_string(template, common | context, request)
