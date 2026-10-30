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
| Sign-in with the Bauhaus account (Shibboleth, DFN-AAI) | partly | Built (`core/sso.py`): Campus as a SAML service provider, tested with a signed answer as a Shibboleth IdP gives it; applicants keep their passwords, having no Bauhaus account. Still to do: register Campus with the Bauhaus-Universität's IdP and agree on the attributes it releases |
| Health insurers (§ 199a SGB V) | no | `docs/smv.md` |
| Official statistics | yes | Files for Studierende, Prüfungen, Promovierende and Gasthörer:innen; not yet delivered for real. Staff statistics: from MACH, to check |
| Moodle | no | Courses carry Moodle's Fachgebiete; Campus neither creates them in Moodle nor enrols participants |
| Erasmus Without Paper | no | No mobility: no inter-institutional agreements, no online learning agreements |
| XHochschule | partly | Certificates as pages; no data, no seal |
| Migration | partly | Campus loads people, studies, semesters, results, insurance and Studienpläne from one document (`docs/transfer.md`); HISinOne's export is still to be mapped onto it |

Manual today and at launch, no interface: thoska card, library, MACH ERP, Studierendenwerk, marks from FSU Jena (taken
in as Anerkennung).

## For other Hochschulen

Already data under Einstellungen: name, address, bank, offices, texts, fees, periods, Studiengänge. One instance per
Hochschule, on its own subdomain.

To change:

1. Done: name, logo, accent colour and corner radius under Einstellungen › Hochschule (`core/static/core/campus.css`
   takes them as variables); the sender address from the server's settings (`DEFAULT_FROM_EMAIL`). A font of its own
   would need a licence for the web and its files on our server, so it waits until a Hochschule asks.
2. Thuringian rules in code: the Deputat after the ThürLVVO; other states' rules beside it. The accessibility
   statement's enforcement body is an editable text.
3. Studienpläne and courses imported, not typed in (`docs/sweep.md` D8); the seed is HfM's.
4. A new instance set up by script, each with its database, backups and migrations.
5. English.
6. Built for Musikhochschulen: Hauptfach, Klassen, Eignungsprüfung, Korrepetition, Übezeiten, instruments.

## Order

Most of it can be built before HfM's systems are open to us; what needs them waits.

1. Done: **name and look from Einstellungen** (1 above).
2. Done: **one list of status changes** (`students.models.Event`): whoever begins or stops studying here, for the
   health insurers now and Bauhaus and Moodle later; a Beurlaubung joins it once one of them needs it. On it **the
   health insurers inside Campus** (`docs/smv.md`); the files to and from dakota wait for the GKV's schemata.
3. Done: **an import format** (`docs/transfer.md`): people, studies, semesters, results by Prüfungsordnung, insurance,
   Studienpläne; the whole demo goes out and back in unchanged. HfM's export then only needs mapping, and the next
   Hochschule's Studienpläne come in the same way.
4. Done: **single sign-on** through SAML (`core/sso.py`), tested with signed answers; the Bauhaus IdP's registration
   and attributes later.
5. **Moodle**: courses and participants through its web services, against a Moodle of our own.
6. **XHochschule data** for the certificates; the seal once HfM has a trust service.
7. **Erasmus Without Paper**: a mobility part, the largest; with HfM's international office.

Alongside, at the desk: our fields against the ThürHDatVO 2019; the escrow (`docs/escrow.md`).

Waiting for HfM: the Bauhaus feed's format; the Absendernummer and its dakota (`docs/smv.md`); the reports it pulls
from HISinOne; an export of HISinOne's data; a test delivery to the Landesamt; whether staff statistics come from MACH.

Outside, at a cost: the BITV test, the escrow agent, a lawyer for the contract terms.
