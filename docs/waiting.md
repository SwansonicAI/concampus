# Waiting on HfM and others

What ConCampus has built up to someone else's part: each interface, delivery and decision that waits for HfM, a
neighbouring system or a third party. Per item: what is built, what is missing, whom it waits on and what to ask them,
the work once the answer is in, and what happens if it is not ready at launch. The details stay in their own documents
(`docs/smv.md`, `docs/moodle.md`, `docs/mobility.md`, `docs/transfer.md`, `docs/escrow.md`, `docs/legal.md`); this one
gathers what is open. State of 13.11.2026.

Work once unblocked, estimated: **S** a few days, **M** one to two weeks, **L** several weeks.
At launch: **blocker** – HfM cannot replace HISinOne without it; **workaround** – by hand or another tool for a while;
**later** – nobody misses it on day one.

## At a glance

| # | Item | Built | Waits on | Work | At launch |
|---|---|---|---|---|---|
| 1 | Data from HISinOne | Import format, tested both ways | HfM: an export of HISinOne's data | L | blocker |
| 2 | Health insurers (dakota) | All reports inside ConCampus | GKV's XML schemata; HfM: Absendernummer, dakota, certificate | M | blocker |
| 3 | Sign-in with the Bauhaus account | SAML service provider | Bauhaus-Universität: IdP registration, attributes | S | workaround |
| 4 | Student accounts at Bauhaus | ConCampus makes its own addresses | Bauhaus-Universität: the feed's format | M | workaround |
| 5 | HfM's Moodle | Sync of courses, groups, people | HfM / TU Ilmenau: token, rights, categories | S | workaround |
| 6 | Official statistics | Files for four statistics | Landesamt: a test delivery; HfM: staff statistics from MACH? | S–M | blocker at first due date |
| 7 | Office reports | Lists as CSV, Auswertungen | HfM: which reports it pulls today | S each | workaround |
| 8 | Erasmus Without Paper | Data shaped after EWP | International Office: its software, the way to connect | S–L | workaround |
| 9 | XHochschule data and seal | Four Bescheinigungen as files | HfM: a trust service for the seal | M | later |
| 10 | Finance and bank | Payments from bank statements | HfM: ERP booking wanted?; reference format with bank and Studierendenwerk | S–M | workaround |
| 11 | No interface planned | – | thoska, library, Studierendenwerk, FSU Jena, Ausländerbehörde | – | by hand |
| 12 | Decisions and rules | Defaults in code and texts | HfM's offices, Datenschutzbeauftragte | S each | some blockers |
| 13 | Outside the code | Documents drafted | Escrow agent, lawyer, BITV tester, hosting | – | tender |

## 1. Data from HISinOne

- **Built**: one JSON document for people, studies, semesters, results by Prüfungsordnung, insurance, Studienpläne and
  Lehrverpflichtungen (`docs/transfer.md`); the demo's 21,173 people and 26,194 results go out and back in unchanged.
  Teachers also from MACH's CSV.
- **Missing**: the mapping from HISinOne's export onto that document. Not carried at all: offices and rights, courses
  and room bookings, Lehraufträge, documents and messages, the Verlauf, auditions, the insurers' reports, stays abroad,
  a thesis's topic.
- **Waits on**: HfM, and through it HIS eG.
- **Ask**:
  1. In what form HISinOne's data can come out: a database dump, HIS's own export, or the reports HISinOne can run.
  2. How many semesters back, and whether former students and applicants come too.
  3. Whether documents (uploads, scans, Bescheide sent) come out, and in which form.
  4. The day of cut-over, and whether HISinOne stays readable after it.
  5. Which of the items above „not carried“ HfM wants carried, chiefly the open insurers' reports (2) and current
     courses.
- **Work**: L. A mapping per section, a trial load with HfM's real data, checks against HISinOne's figures (counts per
  Studiengang, grades per student), a second trial, then the cut-over.
- **At launch**: blocker.

## 2. Health insurers (dakota)

- **Built**: M10 to M13 booked to their person, by hand where none or several match; M20, M30 and Storno made when a
  first study begins or a last one ends; the Absendernummer shown to applicants (`docs/smv.md`).
- **Missing**: the XML files (KVdS 2.0) and their exchange with dakota; a test against the procedure's test system;
  current students' insurer taken over for their M30.
- **Waits on**: the GKV-Spitzenverband's schemata (gkv-datenaustausch.de); HfM for its Absendernummer, its dakota and
  certificate; ITSG for dakota's licence if HIS's no longer covers it.
- **Ask**: the list in `docs/smv.md` › To check, first: where HfM's dakota runs and who looks after it; whether its
  licence comes through HIS and ends with HISinOne; the certificate's holder and expiry.
- **Work**: M. Files after the schemata, a counterpart on the dakota machine, the test run.
- **At launch**: blocker. Reports have been electronic only since 2022: from cut-over every M10 must reach ConCampus,
  or no applicant can be enrolled. One system takes the reports at a time; the change is agreed with HfM for a set day.

## 3. Sign-in with the Bauhaus account

- **Built**: ConCampus as a SAML service provider (`core/sso.py`), tested with signed answers as Shibboleth gives them;
  a person is found by id, at the first sign-in by e-mail.
- **Missing**: registration with the identity provider; the agreed attributes.
- **Waits on**: the Bauhaus-Universität's computing centre (its IdP), possibly the DFN-AAI.
- **Ask**:
  1. Whose IdP HfM's people sign in with, and whether ConCampus is registered bilaterally or through the DFN-AAI.
  2. Which attributes it releases: `eduPersonPrincipalName` (our id) and `mail` (to find the person) at least.
  3. Whether the address it releases is the one ConCampus holds for each person (see 4).
  4. Whether it asks for a second factor, and for whom.
- **Work**: S. Settings, a test sign-in per role.
- **At launch**: workaround. Everyone can sign in with a password set by e-mail; applicants do so anyway.

## 4. Student accounts at Bauhaus

- **Built**: none of the feed. ConCampus makes its own student address at Immatrikulation from the domain under
  Einstellungen › Hochschule (`students/views/enrolment.py` `matriculate()`); every change of status is one list
  (`students.models.Event`) from which a feed would be made.
- **Missing**: the feed itself: enrolment, Exmatrikulation, perhaps leave, to the computing centre that opens and
  closes accounts; the address coming back from there.
- **Waits on**: the Bauhaus-Universität's computing centre; the format is unknown.
- **Ask**:
  1. How it learns of new and leaving students today: a file, a directory (LDAP), an interface, or by hand.
  2. Which fields and events it needs, and how fast.
  3. Who makes the username and address, and how ConCampus learns it.
  4. Whether the semester ticket or the thoska hang on the same feed.
- **Work**: M. Depends wholly on the format.
- **At launch**: workaround. The office tells the computing centre as it does now, if it does so by hand; the address is
  typed in. Only a blocker if HISinOne feeds it automatically today.

## 5. HfM's Moodle

- **Built**: each semester's courses with their Kursinfo, groups, teachers and participants, each change within a
  minute; tested on a Moodle set up as HfM's (`docs/moodle.md`).
- **Missing**: the connection to HfM's Moodle; lesson groups and ensembles as Moodle groups; grades back.
- **Waits on**: HfM and TU Ilmenau, which runs its Moodle.
- **Ask**:
  1. A web service user with only the functions in `docs/moodle.md`, and its token.
  2. The category to put courses under; whether its names differ from ConCampus's Fachgebiete and Lehrformen.
  3. The role ids for Trainer:in and Teilnehmer:in.
  4. That Moodle accounts carry the same e-mail address as ConCampus (people are found by it), and that mail is on.
  5. How courses already made by hand this semester are to be treated: taken up, or left alone.
- **Work**: S.
- **At launch**: workaround. Teachers create courses by hand, as now.

## 6. Official statistics

- **Built**: Studierende, Prüfungen, Promovierende and Gasthörer:innen as eSTATISTIK.core files with the fields the
  Landesamt builds its pseudonym from (`analytics/official.py`).
- **Missing**: a delivery for real; staff statistics; stays abroad in the exam statistics (EF135–146).
- **Waits on**: the Thüringer Landesamt für Statistik; HfM for its access and on whether staff statistics come from
  MACH.
- **Ask**:
  1. A test delivery before the first one due after cut-over, and who at HfM delivers today.
  2. The Gebietsstand of the HZB's Kreis (`docs/rules.md` › For the Hochschule to confirm, 8).
  3. Whether staff statistics come from MACH; if not, ConCampus holds only teaching staff, not the rest.
- **Work**: S for the test; M for staff statistics if they are ours.
- **At launch**: blocker at the first due date, not on day one.

## 7. Office reports

- **Built**: lists as CSV, Auswertungen, the official statistics.
- **Missing**: whatever HfM's offices pull from HISinOne today that ConCampus has no list for.
- **Waits on**: HfM's offices.
- **Ask**: each office's reports from HISinOne in a typical semester, with an example, how often and for whom (the
  Präsidium, the Land, the Fakultäten, BAföG offices).
- **Work**: S each.
- **At launch**: workaround for most; one the Land or the Präsidium asks for by a date may not wait.

## 8. Erasmus Without Paper

- **Built**: partners, agreements, outgoing stays with their Learning Agreements and the recognition after, shaped
  after EWP (`docs/mobility.md`).
- **Missing**: the connection; students coming here; applying for a place and selection; the grant; the partner's
  Transcript of Records.
- **Waits on**: HfM's International Office.
- **Ask**: the list in `docs/mobility.md` › To check, first: which software it uses now and whether that is connected to
  EWP; whether incoming students are to be in ConCampus at launch.
- **Work**: S through the EWP Dashboard; L as ConCampus's own EWP node. Students coming here: M on our side, whatever
  the connection.
- **At launch**: workaround. The Dashboard by hand; incoming students enrolled as now.

## 9. XHochschule data and seal

- **Built**: Immatrikulations-, BAföG-, Exmatrikulations- and Studienzeitbescheinigung as XHochschule 1.2 files,
  checked against the schemas (`students/xhochschule.py`); Leistungsübersicht, Zeugnis, Urkunde and Diploma
  Supplement as printable pages in German and English.
- **Missing**: Leistungsübersicht, Zeugnis and Diploma Supplement as data; the standard's further rules (Schematron);
  an eIDAS seal on all.
- **Waits on**: HfM for a trust service issuing its seal; the deadlines in IT-Planungsrat decision 2025/43
  (`docs/legal.md` › To check, 6).
- **Ask**: whether HfM has or plans a qualified seal, from which provider; who signs Zeugnisse today.
- **Work**: M for the three documents as data; S–M for the seal once there is one.
- **At launch**: later, unless 2025/43 sets an earlier date.

## 10. Finance and bank

- **Built**: Semesterbeitrag, Säumnisgebühr and Eignungsprüfungsgebühr; payments booked from bank statements (CSV,
  camt.053) with a preview and a way back.
- **Missing**: booking to the Hochschule's ERP (MACH); a payment reference newcomers can be matched by (`docs/sweep.md`
  F 5).
- **Waits on**: HfM's Haushalt; the bank; the Studierendenwerk.
- **Ask**:
  1. Whether fees are to be booked to MACH, and how they get there today.
  2. The form of the payment reference, agreed with the bank account and the Studierendenwerk.
  3. Refunds through the Studierendenwerk, and when the Deutschlandsemesterticket bars one (`docs/rules.md`, 4).
  4. Whether HfM is on MACH at all (`docs/legal.md` › To check, 7).
- **Work**: S for the reference; M for ERP booking.
- **At launch**: workaround. Newcomers booked by hand; the ERP as today.

## 11. No interface planned

By hand today and at launch; each a later interface if HfM wants one.

| System | What passes | Today |
|---|---|---|
| thoska (Studierendenwerk Thüringen) | Student card, semester ticket | To check how HISinOne feeds it |
| Library | Accounts | To check |
| Studierendenwerk | Its share of the Semesterbeitrag, refunds | Payment split by hand |
| FSU Jena | Marks of shared Studiengänge | Taken in as Anerkennung |
| Ausländerbehörde | Exmatrikulation of students with a residence permit | `docs/rules.md`, 6 |

Ask HfM for each whether HISinOne feeds it today: if so, it is a blocker, not by hand.

## 12. Decisions and rules

ConCampus has a default for each; HfM confirms or changes it. Most are texts or settings; those in code are marked in
`docs/rules.md`.

| Where | Open | To whom |
|---|---|---|
| `docs/rules.md` › For the Hochschule to confirm | 8 rules: Bekanntgabe, exam prerequisites, exams in an Urlaubssemester, refunds, the Protokoll's way, Ausländerbehörde, practice bookings, Gebietsstand | Studierendenservice, Prüfungsamt, Justiziariat |
| `docs/legal.md` › To check | ThürHDatVO fields, Bescheide to minors, Langzeit- and Zweitstudiengebühren, retention periods, MACH | Justiziariat, Kanzler:in, archive |
| `docs/legal.md`, § 28 VwVfG | Which Bescheide need a hearing first; then a step for them (M) | Justiziariat |
| `docs/privacy.md` › Open for the Hochschule | Approval by the Datenschutzbeauftragte, the Verzeichnis von Verarbeitungstätigkeiten, the Hauptfachlehrer:in's view, retention | Datenschutzbeauftragte |
| `docs/legal.md`, ThürPersVG | A Dienstvereinbarung | Personalrat |

At launch: the Datenschutzbeauftragte's approval and the Dienstvereinbarung are blockers; the rest can run on the
defaults until confirmed.

## 13. Outside the code

| Item | With | Document |
|---|---|---|
| Processor agreement (Art. 28) with `docs/measures.md` as annex | HfM, a lawyer | `docs/legal.md` |
| Terms fitting EVB-IT Cloud; the licence on release | A lawyer | `docs/legal.md`, `docs/escrow.md` |
| Escrow agent and agreement | An escrow agent, HfM | `docs/escrow.md` |
| BITV test and conformance report | An accredited tester | `docs/legal.md` |
| Security concept, penetration test | A security firm | `docs/measures.md` |
| EU hosting, BSI C5 where a cloud is used | A provider | `docs/measures.md` |
| Impressum for concampus.de | Us | `docs/legal.md` |

A tender asks for most of these; none of them waits on code.

## By office

The questions above, for one meeting each.

- **Studierendenservice**: 1 (cut-over), 2 (dakota, SMV), 4 (how Bauhaus learns of students), 10, 11, 12.
- **Prüfungsamt**: 1 (results, semesters back), 7, 12 (the Protokoll, exam prerequisites).
- **International Office**: 8.
- **IT and the Bauhaus-Universität's computing centre**: 3, 4, 5, 11 (thoska, library).
- **Haushalt and Kanzler:in**: 9 (seal), 10, 13.
- **Statistics, whoever delivers it today**: 6.
- **Datenschutzbeauftragte, Personalrat, Justiziariat**: 12.
