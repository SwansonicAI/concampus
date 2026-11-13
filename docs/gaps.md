# Gaps in the domain

What a Musikhochschule's campus system does that ConCampus does not yet, or only in part: how big each gap is, whether
the law or HfM's own Ordnungen ask for it, and whether other systems carry it. Interfaces and deliveries that wait on
others are in `docs/waiting.md`; legal duties ConCampus already meets in `docs/legal.md`. From a reading of the code
and research of November 2026; what is still to be checked is listed at the end.

ThürHG sections are those of the 2018 text (GVBl. 5/2018), last amended 2.7.2024; a second amending act was before the
Landtag in May 2026. Check each against the current text before citing it to HfM.

**Need**: **day one** – HfM's Ordnungen or the law ask for it and HISinOne does it now, so dropping it breaks something;
**soon** – within the first year; **nice** – asked for, not owed; **outside** – a separate tool's job, ConCampus at
most feeds it. **Gap**: **none** built, **partial**, **built**. **Work**, estimated: **S** a few days, **M** one to two
weeks, **L** several weeks.

## At a glance

| # | Feature | Need | Gap | Work | Others |
|---|---|---|---|---|---|
| 1 | Fees beyond the Semesterbeitrag | day one | built | – | HISinOne: yes |
| 2 | Teilzeitstudium | day one | built | – | common |
| 3 | Lehramt with FSU Jena and the Landesprüfungsamt | day one | partial | M | HISinOne: state exams in its statistics |
| 4 | Gasthörer:innen and Zweithörer:innen | day one | built | – | HISinOne: yes |
| 5 | Public Vorlesungsverzeichnis | day one | built | – | every system |
| 6 | Modulhandbuch | soon | partial | M | HISinOne, CAMPUSonline: generated |
| 7 | Lehrevaluation | soon | none | S (export) | separate tool, fed by the CMS |
| 8 | Frühstudierende | soon | none | M | Musikhochschulen: common |
| 9 | Wählerverzeichnis | soon | none | S–M | separate tool, list from the CMS |
| 10 | QM figures and surveys | soon | partial | M | HISinOne: BI |
| 11 | Promovierende not enrolled | soon | partial | M | HISinOne: yes |
| 12 | Widerspruch as a case | soon | none | M | rare in a CMS |
| 13 | Korrepetition for lessons and exams | ask | partial | M–L | none: Musikhochschule only |
| 14 | Orchestra and opera projects | ask | partial | L | none: separate tools |
| 15 | Deutschlandstipendium | nice | none | S | separate portals |
| 16 | Meisterkurse and Weiterbildung for outsiders | nice | none | M–L | HISinOne: fees for Weiterbildung |
| 17 | Mutterschutz for students | nice | partial | S | rare |
| 18 | Kapazitätsberechnung | outside | partial | S (export) | separate calculation |
| 19 | Smaller items | – | – | S each | – |

Not needed: hochschulstart.de's DoSV, which serves programmes with local admission limits; HfM selects by
Eignungsprüfung.

## Day one

### 1. Fees beyond the Semesterbeitrag

- **Law**: ThürHGEG § 4: 500 € a semester once the Regelstudienzeit is exceeded by more than four semesters. It
  counts every semester at a German Hochschule except one early change of programme and leave semesters, and
  part-time semesters pro rata. There are allowances for raising children, caring for relatives and committee work.
  § 6: Weiterbildung at cost. § 11: Seniorenstudium. Unpaid fees lead to Exmatrikulation (§ 75 Abs. 2 Nr. 5 ThürHG).
- **HfM**: its Gebührenordnung (2007) charges for exceeding the Regelstudienzeit (§ 3), postgraduate programmes
  500 € a semester (§ 4), academic Weiterbildung 120 € (§ 5), artistic Weiterbildung at cost under private law (§ 6),
  Seniorenstudium 500 € (§ 7) and Gasthörer:innen 50 €.
- **Others**: HISinOne STU assesses fees per person and semester (inferred from its fee module; not checked in detail).
- **In ConCampus**: built (`students/fees.py`). Fees per status (Studierende, Gasthörende, Zweithörende) and
  Studiengang under Einstellungen › Gebühren; the Langzeitstudiengebühr as an amount and the semesters allowed beyond
  the Regelstudienzeit under Einstellungen › Hochschule, 0 where the Land charges none. It counts the semesters here and
  elsewhere without leave, a part-time one as half. Both are added to the Semesterbeitrag at the Rückmeldung, on the
  study page and in the bookings; the office waives them for a semester on application, with a Bescheid
  (`StudyTerm.waived`); the Rückmeldung sends the Bescheide on the Langzeitstudiengebühr, with Rechtsbehelfsbelehrung.
- **Still open**: the count measures all semesters against the current programme's Regelstudienzeit, so a Master's
  student's Bachelor semesters count too; whether consecutive programmes add up their Regelstudienzeiten, and the one
  early change of programme the law leaves out, are not yet in it.
- **Ask HfM**: whether the Gebührenordnung of 2007 is still in force, and which of its fees it levies today; how many
  students pay the Langzeitgebühr (`docs/legal.md` › To check, 4); how a Master's semesters are counted.

### 2. Teilzeitstudium

- **Law**: § 52 Abs. 1 ThürHG: the Regelstudienzeit applies to part-time study too. § 48 Abs. 3: suitable programmes
  must be open to people in work, with disabilities or with family duties. ThürHGEG § 4 Abs. 3 counts part-time
  semesters pro rata.
- **HfM**: has a form „Antrag Teilzeitstudium“ under § 52: the whole study or phases of it, half the planned credits a
  semester (seen in a search result; the link has expired).
- **Others**: common.
- **In ConCampus**: built. The office enters a coming semester as Teilzeit on application, with a Bescheid
  (`StudyTerm.part_time`); a Hochschule without part-time study switches it off (`Institution.part_time`). It shows in
  the Immatrikulationsbescheinigung, printed and XHochschule (`lernzeitmodell/teilzeit`, the code to check against
  XBildung's list), in the statistics (EF31/47: 2) and counts half towards the Langzeitstudiengebühr (1).
- **Still open**: Fachsemester count whole; an online application.
- **Ask HfM**: how many study part-time now (they come over in the migration); whether Fachsemester count in halves.

### 3. Lehramt with FSU Jena and the Landesprüfungsamt

- **Law**: ThürLbG and ThürEStPLGymVO. The Landesprüfungsamt für Lehrämter runs the Erste Staatsprüfung, appoints its
  examiners and signs the certificate; the final grade is made from HfM's module grades (60 %), the thesis and Jena's
  grades. HfM adds the Diploma Supplement (§ 51 Abs. 3 ThürHG). The cooperation rests on § 5 Abs. 10 and § 43 ThürHG.
- **HfM**: Staatsexamen Gymnasium (10 semesters, the „Jenaer Modell“, the second subject, educational science and the
  practical semester at Jena) and B.Ed./M.Ed. Doppelfach Musik. Students are Haupthörer at HfM and Nebenhörer at Jena
  (FPSO StEx Lehramt Musik 2025). Each side keeps its own records; the Landesprüfungsamt keeps the state exam's.
- **Others**: HISinOne's statistics cover state exams; links between universities are by file, where they exist.
- **In ConCampus**: partial. Both kinds of degree and their Studienpläne are in the seed. Jena's modules are taken in as
  Anerkennung, by hand (`docs/roadmap.md`). `Program.exam_office` names the Landesprüfungsamt for the statistics; there
  is a Prüfungsausschuss Lehramt.
- **To build**: HfM's share of the final grade, handed to the Landesprüfungsamt; Jena's results loaded by file rather
  than typed in; the state exam's result taken back for the record and the Diploma Supplement.
- **Ask HfM**: what goes to the Landesprüfungsamt today and in which form; how Jena's results reach HfM; how many
  Lehramt students there are.

### 4. Gasthörer:innen and Zweithörer:innen

- **Law**: § 77 ThürHG: Gasthörer:innen. § 72 Abs. 5: the Immatrikulationsordnung rules on Zweithörer:innen.
- **HfM**: Immatrikulationsordnung § 16: Zweithörer:innen, students of another Hochschule, attend and take exams in
  named courses or a whole programme. Gasthörer:innen pay 50 €.
- **Others**: HISinOne: yes.
- **In ConCampus**: built. Studierende › „Gast- oder Zweithörende aufnehmen“ takes either on from the paper
  application: account, a number from the Matrikelnummern, the study, a Bescheid and the invitation to sign in;
  Zweithörer:innen (`Study.Status.SECOND`) with their home Hochschule and its Matrikelnummer. Both are entered in
  courses and exams by name, as students are; their fees come from Einstellungen › Gebühren (1). Neither is reported to
  the insurers, who hear only of the enrolled (`docs/smv.md`).
- **Still open**: an online application; Zweithörer:innen are not in the student statistics, whose Hörerstatus (EF14)
  stays Haupthörer: whether and how the second Hochschule reports them is to check.
- **Ask HfM**: how many of each a semester, chiefly from Jena and the Bauhaus-Universität.

### 5. Public Vorlesungsverzeichnis

- **Law**: none directly; applicants and the public expect it, and an accreditation looks at it.
- **Others**: every system; HISinOne shows its own without a sign-in (to check for HfM).
- **In ConCampus**: built. `/vorlesungsverzeichnis/` and the Studienpläne without a sign-in
  (`courses/views/public.py`): each published semester's courses with teachers, Lehrform, time, room, registration and
  the modules they count for; never who takes part. The login page links to both; a Hochschule that keeps them to its
  members switches them off (`Institution.public`). The demo keeps them behind its access code.
- **Ask HfM**: whether its website links to HISinOne's public catalogue now.

## Soon

### 6. Modulhandbuch

- **Law**: § 7 Abs. 2 ThürStAkkrVO: each module described with content and aims, teaching forms, entry requirements,
  where else it can be used, what earns the credits, ECTS and grading, how often it is offered, workload and duration.
  § 47 Abs. 5 ThürHG: a Studienplan per programme.
- **HfM**: each FPSO refers to its Modulhandbuch; HfM is system-accredited, so it reviews its programmes itself.
- **Others**: HISinOne makes the Modulhandbuch from the Prüfungsordnung (FAU's campo); CAMPUSonline a PDF per
  Studienplan (TUMonline).
- **In ConCampus**: partial. Code, title, ECTS, semester, exam, weight, description and prerequisites
  (`curriculum/models.py`); the Studienplan page, behind a sign-in.
- **To build**: the missing fields (aims, teaching forms, workload, frequency, duration, exam form, module lead, other
  uses); the handbook per Studiengang and PO-Version as a page to print, public with 5.
- **Ask HfM**: where its Modulhandbücher are kept now (Word, HISinOne), so they can be loaded rather than retyped.

### 7. Lehrevaluation

- **Law**: § 9 ThürHG: each Hochschule keeps a quality system (Abs. 1); students evaluate courses (Abs. 3); answers
  must not show who gave them (Abs. 2); details in a Satzung (Abs. 4). Results go into the annual report (§ 10). The
  duty is the Hochschule's, not its campus system's.
- **HfM**: Qualitätssatzung 2020: course feedback in the last third of the semester, online or on paper, coordinated
  by the LQE office; results go to the teacher alone. Where too few answer for anonymity, as in Einzelunterricht,
  several courses are pooled into one report. The tool is not known.
- **Others**: HISinOne keeps an evaluation status per course and, since 2025.06, hands courses to an external tool
  (TeachingEvaluationService). Graz runs EvaSys behind CAMPUSonline; Stud.IP has its own questionnaires or feeds
  EvaSys.
- **In ConCampus**: none.
- **To build**: courses with their teachers, Lehrform and participants' addresses, exported for the tool each semester;
  an evaluation status per course. The questionnaire and its pooling stay in the tool.
- **Ask HfM**: which tool, and whether HISinOne feeds it now. If it does, this is day one: the first semester after
  cut-over must still be evaluated.

### 8. Frühstudierende

- **Law**: § 78 ThürHG: pupils found gifted by school and Hochschule may be enrolled outside the
  Immatrikulationsordnung. They attend, earn credits, and have them recognised on application in a later study.
- **HfM**: Immatrikulationsordnung § 18 follows § 78. The Musikgymnasium Schloss Belvedere is HfM's Hochbegabtenzentrum:
  120 pupils in grades 5 to 12 and 10 in a Vorklasse, taught their instrument by HfM teachers. Whether any are enrolled
  at HfM is not known. Elsewhere: HMTM's Young Academy, HfK Bremen's Jungstudierende.
- **Others**: no module found in HISinOne or CAMPUSonline; common at Musikhochschulen as Pre-College.
- **In ConCampus**: none. Applicants under 18 hand in their parents' consent (`Condition.MINOR`), but there is no status
  (`docs/legal.md`); Bescheide go to the minor (`docs/legal.md` › To check, 2).
- **To build**: a status without Hochschulzugangsberechtigung, with the courses allowed; the parents as addressees;
  credits carried into a later study; no insurers' report (family insurance, `docs/smv.md`).
- **Ask HfM**: whether it enrols Frühstudierende, and whether Belvedere's pupils are among them.

### 9. Wählerverzeichnis

- **Law**: § 23 ThürHG: free, equal and secret elections. The Kanzler:in keeps the Wählerverzeichnisse of the
  Hochschule and of the Studierendenschaft, open to every member (Abs. 4). A member votes in one group and one unit
  only (Abs. 5).
- **HfM**: Wahlordnung, amended 2024. Elections at the ballot box, by post or online (§ 3a, § 13a), online to the BSI's
  requirements, with the voters' list on HfM's own server. The administration draws the list electronically by group
  and Wahlbereich on a cut-off day and lays it out for at least seven working days for objections (§ 8). Elections
  every year through a Wahlamt (Senat, Fakultätsräte, Gleichstellungsbeirat, Assistentenrat); the Studierendenrat has
  its own Wahlordnung.
- **Others**: no elections module found in HISinOne or CAMPUSonline. The election runs in POLYAS (HfM Nürnberg 2026) or
  uniWAHL (Oldenburg), with the list uploaded from the campus system.
- **In ConCampus**: none. It holds students and teachers, not the other staff, who come from MACH.
- **To build**: the list on a cut-off day by group (Studierende, Hochschullehrer:innen, akademische Mitarbeitende) and
  Fakultät, one group per person, as a page to lay out and a file for the election tool. Other staff from MACH, or
  added by the Wahlamt.
- **Ask HfM**: how the list is drawn now, and with which tool the election runs.

### 10. QM figures and surveys

- **Law**: § 14 ThürStAkkrVO: each programme monitored continuously, with its students and graduates; no fixed
  figures. § 18: regular reviews. § 11 Abs. 4 ThürHG: former members' data may be used for QM surveys unless they
  object, and they must be told so.
- **HfM**: Qualitätssatzung: a yearly survey of all students on their programme; a survey of all who leave without a
  degree; graduates within three months; alumni after about one and a half and four years.
- **Others**: HISinOne's BI and management cockpit.
- **In ConCampus**: partial. Auswertungen show the share within the Regelstudienzeit, pass rates, admission and
  acceptance, cohorts by Studienbeginn (`analytics/overview.py`). The `alumni` role keeps graduates' portal; the
  private address is kept on leaving.
- **To build**: success and drop-out rates per cohort; address lists for each survey; the objection under § 11 Abs. 4,
  noted on leaving and honoured by the lists.
- **Ask HfM**: which figures its programme reviews use; who runs the surveys, with which tool.

### 11. Promovierende not enrolled

- **Law**: the Promovierendenstatistik (HStatG, since 2017) counts every doctoral candidate the Hochschule has
  accepted, enrolled or not (to check against the HStatG).
- **Others**: HISinOne: yes.
- **In ConCampus**: partial. Promotion is a Studiengang with its Promotionsordnung and the three statistics fields. The
  dissertation runs as a thesis; the Promovierende file goes out with a cut-off of 1 December. But it counts only those
  with a registered semester.
- **To build**: acceptance as Doktorand:in by the committee, the supervision agreement, Promovierende without
  enrolment, and the certificate.
- **Ask HfM**: how many Promovierende, and whether all are enrolled.

### 12. Widerspruch as a case

- **Law**: § 68 ff. VwGO: a Widerspruch against a Bescheid is decided by Widerspruchsbescheid. The hearing before a
  burdensome Bescheid (§ 28 VwVfG) is in `docs/waiting.md`, 12.
- **Others**: rare in a CMS; usually a file at the Justiziariat.
- **In ConCampus**: none as a case. Bescheide carry the Rechtsbehelfsbelehrung and their deadline. A Widerspruch is a
  ground to restrict a record; a decision by Bescheid is undone only by another.
- **To build**: a Widerspruch recorded against its Bescheid, with deadline and decision; the Widerspruchsbescheid
  from the texts; the record held meanwhile. Most come from exams and admissions.
- **Ask HfM**: how many a year, and who decides them.

## Musikhochschule only

These are what make ConCampus a Musikhochschule's system (`docs/roadmap.md`, For other Hochschulen, 6); generic systems
lack them, so HfM does them some other way today.

### 13. Korrepetition for lessons and exams

- **In ConCampus**: partial. Built for the Eignungsprüfung: applicants ask for an accompanist, who is assigned per
  audition, never in two places at once (`courses/views/catalogue.py`). Korrepetitor:innen hold Lehraufträge with
  their hours.
- **Missing**: accompanists for enrolled students: in lessons, Vorspiele and exams, a budget of hours per student or
  class, and the hours given against it.
- **Ask HfM**: how Korrepetition is planned and counted today, and by whom.

### 14. Orchestra and opera projects

- **In ConCampus**: partial. Ensembles with their line-up, members, repertoire and coach; rehearsals booked with all
  members (`Booking.ensemble`).
- **Missing**: projects with rehearsal phases and concerts, a Dienstplan, seating by section.
- **Others**: orchestras use their own tools (such as OPAS).
- **Ask HfM**: how its orchestra and opera school plan projects now.

## Nice to have and outside

### 15. Deutschlandstipendium

- **Law**: § 13 StipG: a yearly federal statistic, which the Hochschulen report. Per holder: sex, nationality, degree,
  field, semesters, months funded, BAföG; per sponsor: legal form, amount, ties to programmes.
- **HfM**: takes part through its fundraising office; 42 holders in 2025/26.
- **Others**: separate portals.
- **In ConCampus**: none. **To build**: a holder per semester with sponsor and months, and the § 13 file. Selection and
  sponsors stay with fundraising.

### 16. Meisterkurse and Weiterbildung for outsiders

- **Law**: § 57 ThürHG: Weiterbildung, also under private law; ThürHGEG § 6: at cost.
- **HfM**: the Weimarer Meisterkurse: application with recordings, 80 € registration, PayPal or bank, invoice, waiting
  list; HfM's and Belvedere's students pay half. The Thüringer Opernstudio is a Zertifikat programme, already in
  ConCampus as a Studiengang.
- **Others**: HISinOne prices Weiterbildung at cost (COA, 2025.06); master classes mostly in event software.
- **In ConCampus**: none for outsiders; Meisterkurse are a room booking category.
- **To build, if wanted**: application, fee and certificate for outside participants, from the Eignungsprüfung's
  application and the payments. Otherwise outside.

### 17. Mutterschutz for students

- **Law**: § 1 Abs. 2 Nr. 8 MuSchG applies where the Hochschule sets place and time of compulsory courses: a reported
  pregnancy leads to a risk assessment and adjusted exams. ThürHG § 52 Abs. 5 and § 74 Abs. 4 count it towards the
  Regelstudienzeit and leave.
- **In ConCampus**: partial. Leave for Mutterschutz, in the first semester too; Nachteilsausgleich for exams.
- **Missing**: a pregnancy reported, and the assessment's outcome passed to teachers and examiners. Usually kept
  outside a CMS by whoever handles Mutterschutz.

### 18. Kapazitätsberechnung

- **Law**: § 52 Abs. 1 ThürHG: the Regelstudienzeit is the basis of capacity; § 11 Abs. 1 Nr. 6 allows the data for
  it. A Thuringian KapVO for artistic programmes was not found.
- **Others**: a separate calculation, from Lehrangebot (Deputate, Lehraufträge), Lehrnachfrage and Schwund.
- **In ConCampus**: the supply side: Lehrverpflichtungen, reductions and Lehraufträge (`courses/views/load.py`).
- **To build, if asked**: an export of that supply per Lehreinheit.

### 19. Smaller items

| Item | In ConCampus | Need |
|---|---|---|
| Leistungsübersicht on leaving without a degree (§ 51 Abs. 4 ThürHG) | Available for every study | Check that it is issued with the Exmatrikulation |
| Exmatrikulation for misconduct, reported to other Hochschulen (§ 76 Abs. 5 ThürHG) | Ground „von Amts wegen: anderer Grund“; no report | nice |
| Attendance in courses | Only absences announced for bookings | nice; ask whether HfM records attendance |
| Sprechstunden and Studienberatung | None | nice |
| Plagiarism check of theses | Upload with Selbstständigkeitserklärung | outside |
| Settings for notifications | Postfach and e-mail for every message | nice |
| Berufungsverfahren | None | outside |

## Already built

To keep the list honest, these were checked and are there: Übezeiten with quotas and lead times; Nachteilsausgleich
with consent and Bescheid; Art. 15 export (`core/dossier.py`); concerts and Vorspiele as bookings with programme,
admission and a public calendar feed; Hochschulsemester; messages for about 50 events.

## To check

1. The current ThürHG and ThürHGEG, after the 2026 bills.
2. Whether HfM's Gebührenordnung of 2007 still applies, and which fees it levies.
3. Whether the Promovierendenstatistik counts doctoral candidates who are not enrolled.
4. Which evaluation tool and which election tool HfM uses, and what HISinOne feeds them.
5. Whether pupils of Schloss Belvedere are enrolled as Frühstudierende.
6. How many Zweithörer:innen, Gasthörer:innen, part-time students and Lehramt students HfM has.
7. Who receives the § 13 StipG statistics.
8. Whether part-time semesters count as half Fachsemester at HfM.
9. For the Langzeitstudiengebühr: whether a consecutive Master adds its Regelstudienzeit to the Bachelor's, and how the
   one early change of programme is left out.
10. Whether and with which Hörerstatus Zweithörer:innen go into the student statistics.
11. XBildung's code for part-time study in the Bescheinigung (`lernzeitmodell/teilzeit` assumed).

## Sources

- [ThürHG 2018, GVBl. 5/2018](https://parldok.thueringer-landtag.de/ParlDok/dokument/67011/gesetz_und_verordnungsblatt_nr_5_2018.pdf)
- [ThürHGEG](https://umwelt-online.de/recht/allgemei/laender/th/hschgeg.htm)
- [ThürStAkkrVO, 2022](https://media.frag-den-staat.de/files/foi/718800/hschulqsakkrrglv-th.pdf)
- [§ 13 StipG](https://www.gesetze-im-internet.de/stipg/__13.html)
- [HfM Weimar, Qualitätssatzung 2020](https://www.hfm-weimar.de/fileadmin/Ordnungen_Richtlinien_Regelungen/02_Satzungen/Qualitaetssatzung_2020.pdf)
- [HfM Weimar, Wahlordnung with the 2024 amendment](https://www.hfm-weimar.de/fileadmin/Ordnungen_Richtlinien_Regelungen/01_Ordnungen/Wahlordnung_Lesefassung_mit_1._Aenderung_2024.pdf), [committee elections](https://www.hfm-weimar.de/en/university/committees-and-elections/committee-elections)
- [HfM Weimar, Gebührenordnung 2007](https://www.hfm-weimar.de/fileadmin/Ordnungen_Richtlinien_Regelungen/01_Ordnungen/Gebuehrenordnung_2007.pdf)
- [HfM Weimar, FPSO StEx Lehramt Musik 2025](https://www.hfm-weimar.de/fileadmin/user_upload/Studienplaene_und_Ordnungen/Studien-_und_Pruefungsordnungen/FPSO_StEx_Lehramt_Musik_2025.pdf)
- [HfM Weimar, Musikgymnasium Schloss Belvedere](https://www.hfm-weimar.de/en/university/music-high-school-schloss-belvedere)
- [HfM Weimar, Deutschlandstipendium](https://www.hfm-weimar.de/en/giving/germany-scholarship)
- [HfM Weimar, Weimarer Meisterkurse 2026](https://www.hfm-weimar.de/en/weimar-master-classes/66th-weimar-master-classes-2026/information-conditions)
- [HfM Weimar, applying](https://www.hfm-weimar.de/en/applying/how-to-apply)
- [HIS, Release 2024.12](https://his.de/fileadmin/his/news/Release_2024.12/Release_Special_2024_12.pdf), [Release 2025.06](https://www.his.de/fileadmin/his/news/Release_2025.06/Release_Special_2025.06.pdf)
- [Uni Graz, Lehrveranstaltungsevaluierung](https://lehre.uni-graz.at/de/themenladen-lehre/rueckmeldung-zur-lehre-einholen/lehrveranstaltungsevaluierung)
- [FAU, Modulhandbuch erzeugen (HISinOne)](https://www.doc.zuv.fau.de//L2/Anleitungen/Modulverwaltung/Deutsch/Modulhandbuch_erzeugen.pdf)
