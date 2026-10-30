# Legal requirements

What the law asks of a campus management system at a Thuringian Hochschule, what Campus does about it and what is
missing. Who sees what is in `docs/privacy.md`; each rule Campus applies is in `docs/rules.md`.

Status: **yes**, **partly**, **no**; **Hochschule** where the duty is the Hochschule's own and Campus only feeds it;
**n/a** where it does not apply. The legal points come from research of October 2026; those still to be checked
against the text are listed at the end.

## Data protection

The Hochschule is the controller. HfM Weimar's own notices give Art. 6(1)(e) GDPR with the ThürDSG as the legal basis,
and consent (Art. 9(2)(a)) for special categories: here attests for a Rücktritt or Nachteilsausgleich, the phoniatric
report, grounds of leave (illness, Mutterschutz) and a Schwerbehinderung behind a Deputat reduction.

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Each role sees what its task needs, by default | GDPR Art. 25 | yes | Tiers and roles in `docs/privacy.md`; the jury sees only documents marked for it; nobody lists all students; form input never in the address |
| Information when data is collected | Art. 13 | yes | Baustein „Datenschutz beim Bewerben“ at signup and application, with the deletion period and the Hochschule's notice (`admissions/templates/admissions/_privacy.html`) |
| Consent for health data, recorded and withdrawable | Art. 9(2)(a), Art. 7 | yes | Asked with every attest or health report, recorded with its wording, when and by whom (the person, or an office from the signed paper); withdrawn under Mein Konto, which deletes the document; decisions taken stay (`core.models.Consent`) |
| Access | Art. 15 | partly | The person sees their record, results, documents and Postfach; no complete copy with the Verlauf, the exports they were in and who received them |
| Rectification | Art. 16 | yes | Address and phone in Mein Konto; name and birth data by the office on proof (`core/views.py` `account()`) |
| Erasure | Art. 17 | partly | Applications that came to nothing: documents deleted and the person made anonymous after the Löschfrist; health data once the application is decided; room bookings after two years. Anonymising empties every field but those the figures count (`admissions/retention.py` `KEPT`). Students, alumni and staff are never deleted |
| Restriction | Art. 18 | no | No way to mark a record as restricted |
| Objection | Art. 21 | Hochschule | Decided by the office; nothing in Campus |
| Portability | Art. 20 | n/a | Not for processing under Art. 6(1)(e) (Art. 20(3)) |
| No decision by machine alone | Art. 22, § 35a VwVfG | yes | Rankings, the Nachrückliste and a lost Prüfungsanspruch are worked out; every Bescheid is issued by an office after its preview. Resits are registered automatically as the RPSO says |
| Deletion concept | Art. 5(1)(e), Art. 17 | partly | Periods for applications and bookings; none for students after Exmatrikulation, staff, messages, the Verlauf or the export log |
| Security of processing | Art. 32 | partly | See IT security |
| Records of processing; impact assessment | Art. 30, Art. 35 | Hochschule | `docs/privacy.md` is the input; a description of the technical and organisational measures still to write |
| Processor agreement | Art. 28 | no | Needed once we host or maintain Campus; to draft (precedent: HIS eG's access at Jena) |
| Transfers outside the EU | Chapter V | partly | Demo: app and files at Hetzner Nuremberg, database at Neon on AWS Frankfurt; both in the EU, but Neon and AWS are US companies. Production: an EU provider, with BSI C5 where a cloud is used |
| Minors | § 12 VwVfG, BGB | partly | Applicants under 18 hand in the parents' consent (Unterlagenart „bei Minderjährigen“); Bescheide go to the minor; no status for Jungstudierende |
| Cookies | § 25 TDDDG | yes | Only the session and CSRF cookies, and on the demo the demo login; no tracking, nothing loaded from elsewhere: no banner needed |
| Impressum, privacy notice | § 5 DDG | yes | Footer links to the Hochschule's pages (Einstellungen › Hochschule) |

## Thuringian higher-education law

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Only the data the ordinance lists | § 11 ThürHG, ThürHDatVO of 16.8.2019 | partly | Fields follow the official statistics and the Immatrikulationsordnung; not yet compared one by one with the 2019 list. HfM's website still has the 2012 text |
| Statistics to the Thüringer Landesamt für Statistik | § 2 ThürHDatVO, HStatG | partly | Studierende, Prüfungen, Promovierende and Gasthörer:innen as eSTATISTIK.core files, with the fields the office builds its pseudonym from (`analytics/official.py`); no statistics of academic and artistic staff |
| Student data kept five years after Exmatrikulation, parts longer | Thüringer Aufbewahrungsrichtlinie, ThürArchivG | no | Nothing ends for students |
| Records offered to the archive before deletion | ThürArchivG 2018 | no | No offer and assessment step; needed before student records are deleted |
| Admission | ThürHZG, Thüringer Vergabeverordnung | partly | Eignungsprüfung by the EPO: rounds, juries, ranking, offers with a Frist, Nachrückliste; no quotas, needed only for a Studiengang with restricted admission |
| Fees | ThürHGEG | partly | Semesterbeitrag in parts, Säumnisgebühr, Eignungsprüfungsgebühr; payments booked from bank statements (CSV, camt.053); no Langzeit- or Zweitstudiengebühren |
| Rules set by the Hochschule's Ordnungen, not by code | EPO, RPSO, Studienpläne | partly | Studiengänge versioned by Prüfungsordnung; modules, Fristen, Unterlagenarten, fees and texts under Einstellungen; rules marked „code“ in `docs/rules.md` need a developer |

## Administrative procedure

Admission decisions, results and Exmatrikulationen are Verwaltungsakte.

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Reasons | § 39 VwVfG | yes | A rejection names the part not passed with its points (`admissions/views.py` `grounds()`) |
| Rechtsbehelfsbelehrung | § 58, § 70 VwGO | yes | On every Bescheid that turns someone down or that the Hochschule issues by itself (`core/texts.py`) |
| Electronic Bekanntgabe only with consent | § 41(2a) VwVfG | yes | Consent at signup, else by post (`Person.electronic`) |
| Hearing before a burdensome Bescheid | § 28 VwVfG | no | Not a step of its own |
| Inspection of one's file | § 29 VwVfG | partly | Own file in the portal; Gutachten on request, outside it; Einsicht given is not recorded |
| Grade changes kept and not altered | Prüfungsrecht | partly | Every change with who, when, before and after; a deviating grade needs a reason; released results change only through the Prüfungsausschuss; an undo is itself recorded; a decision by Bescheid is undone only by another. Not proof against direct database access |

## Accessibility

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| WCAG 2.1 AA (EN 301 549) | ThürBarrWebG, ThürBITVO, § 3 BITV 2.0 | partly | German page language, labelled fields, German browser messages, ARIA where needed; never audited; printed documents are web pages, not tagged PDF |
| Accessibility statement for Campus | ThürBarrWebG | partly | Footer link to the Hochschule's page; the statement for Campus itself not written |
| Feedback answered within the deadline | ThürBarrWebG | partly | „Hilfe und Rückmeldung“ mails the Hochschule; the deadline is not tracked |
| Accessibility criteria in a tender | § 121(2) GWB | no | Needs a conformance report from an audit |

## IT security

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| BSI IT-Grundschutz, Profil für Hochschulen | de facto, through Art. 32 GDPR | no | No security concept |
| Encrypted connections | Art. 32 | yes | HTTPS through Caddy, HSTS, secure cookies, CSRF protection |
| Login | Art. 32 | partly | Hashed passwords with rules; five failures for a name from one address hold it back there a quarter of an hour (`core/backends.py` `held()`); signed out after two hours idle and when the browser closes. No second factor: in production it comes with single sign-on (Shibboleth) from the Hochschule's identity provider |
| Rights | Art. 32 | yes | New rights and members confirmed by a second administrator, offices held until a date, exports logged |
| Security log | Art. 32 | yes | Sign-ins and failed tries with the address, kept 90 days; each person sees their last five under Mein Konto, `manage_accounts` all under Einstellungen › Anmeldungen |
| Backups | Art. 32 | partly | Database: Neon's restore window; uploaded files on the server disk without backup |
| NIS2 | NIS2UmsuCG | n/a | Universities exempt; for us from 50 staff or €10 million turnover |

## Interoperability

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| XHochschule documents | XBildung, IT-Planungsrat 2025/43 | partly | Immatrikulations-, Studienzeit- and Exmatrikulationsbescheinigung, Leistungsübersicht, Zeugnis, Urkunde, Diploma Supplement in English and German, as printable pages; the Immatrikulationsbescheinigung checkable by its Prüfcode. No XHochschule data, no BAföG § 9 version, no eIDAS seal |
| Statistics delivery | HStatG | yes | eSTATISTIK.core files |
| Erasmus Without Paper | European Commission | no | No mobility |
| Finance system | – | no | Payments come in from bank statements; nothing is booked to the Hochschule's ERP |

## Staff

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Co-determination for systems able to monitor staff | ThürPersVG | Hochschule | Campus records who changed what (Verlauf), who exported what, when each mark was given, room bookings for two years, sign-ins for 90 days and the Deputat; no analysis of activity. A Dienstvereinbarung is needed; this list is its starting point |
| Personnel records | Thuringian civil-service law | yes | Beschäftigung and Lehrverpflichtung show only on the Deputat; Mein Konto refers staff to HR; no personnel file |

## If ConCampus is sold

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Impressum for concampus.de | § 5 DDG | no | The product page has none |
| Cyber Resilience Act | Regulation (EU) 2024/2847 | n/a | Out of scope as SaaS; in scope if a Hochschule runs Campus on its own servers: reporting from 11.9.2026, CE marking from 11.12.2027 |
| Switching provider | Data Act | no | No complete export of a Hochschule's data; switching charges banned from 12.1.2027 |
| AI Act | Regulation (EU) 2024/1689 | n/a | No AI; the planned reading of paper Protokolle touches grading: assess before building |
| Other states | each state's laws | partly | Thuringian rules in editable texts and in code (ThürLVVO, VwVfG periods); official keys are national |
| Barrierefreiheitsstärkungsgesetz | BFSG | n/a | Private institutions only |

## To do in Campus

1. Deletion concept for every kind of record; students five years after Exmatrikulation, offered to the archive first.
2. A complete copy of one's data.
3. Backup of uploaded files.
4. Statistics of academic and artistic staff.
5. Mark a record as restricted.
6. XHochschule data for the certificates.
7. WCAG audit and the accessibility statement for Campus.

## To check

1. ThürHDatVO of 16.8.2019 as amended in 2020: its list of data against our fields.
2. Whether Bescheide to a minor go to the legal representatives.
3. Which Bescheide need a hearing first.
4. Whether HfM levies Langzeit- or Zweitstudiengebühren under the ThürHGEG.
5. Retention under the Thüringer Aufbewahrungsrichtlinie, and which section of the ThürArchivG covers university
   archives.
6. The deadlines in IT-Planungsrat decision 2025/43.
7. Whether HfM is among the seven Thuringian universities on the MACH ERP.
8. The answer deadline for accessibility feedback in the ThürBarrWebG.
