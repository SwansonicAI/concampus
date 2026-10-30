# Privacy: who sees what

Internal reference for development. It records which roles see which personal data, why, and where the code enforces it.
It is also the starting point for the Hochschule's own Rollen- und Berechtigungskonzept, which its Datenschutzbeauftragte
approves before production use.

## Legal frame

- **§ 11 ThürHG**: the Hochschule may process personal data "soweit erforderlich" for admission, studies, examinations and
  the other listed purposes. The Thüringer Hochschul-Datenverarbeitungsverordnung (ThürHDatVO) names the kinds of data.
  The HfM's Eignungsprüfungsordnung § 12 cites § 11 Abs. 1 Nr. 1 and § 68 Abs. 4 ThürHG with the ThürHDatVO for audition
  audio and video.
- **GDPR**: purpose limitation and data minimisation (Art. 5(1)(b), (c)); by default only the data each purpose needs is
  accessible (Art. 25(2)); health data is a special category (Art. 9); access control (Art. 32).
- **Health insurance**: the insurer reports the status electronically for enrolment and Rückmeldung, the Hochschule
  the start and end of studies (§ 199a SGB V, Gemeinsame Grundsätze of the GKV-Spitzenverband and the HRK;
  `docs/smv.md`). It is the student office's business alone.

## Principle

Access follows the task (aufgaben- und zuständigkeitsbezogen), as at JGU Mainz, KH Berlin-Weißensee and Bamberg: each role
gets what its task needs, within the Institute and Studiengänge its office is limited to. Being allowed to open a page does
not mean seeing every field on it.

## Tiers of data

| Tier | What |
|---|---|
| 1 Study facts | Name, Matrikelnummer, Studiengang, Hauptfach, Schwerpunkt, status, Fachsemester, Studienbeginn, Hauptfachlehrer:in, Hochschul-E-Mail, that an Urlaubssemester was taken |
| 2 Academic record | Studienverlauf, grades and ECTS, Leistungsübersicht, attempts, a lost Prüfungsanspruch, the teachers' recommended Schwerpunkt |
| 3 Identity and contact | All first names, birth name, gender, birth date and place, address, phone, private e-mail; documents and Bescheide in the file |
| 4 Personal administration | Nationality, Hochschulzugang, health insurance and its Meldung, fees and payments, grounds of leave (Art. 9: Erkrankung, Mutterschutz), Sperre and its reason, what blocks the Rückmeldung, loans, other studies not otherwise open to the viewer |

## Who sees which tier

| Role | How it is given | Students | Applications |
|---|---|---|---|
| The person | own account | all | all |
| Hauptfachlehrer:in | `Study.teacher` | 1, 2 of their class | – |
| Examiner | staff of an exam | names, programme and results of that exam | – |
| Jury | staff of an audition | – | name, Studiengang, Hauptfach, Wunschlehrer:in, Prüfungsprogramm, documents marked for the jury |
| Institut office | `view_students`, `view_files`, `view_applications`, `run_auditions` | 1, 2 | all |
| Prüfungsamt | `decide_results`, `view_students` for the list | 1, 2, 3 | – |
| Prüfungsausschuss | `correct_scores`: corrects marks, sets an abweichende Note with its reason, reopens released results, undoes changes to them | 1, 2 | marks of auditions |
| Student office | `manage_students` | 1, 3, 4; of 2 only outcomes: a lost Prüfungsanspruch, the Abschluss | all with `view_applications` |

Grounded in practice: teachers see their own students' Leistungsdaten (KH Berlin-Weißensee, Dienstvereinbarung CMS 2017);
exam offices process identity and contact data for Zeugnisse and Bescheide (Bamberg, Göttingen, Ulm, FU Berlin); the
admission commission judges the recordings and the artistic CV, while personal and administrative application data stays
with the administration (HfM Hanns Eisler Berlin, HMTM München, HfM Würzburg).

### Applications

- Document types say whether the jury sees them (`DocumentType.jury`, "Für die Kommission"; in the demo: video and CV).
  An applicant may take a video over into another application in the same Hauptfach (`admissions/views.py`, `reuse()`);
  that application's jury then sees it too.
  Everything else, such as school certificates, the parents' consent, the fee receipt, language certificates or a
  phoniatric report (health data), is for the administration only. Health data (`DocumentType.health`) opens only to
  the person and the office deciding the application, and is deleted once it is decided (`admissions/retention.py`).
- Document types marked "Für alle Bewerbungen" (`DocumentType.shared`: CV, certificates, consent) count for every
  application of the person; access to them extends to the juries of those applications, still only where marked for the jury.

### Marks

Examiners judge on their own: until every examiner has marked a candidate, an examiner sees neither the others' marks
nor the mean, and no ranking, even one whose office otherwise sees results (`blind()`, `office`). Once all have marked, the marks show and are locked: an examiner can no longer change or
delete theirs, so nobody adjusts to the others; only the office that decides results (for auditions, the one that runs
them) changes a mark, and the Verlauf keeps it (`courses/panel.py`, `blind()`, `marks()`). A single examiner gives a
step of the scale (1,0 to 4,0 by 0,3, or 5,0); with several examiners the grade is the mean of theirs and may fall
between the steps (HfM Weimar RPSO 2026, § 15).

An examiner who fears bias towards a candidate (Besorgnis der Befangenheit, § 21 VwVfG), say as their former private
teacher, says so with a reason before marking them (`courses.Recusal`). The office that makes up the panel appoints a
substitute, so everyone is marked by as many (EPO § 3 Abs. 5); the substitute sees and marks only that candidate. The
reason shows to the panel and the office on the sheet; only the office lifts it.

Gutachten on a thesis (`Score.report`) belong to tier 2. Each Gutachter:in sees their own until all are in, then all;
the office that decides results sees them throughout, but not a draft, saved without its grade, which only its author sees. Candidates see them on request (Einsicht, RPSO 2026 § 22), not
in the portal.

### Finding students by name

Nobody gets a list of all students. Where a teacher or student adds someone to a course, a booking, an ensemble or an
instrument loan, the form lists only who is near (their class, their courses' and ensembles' members, the ticked) and finds
anyone else enrolled by name from three letters, eight at most, with Hauptfach and degree to tell namesakes apart and
no Matrikelnummer (`students/views.py`, `suggest()`). Who already knows a Matrikelnummer may still type it.

### Staff

A teacher's Beschäftigung and Lehrverpflichtung (`Person.employment`, `Person.obligation`) show only on the Deputat:
to the teacher for their own row, and to `view_teaching_load` within the Institute its office is limited to
(`courses/views.py`, `load()`). Accounts keep them (`manage_accounts`). Ermäßigungen (`courses.Reduction`) show the same
way, their reason as the function (Dekan:in, Studiendekan:in …); `manage_teaching_load` enters them. A reduction for a
Schwerbehinderung is health data (Art. 9): its reason shows only as that, never the condition.

### Forms

When a form is refused, what was typed is kept in the session to fill it in again (`core/forms.py`, `again()`, `sent()`),
never in the address: a note may say why someone was ill, and addresses end up in logs.

### Consent to health data

Health data in a document (an attest to a Rücktritt, a Nachteilsausgleich or an Urlaubssemester, a phoniatric report)
is processed on the person's consent (Art. 9 Abs. 2 lit. a DSGVO, as in the HfM's own notices). It is asked with each
upload, in the Baustein „Einwilligung in Gesundheitsdaten“; an office filing a paper attest confirms the signed consent.
`core.models.Consent` keeps the wording, when and who recorded it. The person withdraws it under Mein Konto: the
documents given under it are deleted, the consent stays marked as withdrawn, and what was decided before stays
(Art. 7 Abs. 3). The consent to electronic Bescheide (§ 41 Abs. 2a VwVfG) is given or withdrawn there too.

### Sign-ins

Signing in through the Hochschule's login, Campus keeps only the id the login gives the person (`Person.sso_id`); at the
first sign-in it finds them by the e-mail address the login sends, which it does not keep (`core/sso.py`).

Each sign-in and failed try is kept with the name given, the person it stands for and the address it came from, for
the accounts' security alone (Art. 32), and deleted after 90 days (`core.models.SignIn`, `manage.py prune`). The person
sees their last five under Mein Konto; `manage_accounts` sees all under Einstellungen › Anmeldungen. It is no record of
anyone's work: the Personalrat agrees to it in the Dienstvereinbarung.

### A copy, and the end of a file

Under Mein Konto everyone downloads a copy of what Campus keeps about them, with their documents (Art. 15 Abs. 3;
`core/dossier.py`); a booking or course shows without who else took part. The copy is logged as an export.

A former student's file is kept `file_years` (5) after the last Exmatrikulation, then offered to the archive by
`dispose_files` (Studierende › Aussonderung). The archive (`assess_files`) sees the offered names with their studies,
downloads a copy of those it keeps, and assesses each; nothing goes before that (ThürArchivG). The night after, the
file is cut down to the Grunddaten: names, birth date and place, Matrikelnummer, the studies with their semesters and
results. Documents, messages, the Verlauf, contact and personal administration data, consents, sign-ins, loans,
bookings, notes, Gutachten, grounds of leave and a Nachteilsausgleich go. After `core_years` (50) the Grunddaten are
made anonymous (`students/disposal.py`, `manage.py prune`).

### Moodle and XHochschule files

Each night Campus gives the Hochschule's Moodle the e-mail addresses of the teachers and participants of its courses, to
find their accounts there, and enrols them; Moodle already holds their names (`docs/moodle.md`). Only on the demo,
whose people are made up, Campus also makes the accounts, with name and e-mail address. A Bescheinigung as an
XHochschule file carries what the printed one does; the Studienzeitbescheinigung also each leave's ground, as the
standard asks of it, Erkrankung among them: it goes to whoever downloads it, the person or the student office.

### Stays abroad

A stay abroad and its Learning Agreement (`mobility/`) are seen by the student, the International Office
(`manage_mobility`, for the whole Hochschule) and whoever recognises in the study; the partner Hochschule learns of
them from the International Office, by Erasmus Without Paper once Campus is connected (`docs/mobility.md`). They go
with the file.

### Reports with the health insurers

An insurer's report names the person (names, birth date), their insurer and its Betriebsnummer, and the status, a new
insurer from a day, or arrears; the Hochschule's names the person, the semester and the day they began or stopped
studying (`students.models.InsuranceReport`). Only the student office for the whole Hochschule sees them (Studierende ›
Krankenkassen): until a report is booked it may be about anyone. Booked, it goes with the file and into the person's
copy; about nobody here, it is deleted after the period for applications (`manage.py prune`). The list of who began or
stopped studying (`students.models.Event`) holds no more than the study's status does, and goes with the file.

### Reports of barriers

Who reports a barrier under the accessibility statement leaves an e-mail address, a name if they like, the page and
their message (`core.models.Feedback`). `manage_institution` sees them under Einstellungen › Barrierefreiheit; each is
deleted a year after it was answered.

## Where the code enforces it

- `students/views.py`, `study()`: `personal` (the person or `manage_students`) and `identity` (also `decide_results`);
  `students/templates/students/study.html` shows each field only for its tier.
- `admissions/views.py`, `application()`: `personal` (the person or `view_applications`); the jury's checklist holds only
  document types marked for it. `document()`: who may open a file.
- `core/views.py`, `people()`: birth dates in search results only for `manage_students`, `decide_results`,
  `view_applications`. `letter()`: filed Bescheide for `manage_students` and `decide_results`.
- `students/views.py`, `roster()`: gender and nationality in the export only for `manage_students`. Every file with
  personal data that leaves (student list, Notenliste, amtliche Statistik) is logged with who, when, what and how many
  rows (`core.models.Export`), shown to `manage_accounts` under Einstellungen › Exporte.
- `students/views.py`, `study()`: grades, Gesamtnote and Leistungsübersicht for the person, `view_files` and the
  Hauptfachlehrer:in; the student office sees outcomes only. The Schwerpunkt is recorded by `decide_results`.
- `core/views.py`, `found()`: a rejection older than the retention period is no longer found. Teachers find the
  applications they rate and the students of their courses and ensembles, by name and course only, not their file.
  Everyone signed in but applicants and alumni finds who teaches or holds an office, with Institut and work e-mail, and
  the offices with their shared address and members; applicants and alumni find the offices by their shared address
  only, without who works there.
- `core/views.py`, `account()`, `confirm_email()`: an applicant's new e-mail address is taken on only once confirmed from
  a link sent to it; until then the old one holds.
- `students/views.py`, `verify()`: whoever holds an Immatrikulationsbescheinigung's Prüfcode sees what it says (name,
  Studiengang, semester) and whether the study has ended since; without the code, nothing.
- `core/models.py`, `Tracked.keepers()`, `seen_by()`: a record's Verlauf and undoing its changes belong to the offices
  that look after it, a course's by its kind (an audition's to the office that runs auditions, an exam's to those that
  manage or decide exams), results through their course (`courses.models.Judged`); a person's Verlauf opens to each
  office for its own part. Whoever is refused hears which offices keep it, never the record's name.
- `core/models.py`, `Grant`, `Membership`: new rights for an office and new members of one take effect once a second
  holder of `manage_accounts` confirms them (Einstellungen › Freigaben); offices can be held until a day, for stand-ins
  and terms of office. Taking rights away is immediate.
- `core/access.py`: the capabilities and their wording; `Person.within`, `Person.may`, `Study.reachable` scope them to
  Institute and Studiengänge.
- Test: `students/tests.py`, `FileTest.test_each_sees_what_their_task_needs`.

## Rules for new work

1. A new personal field gets a tier before it gets a template; when unsure, the more restrictive one.
2. A new page or role states its task first, then shows the lowest tiers that task needs.
3. Lists, exports, search results and messages never show more than the record page would show the same person.
4. Health data (Art. 9) stays with the student office, and with the Prüfungsamt or Prüfungsausschuss where an exam
   depends on it (Rücktritt with an attest, Nachteilsausgleich).
5. Change this document with the code, and extend the test when a tier or role changes.

- `core/models.py`, `Person.home_town`, `residence_town`, `entrance_town`: the Gemeinde, kept when someone finds their place
  by its town or postcode; seen wherever the Kreis is. It keeps the Kreis right when a town changes Kreis (`core/keys.py`,
  `follow()`), each change in the Verlauf with its reason. The list of residences to check after such a change, with
  names and addresses, is for `manage_students` only (Einstellungen › Amtliche Verzeichnisse).

## Open for the Hochschule

- Approval by the Datenschutzbeauftragte and the entry in the Verzeichnis von Verarbeitungstätigkeiten.
- Whether the Hauptfachlehrer:in keeps the whole academic record of the class, or sees only their own courses as at some
  universities (HöMS Hessen).
- Retention periods (applications: `admissions/retention.py`).
- Nachteilsausgleich (`Study.compensation`, granted with `grant_compensation`): the measure is shown to the examiners of
  the person's exams; the attest behind it, like the attest to a Rücktritt, is tier 4 and opens only to the person and
  the offices whose decision depends on it (`admissions/views.py`, `document()`).

## Sources

- [ThürHG 2018](https://www.lexaris.de/book/version/documentflat/head/1320333), § 11
- [HfM Weimar, Eignungsprüfungsordnung](https://www.hfm-weimar.de/fileadmin/user_upload/Eignungspruefung/Eignungspruefungsordnung.pdf), § 12
- [Uni Erfurt, Information nach Art. 13 DSGVO](https://www.uni-erfurt.de/studium/vor-dem-studium/bewerbung-zulassung-einschreibung/information-nach-art-13-eu-dsgvo)
- [KH Berlin-Weißensee, Mitteilungsblatt 227](https://kh-berlin.de/fileadmin/user_upload/Verwaltung/Mitteilungsblaetter/227_Mitteilungsblatt.pdf)
- [JGU Mainz, Datenschutzkonzept Jogustine](https://info.jogustine.uni-mainz.de/files/2024/08/20240705_Datenschutzkonzept_Jogustine_2024.pdf)
- [Uni Bamberg, Prüfungsverwaltung](https://www.uni-bamberg.de/datenschutz/datenschutzinformationen/studium-lehre/pruefungsverwaltung/)
- [Uni Göttingen, Datenschutzerklärung](https://www.uni-goettingen.de/en/593533.html)
- [HöMS Hessen, Handbuch Studierende](https://hoems.hessen.de/sites/hoems.hessen.de/files/2023-12/handbuch_studierende_30_1.pdf)
- [HfM Hanns Eisler Berlin, FAQ Bewerbung](https://www.hfm-berlin.de/bewerbung/haeufig-gestellte-fragen/)
- [HMTM München, Datenschutzhinweise Bewerbung](https://hmtm.de/wp-content/uploads/PDF/datenschutz/HMTM_Datenschutzhinweise_Bewerbung-Zulassung_dt.pdf)
- [HfM Würzburg, Datenschutz Zulassung](https://hfm-wuerzburg.de/datenschutz/zulassung)
- [Uni Oldenburg, Krankenversicherung (§ 199a SGB V)](https://UOL.de/fileadmin/user_upload/studium/download/iamt/uni-oldenburg-regelungen-zur-krankenversicherung.pdf)
- [Hamburg, Datenschutz in der Studierendenverwaltung](https://www.hh-datenschutz.de/themen/studierendenverwaltung.html)
