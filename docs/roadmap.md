# Roadmap

What replacing HISinOne at HfM takes, what selling Campus to other Hochschulen takes, and the order to build it. From
a list of what HISinOne does at HfM today, October 2026. Legal requirements are in `docs/legal.md`, the reports to the
health insurers in `docs/smv.md`.

## Replacing HISinOne

| Part | Status | In Campus, or missing |
|---|---|---|
| Student management (STU) | yes | Immatrikulation, Rückmeldung with fees and Säumnisgebühr, payments from bank statements, Beurlaubung, Exmatrikulation with Bescheid, Studienakte, Bescheinigungen |
| Exam management (EXA) | yes | Prüfungsordnungen by version, modules, registration and withdrawal, marks with Protokoll, Prüfungsausschuss, Leistungsübersicht, Zeugnis, Urkunde, Diploma Supplement; open items in `docs/sweep.md` (A5, A13) |
| Applications (APP), uploads in Gecko CAMPUS | yes | Eignungsprüfung with rounds, juries, video round, ranking, offers, Nachrückliste; uploads in Campus itself |
| Office reports, exported by hand | partly | Lists as CSV, Auswertungen, the official statistics; which reports HfM pulls today is not known |
| Bauhaus accounts | no | Enrolment and Exmatrikulation are not sent to the Bauhaus-Universität's computing centre, which opens and closes student accounts, perhaps the semester ticket too; format unknown. Campus makes its own student addresses (`students/views.py` `matriculate()`): once Bauhaus makes the accounts, the address comes from there |
| Sign-in with the Bauhaus account (Shibboleth, DFN-AAI) | no | Campus's own passwords; applicants keep them, having no Bauhaus account |
| Health insurers (§ 199a SGB V) | no | `docs/smv.md` |
| Official statistics | yes | Files for Studierende, Prüfungen, Promovierende and Gasthörer:innen; not yet delivered for real. Staff statistics: from MACH, to check |
| Moodle | no | Courses carry Moodle's Fachgebiete; Campus neither creates them in Moodle nor enrols participants |
| Erasmus Without Paper | no | No mobility: no inter-institutional agreements, no online learning agreements |
| XHochschule | partly | Certificates as pages; no data, no seal |
| Migration | no | No import of students, applicants and exams with their history, nor of current students' insurance, which their M30 needs |

Manual today and at launch, no interface: thoska card, library, MACH ERP, Studierendenwerk, marks from FSU Jena (taken
in as Anerkennung).

## For other Hochschulen

Already data under Einstellungen: name, address, bank, offices, texts, fees, periods, Studiengänge. One instance per
Hochschule, on its own subdomain.

To change:

1. HfM's name in the header and letterhead (`core/templates/core/base.html`, `core/templates/core/_letterhead.html`):
   from Einstellungen, with a logo.
2. HfM's look (`core/static/core/hfm.css`): accent colour and logo per Hochschule.
3. The sender address `campus@hfm-weimar.de` (`concampus/settings.py`).
4. Thuringian rules in code: the Deputat after the ThürLVVO; other states' rules beside it. The accessibility
   statement's enforcement body is an editable text.
5. Studienpläne and courses imported, not typed in (`docs/sweep.md` D8); the seed is HfM's.
6. A new instance set up by script, each with its database, backups and migrations.
7. English.
8. Built for Musikhochschulen: Hauptfach, Klassen, Eignungsprüfung, Korrepetition, Übezeiten, instruments.

## Order

Most of it can be built before HfM's systems are open to us; what needs them waits.

1. **Name and look from Einstellungen** (1 to 3 above). Small; ends hard-coded HfM before more is built on it.
2. **One outbox of status changes**: Immatrikulation, Beurlaubung, Exmatrikulation and their cancellation, as events
   the health insurers, Bauhaus and Moodle each read. On it **the health insurers inside Campus**: the insurer by its
   number, an inbox that matches reports to people, M20, M30 and Storno, the Sperre from M12 and M13, the log; towards
   dakota a folder of files. To the published schemata; the connection itself with HfM.
3. **An import format**: people, studies, semesters, results by Prüfungsordnung, insurance, Studienpläne; tested by a
   round trip of the seed. HfM's export then only needs mapping, and the next Hochschule's Studienpläne come in the
   same way.
4. **Single sign-on** through SAML, against a test identity provider; Bauhaus's attributes later.
5. **Moodle**: courses and participants through its web services, against a Moodle of our own.
6. **XHochschule data** for the certificates; the seal once HfM has a trust service.
7. **Erasmus Without Paper**: a mobility part, the largest; with HfM's international office.

Alongside, at the desk: our fields against the ThürHDatVO 2019; the escrow (`docs/escrow.md`).

Waiting for HfM: the Bauhaus feed's format; the Absendernummer and its dakota (`docs/smv.md`); the reports it pulls
from HISinOne; an export of HISinOne's data; a test delivery to the Landesamt; whether staff statistics come from MACH.

Outside, at a cost: the BITV test, the escrow agent, a lawyer for the contract terms.
