# Data transfer

One JSON document carries a Hochschule's data in and out of Campus (`core/transfer.py`): to take over what a former
system holds, to set up the Studienpläne of a new Hochschule, and to hand a Hochschule its data when it leaves.

```
manage.py transfer daten.json              load: all or nothing, every fault named with where it is
manage.py transfer daten.json --export     write everything (logged under Einstellungen › Exporte)
```

Loading updates what is there and adds what is not; it removes nothing, so a document can be loaded again after a fix.
Each record is checked as Campus checks what is typed in: official keys, choices, a Semester against its neighbours.

## The document

```json
{
  "format": "campus/1",
  "terms": [{"name": "WiSe 2025/26", "start": "2025-10-01", "end": "2026-03-31", ...}],
  "institutes": [{"name": "Tasteninstrumente"}],
  "subjects": [{"name": "Klavier", "institute": "Tasteninstrumente", ...}],
  "programs": [{"degree": "B.Mus.", "name": "Instrumentalmusik", "version": 2026, "subjects": ["Klavier"],
                "modules": [{"code": "HF1", "title": "Hauptfach 1", "credits": 20, "waived": [], "modules": [...]}]}],
  "people": [{"matriculation": "100123", "last_name": "…", "birth_date": "2004-05-17", ...,
              "studies": [{"program": ["B.Mus.", "Instrumentalmusik", 2026], "subject": "Klavier", "start_term": "WiSe 2025/26",
                           "status": "enrolled", "teacher": "mueller",
                           "terms": [{"term": "WiSe 2025/26", "paid": "2025-09-12"}],
                           "results": [{"module": "HF1", "term": "WiSe 2025/26", "on": "2026-02-20", "grade": "1.70", "passed": true}]}]}]
}
```

Dates as `JJJJ-MM-TT`, amounts and grades as text with a point, official keys as their codes (`core/destatis.py`,
Einstellungen › Verzeichnisse). Empty fields may be left out. Each section is optional.

## What names what

| Record | Named by |
|---|---|
| Semester | `name` |
| Institut, Hauptfach | `name` |
| Studiengang | `[degree, name, version]` |
| Module | its `code` (Kürzel), else its titles from the top of the plan down, joined by „ › “; where two share them, give them a Kürzel |
| Person | `matriculation`, else `username`; without `username` the Matrikelnummer becomes it |
| Studium | person, Studiengang, `subject` and `start_term` |
| Teacher of a study | `username` |

## Fields

The model fields of the same name; their labels and choices are under Einstellungen and in the models.

| Section | Fields |
|---|---|
| `terms` | `name`, `start`, `end`, `lectures_start`, `lectures_end`, `reregistration_start`, `deadline`, `late_deadline`, `fee`, `fee_union`, `fee_ticket`, `fee_council`, `exam_signup_start`, `exam_signup_end`, `exams_start`, `exams_end`, `planning`, `ready_by`, `replies_by`, `course_signup_start`, `course_signup_end` |
| `institutes` | `name` |
| `subjects` | `name`, `english`, `field`, `institute` |
| `programs` | `name`, `degree`, `version`, `semesters`, `postgraduate`, `attempts`, `rounding`, `mean_rounding`, `honours`, `thesis_weeks`, `level`, `qualification`, `english`, `outcomes`, `exam`, `kind`, `field`, `exam_office`, `subjects`, `modules` |
| `modules` | `code`, `title`, `credits`, `semester`, `exam`, `final`, `weight`, `description`, `waived` (Hauptfächer), `modules` (below it) |
| `people` | `username`, `first_name`, `last_name`, `email`, `is_active`, `title`, `given_names`, `birth_name`, `gender`, `birth_date`, `birth_place`, `nationality`, `second_nationality`, `address`, `phone`, `private_email`, `home`, `home_town`, `residence`, `residence_town`, `entrance`, `entrance_date`, `entrance_region`, `entrance_town`, `first_university`, `first_term`, `external_terms`, `degree_university`, `degree_exam`, `degree_field`, `degree_date`, `degree_grade`, `vocational`, `internship`, `insurer`, `insurance`, `insurance_confirmed`, `insurer_number`, `insurance_arrears`, `electronic`, `entrance_checked`, `matriculation`, `sso_id`, `hold`, `employment`, `obligation`, `institute`, `studies` |
| `studies` | `program`, `subject`, `start_term`, `teacher`, `focus`, `advised` (modules), `status`, `since`, `issued`, `grade`, `prior_terms`, `exit_reason`, `structured`, `employed`, `cumulative`, `compensation`, `terms`, `results` |
| `terms` of a study | `term`, `leave`, `leave_reason`, `paid`, `amount`, `withdrawn`, `reference` |
| `results` | `module`, `term`, `on`, `grade`, `passed`, `remark`, `note` |

A result comes in as an exam of its semester, released and told on its day (`on`); a second one in the same module and
semester as a second date. Passwords never travel: people loaded sign in through the Hochschule's login or set a
password by e-mail.

## Not carried

Offices and rights, courses and room bookings, documents and messages, the Verlauf, applications' auditions, the
reports with the health insurers (`docs/smv.md`): each is set up anew or stays where it was. A thesis comes in as a
result, without its topic.

## Checked

`manage.py transfer --export` of the demo, loaded into an empty database and written again, gives the same document,
section by section (30 Semester, 13 Studiengänge with 1057 modules, 21173 people, 22686 studies, 26194 results; four
minutes). A test does the same for one Studiengang (`core/tests.py` `TransferTest`).

## Still to do

Mapping HISinOne's export onto this document, with HfM's data (`docs/roadmap.md`).
