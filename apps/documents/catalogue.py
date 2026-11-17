"""Every document ConCampus makes, as people name it. On paper, one for each occasion the code issues it on
(OCCASIONS): the Bescheide, letters each, and the Bescheinigungen, Zeugnisse and Urkunden. Each is the Hochschule's
own once it saves it (models.Paper), else ConCampus's Vorschlag (vorschlag); more of an occasion stand where the
Hochschule copies one, each for the Studiengänge it names (chosen). Each says what it says on a Vorlage
(letterheads), a handful for all of them: the Brief for every Bescheid, the Bescheinigung, the Urkunde. Besides, the
messages and the texts on ConCampus's own pages, worded each on its own (core.texts)."""
from copy import deepcopy
from dataclasses import dataclass

from django.utils.text import slugify

from core import paper
from core.texts import BAUSTEIN, BESCHEID, NACHRICHT, TEXTS

from .compose import FREE
from .compose import vorschlag as content_of
from .models import Letterhead, Paper

KINDS = {  # URL segment: (the list's title, what it holds)
    'bescheide': ('Bescheide', 'Was die Hochschule entscheidet: ein Brief im Postfach, als PDF in der Akte. ConCampus schickt jeden, wenn es so weit ist.'),
    'bescheinigungen': ('Bescheinigungen und Urkunden', 'Gesetzt aus den Daten der Person, wenn sie gebraucht werden.'),
    'eigene': ('Eigene Dokumente', 'Was die Hochschule selbst schreibt, als Brief, Bescheinigung oder Urkunde, und von Hand an ausgewählte Studierende schickt.'),
    'nachrichten': ('Nachrichten', 'Was ins Postfach und per E-Mail geht und nichts entscheidet. ConCampus schickt jede, wenn es so weit ist.'),
    'seiten': ('Auf Seiten', 'Was ConCampus auf seinen eigenen Seiten sagt.'),
}
OCCASIONS = {  # key: (kind, title): when ConCampus issues a document on paper
    'brief': ('bescheide', 'Brief'),  # any message, printed as a letter
    **{key: ('bescheide', spec.title) for key, spec in TEXTS.items() if spec.group == BESCHEID},
    **{key: ('bescheinigungen', title) for key, (title, _) in paper.DOCUMENTS.items() if key != 'brief'},
    FREE: ('eigene', 'Eigenes Dokument'),  # the Hochschule's own, as many as it writes, none of ConCampus's
}
assert len(OCCASIONS) == 2 + sum(s.group == BESCHEID for s in TEXTS.values()) + len(paper.DOCUMENTS) - 1, 'one key for two occasions'
WHEN = {  # when ConCampus sends or makes each document, as the lists say it
    'zulassung': 'Bei Zulassung nach der Eignungsprüfung', 'nachrueckliste': 'Bestanden, aber kein Platz frei', 'ablehnung': 'Bei Ablehnung einer Bewerbung',
    'immatrikulation': 'Bei der Immatrikulation', 'exmatrikulation': 'Bei der Exmatrikulation', 'schwerpunkt_eingetragen': 'Wenn ein Schwerpunkt eingetragen wird',
    'fachwechsel': 'Beim Wechsel von Studiengang oder Hauptfach', 'beurlaubung': 'Wenn ein Urlaubssemester bewilligt wird',
    'beurlaubung_aufgehoben': 'Wenn eine Beurlaubung aufgehoben wird', 'gaststudium': 'Bei Zulassung als Gast oder Nebenhörer',
    'langzeitgebuehr': 'Wenn die Langzeitstudiengebühr fällig wird', 'gebuehren_erlassen': 'Wenn Gebühren erlassen werden',
    'gebuehren_erhoben': 'Wenn ein Erlass der Gebühren endet', 'teilzeit': 'Wenn ein Teilzeitstudium bewilligt wird', 'teilzeit_aufgehoben': 'Wenn das Teilzeitstudium endet',
    'exmatrikulation_rueckmeldung': 'Exmatrikulation, weil die Rückmeldung fehlt', 'abschlussarbeit': 'Wenn eine Abschlussarbeit angemeldet wird',
    'verlaengerung': 'Wenn eine Abgabefrist verlängert wird', 'nachteilsausgleich': 'Wenn ein Nachteilsausgleich gewährt wird',
    'endgueltig': 'Wenn eine Prüfung endgültig nicht bestanden ist', 'einladung_eignungspruefung': 'Wenn ein Termin der Eignungsprüfung vergeben wird',
    'anerkennung': 'Wenn Leistungen anerkannt werden', 'lehrauftrag': 'Wenn ein Lehrauftrag erteilt wird', 'lehrauftrag_zurueckgezogen': 'Wenn ein Lehrauftrag zurückgezogen wird',
    'immatrikulationsbescheinigung': 'Auf Abruf, für ein Semester', 'studienzeiten': 'Auf Abruf, über alle Semester', 'exmatrikulationsbescheinigung': 'Auf Abruf, nach der Exmatrikulation',
    'zeugnis': 'Wenn alle Leistungen erbracht sind', 'leistungen': 'Auf Abruf, solange das Studium läuft', 'urkunde': 'Mit dem Zeugnis zum Abschluss',
    'supplement': 'Mit dem Zeugnis zum Abschluss', 'supplement_de': 'Mit dem Zeugnis zum Abschluss', 'notenliste': 'Nach einer Prüfung, für die Akte',
    'aushang': 'Nach einer Prüfung, zum Aushängen', 'leihvertrag': 'Wenn ein Instrument verliehen wird', 'vordruck': 'Beim Bewerben, für Unterlagen zum Unterschreiben',
    FREE: 'Von Hand: unter Studierende Personen wählen, dann „Nachricht schreiben“',
}
PLACES = {  # where in ConCampus what sends or makes a document happens: (the page's name, its URL name, who may open it)
    'bewerbungen': ('Bewerbungen', 'admissions:board', {'view_applications'}),
    'eignungspruefungen': ('Eignungsprüfungen', 'admissions:auditions', {'run_auditions'}),
    'studierende': ('Studierende', 'students:list', {'view_students'}),
    'rueckmeldung': ('Rückmeldung', 'students:reregistration', {'manage_reregistration'}),
    'pruefungen': ('Prüfungen', 'exams:index', {'manage_exams', 'decide_results', 'correct_scores'}),
    'anerkennung': ('Anerkennung', 'exams:recognition', {'recognise'}),
    'lehrveranstaltungen': ('Lehrveranstaltungen', 'courses:catalogue', {'manage_courses'}),
    'lehrauftraege': ('Lehraufträge', 'courses:assignments', {'grant_assignments'}),
    'lehrende': ('Lehrende', 'courses:staff', {'manage_staff'}),
    'instrumente': ('Leihinstrumente', 'instruments:index', {'manage_instruments'}),
    'raeume': ('Raumanfragen', 'rooms:requests', {'manage_bookings'}),
    'freigaben': ('Freigaben', 'setup:approvals', {'manage_accounts'}),
}
AT = {  # each document's and message's place (PLACES); none: what a person does for themselves, as confirming an address
    **dict.fromkeys(('zulassung', 'nachrueckliste', 'ablehnung', 'vordruck', 'bewerbung', 'unterlagen_fehlen', 'immatrikulation_offen', 'studienplatz_angenommen'), 'bewerbungen'),
    **dict.fromkeys(('einladung_eignungspruefung', 'naechste_stufe'), 'eignungspruefungen'),
    **dict.fromkeys((
        'immatrikulation', 'exmatrikulation', 'schwerpunkt_eingetragen', 'fachwechsel', 'beurlaubung', 'beurlaubung_aufgehoben', 'gaststudium', 'langzeitgebuehr',
        'gebuehren_erlassen', 'gebuehren_erhoben', 'teilzeit', 'teilzeit_aufgehoben', 'immatrikulationsbescheinigung', 'studienzeiten', 'exmatrikulationsbescheinigung',
        'schwerpunkt', FREE), 'studierende'),
    **dict.fromkeys(('exmatrikulation_rueckmeldung', 'rueckmeldung', 'zahlung_zurueckgenommen', 'rueckmeldung_offen'), 'rueckmeldung'),
    **dict.fromkeys((
        'abschlussarbeit', 'verlaengerung', 'nachteilsausgleich', 'endgueltig', 'zeugnis', 'leistungen', 'urkunde', 'supplement', 'supplement_de', 'notenliste', 'aushang',
        'einladung', 'pruefungsanmeldung', 'ergebnis', 'zeugnis_ueberfaellig', 'wertung_fehlt', 'korrektur', 'ersatz'), 'pruefungen'),
    'anerkennung': 'anerkennung', 'lehrauftrag': 'lehrauftraege', 'lehrauftrag_zurueckgezogen': 'lehrauftraege', 'konto': 'lehrende',
    **dict.fromkeys((
        'semesterplanung', 'entfaellt', 'eingetragen', 'ensemble', 'ensemble_zurueckgezogen', 'betreuung', 'kein_platz', 'ausgetragen', 'angefragt', 'warteliste',
        'gruppe', 'nachgerueckt', 'unterrichtszeit', 'abwesenheit'), 'lehrveranstaltungen'),
    **dict.fromkeys(('leihvertrag', 'leihe_erinnerung', 'leihe_zurueckgefordert', 'leihe_verlaengert', 'leihe'), 'instrumente'),
    **dict.fromkeys(('buchung_geaendert', 'buchung_storniert', 'termin_abgesagt', 'tag_abgesagt', 'termin_verlegt', 'raumanfrage_bestaetigt', 'raumanfrage_abgesagt'), 'raeume'),
    **dict.fromkeys(('berechtigung_erteilt', 'berechtigung_abgelehnt'), 'freigaben'),
}


def where(key, person):
    """Where in ConCampus what sends or makes a document happens, for whoever may open that page: {'name', 'url'}, else None."""
    from django.urls import reverse

    if key not in AT:
        return None
    title, name, roles = PLACES[AT[key]]
    return {'name': title, 'url': reverse(name)} if person.roles & roles else None


SENT = {  # when ConCampus sends each message, or where a page's text stands
    'email_bestaetigen': 'Wenn jemand eine neue E-Mail-Adresse einträgt', 'willkommen': 'Wenn sich jemand ein Konto anlegt', 'bewerbung': 'Wenn eine Bewerbung eingeht',
    'naechste_stufe': 'Wenn jemand eine Prüfungsstufe besteht', 'einladung': 'Wenn ein Prüfungstermin feststeht', 'unterlagen_fehlen': 'Wenn einer Bewerbung Unterlagen fehlen',
    'immatrikulation_offen': 'Wenn zur Immatrikulation noch etwas fehlt', 'studienplatz_angenommen': 'Wenn jemand den Studienplatz annimmt',
    'schwerpunkt': 'Wenn jemand einen Schwerpunkt wählt', 'semesterplanung': 'Wenn das nächste Semester geplant wird', 'entfaellt': 'Wenn eine Lehrveranstaltung entfällt',
    'eingetragen': 'Wenn jemand in eine Lehrveranstaltung eingetragen wird', 'ensemble': 'Wenn ein Ensemble angemeldet wird', 'ensemble_zurueckgezogen': 'Wenn ein Ensemble zurückgezogen wird',
    'betreuung': 'Wenn jemand ein Ensemble betreut', 'leihe_erinnerung': 'Wenn ein Leihinstrument überfällig ist', 'leihe_zurueckgefordert': 'Wenn ein Leihinstrument zurückgefordert wird',
    'leihe_verlaengert': 'Wenn eine Leihe verlängert wird', 'leihe': 'Wenn ein Instrument verliehen wird', 'konto': 'Wenn ein Konto für jemanden angelegt wird',
    'kein_platz': 'Wenn eine Lehrveranstaltung voll ist', 'ausgetragen': 'Wenn jemand ausgetragen wird', 'angefragt': 'Wenn jemand einen Platz anfragt',
    'warteliste': 'Wenn jemand auf die Warteliste kommt', 'gruppe': 'Wenn jemand in eine andere Gruppe kommt', 'nachgerueckt': 'Wenn jemand von der Warteliste nachrückt',
    'pruefungsanmeldung': 'Wenn jemand zu einer Prüfung angemeldet wird', 'ergebnis': 'Wenn ein Ergebnis freigegeben wird', 'zeugnis_ueberfaellig': 'Wenn ein Zeugnis überfällig ist',
    'wertung_fehlt': 'Wenn Prüfenden eine Wertung fehlt', 'korrektur': 'Wenn über eine Korrektur entschieden ist', 'zeugnis': 'Wenn ein Zeugnis ausgestellt wird',
    'buchung_geaendert': 'Wenn eine Raumbuchung geändert wird', 'buchung_storniert': 'Wenn eine Raumbuchung storniert wird', 'unterrichtszeit': 'Wenn eine Unterrichtszeit eingetragen wird',
    'termin_abgesagt': 'Wenn ein Termin abgesagt wird', 'ersatz': 'Wenn jemand in einer Kommission einspringt', 'berechtigung_erteilt': 'Wenn eine Berechtigung bewilligt wird',
    'berechtigung_abgelehnt': 'Wenn eine Berechtigung abgelehnt wird', 'abwesenheit': 'Wenn sich jemand von einem Termin abmeldet', 'tag_abgesagt': 'Wenn ein ganzer Tag abgesagt wird',
    'raumanfrage_bestaetigt': 'Wenn eine Raumanfrage bestätigt wird', 'raumanfrage_abgesagt': 'Wenn eine Raumanfrage abgesagt wird', 'termin_verlegt': 'Wenn ein Termin verlegt wird',
    'rueckmeldung': 'Wenn die Rückmeldung erledigt ist', 'zahlung_zurueckgenommen': 'Wenn eine Zahlung zurückgenommen wird', 'rueckmeldung_offen': 'Wenn zur Rückmeldung noch etwas fehlt',
    'barrierefreiheit_stand': 'Seite Barrierefreiheit', 'barrierefreiheit_erstellung': 'Seite Barrierefreiheit', 'barrierefreiheit_durchsetzung': 'Seite Barrierefreiheit',
    'anmeldung': 'Anmeldeseite', 'datenschutz_bewerbung': 'Beim Bewerben', 'einwilligung_gesundheit': 'Bei Unterlagen mit Gesundheitsdaten', 'bewerbung_hinweise': 'Beim Bewerben',
}


def vorschlag(occasion):
    """ConCampus's document for an occasion, unsaved, on its occasion's Vorlage."""
    return Paper(key=occasion, occasion=occasion, name=OCCASIONS[occasion][1], content=deepcopy(content_of(occasion)))


def papers():
    """{key: Paper}: for each occasion its document, the Hochschule's or ConCampus's, then its copies by name."""
    stored = {p.key: p for p in Paper.objects.prefetch_related('programs')}
    found = {}
    for occasion in OCCASIONS:
        if occasion != FREE:  # a letter of the Hochschule's own has none of ConCampus's beneath it
            found[occasion] = stored.pop(occasion, None) or vorschlag(occasion)
        found |= {p.key: p for p in sorted((p for p in stored.values() if p.occasion == occasion), key=lambda p: p.name)}
    for p in found.values():
        if p.content is None:  # nothing of its own written yet: as ConCampus has it
            p.content = deepcopy(content_of(p.occasion))
    return found


def letterheads():
    """{key: Letterhead}: ConCampus's own, as the Hochschule keeps them, then the Hochschule's others by name."""
    stored = {h.key: h for h in Letterhead.objects.all()}
    found = {key: stored.pop(key, None) or Letterhead(key=key, name=name, page=deepcopy(page)) for key, (name, page) in paper.STANDARD.items()}
    return found | {h.key: h for h in sorted(stored.values(), key=lambda h: h.name)}


def standard(occasion):
    """The Vorlage an occasion's documents stand on unless they choose another: a letter on the Brief."""
    return 'brief' if OCCASIONS[occasion][0] in ('bescheide', 'eigene') else paper.DOCUMENTS[occasion][1]


def variant(p):
    """Whether a document stands instead of its occasion's for some Studiengänge: a copy, not a letter of the Hochschule's own."""
    return p.occasion != FREE and p.key != p.occasion


def letterhead_of(p, heads):
    """The Vorlage a document stands on: the one it chose, while there is one, else its occasion's."""
    return heads.get(p.letterhead) or heads[standard(p.occasion)]


def on(head, found, heads):
    """The documents that stand on a Vorlage."""
    return [p for p in found.values() if letterhead_of(p, heads).key == head.key]


def programs(p):
    return list(p.programs.all()) if p.pk else []


def chosen(occasion, program, found):
    """The document a case gets: a copy of the occasion's for its Studiengang, else the occasion's own."""
    pk = getattr(program, 'pk', None)
    return next((p for p in found.values() if p.occasion == occasion and p.key != occasion and pk and any(x.pk == pk for x in programs(p))), found[occasion])


def bescheid(occasion, values, after=(), program=None):
    """A Bescheid for its case, as it is issued (core.texts.send, students.Study.notify): (its document's key, its
    subject, its text for the Postfach, its content as set)."""
    from .compose import html, letter_values, subject, text

    paper = chosen(occasion, program, papers())
    content, values = paper.content, letter_values(occasion, values, after)
    return paper.key, subject(content, values), text(content, values), html(content, values, {})


def issue(p, person, sender, study=None):
    """A letter of the Hochschule's own (FREE) to a person, filled in for them, chosen when writing to them
    (core.forms.ComposeForm): into their Postfach and by e-mail, with the HTML it was issued with, so it prints the same
    ever after."""
    from core.models import Message

    from .compose import html, letter_values, subject, text

    values = {'vorname': person.first_name, 'nachname': person.last_name, 'matrikelnummer': person.matriculation or ''}
    values |= {'studiengang': study.program.title, 'hauptfach': str(study.subject)} if study else {}
    values = letter_values(FREE, values) | {'absender': sender}
    return Message.send(person, subject(p.content, values) or p.name, text(p.content, values), sender=sender, template=p.key, content=html(p.content, values, {}))


def page(key):
    """The page a document is set on (core.paper.pdf): its Vorlage's; one no longer there, the Brief's."""
    found = papers()
    return letterhead_of(found.get(key) or found['brief'], letterheads()).page


def free_key(name, model=Paper, more=OCCASIONS):
    taken = set(model.objects.values_list('key', flat=True)) | set(more) | {'neu'}
    base = slugify(name)[:50] or 'dokument'
    return next(k for n in range(1, 1000) if (k := base if n == 1 else f'{base}-{n}') not in taken)


@dataclass(frozen=True)
class Text:
    """A message or a page's text: worded on its own (core.texts)."""
    kind: str
    key: str
    title: str


def texts():
    """{(kind, key): Text}: the messages, then the texts on ConCampus's own pages."""
    found = [Text('nachrichten', key, spec.title) for key, spec in TEXTS.items() if spec.group == NACHRICHT]
    found += [Text('seiten', key, spec.title) for key, spec in TEXTS.items() if spec.group == BAUSTEIN and not spec.on]
    return {(t.kind, t.key): t for t in found}
