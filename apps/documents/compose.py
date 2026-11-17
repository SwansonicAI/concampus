"""What a document says, written on the document itself (core/static/core/flow.js): its content as paragraphs, each in
a style, and tables of a list; their words in runs, text and values. A value is what ConCampus knows of the case
(fields_of): from a person's records, or what the occasion gives; a list fills a table, a row each. The code says only
what there is to say; every word, and in which order, is the Hochschule's, kept with the document (catalogue).
ConCampus brings a Vorschlag for each to start from.

A run is {"t": text} or {"f": field, "pre": what stands before it}, either {"b": true} for bold. A value that is empty
is left out with what stands before it; a line whose values without such a lead-in are all empty is left out. A Tab
divides a line into its parts (PARTS): a term and what it says, place and who issues, each signature."""
import re

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core.models import Institution
from core.texts import BESCHEID, NAMES, TEXTS, basics, block

STYLES = {  # a paragraph's style: (its name, what it is for)
    'title': ('Titel', 'Die Überschrift des Dokuments'),
    'heading': ('Überschrift', 'Über einem Abschnitt; im Brief der Betreff'),
    'text': ('Text', 'Ein Absatz'),
    'large': ('Groß', 'Hervorgehoben, wie Name und Grad auf der Urkunde'),
    'small': ('Hinweis', 'Klein und grau'),
    'fact': ('Angabe', 'Bezeichnung, Tab, Wert; ohne Wert entfällt die Zeile'),
    'sign': ('Ort und Datum', 'Links Ort und Datum, Tab, rechts wer ausstellt'),
    'signatures': ('Unterschriften', 'Je Teil, mit Tab getrennt, eine Linie zum Unterschreiben'),
    'qr': ('Prüfcode', 'Ein Hinweis, daneben der QR-Code, mit dem sich das Dokument prüfen lässt'),
}
PARTS = {'fact', 'sign', 'signatures'}  # styles whose lines a Tab divides
COMMON = {'hochschule': 'Hochschule', 'ort': 'Ort der Hochschule', 'datum': 'Datum', 'amt': 'Ausgestellt von'}
PERSON = {'name': 'Name', 'geburtsname': 'Geburtsname', 'geburtsdatum': 'Geburtsdatum', 'geburtsort': 'Geburtsort', 'matrikelnummer': 'Matrikelnummer'}
STUDY = {'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach', 'abschluss': 'Abschlussziel'}
RESULTS = (COMMON | PERSON | {  # the Zeugnis and the Leistungsübersicht
    'studiengang': 'Studiengang', 'pruefungsordnung': 'Prüfungsordnung', 'hauptfach': 'Hauptfach', 'vertiefung_art': 'Art der Vertiefung', 'vertiefung': 'Vertiefung',
    'abschlussarbeit': 'Abschlussarbeit', 'letzte_pruefung': 'Datum der letzten Prüfung', 'ausgefertigt': 'Ausgefertigt am', 'erworben': 'Erworbene Leistungspunkte',
    'gesamt': 'Leistungspunkte insgesamt', 'gesamtnote': 'Gesamtnote', 'gesamtnote_worte': 'Gesamtnote in Worten', 'auszeichnung': 'Auszeichnung',
    'vorlaeufig': 'Vorläufige Durchschnittsnote', 'anteil': 'Anteil der Gewichte darin, %', 'rundung': 'Wie die Gesamtnote gerundet wird', 'abschrift': 'Abschrift',
}, {
    'module': ('Module', {'code': 'Kürzel', 'titel': 'Titel', 'cp': 'Leistungspunkte', 'gewicht': 'Gewicht', 'note': 'Note'}),
})
ROSTER = (COMMON | {  # the Notenliste and the Aushang
    'pruefung': 'Prüfung', 'semester': 'Semester', 'termine': 'Termine', 'kommission': 'Kommission', 'bewertung': '„Note“ oder „Punkte“',
    **{f'pruefer_{n}': f'{n}. Prüfer:in, Nachname' for n in range(1, 6)}, **{f'unterschrift_{n}': f'{n}. Prüfer:in, ganzer Name' for n in range(1, 6)},
}, {
    'teilnehmende': ('Teilnehmende', {
        'matrikelnummer': 'Matrikelnummer', 'name': 'Name', 'studium': 'Hauptfach, Abschluss, Versuch', 'note': 'Note oder Punkte', 'ergebnis': 'Ergebnis',
        **{f'wertung_{n}': f'Wertung der {n}. Prüfer:in' for n in range(1, 6)},
    }),
})
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
DATA = {  # a document on paper: (its fields {key: what it says}, its lists {list: (what it is, {field of a row: what it says})})
    'immatrikulationsbescheinigung': (COMMON | PERSON | STUDY | {
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
    'exmatrikulationsbescheinigung': (COMMON | PERSON | STUDY | {'von': 'Immatrikuliert seit', 'bis': 'Exmatrikuliert zum', 'grund': 'Grund'}, {}),
    'zeugnis': RESULTS, 'leistungen': RESULTS,
    'urkunde': (COMMON | PERSON | {'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach', 'grad': 'Akademischer Grad', 'abschrift': 'Abschrift'}, {}),
    'supplement': SUPPLEMENT, 'supplement_de': SUPPLEMENT,
    'notenliste': ROSTER, 'aushang': ROSTER,
    'leihvertrag': (COMMON | {
        'name': 'Name', 'matrikelnummer': 'Matrikelnummer', 'anschrift': 'Anschrift', 'instrument': 'Instrument', 'inventarnummer': 'Inventarnummer',
        'hersteller': 'Hersteller', 'leihbeginn': 'Leihbeginn', 'leihende': 'Rückgabe bis', 'zustand': 'Zustand bei Ausgabe', 'zubehoer': 'Zubehör',
        'zurueckgegeben': 'Zurückgegeben',
    }, {}),
    'vordruck': (COMMON | {'name': 'Name', 'geburtsdatum': 'Geburtsdatum', 'studiengang': 'Studiengang', 'hauptfach': 'Hauptfach',
                           'unterlagenart': 'Unterlagenart', 'wortlaut': 'Wortlaut der Unterlagenart'}, {}),
    'brief': ({  # any message, printed as a letter
        'hochschule': 'Hochschule', 'datum': 'Datum', 'betreff': 'Betreff der Nachricht', 'text': 'Text der Nachricht', 'vorname': 'Vorname', 'nachname': 'Nachname',
        'absender': 'Absender',
    }, {}),
}
WORDED = {'rechtsbehelfsbelehrung': 'rechtsbehelf', 'absender': 'absender_bescheide', 'begruendung': 'ablehnung_begruendung'}  # values the Hochschule words once (core.texts)
FOLLOWING = {'exmatrikulation': ('erstattung', 'auslaenderbehoerde')}  # a Bescheid's paragraphs that stand only where its case says so
SOURCES = {  # where a value comes from, as the editor says it
    **dict.fromkeys(('hochschule', 'ort', 'website'), 'Hochschule'), **dict.fromkeys(('datum', 'amt'), 'Dieses Dokument'),
    **dict.fromkeys((*PERSON, 'vorname', 'nachname', 'familienname', 'vornamen'), 'Person'),
    **dict.fromkeys(('studiengang', 'hauptfach', 'abschluss', 'semester'), 'Studium'), **dict.fromkeys(WORDED, 'Texte der Hochschule'),
}


def letter(occasion):
    """Whether the document is a letter: a Bescheid, or any message printed as one; it has an address."""
    return occasion == 'brief' or occasion in TEXTS and TEXTS[occasion].group == BESCHEID


def fields_of(occasion):
    """({field: what it says}, {list: (what it is, {field: what it says})}) a document of the occasion knows."""
    if occasion in DATA:
        return DATA[occasion]
    spec, following = TEXTS[occasion], FOLLOWING.get(occasion, ())
    allowed = [*spec.allowed, *(name for flag in following for name in TEXTS[flag].allowed)]  # also what the paragraphs that follow hold
    return {name: NAMES.get(name, name) for name in allowed} | {'absender': 'Absender', 'rechtsbehelfsbelehrung': 'Rechtsbehelfsbelehrung'} | {
        flag: TEXTS[flag].title for flag in following}, {}


def source(field, occasion):
    """Where a value comes from, in a word: whose it is."""
    if field == 'anschrift':
        return 'Hochschule' if occasion in TEXTS else 'Person'
    if occasion == 'brief' and field in ('betreff', 'text', 'absender'):
        return 'Nachricht'
    if field in FOLLOWING.get(occasion, ()):
        return ''  # whether a paragraph stands, not a value to put in
    return SOURCES.get(field, 'Dieser Fall')


# ---- ConCampus's Vorschläge, written in a notation of their own: **bold**, [lead-in {field}], {field}, a Tab between parts

NOTATION = re.compile(r'\*\*|\[([^\[\]{}]*)\{(\w+)\}\]|\{(\w+)\}')


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


def p(words, style='text', **more):
    return {'type': 'p', 'style': style, 'runs': runs(words), **more}


def paragraphs(words, style='text', **more):
    return [p(part, style, **more) for part in words.split('\n\n')]


def fact(label, value, **more):
    return p(f'{label}\t{value}', 'fact', **more)


def column(head, cell, right=False):
    return {'head': runs(head), 'cell': runs(cell), 'right': right}


def table(found, *columns, **more):
    return {'type': 'table', 'list': found, 'columns': list(columns), **more}


def title(words, **more):
    return p(words, 'title', **more)


def heading(words, **more):
    return p(words, 'heading', **more)


def small(words, **more):
    return p(words, 'small', **more)


PERSONAL = [fact('Name', '**{name}**[, geb. {geburtsname}]'), fact('Geboren', '{geburtsdatum}[ in {geburtsort}]'), fact('Matrikelnummer', '{matrikelnummer}')]
UNSIGNED = 'Diese Bescheinigung wurde maschinell erstellt und ist ohne Unterschrift gültig.'
SIGNED_BY = p('{ort}, {datum}\t{amt}', 'sign')
RESULTS_HEAD = [
    *PERSONAL[:2], PERSONAL[2], fact('Studiengang', '{studiengang}[, Prüfungsordnung {pruefungsordnung}]'), fact('Hauptfach', '{hauptfach}'),
    fact('{vertiefung_art}', '{vertiefung}'), fact('Abschlussarbeit', '{abschlussarbeit}'),
]
MODULES = table('module', column('Modul', '{code}'), column('Titel', '{titel}'), column('CP', '{cp}', True), column('Gewicht', '{gewicht}', True), column('Note', '{note}', True))
VORSCHLAG = {  # ConCampus's own wording and order, to start from
    'immatrikulationsbescheinigung': {'office': '', 'blocks': [
        title('Immatrikulationsbescheinigung[ {bafoeg}]'), p('Hiermit wird bescheinigt, dass'), *PERSONAL,
        p('im {semester} ({zeitraum}) an der {hochschule} immatrikuliert ist:'),
        fact('Studiengang', '{studiengang}'), fact('Hauptfach', '{hauptfach}'), fact('Abschlussziel', '{abschluss}'), fact('Studienart', '{studienart}, {studienform}'),
        fact('Fachsemester', '{fachsemester}'), fact('Hochschulsemester', '{hochschulsemester}'), fact('Beurlaubung', 'für dieses Semester {beurlaubt}'),
        fact('Beginn des Studiums', '{beginn}'), fact('Ersteinschreibung', '{ersteinschreibung}'), fact('Regelstudienzeit', '{regelstudienzeit} Semester'),
        p('Die Immatrikulation erfolgte nicht aufgrund vorläufiger Zulassung. Diese Bescheinigung gilt als Ersatz für das Formblatt 2 '
          '(Bescheinigung nach § 9 BAföG).', when='bafoeg'),
        SIGNED_BY, p(f'{UNSIGNED} Prüfcode {{pruefcode}}, zu prüfen unter {{pruefadresse}}', 'qr'),
    ]},
    'studienzeiten': {'office': '', 'blocks': [
        title('Studienzeitbescheinigung'), p('Hiermit werden die Studienzeiten an der {hochschule} bescheinigt für'), *PERSONAL,
        table('semester', column('Semester', '{semester}'), column('Zeitraum', '{zeitraum}'), column('Studiengang und Hauptfach', '{studium}'), column('Fachsemester', '{fachsemester}', True)),
        table('studien', column('Studium', '{studium}'), column('Stand', '{stand}')),
        fact('Semester an dieser Hochschule', '{semester_anzahl}'), fact('Davon Urlaubssemester', '{urlaubssemester}'), fact('An anderen Hochschulen', '{andere_hochschulen} Semester'),
        SIGNED_BY, small(UNSIGNED),
    ]},
    'exmatrikulationsbescheinigung': {'office': '', 'blocks': [
        title('Exmatrikulationsbescheinigung'), p('Hiermit wird bescheinigt, dass'), *PERSONAL,
        p('an der {hochschule} vom {von} bis zum {bis} immatrikuliert war und mit Ablauf dieses Tages exmatrikuliert ist:'),
        fact('Studiengang', '{studiengang}'), fact('Hauptfach', '{hauptfach}'), fact('Grund', '{grund}'),
        SIGNED_BY, small(UNSIGNED),
    ]},
    'zeugnis': {'office': '', 'blocks': [
        title('Zeugnis'), small('Abschrift zur Information. Gültig ist das unterschriebene und gesiegelte Zeugnis.', when='abschrift'),
        *RESULTS_HEAD, fact('Datum der letzten Prüfungsleistung', '{letzte_pruefung}'), fact('Ausgefertigt am', '{ausgefertigt}'),
        MODULES, fact('Leistungspunkte', '{erworben} / {gesamt}'),
        small('Die Gesamtnote ist das gewichtete Mittel der Modulnoten nach dem Studienplan, {rundung}. Unbenotete Module gehen nicht ein.'),
        p('Gesamtnote: **{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**'),
        p('{ort}, {letzte_pruefung}\t', 'sign'), p('Vorsitz Prüfungsausschuss', 'signatures'),
    ]},
    'leistungen': {'office': '', 'blocks': [
        title('Leistungsübersicht'), *RESULTS_HEAD, MODULES, fact('Leistungspunkte', '{erworben} / {gesamt}'),
        small('Die Gesamtnote ist das gewichtete Mittel der Modulnoten nach dem Studienplan, {rundung}. Unbenotete Module gehen nicht ein.'),
        p('Vorläufige Durchschnittsnote: **{vorlaeufig}**, berechnet aus Modulen mit {anteil} % des Gewichts der Gesamtnote.'),
        SIGNED_BY,
    ]},
    'urkunde': {'office': '', 'blocks': [
        title('Urkunde'), small('Abschrift zur Information. Gültig ist die unterschriebene und gesiegelte Urkunde.', when='abschrift'),
        p('Die {hochschule} verleiht'), p('**{name}**[, geb. {geburtsname}]', 'large'), p('geboren am {geburtsdatum}[ in {geburtsort}],'),
        p('nach bestandener Prüfung im Studiengang {studiengang}, Hauptfach {hauptfach}, den akademischen Grad'), p('**{grad}**', 'large'),
        p('{ort}, {datum}', 'small'), p('Präsidentin oder Präsident\tVorsitz Prüfungsausschuss', 'signatures'),
    ]},
    'notenliste': {'office': '', 'blocks': [
        title('Notenliste'), fact('Prüfung', '**{pruefung}**'), fact('Semester', '{semester}'), fact('Termin', '{termine}'), fact('Kommission', '{kommission}'),
        table('teilnehmende', column('Matrikelnr.', '{matrikelnummer}'), column('Name', '{name}\n{studium}'),
              *(column(f'{{pruefer_{n}}}', f'{{wertung_{n}}}', True) for n in range(1, 6)), column('{bewertung}', '{note}', True), column('Ergebnis', '{ergebnis}')),
        p('\t'.join(f'{{unterschrift_{n}}}' for n in range(1, 6)), 'signatures'),
    ]},
    'aushang': {'office': '', 'blocks': [
        title('Ergebnisse'), fact('Prüfung', '**{pruefung}**'), fact('Semester', '{semester}'), fact('Termin', '{termine}'),
        table('teilnehmende', column('Matrikelnr.', '{matrikelnummer}'), column('{bewertung}', '{note}', True), column('Ergebnis', '{ergebnis}')),
        small('Ohne Gewähr; maßgeblich ist das Ergebnis im Portal.'), SIGNED_BY,
    ]},
    'leihvertrag': {'office': '', 'blocks': [
        title('Leihvertrag'),
        fact('Instrument', '**{instrument}**[, Inventarnummer {inventarnummer}]\n{hersteller}'), fact('Ausgeliehen von', '{name}[, Matrikelnummer {matrikelnummer}]\n{anschrift}'),
        fact('Leihzeit', '{leihbeginn} bis {leihende}'), fact('Zustand bei Ausgabe', '{zustand}'), fact('Zubehör und Notiz', '{zubehoer}'), fact('Zurückgegeben', '{zurueckgegeben}'),
        p('Das Instrument bleibt Eigentum der Hochschule und dient dem Studium. Es ist pfleglich zu behandeln, nicht weiterzugeben und bis zum '
          'vereinbarten Tag mit dem Zubehör zurückzugeben. Schäden und Verlust sind sofort zu melden; für Schäden, die über die übliche Abnutzung '
          'hinausgehen, haftet, wer es ausgeliehen hat. Solange es überfällig ist, ist keine Rückmeldung möglich.'),
        p('Ort, Datum\tFür die Hochschule\t{name}', 'signatures'),
    ]},
    'vordruck': {'office': '', 'blocks': [
        title('{unterlagenart}'), *paragraphs('{wortlaut}'), p('Ort, Datum\tUnterschrift\tUnterschrift', 'signatures'),
    ]},
    'brief': {'office': '', 'blocks': [
        heading('{betreff}'), p('Guten Tag {vorname} {nachname},'), p('{text}'), p('Mit freundlichen Grüßen\n{absender}'),
        small('Dieses Schreiben wurde maschinell erstellt und ist ohne Unterschrift gültig.'),
    ]},
    'supplement': {'office': '', 'blocks': [
        title('Diploma Supplement'),
        small('Copy for information. Valid only as the signed and sealed original.', when='abschrift'),
        small('This Diploma Supplement follows the model developed by the European Commission, the Council of Europe and UNESCO/CEPES. It provides '
              'independent data to improve the international transparency and fair academic and professional recognition of qualifications.'),
        heading('1. Holder of the qualification'),
        fact('1.1 Family name', '**{familienname}**[, born {geburtsname}]'), fact('1.2 Given names', '{vornamen}'), fact('1.3 Date of birth', '{geburtsdatum}[, {geburtsort}]'),
        fact('1.4 Student ID', '{matrikelnummer}'),
        heading('2. Qualification'),
        fact('2.1 Name of qualification', '{grad}'), fact('2.2 Main field of study', '{studiengang}, main subject {hauptfach}'),
        fact('2.3 Awarding institution', '{hochschule}\nUniversity of Music, state institution'), fact('2.4 Institution administering studies', 'Same as 2.3'),
        fact('2.5 Language of instruction and examination', 'German'),
        heading('3. Level, length and access'),
        fact('3.1 Level', 'First degree (Bachelor), EQF and DQR level 6', when='bachelor'),
        fact('3.1 Level', 'Second degree (Master), EQF and DQR level 7', when='master'),
        fact('3.1 Level', 'First degree of one integrated cycle (Diplom), equivalent to Master level, EQF and DQR level 7', when='diplom'),
        fact('3.2 Official length', '{regelstudienzeit} semesters full-time, {ects_gesamt} ECTS credits'),
        fact('3.3 Access requirements', 'Higher education entrance qualification (Hochschulreife) or, by way of exception, outstanding artistic talent; '
                                        'in either case an entrance examination of artistic aptitude', when='grundstaendig'),
        fact('3.3 Access requirements', 'A first higher education degree in a relevant field and an entrance examination of artistic aptitude', when='aufbau'),
        heading('4. Contents and results'),
        fact('4.1 Mode of study', 'Full-time'), fact('4.2 Learning outcomes', '{lernergebnisse}'),
        fact('4.3 Programme details', 'Modules with credits and grades below, in the original German. Examination regulations of {pruefungsordnung}; studies commenced {studienbeginn}.'),
        table('module', column('Module', '{code}'), column('Title', '{titel}'), column('ECTS', '{ects}', True), column('Grade', '{note}', True)),
        fact('Total', '{ects_erworben} / {ects_gesamt} ECTS credits'),
        fact('4.4 Grading scheme', 'The lowest pass grade is 4.0. The overall grade is the mean of the module grades weighted as the examination regulations set.'
                                   '[ Up to {auszeichnung_ab} the degree is awarded with distinction (mit Auszeichnung).]'),
        table('noten', column('Up to', '{bis}', True), column('German grade', '{note}'), column('Meaning', '{uebersetzung}')),
        table('verteilung', column('Overall grade', '{note}'), column('Share', '{anteil} %', True), when='verteilung'),
        small('Grading table as the ECTS Users’ Guide describes it: the {referenz} graduates of the programme in the three years before.', when='verteilung'),
        small('The reference group is too small for a grade distribution ({referenz} degrees in three years).', when='keine_verteilung'),
        fact('4.5 Overall classification', '**{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**', when='abgeschlossen'),
        fact('4.5 Overall classification', 'Not yet awarded', when='offen'),
        heading('5. Function of the qualification'),
        fact('5.1 Access to further study', 'Qualifies to apply for Master programmes', when='bachelor'),
        fact('5.1 Access to further study', 'Qualifies to apply for doctoral studies and for postgraduate artistic programmes', when='weiterfuehrend'),
        fact('5.2 Professional status', 'Not applicable'),
        heading('6. Additional information'), fact('6.1 Additional information', '–'), fact('6.2 Further information', '{website}'),
        heading('7. Certification'),
        p('This Diploma Supplement refers to the Zeugnis and the degree certificate (Urkunde) dated {datum_zeugnis}.'),
        p('{ort}, {datum_zeugnis}\tOfficial stamp', 'sign'), p('Chair of the examination board', 'signatures'),
        heading('8. National higher education system'), *paragraphs(TEXTS['ds_hochschulsystem'].body),
    ]},
    'supplement_de': {'office': '', 'blocks': [
        title('Diploma Supplement'),
        small('Abschrift zur Information. Gültig ist das unterschriebene und gesiegelte Original.', when='abschrift'),
        small('Diese Diploma-Supplement-Vorlage wurde von der Europäischen Kommission, dem Europarat und UNESCO/CEPES entwickelt. Das Diploma Supplement soll '
              'hinreichende Daten zur Verfügung stellen, die die internationale Transparenz und angemessene akademische und berufliche Anerkennung von '
              'Qualifikationen verbessern.'),
        heading('1. Angaben zum Inhaber oder zur Inhaberin der Qualifikation'),
        fact('1.1 Familienname', '**{familienname}**[, geb. {geburtsname}]'), fact('1.2 Vornamen', '{vornamen}'), fact('1.3 Geburtsdatum', '{geburtsdatum}[, {geburtsort}]'),
        fact('1.4 Matrikelnummer', '{matrikelnummer}'),
        heading('2. Angaben zur Qualifikation'),
        fact('2.1 Bezeichnung der Qualifikation', '{grad}'), fact('2.2 Hauptstudienfach', '{studiengang}, Hauptfach {hauptfach}'),
        fact('2.3 Verleihende Hochschule', '{hochschule}\nStaatliche Musikhochschule'), fact('2.4 Durchführende Einrichtung', 'wie 2.3'),
        fact('2.5 Unterrichts- und Prüfungssprache', 'Deutsch'),
        heading('3. Ebene und Zeitdauer der Qualifikation'),
        fact('3.1 Ebene der Qualifikation', 'Erster berufsqualifizierender Abschluss (Bachelor), DQR- und EQR-Niveau 6', when='bachelor'),
        fact('3.1 Ebene der Qualifikation', 'Zweiter berufsqualifizierender Abschluss (Master), DQR- und EQR-Niveau 7', when='master'),
        fact('3.1 Ebene der Qualifikation', 'Abschluss eines integrierten Studiengangs (Diplom), dem Master gleichwertig, DQR- und EQR-Niveau 7', when='diplom'),
        fact('3.2 Regelstudienzeit', '{regelstudienzeit} Semester in Vollzeit, {ects_gesamt} ECTS-Leistungspunkte'),
        fact('3.3 Zugangsvoraussetzungen', 'Hochschulzugangsberechtigung oder ausnahmsweise eine besondere künstlerische Begabung; in jedem Fall eine Eignungsprüfung', when='grundstaendig'),
        fact('3.3 Zugangsvoraussetzungen', 'Ein erster einschlägiger Hochschulabschluss und eine Eignungsprüfung', when='aufbau'),
        heading('4. Angaben zum Inhalt des Studiums und zu den erzielten Ergebnissen'),
        fact('4.1 Studienform', 'Vollzeit'), fact('4.2 Lernergebnisse', 'Siehe die englische Fassung.'),
        fact('4.3 Einzelheiten zum Studiengang', 'Module mit Leistungspunkten und Noten unten. Prüfungsordnung {pruefungsordnung}; Studienbeginn {studienbeginn}.'),
        table('module', column('Modul', '{code}'), column('Titel', '{titel}'), column('ECTS', '{ects}', True), column('Note', '{note}', True)),
        fact('Summe', '{ects_erworben} / {ects_gesamt} ECTS-Leistungspunkte'),
        fact('4.4 Notensystem und Notenverteilung', 'Bestanden ist bis 4,0. Die Gesamtnote ist das nach der Prüfungsordnung gewichtete Mittel der Modulnoten.'
                                                    '[ Bis {auszeichnung_ab} wird der Abschluss mit Auszeichnung verliehen.]'),
        table('noten', column('Bis', '{bis}', True), column('Note', '{note}')),
        table('verteilung', column('Gesamtnote', '{note}'), column('Anteil', '{anteil} %', True), when='verteilung'),
        small('Einstufungstabelle nach dem ECTS-Leitfaden: die {referenz} Absolventinnen und Absolventen des Studiengangs in den drei Jahren davor.', when='verteilung'),
        small('Für eine Notenverteilung ist die Referenzgruppe zu klein ({referenz} Abschlüsse in drei Jahren).', when='keine_verteilung'),
        fact('4.5 Gesamtnote', '**{gesamtnote} ({gesamtnote_worte})[, {auszeichnung}]**', when='abgeschlossen'),
        fact('4.5 Gesamtnote', 'Noch nicht vergeben', when='offen'),
        heading('5. Angaben zur Berechtigung der Qualifikation'),
        fact('5.1 Zugang zu weiterführenden Studien', 'Berechtigt zur Bewerbung für Masterstudiengänge', when='bachelor'),
        fact('5.1 Zugang zu weiterführenden Studien', 'Berechtigt zur Bewerbung für die Promotion und für künstlerische Aufbaustudiengänge', when='weiterfuehrend'),
        fact('5.2 Zugang zu reglementierten Berufen', 'Entfällt'),
        heading('6. Weitere Angaben'), fact('6.1 Weitere Angaben', '–'), fact('6.2 Weitere Informationsquellen', '{website}'),
        heading('7. Zertifizierung'),
        p('Dieses Diploma Supplement nimmt Bezug auf das Zeugnis und die Urkunde vom {datum_zeugnis}.'),
        p('{ort}, {datum_zeugnis}\tSiegel', 'sign'), p('Vorsitz Prüfungsausschuss', 'signatures'),
        heading('8. Angaben zum nationalen Hochschulsystem'), *paragraphs(TEXTS['ds_hochschulsystem_de'].body),
    ]},
}


def vorschlag(occasion):
    """ConCampus's content for a document of the occasion: a Bescheid, its subject over its greeting, its text, what
    follows where it applies, the Rechtsbehelfsbelehrung where it can be challenged, and its closing."""
    if occasion in VORSCHLAG:
        return VORSCHLAG[occasion]
    spec = TEXTS[occasion]
    return {'office': '', 'blocks': [
        heading(spec.subject), p('Guten Tag {vorname} {nachname},'), *paragraphs(spec.body),
        *(q for flag in FOLLOWING.get(occasion, ()) for q in paragraphs(TEXTS[flag].body, when=flag)),
        *([p('{rechtsbehelfsbelehrung}')] if spec.appealable else []),
        p('Mit freundlichen Grüßen\n{absender}'), small('Dieses Schreiben wurde maschinell erstellt und ist ohne Unterschrift gültig.'),
    ]}


# ---- what a document knows -----------------------------------------------------------------------------------------

def day(value):
    return date_format(value, 'j. F Y') if value else ''


def person_of(p):
    return {'name': p.legal_name, 'geburtsname': p.birth_name or '', 'geburtsdatum': day(p.birth_date), 'geburtsort': p.birth_place or '', 'matrikelnummer': p.matriculation or ''}


def study_of(s):
    return {'studiengang': s.program.title, 'hauptfach': str(s.subject), 'abschluss': s.program.qualification or s.program.degree}


def letter_values(occasion, values, after=()):
    """What a Bescheid knows of its case: what its occasion gives, the Hochschule's data and its own texts filled in,
    and which of the paragraphs that follow only where they apply do (FOLLOWING)."""
    found = basics() | {name: str(value) for name, value in values.items()}
    found |= {flag: 'ja' for flag in after}
    return found | {'absender': block('absender_bescheide', **found), 'rechtsbehelfsbelehrung': block('rechtsbehelf', **found)}


def data(doc, c):
    """What a document on paper knows of its case, from what its view gives its template (`c`), the made-up one's too
    (samples): ({field: value}, {list: [{field: value}]})."""
    institution, lists = Institution.get(), {}
    found = {'hochschule': institution.name, 'ort': institution.place, 'datum': day(c.get('today') or timezone.localdate())}
    if doc == 'immatrikulationsbescheinigung':
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
    elif doc == 'exmatrikulationsbescheinigung':
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
    elif doc in ('notenliste', 'aushang'):
        from core.templatetags.marks import mark

        course, staff, notice = c['course'], list(c['staff']), doc == 'aushang'
        found |= {
            'pruefung': course.title, 'semester': str(course.term), 'termine': '\n'.join(f'{date_format(b.start, "j. F Y, H:i")}, {b.room}' for b in c['bookings']),
            'kommission': '' if notice else ', '.join(map(str, staff)), 'bewertung': 'Punkte' if course.points else 'Note',
        } | {f'pruefer_{n}': '' if notice or n > len(staff) else staff[n - 1].last_name for n in range(1, 6)} \
          | {f'unterschrift_{n}': '' if notice or n > len(staff) else str(staff[n - 1]) for n in range(1, 6)}
        lists['teilnehmende'] = [{
            'matrikelnummer': p.study.person.matriculation or '', 'name': p.study.person.name,
            'studium': ' · '.join(filter(None, [str(p.study.subject), p.study.program.degree, f'{p.attempt}. Versuch' if p.attempt > 1 else ''])),
            'note': f'{p.grade:.1f}'.replace('.', ',') if p.grade is not None else '',
            'ergebnis': ('bestanden' if p.passed else 'nicht bestanden' if p.passed is False else 'entschuldigt' if p.remark else '') + (f' ({p.remark.upper()})' if p.remark else ''),
        } | {f'wertung_{n}': mark(value) for n, (_, value) in enumerate(getattr(p, 'marks', []), 1)} for p in c['rows']]
    elif doc in ('zeugnis', 'leistungen'):
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
        }
        lists['module'] = modules(c['rows'], complete)
    elif doc == 'urkunde':
        study = c['study']
        found |= person_of(study.person) | {
            'studiengang': study.program.name, 'hauptfach': str(study.subject), 'grad': study.program.qualification or study.program.degree,
            'datum': day(c.get('completed') or study.issued), 'abschrift': 'ja' if c.get('copy') else '',
        }
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
    elif doc == 'vordruck':
        found |= {name: str(c.get('values', {}).get(name, '')) for name in ('name', 'geburtsdatum', 'studiengang', 'hauptfach')} | {
            'unterlagenart': c['kind'].name, 'wortlaut': c['body'],
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
    for c in b.get('columns', []):
        yield from (c['head'], c['cell'])


# ---- set as HTML for the PDF (core.paper.blocks), and as text for the Postfach ---------------------------------------

def lines(found, values):
    """The runs' lines with their values filled in: [[(text, bold)]]. A value with what stands before it is a part the
    line may do without; a line whose other values are all empty is left out."""
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


def say(found, values):
    """The runs as HTML: escaped and bold, their lines, a value's too, still divided by \\n (br())."""
    return '\n'.join(''.join(f'<b>{escape(t)}</b>' if bold else str(escape(t)) for t, bold in line) for line in lines(found, values)).strip()


def br(html):
    return html.replace('\n', '<br>')


def shown_blocks(content, values):
    return [b for b in content.get('blocks', []) if not b.get('when') or values.get(b['when'])]


def html(content, values, lists):
    """The content as HTML, as core.paper.blocks sets it: Angaben side by side one under another, each table filled."""
    out, facts = [], []
    for b in shown_blocks(content, values):
        if b['type'] == 'p' and b['style'] == 'fact':
            label, _, value = say(b['runs'], values).partition('\t')
            if label.strip() or value.strip():
                facts.append(f'<dt>{br(label)}</dt><dd>{br(value)}</dd>')
            continue
        if facts:
            out.append(f'<dl class="facts">{"".join(facts)}</dl>')
            facts = []
        out.append(shown(b, values, lists))
    if facts:
        out.append(f'<dl class="facts">{"".join(facts)}</dl>')
    return ''.join(out)


def shown(b, values, lists):
    put = lambda found, row=None: br(say(found, values | (row or {})))
    if b['type'] == 'table':
        columns = [c for c in b.get('columns', []) if put(c['head']) or not c['head']]  # a heading of values all empty: the column falls away
        rows = lists.get(b.get('list'), [])
        if not (columns and rows):
            return ''
        cls = lambda c: ' class="num"' if c.get('right') else ''
        head = ''.join(f'<th{cls(c)}>{put(c["head"])}</th>' for c in columns)
        body = ''.join('<tr>' + ''.join(f'<td{cls(c)}>{put(c["cell"], row)}</td>' for c in columns) + '</tr>' for row in rows)
        return f'<section><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>'
    style, words = b['style'], say(b['runs'], values)
    if not words.replace('\t', '').strip():
        return '<p>\xa0</p>' if not b['runs'] else ''  # an empty one: space
    if style == 'sign':
        left, _, right = words.partition('\t')
        return f'<div class="sign"><span>{br(left)}</span><span>{br(right)}</span></div>'
    if style == 'signatures':
        return '<div class="signatures">' + ''.join(f'<span>{br(name)}</span>' for name in words.split('\t') if name.strip()) + '</div>'
    words = words.replace('\t', ' ')
    if style == 'title':
        return f'<h1>{br(words)}</h1>'
    if style == 'heading':
        return f'<h2>{br(words)}</h2>'
    if style == 'qr':
        return f'<p class="muted" data-qr="{escape(values.get("pruefadresse", ""))}"><small>{br(words)}</small></p>'
    tag = {'small': '<p class="muted"><small>{}</small></p>', 'large': '<p class="large">{}</p>'}.get(style, '<p>{}</p>')
    return ''.join(tag.format(br(part)) for part in re.split(r'\n\s*\n', words) if part.strip())  # a value's paragraphs, apart


def subject(content, values):
    """What the document is about, as its first heading says it: a letter's Betreff."""
    first = next((b for b in shown_blocks(content, values) if b['type'] == 'p' and b['style'] in ('title', 'heading')), None)
    return plain(first['runs'], values).replace('\t', ' ') if first else ''


def text(content, values):
    """The content as text, its subject aside: a letter as the Postfach and the mail show it."""
    found, head = [], True
    for b in shown_blocks(content, values):
        if b['type'] != 'p':
            continue
        if head and b['style'] in ('title', 'heading'):
            head = False
            continue
        words = plain(b['runs'], values).replace('\t', '   ').strip()
        if words:
            found.append(words)
    return '\n\n'.join(found)


def compose(occasion, c, draft=None, key=None):
    """A document on paper as HTML, from what its view gave its template (`c`): under its letterhead, unless a letter,
    which has its own (core/_letter.html), its content filled in; a marker says which document it is, for its page
    (core.paper.document_of). `draft`: content not saved, as the editor shows it; `key`: that document."""
    from django.template.loader import render_to_string

    from .catalogue import chosen, papers, resolved

    found = papers()
    paper = found[key] if key in found else chosen(occasion, getattr(c.get('study'), 'program', None), found)
    content = draft or resolved(paper.key, 'content', found)[0]
    values, lists = data(occasion, c)
    values['amt'] = content.get('office') or values['hochschule']
    head = '' if letter(occasion) else render_to_string('core/_letterhead.html', {'institution': Institution.get(), 'office': values['amt']})
    return mark_safe(f'<i hidden class="no-print" data-page="{escape(paper.key)}"></i>{head}{html(content, values, lists)}')


# ---- checked as the editor sends it --------------------------------------------------------------------------------

def clean(occasion, content):
    """A document's content as the editor sends it, checked and reduced to what it may hold; ValidationError where it
    cannot stand."""
    fields, lists = fields_of(occasion)
    if not isinstance(content, dict) or not isinstance(content.get('blocks'), list) or len(content['blocks']) > 200:
        raise ValidationError('Kein Inhalt, oder mehr als 200 Absätze.')

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
                raise ValidationError(f'Einen Wert {r.get("f") if isinstance(r, dict) else ""} gibt es hier nicht.')
            if r.get('b'):
                out[-1]['b'] = True
        return out

    out = {'office': str(content.get('office') or '')[:120], 'blocks': []}
    for b in content['blocks']:
        kind = b.get('type') if isinstance(b, dict) else None
        if kind == 'p':
            if b.get('style') not in STYLES:
                raise ValidationError('Einen Stil gibt es nicht.')
            if b['style'] == 'qr' and 'pruefadresse' not in fields:
                raise ValidationError('Einen Prüfcode hat dieses Dokument nicht.')
            found = {'type': 'p', 'style': b['style'], 'runs': checked(b.get('runs') or [])}
        elif kind == 'table':
            if b.get('list') not in lists:
                raise ValidationError('Eine Tabelle braucht eine Liste des Dokuments.')
            row = fields | lists[b['list']][1]
            found = {'type': 'table', 'list': b['list'], 'columns': [
                {'head': checked(c.get('head') or []), 'cell': checked(c.get('cell') or [], row), 'right': bool(c.get('right'))} for c in (b.get('columns') or [])[:12]
            ]}
        else:
            raise ValidationError('Einen Teil des Inhalts gibt es nicht.')
        if b.get('when'):
            if b['when'] not in fields:
                raise ValidationError('Eine Bedingung nennt einen Wert, den es nicht gibt.')
            found['when'] = b['when']
        out['blocks'].append(found)
    return out
