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
- **Health insurance**: the insurer reports the status electronically for enrolment and Rückmeldung (§ 199a SGB V,
  Studentenkrankenversicherungs-Meldeverordnung). It is the student office's business alone.

## Principle

Access follows the task (aufgaben- und zuständigkeitsbezogen), as at JGU Mainz, KH Berlin-Weißensee and Bamberg: each role
gets what its task needs, within the Institute and Studiengänge its office is limited to. Being allowed to open a page does
not mean seeing every field on it.

## Tiers of data

| Tier | What |
|---|---|
| 1 Study facts | Name, Matrikelnummer, Studiengang, Hauptfach, Schwerpunkt, status, Fachsemester, Studienbeginn, Hauptfachlehrer:in, Hochschul-E-Mail, that an Urlaubssemester was taken |
| 2 Academic record | Studienverlauf, grades and ECTS, Leistungsübersicht, attempts, a lost Prüfungsanspruch |
| 3 Identity and contact | All first names, birth name, gender, birth date and place, address, phone; documents and Bescheide in the file |
| 4 Personal administration | Nationality, Hochschulzugang, health insurance and its Meldung, fees and payments, grounds of leave (Art. 9: Erkrankung, Mutterschutz), Sperre and its reason, what blocks the Rückmeldung, loans, other studies not otherwise open to the viewer |

## Who sees which tier

| Role | How it is given | Students | Applications |
|---|---|---|---|
| The person | own account | all | all |
| Hauptfachlehrer:in | `Study.teacher` | 1, 2 of their class | – |
| Examiner | staff of an exam | names, programme and results of that exam | – |
| Jury | staff of an audition | – | name, Studiengang, Hauptfach, Wunschlehrer:in, Prüfungsprogramm, documents marked for the jury |
| Institut office | `view_students`, `view_files`, `view_applications`, `run_auditions` | 1, 2 | all |
| Prüfungsamt | `decide_results` | 1, 2, 3 | – |
| Student office | `manage_students` | 1 to 4 | all with `view_applications` |

Grounded in practice: teachers see their own students' Leistungsdaten (KH Berlin-Weißensee, Dienstvereinbarung CMS 2017);
exam offices process identity and contact data for Zeugnisse and Bescheide (Bamberg, Göttingen, Ulm, FU Berlin); the
admission commission judges the recordings and the artistic CV, while personal and administrative application data stays
with the administration (HfM Hanns Eisler Berlin, HMTM München, HfM Würzburg).

### Applications

- Document types say whether the jury sees them (`DocumentType.jury`, "Für die Kommission"; in the demo: video and CV).
  Everything else, such as school certificates, the parents' consent, the fee receipt, language certificates or a
  phoniatric report (health data), is for the administration only.
- Document types marked "Für alle Bewerbungen" (`DocumentType.shared`: CV, certificates, consent) count for every
  application of the person; access to them extends to the juries of those applications, still only where marked for the jury.

### Marks

Examiners judge on their own: until an examiner has entered their own mark for a candidate, they see neither the other
examiners' marks nor the mean, and no ranking (`courses/panel.py`, `blind()`). Offices that decide results see everything.
A single examiner gives a step of the scale (1,0 to 4,0 by 0,3, or 5,0); with several examiners the grade is the mean of
theirs and may fall between the steps (HfM Weimar RPSO 2026, Leistungsbewertung).

### Staff

A teacher's Beschäftigung and Lehrverpflichtung (`Person.employment`, `Person.obligation`) show only on the Deputat:
to the teacher for their own row, and to `view_teaching_load` within the Institute its office is limited to
(`courses/views.py`, `load()`). Accounts keep them (`manage_accounts`).

### Forms

When a form is refused, what was typed is kept in the session to fill it in again (`core/forms.py`, `again()`, `sent()`),
never in the address: a note may say why someone was ill, and addresses end up in logs.

## Where the code enforces it

- `students/views.py`, `study()`: `personal` (the person or `manage_students`) and `identity` (also `decide_results`);
  `students/templates/students/study.html` shows each field only for its tier.
- `admissions/views.py`, `application()`: `personal` (the person or `view_applications`); the jury's checklist holds only
  document types marked for it. `document()`: who may open a file.
- `core/views.py`, `people()`: birth dates in search results only for `manage_students`, `decide_results`,
  `view_applications`. `letter()`: filed Bescheide for `manage_students` and `decide_results`.
- `students/views.py`, `roster()`: gender and nationality in the export only for `manage_students`.
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

## Open for the Hochschule

- Approval by the Datenschutzbeauftragte and the entry in the Verzeichnis von Verarbeitungstätigkeiten.
- Whether the Hauptfachlehrer:in keeps the whole academic record of the class, or sees only their own courses as at some
  universities (HöMS Hessen).
- Retention periods (applications: `admissions/retention.py`).
- Nachteilsausgleich is not modelled yet; when it is, it belongs to tier 4.

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
