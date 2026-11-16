"""What a document says, composed by the Hochschule: blocks in order (BLOCKS), their words with placeholders for what
ConCampus knows of the case (DATA), its lists set as tables. The code says only what there is to say, from a person's
records; every word, and in which order, is the Hochschule's (Content). ConCampus brings a Vorschlag for each document
to start from; the Hochschule changes it, or begins from nothing."""
import re
from string import Formatter

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.utils.dateformat import format as date_format
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core import paper
from core.models import Institution
from core.texts import check

from .models import Content

BLOCKS = {  # type: (what it is called, what it does)
    'title': ('Überschrift', 'Der Titel des Dokuments'),
    'text': ('Absatz', 'Text mit Platzhaltern; ein Absatz, dessen Platzhalter alle leer sind, entfällt'),
    'facts': ('Angaben', 'Bezeichnung und Wert, Zeile für Zeile; eine Zeile ohne Wert entfällt'),
    'table': ('Tabelle', 'Eine Liste des Dokuments, je Eintrag eine Zeile, die Spalten wie gewählt'),
    'sign': ('Ort und Datum', 'Links Ort und Datum, rechts, wer ausstellt'),
    'signatures': ('Unterschriften', 'Je Zeile eine Unterschrift über ihrem Namen'),
    'note': ('Hinweis', 'Klein und grau, etwa: ohne Unterschrift gültig'),
    'qr': ('Prüfcode', 'Ein Hinweis mit QR-Code, unter dem sich das Dokument prüfen lässt'),
}
GAPS = {0: 0, 1: 12, 2: 24, 3: 48}  # space before a block, none to large: px, as the PDF reads them (core.paper.px)
COMMON = {  # what every document knows: placeholder: what it says
    'hochschule': 'Name der Hochschule', 'ort': 'Ort der Hochschule', 'datum': 'Datum der Ausstellung', 'amt': 'Das Amt, das ausstellt (Absender)',
}
PERSON = {
    'name': 'Name, wie im Ausweis', 'geburtsname': 'Geburtsname, wo ein anderer', 'geburtsdatum': 'Geburtsdatum', 'geburtsort': 'Geburtsort',
    'matrikelnummer': 'Matrikelnummer',
}
STUDY = {'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach', 'abschluss': 'Abschlussziel, der Grad'}
DATA = {  # document: (its placeholders, its lists: {list: (what it is, {column placeholder: what it says})})
    'immatrikulation': (COMMON | PERSON | STUDY | {
        'semester': 'Semester', 'zeitraum': 'Zeitraum des Semesters, bei Immatrikulation im Semester ab ihr', 'beurlaubt': '„beurlaubt“, wo beurlaubt',
        'studienart': 'Studienart, etwa Erststudium', 'studienform': 'Vollzeit- oder Teilzeitstudium', 'fachsemester': 'Fachsemester', 'hochschulsemester': 'Hochschulsemester',
        'bafoeg': '„nach § 9 BAföG“ in der Fassung für das BAföG, sonst leer', 'beginn': 'Beginn des Studiums (BAföG)',
        'ersteinschreibung': 'Ersteinschreibung (BAföG)', 'regelstudienzeit': 'Regelstudienzeit in Semestern (BAföG)',
        'pruefcode': 'Code, unter dem sie sich prüfen lässt', 'pruefadresse': 'Adresse, unter der sie sich prüfen lässt',
    }, {}),
    'studienzeiten': (COMMON | PERSON | {
        'semester_anzahl': 'Semester an dieser Hochschule', 'urlaubssemester': 'Davon Urlaubssemester', 'andere_hochschulen': 'Semester an anderen Hochschulen',
    }, {
        'semester': ('Semester', {'semester': 'Semester', 'zeitraum': 'Zeitraum', 'studium': 'Studiengang und Hauptfach', 'fachsemester': 'Fachsemester oder Urlaubssemester'}),
        'studien': ('Studien', {'studium': 'Studiengang und Hauptfach', 'stand': 'Wie es steht: immatrikuliert, abgeschlossen, beendet'}),
    }),
    'exmatrikulation': (COMMON | PERSON | STUDY | {
        'von': 'Immatrikuliert seit', 'bis': 'Exmatrikuliert mit Ablauf des', 'grund': 'Grund, wo er genannt werden soll',
    }, {}),
    'leihvertrag': (COMMON | {
        'name': 'Name, wer ausleiht', 'matrikelnummer': 'Matrikelnummer', 'anschrift': 'Anschrift', 'instrument': 'Instrument', 'inventarnummer': 'Inventarnummer',
        'hersteller': 'Hersteller und Baujahr', 'leihbeginn': 'Leihbeginn', 'leihende': 'Rückgabe bis', 'zustand': 'Zustand bei Ausgabe', 'zubehoer': 'Zubehör und Notiz',
        'zurueckgegeben': 'Zurückgegeben, wann und wie',
    }, {}),
}


def block(kind, text='', gap=0, **more):
    """A block as the Vorschläge write it."""
    return {'type': kind, 'text': text, 'gap': gap, **more}


VORSCHLAG = {  # ConCampus's own wording and order, to start from
    'immatrikulation': {'office': '{hochschule}', 'blocks': [
        block('title', 'Immatrikulationsbescheinigung[ {bafoeg}]'),
        block('text', 'Hiermit wird bescheinigt, dass'),
        block('facts', rows=[['Name', '**{name}**[, geb. {geburtsname}]'], ['Geboren', '{geburtsdatum}[ in {geburtsort}]'], ['Matrikelnummer', '{matrikelnummer}']]),
        block('text', 'im {semester} ({zeitraum}) an der {hochschule} immatrikuliert ist:', 2),
        block('facts', rows=[
            ['Studiengang', '{studiengang}'], ['Hauptfach', '{hauptfach}'], ['Abschlussziel', '{abschluss}'], ['Studienart', '{studienart}, {studienform}'],
            ['Fachsemester', '{fachsemester}'], ['Hochschulsemester', '{hochschulsemester}'], ['Beurlaubung', 'für dieses Semester {beurlaubt}'],
            ['Beginn des Studiums', '{beginn}'], ['Ersteinschreibung', '{ersteinschreibung}'], ['Regelstudienzeit', '{regelstudienzeit} Semester'],
        ]),
        block('text', 'Die Immatrikulation erfolgte nicht aufgrund vorläufiger Zulassung. Diese Bescheinigung gilt als Ersatz für das Formblatt 2 '
                      '(Bescheinigung nach § 9 BAföG).', 2, when='bafoeg'),
        block('sign', '{ort}, {datum}', 3, by='{amt}'),
        block('qr', 'Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig. Prüfcode {pruefcode}, zu prüfen unter {pruefadresse}', 3),
    ]},
    'studienzeiten': {'office': '{hochschule}', 'blocks': [
        block('title', 'Studienzeitbescheinigung'),
        block('text', 'Hiermit werden die Studienzeiten an der {hochschule} bescheinigt für'),
        block('facts', rows=[['Name', '**{name}**[, geb. {geburtsname}]'], ['Geboren', '{geburtsdatum}[ in {geburtsort}]'], ['Matrikelnummer', '{matrikelnummer}']]),
        block('table', gap=1, list='semester', columns=[
            ['Semester', '{semester}', 'left'], ['Zeitraum', '{zeitraum}', 'left'], ['Studiengang und Hauptfach', '{studium}', 'left'], ['Fachsemester', '{fachsemester}', 'right'],
        ]),
        block('table', gap=1, list='studien', columns=[['Studium', '{studium}', 'left'], ['Stand', '{stand}', 'left']]),
        block('facts', gap=1, rows=[
            ['Semester an dieser Hochschule', '{semester_anzahl}'], ['Davon Urlaubssemester', '{urlaubssemester}'], ['An anderen Hochschulen', '{andere_hochschulen} Semester'],
        ]),
        block('sign', '{ort}, {datum}', 3, by='{amt}'),
        block('note', 'Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig.', 3),
    ]},
    'exmatrikulation': {'office': '{hochschule}', 'blocks': [
        block('title', 'Exmatrikulationsbescheinigung'),
        block('text', 'Hiermit wird bescheinigt, dass'),
        block('facts', rows=[['Name', '**{name}**[, geb. {geburtsname}]'], ['Geboren', '{geburtsdatum}[ in {geburtsort}]'], ['Matrikelnummer', '{matrikelnummer}']]),
        block('text', 'an der {hochschule} vom {von} bis zum {bis} immatrikuliert war und mit Ablauf dieses Tages exmatrikuliert ist:', 2),
        block('facts', rows=[['Studiengang', '{studiengang}'], ['Hauptfach', '{hauptfach}'], ['Grund', '{grund}']]),
        block('sign', '{ort}, {datum}', 3, by='{amt}'),
        block('note', 'Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig.', 3),
    ]},
    'leihvertrag': {'office': '{hochschule}', 'blocks': [
        block('title', 'Leihvertrag'),
        block('facts', rows=[
            ['Instrument', '**{instrument}**, Inventarnummer {inventarnummer}\n{hersteller}'], ['Ausgeliehen von', '{name}[, Matrikelnummer {matrikelnummer}]\n{anschrift}'],
            ['Leihzeit', '{leihbeginn} bis {leihende}'], ['Zustand bei Ausgabe', '{zustand}'], ['Zubehör und Notiz', '{zubehoer}'], ['Zurückgegeben', '{zurueckgegeben}'],
        ]),
        block('text', 'Das Instrument bleibt Eigentum der Hochschule und dient dem Studium. Es ist pfleglich zu behandeln, nicht weiterzugeben und bis zum '
                      'vereinbarten Tag mit dem Zubehör zurückzugeben. Schäden und Verlust sind sofort zu melden; für Schäden, die über die übliche Abnutzung '
                      'hinausgehen, haftet, wer es ausgeliehen hat. Solange es überfällig ist, ist keine Rückmeldung möglich.', 1),
        block('signatures', 'Ort, Datum\nFür die Hochschule\n{name}'),
    ]},
}
EMPTY = {'office': '', 'blocks': []}


def day(value):
    return date_format(value, 'j. F Y') if value else ''


def person_of(p):
    return {
        'name': p.legal_name, 'geburtsname': p.birth_name or '', 'geburtsdatum': day(p.birth_date), 'geburtsort': p.birth_place or '',
        'matrikelnummer': p.matriculation or '',
    }


def study_of(s):
    return {'studiengang': s.program.title, 'hauptfach': str(s.subject), 'abschluss': s.program.qualification or s.program.degree}


def data(doc, c):
    """What a document knows of its case, from what its view gives its template (`c`), the made-up one's too
    (samples): ({placeholder: value}, {list: [{column placeholder: value}]})."""
    institution, lists = Institution.get(), {}
    found = {'hochschule': institution.name, 'ort': institution.place, 'datum': day(c['today'])}
    if doc == 'immatrikulation':
        study, term, registration = c['study'], c['term'], c['registration']
        since = study.start_term_id == term.pk and study.since > term.start  # enrolled during the semester: from then
        found |= person_of(study.person) | study_of(study) | {
            'semester': str(term), 'zeitraum': f'{"immatrikuliert seit " if since else ""}{date_format(study.since if since else term.start, "j.n.Y")} bis {date_format(term.end, "j.n.Y")}',
            'beurlaubt': 'beurlaubt' if registration and registration.leave else '', 'studienart': study.studienart,
            'studienform': 'Teilzeitstudium' if registration and registration.part_time else 'Vollzeitstudium', 'fachsemester': str(c['fs']), 'hochschulsemester': str(c['hs']),
            'pruefcode': c['code'], 'pruefadresse': f'{c["check"]}?code={c["code"]}',
        } | ({
            'bafoeg': 'nach § 9 BAföG', 'beginn': f'{day(study.start_term.lectures_start)} ({study.start_term})', 'ersteinschreibung': str(c['first'] or '–'),
            'regelstudienzeit': str(study.program.semesters),
        } if c['bafoeg'] else {})
    elif doc == 'studienzeiten':
        person = c['person']
        found |= person_of(person) | {'semester_anzahl': str(c['count']), 'urlaubssemester': str(c['leave'] or ''), 'andere_hochschulen': str(person.external_terms or '')}
        lists['semester'] = [
            {'semester': str(term), 'zeitraum': f'{term.start:%d.%m.%Y} bis {term.end:%d.%m.%Y}', 'studium': f'{s.program.title}, {s.subject}',
             'fachsemester': 'Urlaubssemester' if registration.leave else str(semester)}
            for term, s, registration, semester in c['rows']
        ]
        lists['studien'] = [
            {'studium': f'{s.program.title}, {s.subject}', 'stand': 'immatrikuliert' if s.status == 'enrolled' else f'abgeschlossen zum {day(s.since)}' if s.status == 'graduated' else f'ohne Abschluss beendet zum {day(s.since)}'}
            for s in c['studies']
        ]
    elif doc == 'exmatrikulation':
        study = c['study']
        found |= person_of(study.person) | study_of(study) | {
            'von': day(study.start_term.start), 'bis': day(study.since), 'grund': study.get_exit_reason_display() if study.exit_reason and c['reason'] else '',
        }
    elif doc == 'leihvertrag':
        loan = c['loan']
        found |= {
            'name': loan.person.legal_name, 'matrikelnummer': loan.person.matriculation or '', 'anschrift': loan.person.address or '',
            'instrument': loan.instrument.name, 'inventarnummer': loan.instrument.number, 'hersteller': loan.instrument.maker or '',
            'leihbeginn': day(loan.start), 'leihende': day(loan.due), 'zustand': loan.condition or '', 'zubehoer': loan.note or '',
            'zurueckgegeben': f'{day(loan.returned)}, {loan.returned_condition}'.strip(', ') if loan.returned else '',
        }
    return found, lists


def composition(doc):
    """The document's content as the Hochschule keeps it, else the Vorschlag."""
    record = Content.objects.filter(key=doc).first()
    return record.content if record else VORSCHLAG[doc]


class Blank(dict):
    def __missing__(self, key):
        return ''


OPTIONAL = re.compile(r'\[([^\[\]]*)\]')  # [ in {geburtsort}]: left out where its placeholders are all empty


def plain(words, values):
    """Words with their placeholders filled in: a part in [brackets] left out where its placeholders are all empty, its
    brackets dropped where not; a line or paragraph whose placeholders are all empty left out (core.texts.filled)."""
    def optional(m):
        names = [name for _, name, _, _ in Formatter().parse(m.group(1)) if name]
        return m.group(1) if not names or any(str(values.get(name, '')).strip() for name in names) else ''
    lines = []
    for line in OPTIONAL.sub(optional, words).split('\n'):
        names = [name for _, name, _, _ in Formatter().parse(line) if name]
        if not names or any(str(values.get(name, '')).strip() for name in names):
            lines.append(line.format_map(Blank(values)))
    return '\n'.join(lines).strip()


def fill(words, values, mark=False):
    """plain(), as HTML: escaped, **bold**, its lines broken; `mark`: on a tint, being worded (core.paper.marked)."""
    text = plain(words, values)
    text = paper.marked(text) if mark and text else text
    return paper.BOLD.sub(r'<b>\1</b>', str(escape(text)))


def compose(doc, c, content=None, marked=None):
    """The document's own part as HTML, under its letterhead: each block filled in from what the document knows (data);
    a block whose `when` is empty left out; `marked`: the block of that number on a tint (documents.views.document_preview)."""
    values, lists = data(doc, c)
    content = content or composition(doc)
    values['amt'] = plain(content.get('office', ''), values)
    parts = [render_to_string('core/_letterhead.html', {'institution': Institution.get(), 'office': values['amt'], 'plain': not values['amt']})]
    for n, b in enumerate(content.get('blocks', [])):
        if b.get('when') and not values.get(b['when']):
            continue
        part = shown(b, values, lists, n == marked)
        if part:
            gap = GAPS.get(b.get('gap', 0), 0)
            parts.append(part.replace('>', f' style="margin-top: {gap}px">', 1) if gap else part)
    return mark_safe(''.join(parts))


def shown(b, values, lists, mark):
    kind, say = b['type'], lambda words, row=None: fill(words, values | (row or {}), mark)
    if kind == 'title':
        return f'<h1>{say(b["text"])}</h1>' if say(b['text']) else ''
    if kind in ('text', 'note'):
        paragraphs = [p for p in say(b['text']).split('\n\n') if p.strip()]
        return ''.join(f'<p class="muted"><small>{p.replace(chr(10), "<br>")}</small></p>' if kind == 'note' else f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)
    if kind == 'facts':
        rows = [(say(label), say(value)) for label, value in b.get('rows', [])]
        rows = [(label, value) for label, value in rows if value]
        return '<dl class="facts">' + ''.join(f'<dt>{label}</dt><dd>{value.replace(chr(10), "<br>")}</dd>' for label, value in rows) + '</dl>' if rows else ''
    if kind == 'table':
        columns, rows = b.get('columns', []), lists.get(b.get('list'), [])
        if not (columns and rows):
            return ''
        cls = lambda align: ' class="num"' if align == 'right' else ''
        head = ''.join(f'<th{cls(a)}>{say(h)}</th>' for h, _, a in columns)
        body = ''.join('<tr>' + ''.join(f'<td{cls(a)}>{say(cell, row)}</td>' for _, cell, a in columns) + '</tr>' for row in rows)
        return f'<section><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>'
    if kind == 'sign':
        return f'<div class="sign"><span>{say(b["text"])}</span><span>{say(b.get("by", ""))}</span></div>'
    if kind == 'signatures':
        names = [name for name in (say(line) for line in b['text'].split('\n')) if name]
        return '<div class="signatures">' + ''.join(f'<span>{name}</span>' for name in names) + '</div>' if names else ''
    if kind == 'qr':
        words = say(b['text'])
        return f'<p class="muted" data-qr="{escape(values.get("pruefadresse", ""))}"><small>{words}</small></p>' if words else ''
    return ''


def clean(doc, content):
    """A composition as the page sends it, checked and reduced to what it may hold; ValidationError where it cannot stand."""
    fields, lists = DATA[doc]
    if not isinstance(content, dict) or not isinstance(content.get('blocks'), list):
        raise ValidationError('Kein Inhalt.')
    if len(content['blocks']) > 60:
        raise ValidationError('Mehr als 60 Blöcke.')

    def words(value, allowed, limit=2000):
        value = str(value or '').replace('\r\n', '\n')[:limit]
        check(value, tuple(allowed))
        return value

    out = {'office': words(content.get('office'), fields, 200), 'blocks': []}
    for b in content['blocks']:
        if not isinstance(b, dict) or b.get('type') not in BLOCKS:
            raise ValidationError('Einen Block gibt es nicht.')
        kind = b['type']
        found = {'type': kind, 'text': words(b.get('text'), fields), 'gap': b.get('gap') if b.get('gap') in GAPS else 0}
        if b.get('when'):
            if b['when'] not in fields:
                raise ValidationError(f'„Nur wenn“: einen Platzhalter {b["when"]} gibt es nicht.')
            found['when'] = b['when']
        if kind == 'facts':
            found['rows'] = [[words(label, fields, 200), words(value, fields, 500)] for label, value in (b.get('rows') or [])[:30]]
        elif kind == 'table':
            if b.get('list') not in lists:
                raise ValidationError('Eine Tabelle braucht eine Liste des Dokuments.')
            columns = lists[b['list']][1]
            found |= {'list': b['list'], 'columns': [
                [words(h, fields, 100), words(cell, {**fields, **columns}, 300), 'right' if align == 'right' else 'left'] for h, cell, align in (b.get('columns') or [])[:8]
            ]}
        elif kind == 'sign':
            found['by'] = words(b.get('by'), fields, 200)
        elif kind == 'qr' and 'pruefadresse' not in fields:
            raise ValidationError('Einen Prüfcode hat dieses Dokument nicht.')
        out['blocks'].append(found)
    return out
