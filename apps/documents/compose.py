"""What a document says, as the Hochschule writes it on the document itself (core/static/core/flow.js): its content
in blocks, their words in runs, text and fields; a field shows what ConCampus knows of the case (DATA), from a person's
records, a list as a table with a column per field. The code says only what there is to say; every word, and in which
order, is the Hochschule's, kept with the document's template (core.paper.template) under „flow“. ConCampus brings a
Vorschlag for each document to start from.

A run is {"t": text} or {"f": field, "pre": what stands before it}, either {"b": true} for bold. A field without a value
is left out with what stands before it; a line whose fields without such a lead-in are all empty is left out; a blank
line divides paragraphs."""
import re

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core import paper
from core.models import Institution, Layout
from core.texts import TEXTS

BLOCKS = {  # type: (what it is called, what it is for)
    'heading': ('Überschrift', 'Der Titel des Dokuments'),
    'subheading': ('Zwischenüberschrift', 'Über einem Abschnitt'),
    'paragraph': ('Text', 'Absätze, durch eine Leerzeile getrennt'),
    'facts': ('Angaben', 'Bezeichnung und Wert, Zeile für Zeile; eine Zeile ohne Wert entfällt'),
    'table': ('Tabelle', 'Eine Liste des Dokuments, je Eintrag eine Zeile'),
    'sign': ('Ort und Datum', 'Links Ort und Datum, rechts, wer ausstellt'),
    'signatures': ('Unterschriften', 'Je eine Linie zum Unterschreiben über einem Namen'),
    'qr': ('Prüfcode', 'Ein Hinweis mit QR-Code, unter dem sich das Dokument prüfen lässt'),
}
COMMON = {'hochschule': 'Hochschule', 'ort': 'Ort der Hochschule', 'datum': 'Datum', 'amt': 'Absender'}
PERSON = {'name': 'Name', 'geburtsname': 'Geburtsname', 'geburtsdatum': 'Geburtsdatum', 'geburtsort': 'Geburtsort', 'matrikelnummer': 'Matrikelnummer'}
STUDY = {'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach', 'abschluss': 'Abschlussziel'}
DATA = {  # document: (its fields {key: what it says}, its lists {list: (what it is, {field of a row: what it says})})
    'immatrikulation': (COMMON | PERSON | STUDY | {
        'semester': 'Semester', 'zeitraum': 'Zeitraum', 'beurlaubt': 'Beurlaubung', 'studienart': 'Studienart',
        'studienform': 'Voll- oder Teilzeit', 'fachsemester': 'Fachsemester', 'hochschulsemester': 'Hochschulsemester',
        'bafoeg': 'BAföG-Fassung', 'beginn': 'Studienbeginn', 'ersteinschreibung': 'Ersteinschreibung',
        'regelstudienzeit': 'Regelstudienzeit', 'pruefcode': 'Prüfcode', 'pruefadresse': 'Prüfadresse',
    }, {}),
    'studienzeiten': (COMMON | PERSON | {
        'semester_anzahl': 'Semester an dieser Hochschule', 'urlaubssemester': 'Davon Urlaubssemester', 'andere_hochschulen': 'Semester an anderen Hochschulen',
    }, {
        'semester': ('Semester', {'semester': 'Semester', 'zeitraum': 'Zeitraum', 'studium': 'Studiengang und Hauptfach', 'fachsemester': 'Fachsemester'}),
        'studien': ('Studien', {'studium': 'Studiengang und Hauptfach', 'stand': 'Stand'}),
    }),
    'exmatrikulation': (COMMON | PERSON | STUDY | {'von': 'Immatrikuliert seit', 'bis': 'Exmatrikuliert zum', 'grund': 'Grund'}, {}),
    'leihvertrag': (COMMON | {
        'name': 'Name', 'matrikelnummer': 'Matrikelnummer', 'anschrift': 'Anschrift', 'instrument': 'Instrument', 'inventarnummer': 'Inventarnummer',
        'hersteller': 'Hersteller', 'leihbeginn': 'Leihbeginn', 'leihende': 'Rückgabe bis', 'zustand': 'Zustand bei Ausgabe', 'zubehoer': 'Zubehör',
        'zurueckgegeben': 'Zurückgegeben',
    }, {}),
    'brief': ({
        'hochschule': 'Hochschule', 'datum': 'Datum', 'betreff': 'Betreff des Bescheids', 'text': 'Text des Bescheids', 'vorname': 'Vorname', 'nachname': 'Nachname',
        'absender': 'Absender des Bescheids',
    }, {}),
    'notenliste': (COMMON | {
        'pruefung': 'Prüfung', 'semester': 'Semester', 'termine': 'Termine', 'kommission': 'Kommission', 'bewertung': '„Note“ oder „Punkte“',
        'notenliste': 'Notenliste (nicht der Aushang)', 'aushang': 'Aushang der Ergebnisse',
        **{f'pruefer_{n}': f'{n}. Prüfer:in, Nachname' for n in range(1, 6)}, **{f'unterschrift_{n}': f'{n}. Prüfer:in, ganzer Name' for n in range(1, 6)},
    }, {
        'teilnehmende': ('Teilnehmende', {
            'matrikelnummer': 'Matrikelnummer', 'name': 'Name', 'studium': 'Hauptfach, Abschluss, Versuch', 'note': 'Note oder Punkte', 'ergebnis': 'Ergebnis',
            **{f'wertung_{n}': f'Wertung der {n}. Prüfer:in' for n in range(1, 6)},
        }),
    }),
    'leistungen': (COMMON | PERSON | {
        'studiengang': 'Studiengang', 'pruefungsordnung': 'Prüfungsordnung', 'hauptfach': 'Hauptfach', 'vertiefung_art': 'Art der Vertiefung', 'vertiefung': 'Vertiefung',
        'abschlussarbeit': 'Abschlussarbeit', 'letzte_pruefung': 'Datum der letzten Prüfung', 'ausgefertigt': 'Ausgefertigt am', 'erworben': 'Erworbene Leistungspunkte',
        'gesamt': 'Leistungspunkte insgesamt', 'gesamtnote': 'Gesamtnote', 'gesamtnote_worte': 'Gesamtnote in Worten', 'auszeichnung': 'Auszeichnung',
        'vorlaeufig': 'Vorläufige Durchschnittsnote', 'anteil': 'Anteil der Gewichte darin, %', 'rundung': 'Wie die Gesamtnote gerundet wird',
        'zeugnis': 'Zeugnis (alle Leistungen erbracht)', 'uebersicht': 'Leistungsübersicht (noch nicht alle)', 'abschrift': 'Abschrift',
    }, {
        'module': ('Module', {'code': 'Kürzel', 'titel': 'Titel', 'cp': 'Leistungspunkte', 'gewicht': 'Gewicht', 'note': 'Note'}),
    }),
}
SUPPLEMENT = (COMMON | {  # in either language, as the language says it
    'familienname': 'Familienname', 'vornamen': 'Vornamen', 'geburtsname': 'Geburtsname', 'geburtsdatum': 'Geburtsdatum', 'geburtsort': 'Geburtsort',
    'matrikelnummer': 'Matrikelnummer', 'grad': 'Grad', 'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach', 'regelstudienzeit': 'Regelstudienzeit',
    'ects_gesamt': 'ECTS insgesamt', 'ects_erworben': 'ECTS erworben', 'pruefungsordnung': 'Prüfungsordnung', 'studienbeginn': 'Studienbeginn',
    'lernergebnisse': 'Lernergebnisse', 'website': 'Website', 'gesamtnote': 'Gesamtnote', 'gesamtnote_worte': 'Gesamtnote in Worten',
    'auszeichnung': 'Auszeichnung', 'auszeichnung_ab': 'Auszeichnung bis Note', 'datum_zeugnis': 'Datum des Zeugnisses', 'referenz': 'Abschlüsse der Referenzgruppe',
    'bachelor': 'Bachelor', 'master': 'Master', 'diplom': 'Diplom', 'weiterfuehrend': 'Master oder Diplom', 'aufbau': 'Aufbaustudium', 'grundstaendig': 'Grundständig',
    'abgeschlossen': 'Abgeschlossen', 'offen': 'Noch nicht abgeschlossen', 'verteilung': 'Mit Notenverteilung', 'keine_verteilung': 'Ohne Notenverteilung', 'abschrift': 'Abschrift',
}, {
    'module': ('Module', {'code': 'Kürzel', 'titel': 'Titel', 'ects': 'ECTS', 'note': 'Note'}),
    'noten': ('Notenstufen', {'bis': 'Bis', 'note': 'Note, deutsch', 'uebersetzung': 'Note, englisch'}),
    'verteilung': ('Notenverteilung', {'note': 'Gesamtnote', 'anteil': 'Anteil'}),
})
DATA |= {'supplement': SUPPLEMENT, 'supplement_de': SUPPLEMENT}

NOTATION = re.compile(r'\*\*|\[([^\[\]{}]*)\{(\w+)\}\]|\{(\w+)\}')  # how the Vorschläge are written here: **bold**, [lead {field}], {field}


def runs(words):
    """Runs from the notation the Vorschläge are written in; nobody else writes it."""
    found, bold, at = [], False, 0
    for m in NOTATION.finditer(words):
        if m.start() > at:
            found.append({'t': words[at:m.start()]} | ({'b': True} if bold else {}))
        if m.group(0) == '**':
            bold = not bold
        else:
            found.append({'f': m.group(2) or m.group(3), 'pre': m.group(1) or ''} | ({'b': True} if bold else {}))
        at = m.end()
    if at < len(words):
        found.append({'t': words[at:]} | ({'b': True} if bold else {}))
    return found


def text(words, **more):
    return {'type': 'paragraph', 'runs': runs(words), **more}


def facts(*rows):
    return {'type': 'facts', 'rows': [[runs(label), runs(value)] for label, value in rows]}


def column(head, cell, right=False):
    return {'head': runs(head), 'cell': runs(cell), 'right': right}


PERSONAL = facts(('Name', '**{name}**[, geb. {geburtsname}]'), ('Geboren', '{geburtsdatum}[ in {geburtsort}]'), ('Matrikelnummer', '{matrikelnummer}'))
UNSIGNED = 'Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig.'
VORSCHLAG = {  # ConCampus's own wording and order, to start from
    'immatrikulation': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Immatrikulationsbescheinigung[ {bafoeg}]')},
        text('Hiermit wird bescheinigt, dass'), PERSONAL,
        text('im {semester} ({zeitraum}) an der {hochschule} immatrikuliert ist:'),
        facts(
            ('Studiengang', '{studiengang}'), ('Hauptfach', '{hauptfach}'), ('Abschlussziel', '{abschluss}'), ('Studienart', '{studienart}, {studienform}'),
            ('Fachsemester', '{fachsemester}'), ('Hochschulsemester', '{hochschulsemester}'), ('Beurlaubung', 'für dieses Semester {beurlaubt}'),
            ('Beginn des Studiums', '{beginn}'), ('Ersteinschreibung', '{ersteinschreibung}'), ('Regelstudienzeit', '{regelstudienzeit} Semester'),
        ),
        text('Die Immatrikulation erfolgte nicht aufgrund vorläufiger Zulassung. Diese Bescheinigung gilt als Ersatz für das Formblatt 2 '
             '(Bescheinigung nach § 9 BAföG).', when='bafoeg'),
        {'type': 'sign', 'left': runs('{ort}, {datum}'), 'right': runs('{amt}')},
        {'type': 'qr', 'runs': runs(f'{UNSIGNED} Prüfcode {{pruefcode}}, zu prüfen unter {{pruefadresse}}')},
    ]},
    'studienzeiten': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Studienzeitbescheinigung')},
        text('Hiermit werden die Studienzeiten an der {hochschule} bescheinigt für'), PERSONAL,
        {'type': 'table', 'list': 'semester', 'columns': [
            column('Semester', '{semester}'), column('Zeitraum', '{zeitraum}'), column('Studiengang und Hauptfach', '{studium}'), column('Fachsemester', '{fachsemester}', True),
        ]},
        {'type': 'table', 'list': 'studien', 'columns': [column('Studium', '{studium}'), column('Stand', '{stand}')]},
        facts(('Semester an dieser Hochschule', '{semester_anzahl}'), ('Davon Urlaubssemester', '{urlaubssemester}'), ('An anderen Hochschulen', '{andere_hochschulen} Semester')),
        {'type': 'sign', 'left': runs('{ort}, {datum}'), 'right': runs('{amt}')},
        text(UNSIGNED, small=True),
    ]},
    'exmatrikulation': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Exmatrikulationsbescheinigung')},
        text('Hiermit wird bescheinigt, dass'), PERSONAL,
        text('an der {hochschule} vom {von} bis zum {bis} immatrikuliert war und mit Ablauf dieses Tages exmatrikuliert ist:'),
        facts(('Studiengang', '{studiengang}'), ('Hauptfach', '{hauptfach}'), ('Grund', '{grund}')),
        {'type': 'sign', 'left': runs('{ort}, {datum}'), 'right': runs('{amt}')},
        text(UNSIGNED, small=True),
    ]},
    'leihvertrag': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Leihvertrag')},
        facts(
            ('Instrument', '**{instrument}**[, Inventarnummer {inventarnummer}]\n{hersteller}'), ('Ausgeliehen von', '{name}[, Matrikelnummer {matrikelnummer}]\n{anschrift}'),
            ('Leihzeit', '{leihbeginn} bis {leihende}'), ('Zustand bei Ausgabe', '{zustand}'), ('Zubehör und Notiz', '{zubehoer}'), ('Zurückgegeben', '{zurueckgegeben}'),
        ),
        text('Das Instrument bleibt Eigentum der Hochschule und dient dem Studium. Es ist pfleglich zu behandeln, nicht weiterzugeben und bis zum '
             'vereinbarten Tag mit dem Zubehör zurückzugeben. Schäden und Verlust sind sofort zu melden; für Schäden, die über die übliche Abnutzung '
             'hinausgehen, haftet, wer es ausgeliehen hat. Solange es überfällig ist, ist keine Rückmeldung möglich.'),
        {'type': 'signatures', 'names': [runs('Ort, Datum'), runs('Für die Hochschule'), runs('{name}')]},
    ]},
    'brief': {'office': [], 'blocks': [  # every Bescheid: its subject and text are its own (core.texts), the rest stands round them
        {'type': 'subheading', 'runs': runs('{betreff}')},
        text('Guten Tag {vorname} {nachname},'), text('{text}'), text('Mit freundlichen Grüßen\n{absender}'),
        text('Dieses Schreiben wurde maschinell erstellt und ist ohne Unterschrift gültig.', small=True),
    ]},
    'notenliste': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Notenliste'), 'when': 'notenliste'}, {'type': 'heading', 'runs': runs('Ergebnisse'), 'when': 'aushang'},
        facts(('Prüfung', '**{pruefung}**'), ('Semester', '{semester}'), ('Termin', '{termine}'), ('Kommission', '{kommission}')),
        {'type': 'table', 'list': 'teilnehmende', 'when': 'notenliste', 'columns': [
            column('Matrikelnr.', '{matrikelnummer}'), column('Name', '{name}\n{studium}'),
            *(column(f'{{pruefer_{n}}}', f'{{wertung_{n}}}', True) for n in range(1, 6)), column('{bewertung}', '{note}', True), column('Ergebnis', '{ergebnis}'),
        ]},
        {'type': 'table', 'list': 'teilnehmende', 'when': 'aushang', 'columns': [
            column('Matrikelnr.', '{matrikelnummer}'), column('{bewertung}', '{note}', True), column('Ergebnis', '{ergebnis}'),
        ]},
        text('Ohne Gewähr; maßgeblich ist das Ergebnis im Portal.', small=True, when='aushang'),
        {'type': 'sign', 'left': runs('{ort}, {datum}'), 'right': runs('{amt}'), 'when': 'aushang'},
        {'type': 'signatures', 'names': [runs(f'{{unterschrift_{n}}}') for n in range(1, 6)], 'when': 'notenliste'},
    ]},
    'leistungen': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Zeugnis'), 'when': 'zeugnis'}, {'type': 'heading', 'runs': runs('Leistungsübersicht'), 'when': 'uebersicht'},
        text('Abschrift zur Information. Gültig ist das unterschriebene und gesiegelte Zeugnis.', small=True, when='abschrift'),
        facts(
            ('Name', '**{name}**[, geb. {geburtsname}]'), ('Geboren', '{geburtsdatum}[ in {geburtsort}]'), ('Matrikelnummer', '{matrikelnummer}'),
            ('Studiengang', '{studiengang}[, Prüfungsordnung {pruefungsordnung}]'), ('Hauptfach', '{hauptfach}'), ('{vertiefung_art}', '{vertiefung}'),
            ('Abschlussarbeit', '{abschlussarbeit}'), ('Datum der letzten Prüfungsleistung', '{letzte_pruefung}, das Datum des Zeugnisses'), ('Ausgefertigt am', '{ausgefertigt}'),
        ),
        {'type': 'table', 'list': 'module', 'columns': [
            column('Modul', '{code}'), column('Titel', '{titel}'), column('CP', '{cp}', True), column('Gewicht', '{gewicht}', True), column('Note', '{note}', True),
        ]},
        facts(('Leistungspunkte', '{erworben} / {gesamt}')),
        text('Die Gesamtnote ist das gewichtete Mittel der Modulnoten nach dem Studienplan, {rundung}. Unbenotete Module gehen nicht ein.', small=True),
        text('Gesamtnote: **{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**', when='zeugnis'),
        text('Vorläufige Durchschnittsnote: **{vorlaeufig}**, berechnet aus Modulen mit {anteil} % des Gewichts der Gesamtnote.', when='uebersicht'),
        {'type': 'sign', 'left': runs('{ort}, {letzte_pruefung}'), 'right': [], 'when': 'zeugnis'},
        {'type': 'signatures', 'names': [runs('Vorsitz Prüfungsausschuss')], 'when': 'zeugnis'},
        {'type': 'sign', 'left': runs('{ort}, {datum}'), 'right': runs('{amt}'), 'when': 'uebersicht'},
    ]},
    'supplement': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Diploma Supplement')},
        text('Copy for information. Valid only as the signed and sealed original.', small=True, when='abschrift'),
        text('This Diploma Supplement follows the model developed by the European Commission, the Council of Europe and UNESCO/CEPES. It provides '
             'independent data to improve the international transparency and fair academic and professional recognition of qualifications.', small=True),
        {'type': 'subheading', 'runs': runs('1. Holder of the qualification')},
        facts(('1.1 Family name', '**{familienname}**[, born {geburtsname}]'), ('1.2 Given names', '{vornamen}'), ('1.3 Date of birth', '{geburtsdatum}[, {geburtsort}]'),
              ('1.4 Student ID', '{matrikelnummer}')),
        {'type': 'subheading', 'runs': runs('2. Qualification')},
        facts(('2.1 Name of qualification', '{grad}'), ('2.2 Main field of study', '{studiengang}, main subject {hauptfach}'),
              ('2.3 Awarding institution', '{hochschule}\nUniversity of Music, state institution'), ('2.4 Institution administering studies', 'Same as 2.3'),
              ('2.5 Language of instruction and examination', 'German')),
        {'type': 'subheading', 'runs': runs('3. Level, length and access')},
        facts(('3.1 Level', 'First degree (Bachelor), EQF and DQR level 6')) | {'when': 'bachelor'},
        facts(('3.1 Level', 'Second degree (Master), EQF and DQR level 7')) | {'when': 'master'},
        facts(('3.1 Level', 'First degree of one integrated cycle (Diplom), equivalent to Master level, EQF and DQR level 7')) | {'when': 'diplom'},
        facts(('3.2 Official length', '{regelstudienzeit} semesters full-time, {ects_gesamt} ECTS credits')),
        facts(('3.3 Access requirements', 'Higher education entrance qualification (Hochschulreife) or, by way of exception, outstanding artistic talent; '
                                          'in either case an entrance examination of artistic aptitude')) | {'when': 'grundstaendig'},
        facts(('3.3 Access requirements', 'A first higher education degree in a relevant field and an entrance examination of artistic aptitude')) | {'when': 'aufbau'},
        {'type': 'subheading', 'runs': runs('4. Contents and results')},
        facts(('4.1 Mode of study', 'Full-time'), ('4.2 Learning outcomes', '{lernergebnisse}'),
              ('4.3 Programme details', 'Modules with credits and grades below, in the original German. Examination regulations of {pruefungsordnung}; studies commenced {studienbeginn}.')),
        {'type': 'table', 'list': 'module', 'columns': [column('Module', '{code}'), column('Title', '{titel}'), column('ECTS', '{ects}', True), column('Grade', '{note}', True)]},
        facts(('Total', '{ects_erworben} / {ects_gesamt} ECTS credits')),
        facts(('4.4 Grading scheme', 'The lowest pass grade is 4.0. The overall grade is the mean of the module grades weighted as the examination regulations set.'
                                     '[ Up to {auszeichnung_ab} the degree is awarded with distinction (mit Auszeichnung).]')),
        {'type': 'table', 'list': 'noten', 'columns': [column('Up to', '{bis}', True), column('German grade', '{note}'), column('Meaning', '{uebersetzung}')]},
        {'type': 'table', 'list': 'verteilung', 'when': 'verteilung', 'columns': [column('Overall grade', '{note}'), column('Share', '{anteil} %', True)]},
        text('Grading table as the ECTS Users’ Guide describes it: the {referenz} graduates of the programme in the three years before.', small=True, when='verteilung'),
        text('The reference group is too small for a grade distribution ({referenz} degrees in three years).', small=True, when='keine_verteilung'),
        facts(('4.5 Overall classification', '**{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**')) | {'when': 'abgeschlossen'},
        facts(('4.5 Overall classification', 'Not yet awarded')) | {'when': 'offen'},
        {'type': 'subheading', 'runs': runs('5. Function of the qualification')},
        facts(('5.1 Access to further study', 'Qualifies to apply for Master programmes')) | {'when': 'bachelor'},
        facts(('5.1 Access to further study', 'Qualifies to apply for doctoral studies and for postgraduate artistic programmes')) | {'when': 'weiterfuehrend'},
        facts(('5.2 Professional status', 'Not applicable')),
        {'type': 'subheading', 'runs': runs('6. Additional information')},
        facts(('6.1 Additional information', '–'), ('6.2 Further information', '{website}')),
        {'type': 'subheading', 'runs': runs('7. Certification')},
        text('This Diploma Supplement refers to the Zeugnis and the degree certificate (Urkunde) dated {datum_zeugnis}.'),
        {'type': 'sign', 'left': runs('{ort}, {datum_zeugnis}'), 'right': runs('Official stamp')},
        {'type': 'signatures', 'names': [runs('Chair of the examination board')]},
        {'type': 'subheading', 'runs': runs('8. National higher education system')},
        text(TEXTS['ds_hochschulsystem'].body),
    ]},
    'supplement_de': {'office': runs('{hochschule}'), 'blocks': [
        {'type': 'heading', 'runs': runs('Diploma Supplement')},
        text('Abschrift zur Information. Gültig ist das unterschriebene und gesiegelte Original.', small=True, when='abschrift'),
        text('Diese Diploma-Supplement-Vorlage wurde von der Europäischen Kommission, dem Europarat und UNESCO/CEPES entwickelt. Das Diploma Supplement soll '
             'hinreichende Daten zur Verfügung stellen, die die internationale Transparenz und angemessene akademische und berufliche Anerkennung von '
             'Qualifikationen verbessern.', small=True),
        {'type': 'subheading', 'runs': runs('1. Angaben zum Inhaber oder zur Inhaberin der Qualifikation')},
        facts(('1.1 Familienname', '**{familienname}**[, geb. {geburtsname}]'), ('1.2 Vornamen', '{vornamen}'), ('1.3 Geburtsdatum', '{geburtsdatum}[, {geburtsort}]'),
              ('1.4 Matrikelnummer', '{matrikelnummer}')),
        {'type': 'subheading', 'runs': runs('2. Angaben zur Qualifikation')},
        facts(('2.1 Bezeichnung der Qualifikation', '{grad}'), ('2.2 Hauptstudienfach', '{studiengang}, Hauptfach {hauptfach}'),
              ('2.3 Verleihende Hochschule', '{hochschule}\nStaatliche Musikhochschule'), ('2.4 Durchführende Einrichtung', 'wie 2.3'),
              ('2.5 Unterrichts- und Prüfungssprache', 'Deutsch')),
        {'type': 'subheading', 'runs': runs('3. Ebene und Zeitdauer der Qualifikation')},
        facts(('3.1 Ebene der Qualifikation', 'Erster berufsqualifizierender Abschluss (Bachelor), DQR- und EQR-Niveau 6')) | {'when': 'bachelor'},
        facts(('3.1 Ebene der Qualifikation', 'Zweiter berufsqualifizierender Abschluss (Master), DQR- und EQR-Niveau 7')) | {'when': 'master'},
        facts(('3.1 Ebene der Qualifikation', 'Abschluss eines integrierten Studiengangs (Diplom), dem Master gleichwertig, DQR- und EQR-Niveau 7')) | {'when': 'diplom'},
        facts(('3.2 Regelstudienzeit', '{regelstudienzeit} Semester in Vollzeit, {ects_gesamt} ECTS-Leistungspunkte')),
        facts(('3.3 Zugangsvoraussetzungen', 'Hochschulzugangsberechtigung oder ausnahmsweise eine besondere künstlerische Begabung; in jedem Fall eine Eignungsprüfung')) | {'when': 'grundstaendig'},
        facts(('3.3 Zugangsvoraussetzungen', 'Ein erster einschlägiger Hochschulabschluss und eine Eignungsprüfung')) | {'when': 'aufbau'},
        {'type': 'subheading', 'runs': runs('4. Angaben zum Inhalt des Studiums und zu den erzielten Ergebnissen')},
        facts(('4.1 Studienform', 'Vollzeit'), ('4.2 Lernergebnisse', 'Siehe die englische Fassung.'),
              ('4.3 Einzelheiten zum Studiengang', 'Module mit Leistungspunkten und Noten unten. Prüfungsordnung {pruefungsordnung}; Studienbeginn {studienbeginn}.')),
        {'type': 'table', 'list': 'module', 'columns': [column('Modul', '{code}'), column('Titel', '{titel}'), column('ECTS', '{ects}', True), column('Note', '{note}', True)]},
        facts(('Summe', '{ects_erworben} / {ects_gesamt} ECTS-Leistungspunkte')),
        facts(('4.4 Notensystem und Notenverteilung', 'Bestanden ist bis 4,0. Die Gesamtnote ist das nach der Prüfungsordnung gewichtete Mittel der Modulnoten.'
                                                      '[ Bis {auszeichnung_ab} wird der Abschluss mit Auszeichnung verliehen.]')),
        {'type': 'table', 'list': 'noten', 'columns': [column('Bis', '{bis}', True), column('Note', '{note}')]},
        {'type': 'table', 'list': 'verteilung', 'when': 'verteilung', 'columns': [column('Gesamtnote', '{note}'), column('Anteil', '{anteil} %', True)]},
        text('Einstufungstabelle nach dem ECTS-Leitfaden: die {referenz} Absolventinnen und Absolventen des Studiengangs in den drei Jahren davor.', small=True, when='verteilung'),
        text('Für eine Notenverteilung ist die Referenzgruppe zu klein ({referenz} Abschlüsse in drei Jahren).', small=True, when='keine_verteilung'),
        facts(('4.5 Gesamtnote', '**{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**')) | {'when': 'abgeschlossen'},
        facts(('4.5 Gesamtnote', 'Noch nicht vergeben')) | {'when': 'offen'},
        {'type': 'subheading', 'runs': runs('5. Angaben zur Berechtigung der Qualifikation')},
        facts(('5.1 Zugang zu weiterführenden Studien', 'Berechtigt zur Bewerbung für Masterstudiengänge')) | {'when': 'bachelor'},
        facts(('5.1 Zugang zu weiterführenden Studien', 'Berechtigt zur Bewerbung für die Promotion und für künstlerische Aufbaustudiengänge')) | {'when': 'weiterfuehrend'},
        facts(('5.2 Zugang zu reglementierten Berufen', 'Entfällt')),
        {'type': 'subheading', 'runs': runs('6. Weitere Angaben')},
        facts(('6.1 Weitere Angaben', '–'), ('6.2 Weitere Informationsquellen', '{website}')),
        {'type': 'subheading', 'runs': runs('7. Zertifizierung')},
        text('Dieses Diploma Supplement nimmt Bezug auf das Zeugnis und die Urkunde vom {datum_zeugnis}.'),
        {'type': 'sign', 'left': runs('{ort}, {datum_zeugnis}'), 'right': runs('Siegel')},
        {'type': 'signatures', 'names': [runs('Vorsitz Prüfungsausschuss')]},
        {'type': 'subheading', 'runs': runs('8. Angaben zum nationalen Hochschulsystem')},
        text(TEXTS['ds_hochschulsystem_de'].body),
    ]},
}


# ---- what a document knows -----------------------------------------------------------------------------------------

def day(value):
    return date_format(value, 'j. F Y') if value else ''


def person_of(p):
    return {'name': p.legal_name, 'geburtsname': p.birth_name or '', 'geburtsdatum': day(p.birth_date), 'geburtsort': p.birth_place or '', 'matrikelnummer': p.matriculation or ''}


def study_of(s):
    return {'studiengang': s.program.title, 'hauptfach': str(s.subject), 'abschluss': s.program.qualification or s.program.degree}


def data(doc, c):
    """What a document knows of its case, from what its view gives its template (`c`), the made-up one's too
    (samples): ({field: value}, {list: [{field: value}]})."""
    institution, lists = Institution.get(), {}
    found = {'hochschule': institution.name, 'ort': institution.place, 'datum': day(c.get('today') or timezone.localdate())}
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
    elif doc == 'brief':
        letter = c['letter']
        found |= {
            'betreff': letter.subject, 'text': letter.body, 'vorname': letter.person.first_name, 'nachname': letter.person.last_name,
            'absender': letter.sender or institution.name, 'datum': day(letter.created),
        }
    elif doc == 'notenliste':
        from core.templatetags.marks import mark

        course, staff, notice = c['course'], list(c['staff']), c['notice']
        found |= {
            'pruefung': course.title, 'semester': str(course.term), 'termine': '\n'.join(f'{date_format(b.start, "j. F Y, H:i")}, {b.room}' for b in c['bookings']),
            'kommission': '' if notice else ', '.join(map(str, staff)), 'bewertung': 'Punkte' if course.points else 'Note',
            'notenliste': '' if notice else 'ja', 'aushang': 'ja' if notice else '',
        } | {f'pruefer_{n}': '' if notice or n > len(staff) else staff[n - 1].last_name for n in range(1, 6)} \
          | {f'unterschrift_{n}': '' if notice or n > len(staff) else str(staff[n - 1]) for n in range(1, 6)}
        lists['teilnehmende'] = [{
            'matrikelnummer': p.study.person.matriculation or '', 'name': p.study.person.name,
            'studium': ' · '.join(filter(None, [str(p.study.subject), p.study.program.degree, f'{p.attempt}. Versuch' if p.attempt > 1 else ''])),
            'note': f'{p.grade:.1f}'.replace('.', ',') if p.grade is not None else '',
            'ergebnis': ('bestanden' if p.passed else 'nicht bestanden' if p.passed is False else 'entschuldigt' if p.remark else '') + (f' ({p.remark.upper()})' if p.remark else ''),
        } | {f'wertung_{n}': mark(value) for n, (_, value) in enumerate(getattr(p, 'marks', []), 1)} for p in c['rows']]
    elif doc == 'leistungen':
        study, complete = c['study'], c['complete']
        found |= person_of(study.person) | {
            'studiengang': study.program.title, 'pruefungsordnung': str(study.program.version or ''), 'hauptfach': str(study.subject),
            'vertiefung_art': study.focus.parent.title if study.focus else '', 'vertiefung': study.focus.title if study.focus else '',
            'abschlussarbeit': c.get('thesis') or '', 'letzte_pruefung': day(c.get('completed')) if complete else '',
            'ausgefertigt': (day(study.issued) or 'noch nicht') if complete else '', 'erworben': str(c['earned']), 'gesamt': str(c['total']),
            'gesamtnote': str(c['average'] or '') if complete else '', 'gesamtnote_worte': c.get('verbal', '') if complete else '',
            'auszeichnung': 'mit Auszeichnung' if complete and c.get('honours') else '', 'vorlaeufig': '' if complete else str(c['average'] or ''),
            'anteil': str(c.get('covered') or ''), 'abschrift': 'ja' if c.get('copy') else '',
            'rundung': 'nach der ersten Nachkommastelle abgeschnitten' if study.program.rounding == 'cut' else 'auf eine Nachkommastelle kaufmännisch gerundet',
            'zeugnis': 'ja' if complete else '', 'uebersicht': '' if complete else 'ja',
        }
        lists['module'] = modules(c['rows'], complete)
    elif doc in ('supplement', 'supplement_de'):
        found |= supplement(c, de=doc == 'supplement_de')
        lists['module'] = [
            {'code': r['code'], 'titel': r['titel'], 'ects': r['cp'].split(' / ')[0], 'note': r['note'] if doc == 'supplement_de' else r['note'].replace(',', '.').replace('bestanden', 'passed')}
            for r in modules(c['rows'], True) if r['code']
        ]
        decimal = (lambda v: f'{v:.1f}'.replace('.', ',')) if doc == 'supplement_de' else (lambda v: f'{v:.1f}')
        lists['noten'] = [{'bis': decimal(limit), 'note': word, 'uebersetzung': translated} for limit, word, translated in c['grades']] + [
            {'bis': decimal(5.0), 'note': 'nicht ausreichend', 'uebersetzung': 'fail'}]
        lists['verteilung'] = [{'note': word if doc == 'supplement_de' else translated, 'anteil': str(share)} for _, word, translated, share in c.get('distribution') or []]
    elif doc == 'leihvertrag':
        loan = c['loan']
        found |= {
            'name': loan.person.legal_name, 'matrikelnummer': loan.person.matriculation or '', 'anschrift': loan.person.address or '',
            'instrument': loan.instrument.name, 'inventarnummer': loan.instrument.number, 'hersteller': loan.instrument.maker or '',
            'leihbeginn': day(loan.start), 'leihende': day(loan.due), 'zustand': loan.condition or '', 'zubehoer': loan.note or '',
            'zurueckgegeben': f'{day(loan.returned)}, {loan.returned_condition}'.strip(', ') if loan.returned else '',
        }
    return found, lists


def at(row, key):
    """A value of a row of results, whichever it is: a dict (exams.results.achievements), an object (samples)."""
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def grade(value):
    """A grade as a Zeugnis writes it: one decimal, „1,3“; one written already, as written."""
    return value if isinstance(value, str) else f'{value:.1f}'.replace('.', ',')


def weight(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.').replace('.', ',') if value else ''


def modules(rows, complete):
    """The rows of a Zeugnis: each module with its credits, weight and grade; under a module of parts, each part done,
    and on a Leistungsübersicht each attempt failed."""
    found = []
    for r in rows:
        node, choice, kind = at(r, 'node'), at(r, 'choice'), at(r, 'kind')
        if kind == 'part':
            for i in at(r, 'items') or []:
                for n, f in enumerate([] if complete else i.failures, 1):
                    found.append({'code': '', 'titel': f'{i.title}, {n}. Versuch, {f.course.term}', 'cp': '', 'gewicht': '',
                                  'note': (grade(f.grade) if f.grade is not None else 'nicht bestanden') + (f' {f.remark.upper()}' if f.remark else '')})
                if i.done and not i.excused:
                    found.append({'code': '', 'titel': f'{i.title}' + (f', {i.semester}. Semester' if i.semester else ''), 'cp': '', 'gewicht': weight(i.weight),
                                  'note': grade(i.grade) if i.grade is not None else 'bestanden'})
            continue
        found.append({
            'code': node.code or (choice.code if choice else ''),
            'titel': (f'{choice.title}: ' if choice else '') + node.title + ('' if node.done else ' (Prüfung offen)' if node.earned >= node.credits else ' (laufend)'),
            'cp': str(node.earned) if node.earned == node.credits else f'{node.earned} / {node.credits}',
            'gewicht': weight(choice.weight if choice else node.weight),
            'note': (grade(node.grade) if node.grade is not None else 'bestanden') if node.done and kind != 'group' else '',
        })
    return found


def supplement(c, de):
    """What the Diploma Supplement says of its case, in its language."""
    from django.utils import translation

    study = c['study']
    person, program = study.person, study.program
    with translation.override('de' if de else 'en'):
        dated, born = (date_format(d, 'j F Y') if d else '' for d in (c.get('dated'), person.birth_date))
    point = (lambda v: f'{v:.1f}'.replace('.', ',')) if de else (lambda v: f'{v:.1f}')
    final = point(float(c['average'])) if c['finished'] and c.get('average') else ''
    level, distributed = program.level, bool(c.get('distribution'))
    return {
        'familienname': person.last_name, 'vornamen': person.given_names or person.first_name, 'geburtsname': person.birth_name or '', 'geburtsdatum': born,
        'geburtsort': person.birth_place or '', 'matrikelnummer': person.matriculation or '', 'grad': program.qualification or program.degree,
        'studiengang': program.name if de else program.english or program.name, 'hauptfach': study.subject.name if de else study.subject.english or study.subject.name,
        'regelstudienzeit': str(program.semesters), 'ects_gesamt': str(c['total']), 'ects_erworben': str(c['earned']), 'pruefungsordnung': str(program.version or ''),
        'studienbeginn': str(study.start_term), 'lernergebnisse': program.outcomes or '–', 'website': Institution.get().website or '–',
        'gesamtnote': final, 'gesamtnote_worte': c.get('verbal', '') if final else '',
        'auszeichnung': ('mit Auszeichnung' if de else 'with distinction') if final and c.get('honours') else '',
        'auszeichnung_ab': point(float(program.honours)) if program.honours else '', 'datum_zeugnis': dated, 'referenz': str(c.get('reference') or 0),
        'bachelor': 'ja' if level == 'bachelor' else '', 'master': 'ja' if level == 'master' else '', 'diplom': '' if level in ('bachelor', 'master') else 'ja',
        'weiterfuehrend': '' if level == 'bachelor' else 'ja', 'aufbau': 'ja' if program.postgraduate else '', 'grundstaendig': '' if program.postgraduate else 'ja',
        'abgeschlossen': 'ja' if final else '', 'offen': '' if final else 'ja', 'verteilung': 'ja' if distributed else '', 'keine_verteilung': '' if distributed else 'ja',
        'abschrift': 'ja' if c.get('copy') else '',
    }


def runs_of(b):
    """Every run list a block holds, for a search."""
    yield from [b['runs']] if 'runs' in b else []
    for pair in b.get('rows', []):
        yield from pair
    for c in b.get('columns', []):
        yield from (c['head'], c['cell'])
    yield from [b[side] for side in ('left', 'right') if side in b]
    yield from b.get('names', [])


def flow(doc):
    """The document's content as its template keeps it, else the Vorschlag."""
    record = Layout.objects.filter(key=doc).first()
    return record.design.get('flow') if record and record.design.get('flow') else VORSCHLAG[doc]


# ---- set as HTML for the PDF (core.paper.blocks) -------------------------------------------------------------------

def lines(found, values):
    """The runs' lines with their fields filled in: [[(text, bold)]]. A field with what stands before it is a part the line
    may do without; a line whose other fields are all empty is left out."""
    out, line = [], []

    def close():
        fields = [r for r in line if 'f' in r and not r.get('pre')]
        if not fields or any(values.get(r['f']) for r in fields):
            out.append([(r['t'], r.get('b')) if 't' in r else (r['pre'] + str(values[r['f']]), r.get('b')) for r in line if 't' in r or values.get(r['f'])])

    for r in found:
        parts = r['t'].split('\n') if 't' in r else [None]
        for n, part in enumerate(parts):
            if n:
                close()
                line = []
            line.append(r if part is None else {**r, 't': part})
    close()
    return out


def plain(found, values):
    """The runs as text."""
    return '\n'.join(''.join(t for t, _ in line) for line in lines(found, values)).strip()


def say(found, values, mark=False):
    """The runs as HTML: escaped, bold, a line marked on a tint while it is worded (core.paper.marked); its lines, those of
    a value too, still divided by \\n, so paragraphs can be told apart (br())."""
    out = []
    for line in lines(found, values):
        html = ''.join(f'<b>{escape(t)}</b>' if bold else str(escape(t)) for t, bold in line)
        out.append(f'{paper.MARK}{html}{paper.UNMARK}' if mark and html.strip() else html)
    return '\n'.join(out).strip()


def br(html):
    return html.replace('\n', '<br>')


def compose(doc, c, content=None, marked=None):
    """The document's own part as HTML, under its letterhead: each block filled in from what the document knows (data);
    a block whose `when` is empty left out; `marked`: the block of that number on a tint."""
    from django.template.loader import render_to_string

    values, lists = data(doc, c)
    content = content or flow(doc)
    values['amt'] = plain(content.get('office', []), values)
    parts = [] if doc in paper.LETTERS else [  # a letter's head, address and dates are the letter's own (core/_letter.html)
        render_to_string('core/_letterhead.html', {'institution': Institution.get(), 'office': values['amt'], 'plain': not values['amt']}),
    ]
    for n, b in enumerate(content.get('blocks', [])):
        if not b.get('when') or values.get(b['when']):
            parts.append(shown(b, values, lists, n == marked))
    return mark_safe(''.join(parts))


def shown(b, values, lists, mark):
    put = lambda found, row=None: br(say(found, values | (row or {}), mark))
    kind = b['type']
    if kind in ('heading', 'subheading'):
        words = put(b['runs'])
        return f'<h{1 if kind == "heading" else 2}>{words}</h{1 if kind == "heading" else 2}>' if words else ''
    if kind == 'paragraph':  # its paragraphs, a value's too, apart at each blank line
        words = say(b['runs'], values, mark)
        if not words:
            return '<p>\xa0</p>' if not b['runs'] else ''  # an empty one: space
        return ''.join(f'<p class="muted"><small>{br(p)}</small></p>' if b.get('small') else f'<p>{br(p)}</p>' for p in re.split(r'\n\s*\n', words) if p.strip())
    if kind == 'facts':
        rows = [(put(label), put(value)) for label, value in b.get('rows', [])]
        return '<dl class="facts">' + ''.join(f'<dt>{label}</dt><dd>{value}</dd>' for label, value in rows if value) + '</dl>'
    if kind == 'table':
        columns = [c for c in b.get('columns', []) if put(c['head']) or not c['head']]  # a heading of fields all empty: the column falls away
        rows = lists.get(b.get('list'), [])
        if not (columns and rows):
            return ''
        cls = lambda c: ' class="num"' if c.get('right') else ''
        head = ''.join(f'<th{cls(c)}>{put(c["head"])}</th>' for c in columns)
        body = ''.join('<tr>' + ''.join(f'<td{cls(c)}>{put(c["cell"], row)}</td>' for c in columns) + '</tr>' for row in rows)
        return f'<section><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>'
    if kind == 'sign':
        return f'<div class="sign"><span>{put(b.get("left", []))}</span><span>{put(b.get("right", []))}</span></div>'
    if kind == 'signatures':
        names = [put(name) for name in b.get('names', [])]
        return '<div class="signatures">' + ''.join(f'<span>{name}</span>' for name in names if name) + '</div>'
    if kind == 'qr':
        words = put(b['runs'])
        return f'<p class="muted" data-qr="{escape(values.get("pruefadresse", ""))}"><small>{words}</small></p>' if words else ''
    return ''


# ---- checked as the editor sends it --------------------------------------------------------------------------------

def clean(doc, content):
    """A document's content as the editor sends it, checked and reduced to what it may hold; ValidationError where it
    cannot stand."""
    fields, lists = DATA[doc]
    if not isinstance(content, dict) or not isinstance(content.get('blocks'), list) or len(content['blocks']) > 80:
        raise ValidationError('Kein Inhalt, oder mehr als 80 Blöcke.')

    def checked(found, allowed=fields):
        if not isinstance(found, list) or len(found) > 400:
            raise ValidationError('Ein Text ist zu lang.')
        out = []
        for r in found:
            if isinstance(r, dict) and isinstance(r.get('t'), str):
                out.append({'t': r['t'][:4000].replace('\r\n', '\n')})
            elif isinstance(r, dict) and r.get('f') in allowed:
                out.append({'f': r['f'], 'pre': str(r.get('pre') or '')[:80]})
            else:
                raise ValidationError(f'Ein Feld {r.get("f") if isinstance(r, dict) else ""} gibt es hier nicht.')
            if r.get('b'):
                out[-1]['b'] = True
        return out

    out = {'office': checked(content.get('office') or []), 'blocks': []}
    for b in content['blocks']:
        kind = b.get('type') if isinstance(b, dict) else None
        if kind not in BLOCKS:
            raise ValidationError('Einen Block gibt es nicht.')
        found = {'type': kind}
        if kind in ('heading', 'subheading', 'paragraph', 'qr'):
            found['runs'] = checked(b.get('runs') or [])
            if kind == 'paragraph' and b.get('small'):
                found['small'] = True
            if kind == 'qr' and 'pruefadresse' not in fields:
                raise ValidationError('Einen Prüfcode hat dieses Dokument nicht.')
        elif kind == 'facts':
            found['rows'] = [[checked(label), checked(value)] for label, value in (b.get('rows') or [])[:40]]
        elif kind == 'table':
            if b.get('list') not in lists:
                raise ValidationError('Eine Tabelle braucht eine Liste des Dokuments.')
            row = fields | lists[b['list']][1]
            found |= {'list': b['list'], 'columns': [
                {'head': checked(c.get('head') or []), 'cell': checked(c.get('cell') or [], row), 'right': bool(c.get('right'))} for c in (b.get('columns') or [])[:10]
            ]}
        elif kind == 'sign':
            found |= {'left': checked(b.get('left') or []), 'right': checked(b.get('right') or [])}
        elif kind == 'signatures':
            found['names'] = [checked(name) for name in (b.get('names') or [])[:6]]
        if b.get('when'):
            if b['when'] not in fields:
                raise ValidationError('Eine Bedingung nennt ein Feld, das es nicht gibt.')
            found['when'] = b['when']
        out['blocks'].append(found)
    return out
