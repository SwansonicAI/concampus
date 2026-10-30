# Rules and their sources

Internal reference for development and for the conversation with the Hochschule. Each rule the system applies, the
regulation it comes from, where the code enforces it, and whether the Hochschule changes it itself under Einstellungen
or it needs a code change. Who sees which data is in `docs/privacy.md`.

## Sources

- **RPSO 2026**: Rahmenstudien- und Prüfungsordnung, Verkündungsblatt 1/2026, for everyone enrolled on 1.4.2026
  (§ 25). <https://www.hfm-weimar.de/fileadmin/user_upload/Studienplaene_und_Ordnungen/Studien-_und_Pruefungsordnungen/RPSO_2026.pdf>
- **EPO**: Eignungsprüfungsordnung of 25.10.2024, as amended 19.09.2025.
- **ThürLVVO**: Thüringer Lehrverpflichtungsverordnung, as amended 2025.
- **VwVfG**: Verwaltungsverfahrensgesetz (federal text; Thüringen's ThürVwVfG to be confirmed, see the end).
- **AufenthG**, **SGB V** (§ 199a, Studierendenmeldeverfahren).
- **KMK/HRK**: Diploma Supplement model, section 8 in the 2018 wording.

"Einstellungen › X" means the Hochschule changes it itself; "code" means a developer does.

## Grades and marks

| Rule | Source | Code | Set in |
|---|---|---|---|
| Exam mean of the examiners rounded kaufmännisch to one decimal | RPSO § 15 Abs. 2 | `courses/panel.py` `result()`, `rule()` | Einstellungen › Studiengänge (Mittel der Prüfenden) |
| Audition mean cut after the first decimal | EPO § 6 Abs. 4 | `courses/panel.py` `rounding()` | code |
| Module grades and Gesamtnote rounded kaufmännisch; areas between them (Vertiefung) keep their exact mean | RPSO § 15 Abs. 4 | `curriculum/models.py` `weighted()`, `Program.curriculum()` | Einstellungen › Studiengänge (Modul- und Gesamtnoten) |
| Thesis: Gutachten more than 1,0 apart or one failing → a third; grade the mean of the two better | RPSO § 15 Abs. 2 | `courses/panel.py` `disputed()`, `result()` | code |
| Examiners see the others' marks only once all have marked; then theirs is locked | practice | `courses/panel.py` `blind()`, `marks()` | code |
| A mark set by hand differs from the panel's only with a reason, by the Prüfungsausschuss | practice | `courses/panel.py` `marks()` (`correct`) | Einstellungen › Ämter (`correct_scores`) |
| The office copies examiners' marks only once the signed Protokoll is in, on paper or as a scan; the sheet says who copied | practice | `courses/panel.py` `marks()`, `exams/views.py` `exam()`, `protocol()` | code |
| A Befangenheit takes the examiner out of the mean; a substitute marks in their place | § 21 VwVfG | `courses/panel.py` `sheet()`, `courses/views.py` `recuse()` | code |
| Honours up to a Gesamtnote | per Studiengang | `exams/views.py` `achievements()` | Einstellungen › Studiengänge |

## Panels and examiners

| Rule | Source | Code | Set in |
|---|---|---|---|
| Artistic exam in the Haupt- or Schwerpunktfach: three to eight examiners | RPSO § 17 Abs. 3 | `courses/models.py` `Course.shortfall` | code |
| Oral or practical exam: two examiners, or one with a Beisitz | RPSO § 17 Abs. 3 | same | code |
| Final and repeat exams: at least two, one of them a professor | RPSO § 17 Abs. 5 | same | code |
| Audition commission: three for the Hauptfach, two for other parts | EPO § 3 Abs. 4 | same | code |
| Examiners appointed per Studienjahr and subject; others are flagged, not refused | EPO § 3 Abs. 2, RPSO § 17 | `courses/models.py` `Appointment`, `Course.panels()` | Einstellungen › Bestellte Prüfende |

## Attempts, withdrawal, compensation

| Rule | Source | Code | Set in |
|---|---|---|---|
| Attempts per exam; when used up, the Prüfungsanspruch is lost: no registration for exams or courses | Prüfungsordnung | `courses/models.py` `attempts()`, `Course.refuses()`, `exams/views.py` `exhausted()`, `courses/views.py` `register()` | Einstellungen › Studiengänge (Prüfungsversuche) |
| Withdrawal from an exam until some weeks before the Prüfungszeitraum | RPSO | `courses/models.py` `Course.withdraw_by` | Einstellungen › Hochschule (Abmeldung von Prüfungen) |
| Rücktritt (RT) for good reason only with its attest, on paper or as a scan; RT is no attempt | RPSO § 20 | `courses/panel.py` `marks()`, `exams/views.py` `attest()` | code |
| Nachteilsausgleich granted by the Prüfungsausschuss by Bescheid; examiners see the measure, not the attest | RPSO § 8 | `exams/views.py` `compensate()` | Einstellungen › Ämter (`grant_compensation`) |

## Deadlines

| Rule | Source | Code | Set in |
|---|---|---|---|
| A Frist ending on a Saturday, Sunday or public holiday ends on the next working day | § 31 Abs. 3 VwVfG | `core/dates.py` `working()`, used for theses and the Widerspruchsfrist | Einstellungen › Unterrichtsfreie Tage (Feiertag) |
| Results due: oral and practical exams a week after, Klausuren six weeks | RPSO § 13 Abs. 7 | `exams/apps.py` `graded_by()` | code |
| Gutachten due eight weeks after submission | RPSO § 13 Abs. 11 | `exams/apps.py`, `exams/views.py` `exam()` | code |
| Thesis due after the weeks the Prüfungsordnung allows | per Studiengang | `exams/views.py` `thesis()` | Einstellungen › Studiengänge (Bearbeitungszeit) |
| Thesis handed in with the Selbstständigkeitserklärung, KI included | RPSO § 14 Abs. 8 | `exams/views.py` `submit()` | code |
| Zeugnis due four weeks after the last result; after three months more the Prüfungsausschuss hears | practice | `exams/apps.py` | code |
| Widerspruch within a month of Bekanntgabe | VwVfG, § 70 VwGO | `exams/views.py` `exhausted()`, Baustein „Rechtsbehelfsbelehrung“ | Einstellungen › Texte |
| A video round is rated by its own date, after the Bewerbungsschluss | practice | `courses/models.py` `Course.marks_by`, `courses/forms.py` `AuditionForm` | Einstellungen › Eignungsprüfungen |
| Offer of a place accepted within some days, else it goes down the Nachrückliste | practice | `admissions/views.py` `accept_by()` | Einstellungen › Hochschule |

## Bescheide

| Rule | Source | Code | Set in |
|---|---|---|---|
| A Bescheid in the Postfach counts as delivered the day after it is opened, where the person agreed to that at signup or later under Mein Konto, until they withdraw it; otherwise by post, on the fourth day | § 41 Abs. 2, 2a VwVfG | `core/models.py` `Person.electronic`, `exams/views.py` `exhausted()` | code |
| Every Bescheid has an Aktenzeichen and is filed in the student's file | practice | `core/models.py` `Message.reference`, `students/models.py` `Study.notify()` | code |
| Bescheide that turn someone down, or that the Hochschule issues by itself (Exmatrikulation reasons 0, 7, 8), carry the Rechtsbehelfsbelehrung | VwVfG, VwGO | `core/texts.py` `appeal`, `students/models.py` `OFFICIAL` | Einstellungen › Texte (wording) |
| A rejection names the part of the Eignungsprüfung not passed, with its points | VwVfG § 39 | `admissions/views.py` `grounds()` | Einstellungen › Texte (wording) |
| Bescheide for Immatrikulation, Exmatrikulation, Nachrücken and decisions are shown before they go out | practice | `core/views.py` `preview()` | code |
| A decision made by Bescheid is not undone in the Verlauf, only by another Bescheid | practice | `core/models.py` `Tracked.settled`, `core/history.py` | code |

## Studies

| Rule | Source | Code | Set in |
|---|---|---|---|
| Immatrikulation once the place is accepted, the fee paid, the insurer's report in and the HZB on record | ThürHG, § 199a SGB V | `students/models.py` `Study.missing()` | code |
| An HZB from a school abroad is assessed; German proved where it is not in German; an APS certificate for China, India, Vietnam; both by the Immatrikulation | ThürHG, KMK | `core/models.py` `Person.abroad`, `needs_german`, `needs_aps`; `core/destatis.py` | Einstellungen › Unterlagenarten |
| Each kind of document takes up to 5 files of 20 MB each unless set otherwise; the video one file of 1.5 GB, which may be taken over into another application in the same Hauptfach | Hochschule | `students/models.py` `DocumentType.size`, `max_files`; `admissions/views.py` `upload()`, `reuse()` | Einstellungen › Unterlagenarten |
| Semesterbeitrag in three parts, which add up | HfM fees page | `core/models.py` `Term.parts`, `clean()` | Einstellungen › Semester |
| Rückmeldung within its window; in the Nachfrist with the Säumnisgebühr; after it, Exmatrikulation | Immatrikulationsordnung | `students/views.py` `reregistration()` | Einstellungen › Semester, › Hochschule |
| Urlaubssemester asked for in the Rückmeldung window, at most two in the rule, in the first semester only for illness or Mutterschutz; the office decides beyond, reminded | Immatrikulationsordnung | `students/views.py` `leave()`, `students/models.py` `LEAVE_TERMS`, `FIRST_TERM_LEAVES` | code |
| Exmatrikulation names the last day enrolled; after a final fail the day the Bescheid became final, at the earliest the day before this semester | ThürHG | `students/views.py` `leaving_day()`, `exmatriculate()` | code |
| A semester paid for and left before it began stays on record, counted nowhere, its fee refunded by the Studierendenwerk | HfM fees page | `students/models.py` `StudyTerm.withdrawn`, Baustein „Erstattung“ | Einstellungen › Texte |
| A residence permit holder is told to inform the Ausländerbehörde within two weeks | § 82 Abs. 6 AufenthG | `core/models.py` `Person.permit`, Baustein „Ausländerbehörde“ | Einstellungen › Texte |
| The Schwerpunkt is the student's choice until it begins, confirmed once more; after that the office records it, with its ground, by Bescheid | Studienplan | `students/views.py` `focus()` | code |

## Zeugnis and Diploma Supplement

| Rule | Source | Code | Set in |
|---|---|---|---|
| Zeugnis and Urkunde dated by the last result; signed by the head of the Hochschule and the dean | RPSO § 23 Abs. 1 | `exams/views.py` `achievements()`, Bausteine „Unterschriften“ | Einstellungen › Texte |
| Before issue printed as Entwurf | practice | `exams/templates/exams/transcript.html` | code |
| Diploma Supplement in English and German, dated as the Zeugnis, with the ECTS grade distribution of the Studiengang's graduates of three years (from ten) | RPSO § 23 Abs. 2, ECTS Users' Guide | `exams/views.py` `supplement()`, `distribution()` | code |
| Section 8 in the KMK/HRK wording | KMK/HRK 2018 | Bausteine „Hochschulsystem“ | Einstellungen › Texte |

## Teaching load

| Rule | Source | Code | Set in |
|---|---|---|---|
| One SWS of artistic teaching is 60 minutes; individual lessons count per student by the minutes their plan gives | ThürLVVO § 2 Abs. 2, § 5 Abs. 1 | `courses/views.py` `sws()`, `minutes()` | Einstellungen › Module (form, e.g. „E 90“) |
| Lehrverpflichtung 18 LVS artistic, 9 academic professors | ThürLVVO § 4 | `core/models.py` `Person.obligation` | Einstellungen › Konten |
| Balanced over three Studienjahre, never under half in a term | ThürLVVO § 7 | `courses/views.py` `loads()` | Einstellungen › Hochschule (Deputatskonto ab) |
| Reductions by function, one per person | ThürLVVO § 8 | `courses/models.py` `Reduction` | Einstellungen › Ermäßigungen |
| A Freisemester sets the whole Lehrverpflichtung aside; courses planned for it are flagged | Hochschule | `courses/views.py` `rollover()` | Einstellungen › Ermäßigungen |
| A lesson class of a coming semester not yet filled counts as many students as it had | Hochschule | `courses/views.py` `sws()` | – |

## Semesterwechsel

| Rule | Source | Code | Set in |
|---|---|---|---|
| A semester in planning is unseen by students: no Vorlesungsverzeichnis, search, course page or registration | Hochschule | `core/models.py` `Term.shown()` | Einstellungen › Semester (In Planung) |
| Courses are proposed by Turnus: every semester from the term before, yearly from the same semester a year before, one-off ones only by choice | Hochschule | `courses/views.py` `missing()` | each course (Turnus) |
| A copy keeps staff and Turnus and counts for the modules of its source and of the same semester a year before; the source stays as it was | Hochschule | `courses/views.py` `take_over()` | – |
| New and copied courses take the semester's registration window | Hochschule | `courses/views.py` `take_over()`, `edit()` | Einstellungen › Semester |
| Teachers, or the Institut for its courses, confirm or call off each copied course by the reply date | Hochschule | `courses/views.py` `reply()`, `courses/apps.py` | Semesterwechsel (Antwort bis) |
| Last year's weekly time and room are booked only where the room is free on every date | Hochschule | `courses/views.py` `schedule()` | – |
| Lesson classes are filled after the Rückmeldefrist: who re-registered without leave, and new students of their teachers in the Hauptfach, with the module of their next Fachsemester | Hochschule | `courses/views.py` `fill()` | Einstellungen › Semester (Rückmeldefrist) |
| The Dekanat publishes, the Referat prepares; courses called off before publishing are deleted | Hochschule | `courses/views.py` `publish()` | Einstellungen › Ämter (Recht „Ein geplantes Semester veröffentlichen“) |
| A published course called off stays listed as „entfällt“; its dates are called off, registrations without a result lifted, everyone told why | Hochschule | `courses/views.py` `cancel()` | – |
| Once a semester is over, its courses are corrected only by the office, with a reason kept in the Verlauf | Hochschule | `courses/views.py` `edit()`, `core/history.py` `because()` | – |

## Rooms

| Rule | Source | Code | Set in |
|---|---|---|---|
| Students book practice up to some hours a day, some hours ahead | house rules | `rooms/forms.py` `BookingForm` | Einstellungen › Hochschule, › Buchungskategorien |
| What starts within the short notice is free of the quota, for at most that long | house rules | same | Einstellungen › Hochschule (Kurzfristig ohne Kontingent) |

## Data kept

| Rule | Source | Code | Set in |
|---|---|---|---|
| Applications that came to nothing: documents deleted and people made anonymous after a period | GDPR Art. 5(1)(e) | `admissions/retention.py`, `core/management/commands/prune.py` | Einstellungen › Hochschule (Löschfrist) |
| Health data (phoniatric report) deleted once the application is decided | GDPR Art. 9 | same | Einstellungen › Unterlagenarten (Gesundheitsdaten) |
| Files with personal data that leave are logged | GDPR Art. 5(2) | `core/models.py` `Export` | code |
| Health data only with consent, kept with its wording; withdrawing it deletes the document | GDPR Art. 7, Art. 9(2)(a) | `core/models.py` `Consent`, `core/views.py` `revoke()` | Einstellungen › Texte (wording) |

## Official keys

| Rule | Source | Code | Set in |
|---|---|---|---|
| A record keeps a key as it was given; a key since ended stays valid on it and shows when it ended and what replaced it | Destatis key lists | `core/keys.py` `KeyField`, `label()` | – |
| The official report sends the key valid at reporting time, following each replacement; one ended without a successor is listed to be chosen anew | Destatis, Schlüsselverzeichnis | `analytics/official.py` `now()`, `core/keys.py` `replaced()` | – |
| Heimat- and Semesterwohnsitz follow their Gemeinde to its new Kreis, each change in the Verlauf with its reason; where the HZB was gained stays as it was | Gemeindeverzeichnis | `core/keys.py` `follow()` | – |
| Whoever lives in a Kreis that gave up a town, the town unknown, is listed for the student office to check | – | `core/keys.py` `review()` | Einstellungen › Amtliche Verzeichnisse |
| The lists are kept centrally: the Schlüsselverzeichnis each year, the Gemeindeverzeichnis each quarter, brought in with an update | Destatis | `manage.py keys`, `core/data` | – |

## For the Hochschule to confirm

1. Whether § 41 Abs. 2a ThürVwVfG matches the federal text: Bekanntgabe the day after opening in the Postfach.
2. Whether Künstlerische Präsentation I must be passed before the Bachelor thesis is registered; the plan names
   prerequisites only for Künstlerische Präsentation II.
3. Whether exams are possible during an Urlaubssemester, and for which grounds (Immatrikulationsordnung).
4. That refunds of the Semesterbeitrag go through the Studierendenwerk, and when the Deutschlandsemesterticket bars one.
5. The paper Protokoll workflow: who sends it to the Prüfungsamt, and whether a scan is kept.
6. Whether the Hochschule tells the Ausländerbehörde of an Exmatrikulation itself, beyond the student's own duty.
7. Whether a short-notice practice booking should be capped differently from the notice window.
8. Which Gebietsstand the Kreis of the HZB is reported in: as it was when gained, or today's (sent now: today's). The
   Thüringer Landesamt für Statistik knows.
