"""Every letter and message the platform sends, and the text blocks on its documents, with neutral defaults. A
Hochschule rewords them under Einstellungen; then a Text record holds its wording. Placeholders in braces: {vorname}."""
from dataclasses import dataclass
from string import Formatter

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Institution, Message, Text

ALWAYS = ('vorname', 'nachname', 'hochschule', 'anschrift', 'ort', 'datum')
STUDY = ('studiengang', 'hauptfach', 'semester', 'matrikelnummer')


@dataclass(frozen=True)
class Spec:
    group: str
    title: str
    subject: str
    body: str
    placeholders: tuple = ()
    appeal: bool = False  # turns someone down: the Rechtsbehelfsbelehrung follows

    @property
    def allowed(self):
        return ALWAYS + self.placeholders


BESCHEID, NACHRICHT, BAUSTEIN = 'Bescheide', 'Nachrichten', 'Bausteine'
TEXTS = {
    'zulassung': Spec(
        BESCHEID, 'Zulassung', 'Zulassungsbescheid',
        'Sie haben die Eignungsprüfung bestanden. Wir freuen uns, Ihnen einen Studienplatz im Studiengang {studiengang}, '
        'Hauptfach {hauptfach}, ab dem {semester} anzubieten.\n\nBitte nehmen Sie den Studienplatz bis zum {frist} im Portal an.',
        STUDY + ('frist',),
    ),
    'nachrueckliste': Spec(
        BESCHEID, 'Nachrückliste', 'Bescheid: Nachrückliste',
        'Sie haben die Eignungsprüfung bestanden. Alle Studienplätze im Hauptfach {hauptfach} sind derzeit vergeben; '
        'Sie stehen auf der Nachrückliste. Wird ein Platz frei, erhalten Sie umgehend Nachricht.',
        STUDY,
    ),
    'ablehnung': Spec(
        BESCHEID, 'Ablehnung', 'Bescheid zur Eignungsprüfung',
        'Leider können wir Ihnen für den Studiengang {studiengang}, Hauptfach {hauptfach}, keinen Studienplatz anbieten. '
        'Wir danken Ihnen für Ihre Bewerbung und wünschen Ihnen für Ihren weiteren Weg alles Gute.',
        STUDY, appeal=True,
    ),
    'immatrikulation': Spec(
        BESCHEID, 'Immatrikulation', 'Immatrikulationsbescheid',
        'Willkommen an der {hochschule}. Sie sind ab dem {semester} im Studiengang {studiengang}, Hauptfach {hauptfach}, '
        'immatrikuliert.\n\nIhre Matrikelnummer: {matrikelnummer}',
        STUDY,
    ),
    'exmatrikulation': Spec(
        BESCHEID, 'Exmatrikulation', 'Exmatrikulationsbescheid',
        'Sie sind zum {datum} aus dem Studiengang {studiengang}, Hauptfach {hauptfach}, exmatrikuliert. Grund: {grund}.',
        STUDY + ('grund',),
    ),
    'exmatrikulation_rueckmeldung': Spec(
        BESCHEID, 'Exmatrikulation ohne Rückmeldung', 'Exmatrikulationsbescheid',
        'Sie haben sich nicht fristgerecht zum {rueckmeldesemester} zurückgemeldet und sind daher zum {datum} aus dem '
        'Studiengang {studiengang}, Hauptfach {hauptfach}, exmatrikuliert.',
        STUDY + ('rueckmeldesemester',), appeal=True,
    ),
    'willkommen': Spec(NACHRICHT, 'Konto angelegt', 'Willkommen', 'Ihr Konto ist angelegt. Sie können sich jetzt für die Eignungsprüfung bewerben.'),
    'bewerbung': Spec(
        NACHRICHT, 'Bewerbung eingegangen', 'Bewerbung eingegangen',
        'Ihre Bewerbung für {studiengang}, Hauptfach {hauptfach}, ist eingegangen. '
        'Laden Sie bis zum {frist} Ihr Video und Ihre Unterlagen unter „Meine Bewerbung“ hoch.',
        ('studiengang', 'hauptfach', 'frist'),
    ),
    'naechste_stufe': Spec(
        NACHRICHT, 'Nächste Prüfungsstufe', 'Einladung zur nächsten Prüfungsstufe',
        'Sie haben „{pruefung}“ bestanden und sind zu {stufen} eingeladen. Den Termin erhalten Sie gesondert.', ('pruefung', 'stufen'),
    ),
    'einladung': Spec(
        NACHRICHT, 'Einladung zum Prüfungstermin', 'Einladung: {pruefung}',
        'Ihr Termin: {termin} Uhr, {raum}.\n\nBitte erscheinen Sie 30 Minuten vorher.', ('pruefung', 'termin', 'raum'),
    ),
    'studienplatz_angenommen': Spec(
        NACHRICHT, 'Studienplatz angenommen', 'Studienplatz angenommen',
        'Sie haben den Studienplatz angenommen. Bitte überweisen Sie den Semesterbeitrag von {beitrag} € bis zum {frist}, und bitten '
        'Sie Ihre Krankenkasse, Ihren Versicherungsstatus elektronisch an die Hochschule zu melden. Sobald beides da ist, werden Sie '
        'immatrikuliert und erhalten Ihre Matrikelnummer.',
        ('beitrag', 'frist'),
    ),
    'eingetragen': Spec(NACHRICHT, 'In eine Veranstaltung eingetragen', 'Eingetragen: {veranstaltung}', 'Sie wurden für „{veranstaltung}“ im {semester} eingetragen.', ('veranstaltung', 'semester')),
    'pruefungsanmeldung': Spec(NACHRICHT, 'Prüfungsanmeldung', 'Prüfungsanmeldung: {pruefung}', 'Sie sind zur Prüfung {pruefung} ({modul}) angemeldet.', ('pruefung', 'modul')),
    'ergebnis': Spec(NACHRICHT, 'Ergebnis freigegeben', 'Ergebnis: {pruefung}', 'Ihr Prüfungsergebnis ist freigegeben.', ('pruefung',)),
    'anerkennung': Spec(NACHRICHT, 'Anerkennung', 'Anerkennung: {modul}', 'Anerkannt: {modul}.\nHerkunft: {herkunft}', ('modul', 'herkunft')),
    'buchung_geaendert': Spec(NACHRICHT, 'Buchung geändert', 'Buchung geändert: {buchung}', '{von} hat Ihre Buchung geändert: {raum}, {termin}.', ('buchung', 'von', 'raum', 'termin')),
    'buchung_storniert': Spec(
        NACHRICHT, 'Buchung storniert', 'Buchung storniert: {buchung}', '{von} hat {anzahl} Termin(e) ab {termin} in {raum} storniert.',
        ('buchung', 'von', 'anzahl', 'termin', 'raum'),
    ),
    'rueckmeldung': Spec(NACHRICHT, 'Rückmeldung erledigt', 'Rückmeldung {rueckmeldesemester}', 'Ihr Semesterbeitrag ist eingegangen. Sie sind für das {rueckmeldesemester} zurückgemeldet.', ('rueckmeldesemester',)),
    'rueckmeldung_offen': Spec(
        NACHRICHT, 'Rückmeldung unvollständig', 'Rückmeldung {rueckmeldesemester}',
        'Ihr Semesterbeitrag ist eingegangen. Für die Rückmeldung fehlt noch: {fehlt}.', ('rueckmeldesemester', 'fehlt'),
    ),
    'rechtsbehelf': Spec(
        BAUSTEIN, 'Rechtsbehelfsbelehrung', '',
        'Rechtsbehelfsbelehrung: Gegen diesen Bescheid kann innerhalb eines Monats nach seiner Bekanntgabe Widerspruch erhoben werden. '
        'Der Widerspruch ist schriftlich oder zur Niederschrift bei der {hochschule}, {anschrift}, einzulegen.',
    ),
    'absender_bescheide': Spec(BAUSTEIN, 'Absender der Bescheide', '', '{hochschule}'),
    'absender_bescheinigungen': Spec(BAUSTEIN, 'Absender der Immatrikulationsbescheinigung', '', '{hochschule}'),
    'absender_leistungen': Spec(BAUSTEIN, 'Absender der Leistungsübersichten und Zeugnisse', '', '{hochschule}'),
    'unterschrift_zeugnisse': Spec(BAUSTEIN, 'Unterschrift auf Zeugnissen', '', 'Vorsitz Prüfungsausschuss'),
    'anmeldung': Spec(BAUSTEIN, 'Begrüßung auf der Anmeldeseite', '', 'Campus ist die Verwaltung von Studium, Lehre, Prüfungen, Aufnahme und Räumen der {hochschule}.'),
    'bewerbung_hinweise': Spec(
        BAUSTEIN, 'Hinweise beim Bewerben', '',
        'Pro Studiengang und Hauptfach ist eine Bewerbung möglich. Nach dem Absenden laden Sie unter „Meine Bewerbung“ Ihr Video und Ihre '
        'Unterlagen hoch. Bis zur Frist können Sie alles ändern.\n\nEinladungen und Bescheide erhalten Sie in Ihr Postfach.',
    ),
}


class Values(dict):
    def __missing__(self, key):
        return f'[{key}]'  # only in previews: every text's own placeholders are always given


def check(template, allowed):
    """Raise ValidationError unless every placeholder is one of `allowed`, plainly written."""
    try:
        fields = [(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(template) if name is not None]
    except ValueError:
        raise ValidationError('Geschweifte Klammern nur um Platzhalter, z. B. {vorname}.')
    for name, spec, conversion in fields:
        if name not in allowed or spec or conversion:
            raise ValidationError(f'Unbekannter Platzhalter {{{name}}}. Möglich: {", ".join("{" + a + "}" for a in allowed)}.')


def wording(key):
    """(subject, body) as the Hochschule words it, else the default."""
    text = Text.objects.filter(key=key).first()
    return (text.subject, text.body) if text else (TEXTS[key].subject, TEXTS[key].body)


def basics():
    """The placeholders every text has: the Hochschule's data and today."""
    institution = Institution.get()
    return {
        'hochschule': institution.name, 'anschrift': ', '.join(institution.address.splitlines()), 'ort': institution.place,
        'datum': f'{timezone.localdate():%d.%m.%Y}',
    }


def render(key, **values):
    """(subject, body) with the placeholders filled in; a Bescheid that turns someone down gets the Rechtsbehelfsbelehrung."""
    values = Values(basics(), **{name: str(value) for name, value in values.items()})
    subject, body = (part.format_map(values) for part in wording(key))
    if TEXTS[key].appeal:
        body += '\n\n' + wording('rechtsbehelf')[1].format_map(values)
    return subject, body


def block(key, **values):
    return render(key, **values)[1]


def send(key, person, link='', **values):
    subject, body = render(key, vorname=person.first_name, nachname=person.last_name, **values)
    return Message.send(person, subject, body, link)
