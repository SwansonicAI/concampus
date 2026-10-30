# Legal requirements

What the law asks of a campus management system at a Thuringian Hochschule, what Campus does about it and what is
missing. Who sees what is in `docs/privacy.md`; each rule Campus applies is in `docs/rules.md`; what replacing
HISinOne takes, and in which order, in `docs/roadmap.md`.

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
| Access | Art. 15 | yes | The person sees their record, results, documents and Postfach, and downloads a copy of everything Campus keeps about them, with their documents, under Mein Konto (`core/dossier.py`); others' data stays out |
| Rectification | Art. 16 | yes | Address and phone in Mein Konto; name and birth data by the office on proof (`core/views.py` `account()`) |
| Erasure | Art. 17 | partly | Applications that came to nothing: documents deleted and the person made anonymous after the Löschfrist; health data once the application is decided; room bookings after two years. Former students' files once kept long enough and assessed by the archive (see below). Anonymising empties every field but those the figures count (`Person.COUNTED`). Staff are never deleted |
| Restriction | Art. 18 | no | No way to mark a record as restricted |
| Objection | Art. 21 | Hochschule | Decided by the office; nothing in Campus |
| Portability | Art. 20 | n/a | Not for processing under Art. 6(1)(e) (Art. 20(3)) |
| No decision by machine alone | Art. 22, § 35a VwVfG | yes | Rankings, the Nachrückliste and a lost Prüfungsanspruch are worked out; every Bescheid is issued by an office after its preview. Resits are registered automatically as the RPSO says |
| Deletion concept | Art. 5(1)(e), Art. 17 | partly | Periods for applications, former students' files and their Grunddaten, bookings and sign-ins, set under Einstellungen › Hochschule; none for staff or the export log |
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
| Student data kept five years after Exmatrikulation, parts longer | Thüringer Aufbewahrungsrichtlinie, ThürArchivG | yes | The file 5 years after the last Exmatrikulation, then cut down to the Grunddaten (names, birth, Matrikelnummer, semesters, results) for Studienzeitbescheinigungen and Zweitschriften; those anonymised after 50; both periods set by the Hochschule (`students/disposal.py`) |
| Records offered to the archive before deletion | ThürArchivG 2018 | yes | The student office offers the files due (Studierende › Aussonderung); the archive downloads a copy of those worth keeping and assesses each; only then the night's prune cuts them down. Applications that came to nothing are deleted without an offer: that needs the archive's standing assessment |
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
| WCAG 2.1 AA (EN 301 549) | ThürBarrWebG, ThürBITVO, § 3 BITV 2.0 | partly | 48 pages of every role checked automatically against WCAG 2.1 AA (axe-core 4.10) on 30.10.2026, every finding fixed: contrast, unnamed fields, links told apart by colour alone; a skip link. No manual test (BITV-Test) yet; printed documents are web pages, not tagged PDF |
| Accessibility statement for Campus | ThürBarrWebG | yes | Under „Barrierefreiheit“ in the footer of every page, signed in or not: state, exceptions, how it was made, the enforcement body; worded under Einstellungen › Texte |
| Feedback answered within the deadline | ThürBarrWebG | yes | A form under the statement, without signing in; the report goes to the Hochschule's address and to `manage_institution` as a to-do with its deadline (30 days), answered by e-mail and marked under Einstellungen › Barrierefreiheit |
| Accessibility criteria in a tender | § 121(2) GWB | no | Needs a conformance report from an audit |

## IT security

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| BSI IT-Grundschutz, Profil für Hochschulen | de facto, through Art. 32 GDPR | no | No security concept |
| Encrypted connections | Art. 32 | yes | HTTPS through Caddy, HSTS, secure cookies, CSRF protection |
| Login | Art. 32 | partly | Hashed passwords with rules; five failures for a name from one address hold it back there a quarter of an hour (`core/backends.py` `held()`); signed out after two hours idle and when the browser closes. Sign-in through the Hochschule's identity provider (SAML, as Shibboleth and the DFN-AAI use it; `core/sso.py`), which brings its second factor where it asks for one; not yet registered with an IdP |
| Rights | Art. 32 | yes | New rights and members confirmed by a second administrator, offices held until a date, exports logged |
| Security log | Art. 32 | yes | Sign-ins and failed tries with the address, kept 90 days; each person sees their last five under Mein Konto, `manage_accounts` all under Einstellungen › Anmeldungen |
| Backups | Art. 32 | partly | Database: Neon's restore window; uploaded files on the server disk without backup |
| NIS2 | NIS2UmsuCG | n/a | Universities exempt; for us from 50 staff or €10 million turnover |

## Interoperability

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| XHochschule documents | XBildung, IT-Planungsrat 2025/43 | partly | Immatrikulations-, Studienzeit- and Exmatrikulationsbescheinigung, Leistungsübersicht, Zeugnis, Urkunde, Diploma Supplement in English and German, as printable pages; the Immatrikulationsbescheinigung checkable by its Prüfcode, also as the version of § 9 BAföG, which stands in for Formblatt 2. Immatrikulations-, BAföG-, Exmatrikulations- and Studienzeitbescheinigung also as XHochschule 1.2 files, each checked against the standard's schemas before it goes out (`students/xhochschule.py`); its further rules (Schematron, XSLT 2) not run. Leistungsübersicht, Zeugnis and Diploma Supplement not yet as data; no eIDAS seal |
| Statistics delivery | HStatG | yes | eSTATISTIK.core files |
| Reports with the health insurers, electronic only since 2022 | § 199a SGB V, Gemeinsame Grundsätze of the GKV-Spitzenverband and the HRK; Absendernummer under § 18n SGB IV | partly | Inside Campus: the insurers' reports (M10 to M13) booked to their person, by hand where none or several match; enrolment only with the status, the Rückmeldung held back by arrears; the Hochschule's reports (M20, M30, Storno) made when someone begins or stops studying here; the Absendernummer shown to applicants. Not yet: the files to and from dakota, which wait for the GKV's XML schemata; HfM's Absendernummer and certificate (`docs/smv.md`) |
| Erasmus Without Paper | European Commission | no | No mobility: no inter-institutional agreements, no online learning agreements |
| Finance system | – | no | Payments come in from bank statements; nothing is booked to the Hochschule's ERP |

## Staff

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Co-determination for systems able to monitor staff | ThürPersVG | Hochschule | Campus records who changed what (Verlauf), who exported what, when each mark was given, room bookings for two years, sign-ins for 90 days and the Deputat; no analysis of activity. A Dienstvereinbarung is needed; this list is its starting point |
| Personnel records | Thuringian civil-service law | yes | Beschäftigung and Lehrverpflichtung show only on the Deputat; Mein Konto refers staff to HR; no personnel file |

## If ConCampus is sold

| Requirement | Source | Status | In Campus, or missing |
|---|---|---|---|
| Procurement | GWB, VgV; below the threshold UVgO through the ThürVgG | Hochschule | HfM buys Campus through a procedure fitting its value, counted over the contract's term (48 months if open-ended): EU-wide from €216,000 net (2026–27), a Direktauftrag up to €30,000. A tender asks for the accessibility conformance report and the security concept |
| Contract terms | EVB-IT Cloud (IT-Planungsrat 2022/01) | no | Public buyers of software as a service contract on them: service levels, BSI C5, the data at the contract's end. Our terms to fit them |
| Continuity if ConCampus fails | the tender, the contract | no | Code, deployment and access deposited with an escrow agent, released to HfM on set conditions (`docs/escrow.md`) |
| Impressum for concampus.de | § 5 DDG | no | The product page has none |
| Cyber Resilience Act | Regulation (EU) 2024/2847 | n/a | Out of scope as SaaS; in scope if a Hochschule runs Campus on its own servers: reporting from 11.9.2026, CE marking from 11.12.2027 |
| Switching provider | Data Act | partly | A complete copy per person; none yet of a Hochschule's data as a whole; switching charges banned from 12.1.2027 |
| AI Act | Regulation (EU) 2024/1689 | n/a | No AI; the planned reading of paper Protokolle touches grading: assess before building |
| Other states | each state's laws | partly | Thuringian rules in editable texts and in code (ThürLVVO, VwVfG periods); official keys are national |
| Barrierefreiheitsstärkungsgesetz | BFSG | n/a | Private institutions only |

## To do

Every requirement above that is not „yes“ is here.

In Campus:

1. Electronic reports with the health insurers (§ 199a SGB V): the files to and from dakota (`docs/smv.md`).
2. XHochschule data for the Leistungsübersicht, Zeugnis and Diploma Supplement; an eIDAS seal on all.
3. Statistics of academic and artistic staff.
4. Mark a record as restricted (Art. 18).
5. A hearing step before Bescheide that need one (§ 28 VwVfG), once the Hochschule has named them.
6. Periods for staff records and the export log.
7. A complete export of a Hochschule's data, for switching provider (Data Act).
8. Erasmus Without Paper and booking fees to the Hochschule's finance system, if wanted.

In production:

9. Campus registered with the Hochschule's identity provider (DFN-AAI), whose second factor then counts for Campus.
10. Backup of uploaded files; an EU host, BSI C5 where a cloud is used.

Outside the code:

11. A security concept after BSI IT-Grundschutz (Profil für Hochschulen).
12. A manual accessibility test (BITV-Test) by a third party, and the conformance report a tender asks for.
13. The processor agreement (Art. 28) and the description of technical and organisational measures for the
    Hochschule's records of processing and impact assessment.
14. An Impressum for concampus.de.
15. Terms of service that fit EVB-IT Cloud.
16. Escrow (`docs/escrow.md`).

## To check

1. ThürHDatVO of 16.8.2019 as amended in 2020: its list of data against our fields.
2. Whether Bescheide to a minor go to the legal representatives.
3. Which Bescheide need a hearing first.
4. Whether HfM levies Langzeit- or Zweitstudiengebühren under the ThürHGEG.
5. Retention under the Thüringer Aufbewahrungsrichtlinie: 5 years for the file, 50 for the Grunddaten; which section
   of the ThürArchivG covers university archives; the archive's standing assessment of applications that came to
   nothing.
6. The deadlines in IT-Planungsrat decision 2025/43.
7. Whether HfM is among the seven Thuringian universities on the MACH ERP.
8. The answer deadline for accessibility feedback in the ThürBarrWebG; Campus uses 30 days.
9. HfM's Absendernummer for the Studierendenmeldeverfahren, and the rest of `docs/smv.md`'s list.
10. Thuringia's Wertgrenzen under the ThürVVöA of March 2025, which sources give differently.
