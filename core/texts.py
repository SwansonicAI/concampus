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
        'Ihre Bewerbung für den Studiengang {studiengang}, Hauptfach {hauptfach}, zum {semester} wird abgelehnt.\n\n'
        'Begründung: {begruendung}\n\nWir danken Ihnen für Ihre Bewerbung und wünschen Ihnen für Ihren weiteren Weg alles Gute.',
        STUDY + ('begruendung',), appeal=True,
    ),
    'immatrikulation': Spec(
        BESCHEID, 'Immatrikulation', 'Immatrikulationsbescheid',
        'Willkommen an der {hochschule}. Sie sind ab dem {semester} im Studiengang {studiengang}, Hauptfach {hauptfach}, '
        'immatrikuliert.\n\nIhre Matrikelnummer: {matrikelnummer}',
        STUDY,
    ),
    'exmatrikulation': Spec(
        BESCHEID, 'Exmatrikulation', 'Exmatrikulationsbescheid',
        'Sie sind mit Ablauf des {zum} aus dem Studiengang {studiengang}, Hauptfach {hauptfach}, exmatrikuliert. Grund: {grund}.',
        STUDY + ('grund', 'zum'),
    ),
    'schwerpunkt_eingetragen': Spec(
        BESCHEID, 'Schwerpunkt eingetragen', 'Bescheid über den Schwerpunkt',
        'Für Ihr Studium im Studiengang {studiengang}, Hauptfach {hauptfach}, ist als Schwerpunkt {schwerpunkt} eingetragen. Grund: {grund}.',
        STUDY + ('schwerpunkt', 'grund'),
    ),
    'fachwechsel': Spec(
        BESCHEID, 'Fachwechsel', 'Bescheid über den Fachwechsel',
        'Auf Ihren Antrag studieren Sie ab dem {semester} im Studiengang {studiengang}, Hauptfach {hauptfach}, im {fachsemester}. '
        'Fachsemester. Ihr Studium im Studiengang {bisher} ist damit beendet.\n\nBereits erbrachte Leistungen rechnet der '
        'Prüfungsausschuss auf Antrag an.',
        STUDY + ('fachsemester', 'bisher'),
    ),
    'beurlaubung': Spec(
        BESCHEID, 'Beurlaubung', 'Bescheid über die Beurlaubung',
        'Sie sind für das {rueckmeldesemester} im Studiengang {studiengang}, Hauptfach {hauptfach}, beurlaubt. Grund: {grund}.\n\n'
        'Das Urlaubssemester zählt nicht als Fachsemester.',
        STUDY + ('rueckmeldesemester', 'grund'),
    ),
    'beurlaubung_aufgehoben': Spec(
        BESCHEID, 'Beurlaubung aufgehoben', 'Bescheid über die Aufhebung der Beurlaubung',
        'Ihre Beurlaubung für das {rueckmeldesemester} im Studiengang {studiengang}, Hauptfach {hauptfach}, ist aufgehoben. '
        'Das Semester zählt als Fachsemester.',
        STUDY + ('rueckmeldesemester',),
    ),
    'exmatrikulation_rueckmeldung': Spec(
        BESCHEID, 'Exmatrikulation ohne Rückmeldung', 'Exmatrikulationsbescheid',
        'Sie haben sich nicht fristgerecht zum {rueckmeldesemester} zurückgemeldet und sind daher mit Ablauf des {zum} aus dem '
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
    'nachteilsausgleich': Spec(
        BESCHEID, 'Nachteilsausgleich', 'Bescheid über einen Nachteilsausgleich',
        'Auf Ihren Antrag gewährt Ihnen der Prüfungsausschuss für Prüfungen im Studiengang {studiengang} einen Nachteilsausgleich: '
        '{massnahme}.\n\nDie Prüfenden erfahren nur diese Maßnahme, nicht ihren Grund.',
        STUDY + ('massnahme',),
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
    'einladung_eignungspruefung': Spec(
        BESCHEID, 'Einladung zur Eignungsprüfung', 'Einladung: {pruefung}',
        'Sie sind zur {pruefung} eingeladen.\n\nTermin: {termin} Uhr\nOrt: {raum}\n\nIhr Programm:\n{programm}\n\n'
        'Wenn Sie bestehen, folgen:\n{danach}\n\nBitte erscheinen Sie 30 Minuten vorher und bringen Sie einen Lichtbildausweis mit. '
        'Dieses Schreiben können Sie für ein Visum vorlegen.', STUDY + ('pruefung', 'termin', 'raum', 'programm', 'danach'),
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
    'schwerpunkt': Spec(
        NACHRICHT, 'Schwerpunkt gewählt', 'Schwerpunkt: {schwerpunkt}',
        'Sie haben im Studiengang {studiengang} den Schwerpunkt {schwerpunkt} gewählt.\n\n{inhalt}\n\n'
        'Belegen Sie die Lehrveranstaltungen dazu in den genannten Semestern. Ändern lässt sich die Wahl {aendern}.',
        ('studiengang', 'schwerpunkt', 'inhalt', 'aendern'),
    ),
    'semesterplanung': Spec(
        NACHRICHT, 'Planung des nächsten Semesters', 'Planung {semester}: bitte bis zum {frist} bestätigen',
        'Für das {semester} sind Ihre Lehrveranstaltungen übernommen:\n{veranstaltungen}\n\nBitte bestätigen Sie bis zum {frist} je '
        'Veranstaltung, ob sie so stattfindet; ändern Sie, was anders wird, oder sagen Sie ab, was entfällt. Studierende sehen das '
        'Semester erst, wenn es veröffentlicht ist.',
        ('semester', 'frist', 'veranstaltungen'),
    ),
    'entfaellt': Spec(
        NACHRICHT, 'Lehrveranstaltung entfällt', 'Entfällt: {veranstaltung}',
        '„{veranstaltung}“ im {semester} entfällt. Grund: {grund}.\n\nIhre Anmeldung oder Anfrage ist damit aufgehoben; die Termine sind '
        'abgesagt. Bitte melden Sie sich gegebenenfalls zu einer anderen Veranstaltung an.',
        ('veranstaltung', 'semester', 'grund'),
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
    'leihe_erinnerung': Spec(
        NACHRICHT, 'Leihinstrument überfällig', 'Bitte zurückgeben: {instrument}',
        'Sie haben {instrument} ausgeliehen; die Rückgabe war bis zum {frist} vereinbart. Bitte bringen Sie es bald zurück oder melden Sie sich, '
        'wenn Sie es länger brauchen. Solange es überfällig ist, ist keine Rückmeldung möglich.', ('instrument', 'frist'),
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
    'anerkennung': Spec(
        BESCHEID, 'Anerkennung von Leistungen', 'Bescheid: Anerkennung',
        'Auf Ihren Antrag werden Leistungen aus {herkunft} anerkannt für: {modul}. Sie stehen in Ihrer Leistungsübersicht.', STUDY + ('modul', 'herkunft'),
    ),
    'zeugnis': Spec(
        NACHRICHT, 'Zeugnis ausgestellt', 'Ihr Zeugnis ist ausgestellt',
        'Ihr Zeugnis im Studiengang {studiengang} ist ausgestellt. Abschriften von Zeugnis, Urkunde und Diploma Supplement finden Sie in Ihrer '
        'Leistungsübersicht; die unterschriebenen Originale erhalten Sie von der Hochschule. Die Exmatrikulation folgt.', ('studiengang',),
    ),
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
    'ersatz': Spec(
        NACHRICHT, 'Ersatz in einer Kommission', 'Ersatz: {pruefung}',
        '{von} hat Sie bestellt, in „{pruefung}“ {kandidat} an Stelle von {fuer} zu werten, die oder der befangen ist. Sie sehen und werten nur diese Person.',
        ('pruefung', 'kandidat', 'fuer', 'von'),
    ),
    'abwesenheit': Spec(
        NACHRICHT, 'Abmeldung von einem Termin', 'Abgemeldet: {veranstaltung}', '{von} kommt nicht zu „{veranstaltung}“ am {termin}.',
        ('veranstaltung', 'termin', 'von'),
    ),
    'tag_abgesagt': Spec(
        NACHRICHT, 'Termine eines Tages abgesagt', 'Abgesagt: Ihre Termine am {tag}', '{von} hat die Termine am {tag} abgesagt:\n{termine}',
        ('tag', 'termine', 'von'),
    ),
    'raumanfrage_bestaetigt': Spec(
        NACHRICHT, 'Raumanfrage bestätigt', 'Bestätigt: {raum}, {termin}', 'Ihre Anfrage ist bestätigt: {raum}, {termin}, für „{buchung}“.',
        ('raum', 'termin', 'buchung'),
    ),
    'raumanfrage_abgesagt': Spec(
        NACHRICHT, 'Raumanfrage abgesagt', 'Nicht möglich: {raum}, {termin}', 'Ihre Anfrage für {raum}, {termin}, „{buchung}“, ist leider nicht möglich. {grund}',
        ('raum', 'termin', 'buchung', 'grund'),
    ),
    'termin_verlegt': Spec(
        NACHRICHT, 'Termin verlegt', 'Verlegt: {veranstaltung}', '{von} hat „{veranstaltung}“ vom {termin} auf {neuer_termin} in {raum} verlegt.',
        ('veranstaltung', 'termin', 'neuer_termin', 'raum', 'von'),
    ),
    'rueckmeldung': Spec(NACHRICHT, 'Rückmeldung erledigt', 'Rückmeldung {rueckmeldesemester}', 'Ihr Semesterbeitrag ist eingegangen. Sie sind für das {rueckmeldesemester} zurückgemeldet.', ('rueckmeldesemester',)),
    'zahlung_zurueckgenommen': Spec(
        NACHRICHT, 'Zahlung zurückgenommen', 'Rückmeldung {rueckmeldesemester}',
        'Die Buchung eines Semesterbeitrags für Sie am {datum} war ein Irrtum und ist zurückgenommen. Ihre Rückmeldung zum '
        '{rueckmeldesemester} steht damit noch aus.', ('rueckmeldesemester', 'datum'),
    ),
    'rueckmeldung_offen': Spec(
        NACHRICHT, 'Rückmeldung unvollständig', 'Rückmeldung {rueckmeldesemester}',
        'Ihr Semesterbeitrag ist eingegangen. Für die Rückmeldung fehlt noch: {fehlt}.', ('rueckmeldesemester', 'fehlt'),
    ),
    'rechtsbehelf': Spec(
        BAUSTEIN, 'Rechtsbehelfsbelehrung', '',
        'Rechtsbehelfsbelehrung: Gegen diesen Bescheid kann innerhalb eines Monats nach seiner Bekanntgabe Widerspruch erhoben werden. '
        'Der Widerspruch ist schriftlich oder zur Niederschrift bei der {hochschule}, {anschrift}, einzulegen.',
    ),
    'erstattung': Spec(
        BAUSTEIN, 'Erstattung des Semesterbeitrags', '',
        'Den Semesterbeitrag für das {rueckmeldesemester} erstattet auf Antrag das Studierendenwerk, sofern Sie das '
        'Deutschlandsemesterticket nicht abgerufen haben.', ('rueckmeldesemester',),
    ),
    'auslaenderbehoerde': Spec(
        BAUSTEIN, 'Hinweis zur Ausländerbehörde', '',
        'Bitte teilen Sie der Ausländerbehörde innerhalb von zwei Wochen mit, dass Ihr Studium hier beendet ist (§ 82 Abs. 6 Aufenthaltsgesetz).',
    ),
    'absender_bescheide': Spec(BAUSTEIN, 'Absender der Bescheide', '', '{hochschule}'),
    'absender_bescheinigungen': Spec(BAUSTEIN, 'Absender der Immatrikulationsbescheinigung', '', '{hochschule}'),
    'absender_leistungen': Spec(BAUSTEIN, 'Absender der Leistungsübersichten und Zeugnisse', '', '{hochschule}'),
    'unterschrift_zeugnisse': Spec(BAUSTEIN, 'Unterschriften auf Zeugnissen', '', 'Vorsitz Prüfungsausschuss'),  # one per line
    'leihbedingungen': Spec(
        BAUSTEIN, 'Bedingungen für Leihinstrumente', '',
        'Das Instrument bleibt Eigentum der Hochschule und dient dem Studium. Es ist pfleglich zu behandeln, nicht weiterzugeben und bis zum '
        'vereinbarten Tag mit dem Zubehör zurückzugeben. Schäden und Verlust sind sofort zu melden; für Schäden, die über die übliche Abnutzung '
        'hinausgehen, haftet, wer es ausgeliehen hat. Solange es überfällig ist, ist keine Rückmeldung möglich.',
    ),
    'absender_leihe': Spec(BAUSTEIN, 'Absender der Leihverträge', '', '{hochschule}'),
    'unterschrift_urkunden': Spec(BAUSTEIN, 'Unterschriften auf Urkunden', '', 'Präsidentin oder Präsident\nVorsitz Prüfungsausschuss'),
    'ds_hochschule': Spec(BAUSTEIN, 'Diploma Supplement: Hochschule und Status', '', '{hochschule}\nUniversity of Music, state institution'),
    'ds_hochschulsystem': Spec(  # KMK/HRK 2018
        BAUSTEIN, 'Diploma Supplement: Hochschulsystem (Abschnitt 8)', '',
        'The information on the national higher education system on the following pages provides a context '
        'for the qualification and the type of higher education institution that awarded it.\n\n8.1 Types of '
        'Institutions and Institutional Status\n\nHigher education (HE) studies in Germany are offered at '
        'three types of Higher Education Institutions (HEI).\n\n- Universitäten (Universities) including '
        'various specialized institutions, offer the whole range of academic disciplines. In the German '
        'tradition, universities focus in particular on basic research so that advanced stages of study have '
        'mainly theoretical orientation and research-oriented components.\n\n- Fachhochschulen '
        '(FH)/Hochschulen für Angewandte Wissenschaften (HAW) (Universities of Applied Sciences, UAS) '
        'concentrate their study programs in engineering and other technical disciplines, business-related '
        'studies, social work, and design areas. The common mission of applied research and development '
        'implies an application-oriented focus of studies, which includes integrated and supervised work '
        'assignments in industry, enterprises or other relevant institutions.\n\n- Kunst- und Musikhochschulen '
        '(Universities of Art/Music) offer studies for artistic careers in fine arts, performing arts and '
        'music; in such fields as directing, production, writing in theater, film, and other media; and in a '
        'variety of design areas, architecture, media and communication.\n\nHigher Education Institutions are '
        'either state or state-recognized institutions. In their operations, including the organization of '
        'studies and the designation and award of degrees, they are both subject to higher education '
        'legislation.\n\n8.2 Types of Programs and Degrees Awarded\n\nStudies in all three types of institutions '
        'have traditionally been offered in integrated "long" (one-tier) programs leading to Diplom- or '
        'Magister Artium degrees or completed by a Staatsprüfung (State Examination).\n\nWithin the framework '
        'of the Bologna-Process, one-tier study programs are successively being replaced by a two-tier study '
        'system. Since 1998, two-tier degrees (Bachelor’s and Master’s) have been introduced in almost all '
        'study programs. This change is designed to enlarge variety and flexibility for students in planning '
        'and pursuing educational objectives; it also enhances international compatibility of studies.\n\nThe '
        'German Qualifications Framework for Higher Education Qualifications (HQR) describes the '
        'qualification levels as well as the resulting qualifications and competences of the graduates. The '
        'three levels of the HQR correspond to the levels 6, 7 and 8 of the German Qualifications Framework '
        'for Lifelong Learning and the European Qualifications Framework for Lifelong Learning.\n\nFor details '
        'cf. Sec. 8.4.1, 8.4.2, and 8.4.3 respectively.\n\n8.3 Approval/Accreditation of Programs and '
        'Degrees\n\nTo ensure quality and comparability of qualifications, the organization of studies and '
        'general degree requirements have to conform to principles and regulations established by the '
        'Standing Conference of the Ministers of Education and Cultural Affairs of the Länder in the Federal '
        'Republic of Germany (KMK). In 1999, a system of accreditation for Bachelor’s and Master’s programs '
        'has become operational. All new programs have to be accredited under this scheme; after a '
        'successful accreditation they receive the seal of the Accreditation Council.\n\n8.4 Organization and '
        'Structure of Studies\n\nThe following programs apply to all three types of institutions. Bachelor’s '
        'and Master’s study programs may be studied consecutively, at various higher education institutions, '
        'at different types of higher education institutions and with phases of professional work between '
        'the first and the second qualification. The organization of the study programs makes use of modular '
        'components and of the European Credit Transfer and Accumulation System (ECTS) with 30 credits '
        'corresponding to one semester.\n\n8.4.1 Bachelor\n\nBachelor’s degree programs lay the academic '
        'foundations, provide methodological competences and include skills related to the professional '
        'field. The Bachelor’s degree is awarded after 3 to 4 years.\n\nThe Bachelor’s degree program includes '
        'a thesis requirement. Study programs leading to the Bachelor’s degree must be accredited according '
        'to the Interstate study accreditation treaty.\n\nFirst degree programs (Bachelor) lead to Bachelor of '
        'Arts (BA), Bachelor of Science (BSc), Bachelor of Engineering (BEng), Bachelor of Laws (LLB), '
        'Bachelor of Fine Arts (BFA), Bachelor of Music (BMus) or Bachelor of Education (BEd).\n\nThe '
        'Bachelor’s degree corresponds to level 6 of the German Qualifications Framework/ European '
        'Qualifications Framework.\n\n8.4.2 Master\n\nMaster is the second degree after another 1 to 2 years. '
        'Master’s programs may be differentiated by the profile types “practice-oriented” and '
        '“research-oriented”. Higher Education Institutions define the profile.\n\nThe Master’s degree program '
        'includes a thesis requirement. Study programs leading to the Master’s degree must be accredited '
        'according to the Interstate study accreditation treaty.\n\nSecond degree programs (Master) lead to '
        'Master of Arts (MA), Master of Science (MSc), Master of Engineering (MEng), Master of Laws (LLM), '
        'Master of Fine Arts (MFA), Master of Music (MMus) or Master of Education (MEd). Master´s programs '
        'which are designed for continuing education may carry other designations (e.g. MBA).\n\nThe Master’s '
        'degree corresponds to level 7 of the German Qualifications Framework/ European Qualifications '
        'Framework.\n\n8.4.3 Integrated "Long" Programs (One-Tier): Diplom degrees, Magister Artium, '
        'Staatsprüfung\n\nAn integrated study program is either mono-disciplinary (Diplom degrees, most '
        'programs completed by a Staatsprüfung) or comprises a combination of either two major or one major '
        'and two minor fields (Magister Artium). The first stage (1.5 to 2 years) focuses on broad '
        'orientations and foundations of the field(s) of study. An Intermediate Examination '
        '(Diplom-Vorprüfung for Diplom degrees; Zwischenprüfung or credit requirements for the Magister '
        'Artium) is prerequisite to enter the second stage of advanced studies and specializations. Degree '
        'requirements include submission of a thesis (up to 6 months duration) and comprehensive final '
        'written and oral examinations. Similar regulations apply to studies leading to a Staatsprüfung. The '
        'level of qualification is equivalent to the Master’s level.\n\n- Integrated studies at Universitäten '
        '(U) last 4 to 5 years (Diplom degree, Magister Artium) or 3.5 to 6.5 years (Staatsprüfung). The '
        'Diplom degree is awarded in engineering disciplines, the natural sciences as well as economics and '
        'business. In the humanities, the corresponding degree is usually the Magister Artium (MA). In the '
        'social sciences, the practice varies as a matter of institutional traditions. Studies preparing for '
        'the legal, medical and pharmaceutical professions are completed by a Staatsprüfung. This applies '
        'also to studies preparing for teaching professions of some Länder.\n\nThe three qualifications '
        '(Diplom, Magister Artium and Staatsprüfung) are academically equivalent and correspond to level 7 '
        'of the German Qualifications Framework/European Qualifications Framework. They qualify to apply for '
        'admission to doctoral studies. Further prerequisites for admission may be defined by the Higher '
        'Education Institution, cf. Sec. 8.5.\n\n- Integrated studies at Fachhochschulen (FH)/Hochschulen für '
        'Angewandte Wissenschaften (HAW) (Universities of Applied Sciences, UAS) last 4 years and lead to a '
        'Diplom (FH) degree which corresponds to level 6 of the German Qualifications Framework/European '
        'Qualifications Framework. Qualified graduates of FH/HAW/UAS may apply for admission to doctoral '
        'studies at doctorate-granting institutions, cf. Sec. 8.5.\n\n- Studies at Kunst- and Musikhochschulen '
        '(Universities of Art/Music etc.) are more diverse in their organization, depending on the field and '
        'individual objectives. In addition to Diplom/Magister degrees, the integrated study program awards '
        'include certificates and certified examinations for specialized areas and professional '
        'purposes.\n\n8.5 Doctorate\n\nUniversities as well as specialized institutions of university standing, '
        'some of the FH/HAW/UAS and some Universities of Art/Music are doctorate-granting institutions. '
        'Formal prerequisite for admission to doctoral work is a qualified Master’s degree (UAS and U), a '
        'Magister degree, a Diplom, a Staatsprüfung, or a foreign equivalent. Comparable degrees from '
        'universities of art and music can in exceptional cases (study programs such as music theory, '
        'musicology, pedagogy of arts and music, media studies) also formally qualify for doctoral work. '
        'Particularly qualified holders of a Bachelor’s degree or a Diplom (FH) degree may also be admitted '
        'to doctoral studies without acquisition of a further degree by means of a procedure to determine '
        'their aptitude. The universities respectively the doctorate-granting institutions regulate entry to '
        'a doctorate as well as the structure of the procedure to determine aptitude. Admission further '
        'requires the acceptance of the Dissertation research project by a professor as a supervisor.\n\nThe '
        'doctoral degree corresponds to level 8 of the German Qualifications Framework/ European '
        'Qualifications Framework.\n\n8.6 Grading Scheme\n\nThe grading scheme in Germany usually comprises five '
        'levels (with numerical equivalents; intermediate grades may be given): "Sehr Gut" (1) = Very Good; '
        '"Gut" (2) = Good; "Befriedigend" (3) = Satisfactory; "Ausreichend" (4) = Sufficient; "Nicht '
        'ausreichend" (5) = Non-Sufficient/Fail. The minimum passing grade is "Ausreichend" (4). Verbal '
        'designations of grades may vary in some cases and for doctoral degrees.\n\nIn addition, grade '
        'distribution tables as described in the ECTS Users’ Guide are used to indicate the relative '
        'distribution of grades within a reference group.\n\n8.7 Access to Higher Education\n\nThe General '
        'Higher Education Entrance Qualification (Allgemeine Hochschulreife, Abitur) after 12 to 13 years of '
        'schooling allows for admission to all higher educational studies. Specialized variants '
        '(Fachgebundene Hochschulreife) allow for admission at Fachhochschulen (FH)/Hochschulen für '
        'Angewandte Wissenschaften (HAW) (UAS), universities and equivalent higher education institutions, '
        'but only in particular disciplines. Access to study programs at Fachhochschulen (FH)/Hochschulen '
        'für Angewandte Wissenschaften (HAW) (UAS) is also possible with a Fachhochschulreife, which can '
        'usually be acquired after 12 years of schooling. Admission to study programs at Universities of '
        'Art/Music and comparable study programs at other higher education institutions as well as admission '
        'to a study program in sports may be based on other or additional evidence demonstrating individual '
        'aptitude.\n\nApplicants with a qualification in vocational education and training but without a '
        'school-based higher education entrance qualification are entitled to a general higher education '
        'entrance qualification and thus to access to all study programs, provided they have obtained '
        'advanced further training certificates in particular state-regulated vocational fields (e.g. '
        'Meister/Meisterin im Handwerk, Industriemeister/in, Fachwirt/in (IHK), Betriebswirt/in (IHK) und '
        '(HWK), staatlich geprüfte/r Techniker/in, staatlich geprüfte/r Betriebswirt/in, staatlich '
        'geprüfte/r Gestalter/in, staatlich geprüfte/r Erzieher/in). Vocationally qualified applicants can '
        'obtain a Fachgebundene Hochschulreife after completing a state-regulated vocational education of at '
        'least two years’ duration plus professional practice of normally at least three years’ duration, '
        'after having successfully passed an aptitude test at a higher education institution or other state '
        'institution; the aptitude test may be replaced by successfully completed trial studies of at least '
        'one year’s duration.\n\nHigher Education Institutions may in certain cases apply additional admission '
        'procedures.\n\n8.8 National Sources of Information\n\n- Kultusministerkonferenz (KMK) (Standing '
        'Conference of the Ministers of Education and Cultural Affairs of the Länder in the Federal Republic '
        'of Germany); Graurheindorfer Str. 157, D-53117 Bonn; Phone: +49 (0)228/501-0; www.kmk.org; E-Mail: '
        'hochschulen@kmk.org\n\n- Central Office for Foreign Education (ZAB) as German NARIC; www.kmk.org; '
        'E-Mail: zab@kmk.org\n\n- German information office of the Länder in the EURYDICE Network, providing '
        'the national dossier on the education system; www.kmk.org; E-Mail: Eurydice@kmk.org\n\n- '
        'Hochschulrektorenkonferenz (HRK) (German Rectors’ Conference); Leipziger Platz 11, D-10117 Berlin, '
        'Phone: +49 30 206292-11; www.hrk.de; E-Mail: post@hrk.de\n\n- "Higher Education Compass" of the '
        'German Rectors’ Conference features comprehensive information on institutions, programs of study, '
        'etc. (www.higher-education-compass.de)',
    ),
    'ds_hochschulsystem_de': Spec(
        BAUSTEIN, 'Diploma Supplement: Hochschulsystem (Abschnitt 8, deutsch)', '',
        'Die Informationen über das nationale Hochschulsystem auf den folgenden Seiten geben Auskunft über '
        'den Grad der Qualifikation und den Typ der Institution, die sie vergeben hat.\n\n8.1 Die '
        'unterschiedlichen Hochschulen und ihr institutioneller Status\n\nDie Hochschulausbildung wird in '
        'Deutschland von drei Arten von Hochschulen angeboten.\n\n- Universitäten, einschließlich '
        'verschiedener spezialisierter Institutionen, bieten das gesamte Spektrum akademischer Disziplinen '
        'an. Traditionell liegt der Schwerpunkt an deutschen Universitäten besonders auf der '
        'Grundlagenforschung, so dass das fortgeschrittene Studium vor allem theoretisch ausgerichtet und '
        'forschungsorientiert ist.\n\n- Fachhochschulen (FH)/Hochschulen für Angewandte Wissenschaften (HAW) '
        'konzentrieren ihre Studienangebote auf ingenieurwissenschaftliche technische Fächer und '
        'wirtschaftswissenschaftliche Fächer, Sozialarbeit und Design. Der Auftrag von angewandter Forschung '
        'und Entwicklung impliziert einen praxisorientierten Ansatz und eine ebensolche Ausrichtung des '
        'Studiums, was häufig integrierte und begleitete Praktika in Industrie, Unternehmen oder anderen '
        'einschlägigen Einrichtungen einschließt.\n\n- Kunst- und Musikhochschulen bieten Studiengänge für '
        'künstlerische Tätigkeiten an, in Bildender Kunst, Schauspiel und Musik, in den Bereichen Regie, '
        'Produktion und Drehbuch für Theater, Film und andere Medien sowie in den Bereichen Design, '
        'Architektur, Medien und Kommunikation.\n\nHochschulen sind entweder staatliche oder staatlich '
        'anerkannte Institutionen. Sowohl in ihrem Handeln einschließlich der Planung von Studiengängen als '
        'auch in der Festsetzung und Zuerkennung von Studienabschlüssen unterliegen sie der '
        'Hochschulgesetzgebung.\n\n8.2 Studiengänge und -abschlüsse\n\nIn allen Hochschularten wurden die '
        'Studiengänge traditionell als integrierte „lange“ (einstufige) Studiengänge angeboten, die entweder '
        'zum Diplom oder zum Magister Artium führten oder mit einer Staatsprüfung abschlossen.\n\nIm Rahmen '
        'des Bologna-Prozesses wird das einstufige Studiensystem sukzessive durch ein zweistufiges ersetzt. '
        'Seit 1998 wurden in fast allen Studiengängen gestufte Abschlüsse (Bachelor und Master) eingeführt. '
        'Dies soll den Studierenden mehr Wahlmöglichkeiten und Flexibilität beim Planen und Verfolgen ihrer '
        'Lernziele bieten sowie Studiengänge international kompatibler machen.\n\nDie Abschlüsse des deutschen '
        'Hochschulsystems einschließlich ihrer Zuordnung zu den Qualifikationsstufen sowie die damit '
        'einhergehenden Qualifikationsziele und Kompetenzen der Absolventinnen und Absolventen sind im '
        'Qualifikationsrahmen für deutsche Hochschulabschlüsse (HQR) beschrieben. Die drei Stufen des HQR '
        'sind den Stufen 6, 7 und 8 des Deutschen Qualifikationsrahmens für lebenslanges Lernen (DQR) und '
        'des Europäischen Qualifikationsrahmens für lebenslanges Lernen (EQR) zugeordnet.\n\nEinzelheiten s. '
        'Abschnitte 8.4.1, 8.4.2 bzw. 8.4.3.\n\n8.3 Anerkennung/Akkreditierung von Studiengängen und '
        'Abschlüssen\n\nUm die Qualität und die Vergleichbarkeit von Qualifikationen sicherzustellen, müssen '
        'sich sowohl die Organisation und Struktur von Studiengängen als auch die grundsätzlichen '
        'Anforderungen an Studienabschlüsse an den Prinzipien und Regelungen der Ständigen Konferenz der '
        'Kultusminister der Länder (KMK) orientieren. Seit 1999 existiert ein bundesweites '
        'Akkreditierungssystem für Bachelor- und Masterstudiengänge, nach dem alle neu eingeführten '
        'Studiengänge akkreditiert werden. Akkreditierte Studiengänge sind berechtigt, das Qualitätssiegel '
        'des Akkreditierungsrates zu führen.\n\n8.4 Organisation und Struktur der Studiengänge\n\nDie folgenden '
        'Studiengänge können von allen drei Hochschularten angeboten werden. Bachelor- und '
        'Masterstudiengänge können nacheinander, an unterschiedlichen Hochschulen, an unterschiedlichen '
        'Hochschularten und mit Phasen der Erwerbstätigkeit zwischen der ersten und der zweiten '
        'Qualifikationsstufe studiert werden. Bei der Planung werden Module und das Europäische System zur '
        'Übertragung und Akkumulierung von Studienleistungen (ECTS) verwendet, wobei einem Semester 30 '
        'Kreditpunkte entsprechen.\n\n8.4.1 Bachelor\n\nIn Bachelorstudiengängen werden wissenschaftliche '
        'Grundlagen, Methodenkompetenz und berufsfeldbezogene Qualifikationen vermittelt. Der '
        'Bachelorabschluss wird nach 3 bis 4 Jahren vergeben.\n\nZum Bachelorstudiengang gehört eine '
        'schriftliche Abschlussarbeit. Studiengänge, die mit dem Bachelor abgeschlossen werden, müssen gemäß '
        'dem Studienakkreditierungsstaatsvertrag akkreditiert werden.\n\nStudiengänge der ersten '
        'Qualifikationsstufe (Bachelor) schließen mit den Graden Bachelor of Arts (B.A.), Bachelor of '
        'Science (B.Sc.), Bachelor of Engineering (B.Eng.), Bachelor of Laws (LL.B.), Bachelor of Fine Arts '
        '(B.F.A.), Bachelor of Music (B.Mus.) oder Bachelor of Education (B.Ed.) ab.\n\nDer Bachelorgrad '
        'entspricht der Qualifikationsstufe 6 des DQR/EQR.\n\n8.4.2 Master\n\nDer Master ist der zweite '
        'Studienabschluss nach weiteren 1 bis 2 Jahren. Masterstudiengänge können nach den Profiltypen '
        '„anwendungsorientiert“ und „forschungsorientiert“ differenziert werden. Die Hochschulen legen das '
        'Profil fest.\n\nZum Masterstudiengang gehört eine schriftliche Abschlussarbeit. Studiengänge, die mit '
        'dem Master abgeschlossen werden, müssen gemäß dem Studienakkreditierungsstaatsvertrag akkreditiert '
        'werden.\n\nStudiengänge der zweiten Qualifikationsstufe (Master) schließen mit den Graden Master of '
        'Arts (M.A.), Master of Science (M.Sc.), Master of Engineering (M.Eng.), Master of Laws (LL.M.), '
        'Master of Fine Arts (M.F.A.), Master of Music (M.Mus.) oder Master of Education (M.Ed.) ab. '
        'Weiterbildende Masterstudiengänge können andere Bezeichnungen erhalten (z.B. MBA).\n\nDer Mastergrad '
        'entspricht der Qualifikationsstufe 7 des DQR/EQR.\n\n8.4.3 Integrierte „lange“ einstufige '
        'Studiengänge: Diplom, Magister Artium, Staatsprüfung\n\nEin integrierter Studiengang ist entweder '
        'mono-disziplinär (Diplomabschlüsse und die meisten Staatsprüfungen) oder besteht aus einer '
        'Kombination von entweder zwei Hauptfächern oder einem Haupt- und zwei Nebenfächern (Magister '
        'Artium). Das Vorstudium (1,5 bis 2 Jahre) dient der breiten Orientierung und dem Grundlagenerwerb '
        'im jeweiligen Fach. Eine Zwischenprüfung (bzw. Vordiplom) ist Voraussetzung für die Zulassung zum '
        'Hauptstudium, d.h. zum fortgeschrittenen Studium und der Spezialisierung. Voraussetzung für den '
        'Abschluss sind die Vorlage einer schriftlichen Abschlussarbeit (Dauer bis zu 6 Monate) und '
        'umfangreiche schriftliche und mündliche Abschlussprüfungen. Ähnliche Regelungen gelten für die '
        'Staatsprüfung. Die erworbene Qualifikation entspricht dem Master.\n\n- Die Regelstudienzeit an '
        'Universitäten beträgt bei integrierten Studiengängen 4 bis 5 Jahre (Diplom, Magister Artium) oder '
        '3,5 bis 6,5 Jahre (Staatsprüfung). Mit dem Diplom werden ingenieur-, natur- und '
        'wirtschaftswissenschaftliche Studiengänge abgeschlossen. In den Geisteswissenschaften ist der '
        'entsprechende Abschluss in der Regel der Magister Artium (M.A.). In den Sozialwissenschaften '
        'variiert die Praxis je nach Tradition der jeweiligen Hochschule. Juristische, medizinische und '
        'pharmazeutische Studiengänge schließen mit der Staatsprüfung ab. Dies gilt in einigen Ländern auch '
        'für Lehramtsstudiengänge.\n\nDie drei Qualifikationen (Diplom, Magister Artium und Staatsprüfung) '
        'sind akademisch gleichwertig und auf der Qualifikationsstufe 7 des DQR/EQR angesiedelt. Sie bilden '
        'die formale Voraussetzung zur Promotion. Weitere Zulassungsvoraussetzungen können von der '
        'Hochschule festgelegt werden, s. Abschnitt 8.5.\n\n- Die Regelstudienzeit an Fachhochschulen '
        '(FH)/Hochschulen für Angewandte Wissenschaften (HAW) beträgt bei integrierten Studiengängen 4 Jahre '
        'und schließt mit dem Diplom (FH) ab. Dieses ist auf der Qualifikationsstufe 6 des DQR/EQR '
        'angesiedelt. Qualifizierte Absolventinnen und Absolventen von Fachhochschulen/Hochschulen für '
        'Angewandte Wissenschaften können sich für die Zulassung zur Promotion an promotionsberechtigten '
        'Hochschulen bewerben, s. Abschnitt 8.5.\n\n- Das Studium an Kunst- und Musikhochschulen ist in seiner '
        'Organisation und Struktur abhängig vom jeweiligen Fachgebiet und der individuellen Zielsetzung. '
        'Neben dem Diplom- bzw. Magisterabschluss gibt es bei integrierten Studiengängen Zertifikate und '
        'zertifizierte Abschlussprüfungen für spezielle Bereiche und berufliche Zwecke.\n\n8.5 '
        'Promotion\n\nUniversitäten, gleichgestellte Hochschulen sowie einige Fachhochschulen (FH)/Hochschulen '
        'für Angewandte Wissenschaften (HAW) und einige Kunst- und Musikhochschulen sind '
        'promotionsberechtigt. Formale Voraussetzung für die Zulassung zur Promotion ist ein qualifizierter '
        'Masterabschluss (Fachhochschulen und Universitäten), ein Magisterabschluss, ein Diplom, eine '
        'Staatsprüfung oder ein äquivalenter ausländischer Abschluss. Entsprechende Abschlüsse von Kunst- '
        'und Musikhochschulen können in Ausnahmefällen (wissenschaftliche Studiengänge, z.B. Musiktheorie, '
        'Musikwissenschaften, Kunst- und Musikpädagogik, Medienwissenschaften) formal den Zugang zur '
        'Promotion eröffnen. Besonders qualifizierte Inhaber eines Bachelorgrades oder eines Diploms (FH) '
        'können ohne einen weiteren Studienabschluss im Wege eines Eignungsfeststellungsverfahrens zur '
        'Promotion zugelassen werden. Die Universitäten bzw. promotionsberechtigten Hochschulen regeln '
        'sowohl die Zulassung zur Promotion als auch die Art der Eignungsprüfung. Voraussetzung für die '
        'Zulassung ist außerdem, dass das Promotionsprojekt von einem Hochschullehrer als Betreuer '
        'angenommen wird.\n\nDie Promotion entspricht der Qualifikationsstufe 8 des DQR/EQR.\n\n8.6 '
        'Benotungsskala\n\nDie deutsche Benotungsskala umfasst üblicherweise 5 Grade (mit zahlenmäßigen '
        'Entsprechungen; es können auch Zwischennoten vergeben werden): „Sehr gut“ (1), „Gut“ (2), '
        '„Befriedigend“ (3), „Ausreichend“ (4), „Nicht ausreichend“ (5). Zum Bestehen ist mindestens die '
        'Note „Ausreichend“ (4) notwendig. Die Bezeichnung für die Noten kann in Einzelfällen und für die '
        'Promotion abweichen.\n\nAußerdem findet eine Einstufungstabelle nach dem Modell des ECTS-Leitfadens '
        'Verwendung, aus der die relative Verteilung der Noten in Bezug auf eine Referenzgruppe '
        'hervorgeht.\n\n8.7 Hochschulzugang\n\nDie Allgemeine Hochschulreife (Abitur) nach 12 bis 13 Schuljahren '
        'ermöglicht den Zugang zu allen Studiengängen. Die Fachgebundene Hochschulreife ermöglicht den '
        'Zugang zu allen Studiengängen an Fachhochschulen, an Universitäten und gleichgestellten '
        'Hochschulen, aber nur zu bestimmten Fächern. Das Studium an Fachhochschulen ist auch mit der '
        'Fachhochschulreife möglich, die in der Regel nach 12 Schuljahren erworben wird. Der Zugang zu '
        'Studiengängen an Kunst- und Musikhochschulen und entsprechenden Studiengängen an anderen '
        'Hochschulen sowie der Zugang zu einem Sportstudiengang kann auf der Grundlage von anderen bzw. '
        'zusätzlichen Voraussetzungen zum Nachweis einer besonderen Eignung erfolgen.\n\nBeruflich '
        'qualifizierte Bewerber und Bewerberinnen ohne schulische Hochschulzugangsberechtigung erhalten eine '
        'allgemeine Hochschulzugangsberechtigung und damit Zugang zu allen Studiengängen, wenn sie Inhaber '
        'von Abschlüssen bestimmter, staatlich geregelter beruflicher Aufstiegsfortbildungen sind (zum '
        'Beispiel Meister/in im Handwerk, Industriemeister/in, Fachwirt/in (IHK), Betriebswirt/in (IHK) und '
        '(HWK), staatlich geprüfte/r Techniker/in, staatlich geprüfte/r Betriebswirt/in, staatlich '
        'geprüfte/r Gestalter/in, staatlich geprüfte/r Erzieher/in). Eine fachgebundene '
        'Hochschulzugangsberechtigung erhalten beruflich qualifizierte Bewerber und Bewerberinnen mit einem '
        'Abschluss einer staatlich geregelten, mindestens zweijährigen Berufsausbildung und i.d.R. '
        'mindestens dreijähriger Berufspraxis, die ein Eignungsfeststellungsverfahren an einer Hochschule '
        'oder staatlichen Stelle erfolgreich durchlaufen haben; das Eignungsfeststellungsverfahren kann '
        'durch ein nachweislich erfolgreich absolviertes Probestudium von mindestens einem Jahr ersetzt '
        'werden.\n\nDie Hochschulen können in bestimmten Fällen zusätzliche spezifische Zulassungsverfahren '
        'durchführen.\n\n8.8 Informationsquellen in der Bundesrepublik\n\n- Kultusministerkonferenz (KMK) '
        '(Ständige Konferenz der Kultusminister der Länder in der Bundesrepublik Deutschland); '
        'Graurheindorfer Str. 157, D-53117 Bonn; Tel.: +49(0)228/501-0; www.kmk.org; E-Mail: '
        'hochschulen@kmk.org\n\n- Zentralstelle für ausländisches Bildungswesen (ZAB) als deutsche NARIC; '
        'www.kmk.org; E-Mail: zab@kmk.org\n\n- Deutsche Informationsstelle der Länder im EURYDICE-Netz, für '
        'Informationen zum Bildungswesen in Deutschland; www.kmk.org; E-Mail: eurydice@kmk.org\n\n- '
        'Hochschulrektorenkonferenz (HRK); Leipziger Platz 11, D-10117 Berlin, Tel.: +49 30 206292-11; '
        'www.hrk.de; E-Mail: post@hrk.de\n\n- „Hochschulkompass“ der Hochschulrektorenkonferenz, enthält '
        'umfassende Informationen zu Hochschulen, Studiengängen etc. (www.hochschulkompass.de)',
    ),
    'barrierefreiheit_stand': Spec(
        BAUSTEIN, 'Barrierefreiheit: Stand und Ausnahmen', '',
        'Campus ist mit den Anforderungen der WCAG 2.1, Stufe AA, die § 3 der BITV 2.0 nach der ThürBITVO vorgibt, teilweise vereinbar. '
        'Nicht barrierefrei sind:\n\n'
        '– Bescheinigungen, Bescheide, Zeugnisse und Urkunden: Sie werden als Seite gedruckt oder über den Browser als PDF gespeichert; '
        'ein getaggtes PDF (PDF/UA) gibt es nicht. Auf Anfrage erhalten Sie sie in zugänglicher Form.\n'
        '– Videos, die Bewerbende hochladen, haben keine Untertitel; sie stammen nicht von der {hochschule}.',
    ),
    'barrierefreiheit_erstellung': Spec(
        BAUSTEIN, 'Barrierefreiheit: Erstellung der Erklärung', '',
        'Diese Erklärung beruht auf einer Selbstbewertung vom 30. Oktober 2026: 48 Seiten aller Rollen, von der Anmeldung bis zu den '
        'Auswertungen, wurden automatisch gegen die Regeln der WCAG 2.1, Stufe AA, geprüft (axe-core 4.10) und die Befunde behoben. '
        'Eine Prüfung durch Dritte (BITV-Test) steht aus.',
    ),
    'barrierefreiheit_durchsetzung': Spec(
        BAUSTEIN, 'Barrierefreiheit: Durchsetzungsverfahren', '',
        'Antworten wir nicht oder nicht zufriedenstellend, können Sie sich an die Durchsetzungsstelle nach dem ThürBarrWebG wenden:\n\n'
        'Der Beauftragte der Thüringer Landesregierung für Menschen mit Behinderungen\nWerner-Seelenbinder-Straße 6\n99096 Erfurt\n'
        'Telefon 0361 573 811 741\nE-Mail vz_bmb@tmasgff.thueringen.de',
    ),
    'anmeldung': Spec(BAUSTEIN, 'Begrüßung auf der Anmeldeseite', '', 'Campus ist die Verwaltung von Studium, Lehre, Prüfungen, Aufnahme und Räumen der {hochschule}.'),
    'datenschutz_bewerbung': Spec(
        BAUSTEIN, 'Datenschutz beim Bewerben', '',
        'Ihre Angaben verarbeitet die {hochschule} nur für das Bewerbungsverfahren und, wenn Sie hier studieren, für Ihr Studium. '
        'Sie sehen nur die Stellen, die damit befasst sind; die Prüfungskommission sieht Ihre künstlerischen Unterlagen.',
    ),
    'einwilligung_gesundheit': Spec(
        BAUSTEIN, 'Einwilligung in Gesundheitsdaten', '',
        'Ich willige ein, dass die {hochschule} die Gesundheitsangaben in meinem Nachweis {zweck} verarbeitet (Art. 9 Abs. 2 lit. a DSGVO). '
        'Ihn sehen nur die Stellen, die darüber entscheiden. Ich kann die Einwilligung jederzeit unter „Mein Konto“ widerrufen; der Nachweis '
        'wird dann gelöscht. Was bis dahin entschieden ist, bleibt bestehen.',
        ('zweck',),
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


def render(key, *after, **values):
    """(subject, body) with the placeholders filled in and the text blocks `after` following; a Bescheid that turns
    someone down gets the Rechtsbehelfsbelehrung, others where they name it."""
    values = Values(basics(), **{name: str(value) for name, value in values.items()})
    subject, body = (part.format_map(values) for part in wording(key))
    for block in dict.fromkeys([*after, *(['rechtsbehelf'] if TEXTS[key].appeal else [])]):
        body += '\n\n' + wording(block)[1].format_map(values)
    return subject, body


def block(key, **values):
    return render(key, **values)[1]


def send(key, person, link='', **values):
    subject, body = render(key, vorname=person.first_name, nachname=person.last_name, **values)
    return Message.send(person, subject, body, link)


def mail(key, person, **values):
    """By e-mail only, for what must not stay readable in the Postfach, where offices see it too: a sign-in link."""
    Message.mail(person, *render(key, vorname=person.first_name, nachname=person.last_name, **values))
