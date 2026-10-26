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
        BESCHEID, 'Studienplatzangebot', 'Bescheid über die Eignungsprüfung: Studienplatzangebot',
        'Sie haben die Eignungsprüfung bestanden. Wir freuen uns, Ihnen einen Studienplatz im Studiengang {studiengang}, '
        'Hauptfach {hauptfach}, ab dem {semester} anzubieten.\n\nBitte nehmen Sie den Studienplatz bis zum {frist} im Portal an.',
        STUDY + ('frist',),
    ),
    'nachrueckliste': Spec(
        BESCHEID, 'Nachrückliste', 'Bescheid über die Eignungsprüfung: Nachrückliste',
        'Sie haben die Eignungsprüfung bestanden. Alle Studienplätze im Hauptfach {hauptfach} sind derzeit vergeben; '
        'Sie stehen auf der Nachrückliste. Wird ein Platz frei, erhalten Sie umgehend Nachricht.',
        STUDY,
    ),
    'ablehnung': Spec(
        BESCHEID, 'Ablehnung', 'Bescheid über die Eignungsprüfung',
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
        'Sie sind zum {zum} aus dem Studiengang {studiengang}, Hauptfach {hauptfach}, exmatrikuliert. Grund: {grund}.',
        STUDY + ('grund', 'zum'),
    ),
    'exmatrikulation_rueckmeldung': Spec(
        BESCHEID, 'Exmatrikulation ohne Rückmeldung', 'Exmatrikulationsbescheid',
        'Sie haben sich nicht fristgerecht zum {rueckmeldesemester} zurückgemeldet und sind daher zum {zum} aus dem '
        'Studiengang {studiengang}, Hauptfach {hauptfach}, exmatrikuliert.',
        STUDY + ('rueckmeldesemester', 'zum'), appeal=True,
    ),
    'abschlussarbeit': Spec(
        BESCHEID, 'Abschlussarbeit angemeldet', 'Ausgabe des Themas der Abschlussarbeit',
        'Ihre Abschlussarbeit im Modul {modul} ist angemeldet.\n\nThema: {thema}\nGutachter:innen: {gutachter}\nAbgabe bis: {frist}\n\n'
        'Bitte laden Sie die Arbeit bis dahin unter „Prüfungen“ als PDF hoch.',
        STUDY + ('modul', 'thema', 'gutachter', 'frist'),
    ),
    'verlaengerung': Spec(
        BESCHEID, 'Abgabefrist verlängert', 'Verlängerung der Bearbeitungszeit',
        'Die Bearbeitungszeit Ihrer Abschlussarbeit „{thema}“ ist verlängert. Neue Abgabefrist: {frist}.', STUDY + ('thema', 'frist'),
    ),
    'endgueltig': Spec(
        BESCHEID, 'Endgültig nicht bestanden', 'Bescheid: Prüfung endgültig nicht bestanden',
        'Sie haben die Prüfung „{pruefung}“ im Modul {modul} auch im {versuch}. Versuch nicht bestanden. Weitere Versuche sieht die '
        'Prüfungsordnung nicht vor: Die Prüfung ist endgültig nicht bestanden, der Prüfungsanspruch im Studiengang {studiengang} ist '
        'erloschen. Über die Exmatrikulation erhalten Sie einen gesonderten Bescheid.',
        STUDY + ('pruefung', 'modul', 'versuch'), appeal=True,
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
        'Ihr Termin: {termin} Uhr, {raum}.\n\nBitte erscheinen Sie 30 Minuten vorher und bringen Sie einen Lichtbildausweis mit, wenn Sie '
        'begleitet werden auch die Noten für die Begleitung.', ('pruefung', 'termin', 'raum'),
    ),
    'unterlagen_fehlen': Spec(
        NACHRICHT, 'Bewerbung unvollständig', 'Ihre Bewerbung: es fehlt noch etwas',
        'Zu Ihrer Bewerbung für {studiengang}, Hauptfach {hauptfach}, fehlt noch:\n{fehlt}\n\nBitte laden Sie es bis zum {frist} unter „Meine Bewerbung“ '
        'hoch. Unvollständige Bewerbungen können nicht berücksichtigt werden.',
        STUDY + ('fehlt', 'frist'),
    ),
    'immatrikulation_offen': Spec(
        NACHRICHT, 'Immatrikulation offen', 'Ihre Immatrikulation: es fehlt noch etwas',
        'Sie haben Ihren Studienplatz im Studiengang {studiengang}, Hauptfach {hauptfach}, angenommen. Für die Immatrikulation ist noch offen:\n{fehlt}',
        STUDY + ('fehlt',),
    ),
    'studienplatz_angenommen': Spec(
        NACHRICHT, 'Studienplatz angenommen', 'Studienplatz angenommen',
        'Sie haben den Studienplatz angenommen. Bitte überweisen Sie den Semesterbeitrag von {beitrag} € bis zum {frist}, und bitten '
        'Sie Ihre Krankenkasse, Ihren Versicherungsstatus elektronisch an die Hochschule zu melden. Sobald beides da ist, werden Sie '
        'immatrikuliert und erhalten Ihre Matrikelnummer.',
        ('beitrag', 'frist'),
    ),
    'eingetragen': Spec(NACHRICHT, 'In eine Veranstaltung eingetragen', 'Eingetragen: {veranstaltung}', 'Sie wurden für „{veranstaltung}“ im {semester} eingetragen.', ('veranstaltung', 'semester')),
    'ensemble': Spec(
        NACHRICHT, 'Mit einem Ensemble angemeldet', 'Ensemble: {besetzung}',
        '{von} hat Sie mit „{besetzung}“ für „{veranstaltung}“ angemeldet. Die Betreuung teilen die Lehrenden zu.', ('veranstaltung', 'besetzung', 'von'),
    ),
    'betreuung': Spec(
        NACHRICHT, 'Betreuung eines Ensembles', 'Betreuung: {besetzung}',
        '„{besetzung}“ wird in „{veranstaltung}“ von {betreuung} betreut.', ('veranstaltung', 'besetzung', 'betreuung'),
    ),
    'leihe': Spec(
        NACHRICHT, 'Instrument ausgeliehen', 'Leihinstrument: {instrument}',
        'Sie haben {instrument} ausgeliehen. Bitte geben Sie es bis zum {frist} zurück; danach ist keine Rückmeldung möglich, bis es zurück ist.',
        ('instrument', 'frist'),
    ),
    'konto': Spec(
        NACHRICHT, 'Einladung zum Konto', 'Ihr Zugang zu {hochschule}',
        'Für Sie ist ein Konto angelegt, Benutzername: {benutzername}.\n\nLegen Sie hier Ihr Passwort fest und melden Sie sich an: {zugang}\n\n'
        'Der Link gilt drei Tage und nur einmal.',
        ('benutzername', 'zugang'),
    ),
    'kein_platz': Spec(
        NACHRICHT, 'Kein Platz in einer Veranstaltung', 'Kein Platz: {veranstaltung}',
        'Für „{veranstaltung}“ im {semester} konnten wir Ihnen leider keinen Platz geben.', ('veranstaltung', 'semester'),
    ),
    'ausgetragen': Spec(NACHRICHT, 'Aus einer Prüfung ausgetragen', 'Ausgetragen: {veranstaltung}', 'Ihre Anmeldung zu „{veranstaltung}“ im {semester} ist gelöscht.', ('veranstaltung', 'semester')),
    'pruefungsanmeldung': Spec(NACHRICHT, 'Prüfungsanmeldung', 'Prüfungsanmeldung: {pruefung}', 'Sie sind zur Prüfung {pruefung} ({modul}) angemeldet.', ('pruefung', 'modul')),
    'ergebnis': Spec(NACHRICHT, 'Ergebnis freigegeben', 'Ergebnis: {pruefung}', 'Ihr Prüfungsergebnis ist freigegeben.', ('pruefung',)),
    'wertung_fehlt': Spec(
        NACHRICHT, 'Wertung fehlt', 'Ihre Wertung fehlt: {pruefung}',
        'Für „{pruefung}“ fehlt noch Ihre Wertung ({anzahl}). Bitte tragen Sie sie ein, damit das Ergebnis freigegeben werden kann.',
        ('pruefung', 'anzahl'),
    ),
    'anerkennung': Spec(NACHRICHT, 'Anerkennung', 'Anerkennung: {modul}', 'Anerkannt: {modul}.\nHerkunft: {herkunft}', ('modul', 'herkunft')),
    'buchung_geaendert': Spec(NACHRICHT, 'Buchung geändert', 'Buchung geändert: {buchung}', '{von} hat Ihre Buchung geändert: {raum}, {termin}.', ('buchung', 'von', 'raum', 'termin')),
    'buchung_storniert': Spec(
        NACHRICHT, 'Buchung storniert', 'Buchung storniert: {buchung}', '{von} hat {anzahl} Termin(e) ab {termin} in {raum} storniert.',
        ('buchung', 'von', 'anzahl', 'termin', 'raum'),
    ),
    'unterrichtszeit': Spec(
        NACHRICHT, 'Unterrichtszeit eingetragen', 'Unterrichtszeit: {veranstaltung}', '{von} hat Ihre Unterrichtszeit eingetragen: {zeit} in {raum}, ab {ab}.',
        ('veranstaltung', 'von', 'zeit', 'raum', 'ab'),
    ),
    'termin_abgesagt': Spec(
        NACHRICHT, 'Termin abgesagt', 'Abgesagt: {veranstaltung}', '{von} hat „{veranstaltung}“ am {termin} in {raum} abgesagt.',
        ('veranstaltung', 'termin', 'raum', 'von'),
    ),
    'termin_verlegt': Spec(
        NACHRICHT, 'Termin verlegt', 'Verlegt: {veranstaltung}', '{von} hat „{veranstaltung}“ vom {termin} auf {neuer_termin} in {raum} verlegt.',
        ('veranstaltung', 'termin', 'neuer_termin', 'raum', 'von'),
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
    'unterschrift_zeugnisse': Spec(BAUSTEIN, 'Unterschriften auf Zeugnissen', '', 'Vorsitz Prüfungsausschuss'),  # one per line
    'ds_hochschule': Spec(BAUSTEIN, 'Diploma Supplement: Hochschule und Status', '', '{hochschule}\nUniversity of Music, state institution'),
    'ds_hochschulsystem': Spec(
        BAUSTEIN, 'Diploma Supplement: Hochschulsystem (Abschnitt 8)', '',
        'Higher education in Germany is offered at three types of institutions: Universitäten (universities), Fachhochschulen or '
        'Hochschulen für Angewandte Wissenschaften (universities of applied sciences), and Kunst- und Musikhochschulen (universities of '
        'art and music), which prepare students for artistic careers and for teaching the arts. All are state institutions or '
        'state-recognised, and all award Bachelor and Master degrees.\n\n'
        'Programmes are structured in two cycles. Bachelor programmes last six to eight semesters (180 to 240 ECTS credits) and lead to a '
        'first degree that qualifies for a profession. Master programmes last two to four semesters (60 to 120 ECTS credits) and lead to a '
        'second degree; together with the Bachelor they amount to 300 ECTS credits. Some programmes lead to a Diplom or a Staatsprüfung '
        '(state examination) in one integrated cycle, equivalent to the Master level. Bachelor degrees are at level 6, Master and integrated '
        'degrees at level 7 of the German and the European Qualifications Framework (DQR, EQF). Programmes are accredited under the rules '
        'of the Akkreditierungsrat.\n\n'
        'Grades range from 1.0 (sehr gut, very good) to 4.0 (ausreichend, sufficient); 5.0 (nicht ausreichend) is a fail. Access to higher '
        'education requires the Allgemeine Hochschulreife or an equivalent; universities of art and music also admit by an entrance '
        'examination of artistic aptitude, by way of exception without the Hochschulreife.\n\n'
        'National sources of information: Kultusministerkonferenz (KMK), www.kmk.org; Hochschulrektorenkonferenz (HRK), www.hrk.de; '
        'Akkreditierungsrat, www.akkreditierungsrat.de; German ENIC-NARIC, www.kmk.org/zab.',
    ),
    'anmeldung': Spec(BAUSTEIN, 'Begrüßung auf der Anmeldeseite', '', 'Campus ist die Verwaltung von Studium, Lehre, Prüfungen, Aufnahme und Räumen der {hochschule}.'),
    'datenschutz_bewerbung': Spec(
        BAUSTEIN, 'Datenschutz beim Bewerben', '',
        'Ihre Angaben verarbeitet die {hochschule} nur für das Bewerbungsverfahren und, wenn Sie hier studieren, für Ihr Studium. '
        'Sie sehen nur die Stellen, die damit befasst sind; die Prüfungskommission sieht Ihre künstlerischen Unterlagen.',
    ),
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


def mail(key, person, **values):
    """By e-mail only, for what must not stay readable in the Postfach, where offices see it too: a sign-in link."""
    Message.mail(person, *render(key, vorname=person.first_name, nachname=person.last_name, **values))
