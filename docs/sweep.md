# Sweep, 30 October 2026

The site checked role by role against the patterns of `docs/friction.md`: applicants, students, teachers and
examiners, the student and exam offices, the other offices and Einstellungen. Each finding was verified against the
code and, where it matters, on pages rendered from the seeded data or by a request rolled back afterwards; the most
serious were checked again. One line each: role and page, pattern, what happens, the fix. Grouped by what to fix first:
what costs most when it goes wrong, then what many people meet often.

Status: every group fixed on 30 October 2026, except where an item says „Open“: F 5, F 7 and F 13 among them.

## A. Grades, Bescheide and money

1. [student] Prüfungen – #7 – an exam already sat, not yet marked, can still be withdrawn until the withdrawal date,
   which deletes the attempt (a third and last attempt among them) – withdrawal only before the exam's own date.
2. [Prüfungsausschuss] exam page – #8 – „Wieder öffnen“ then „Freigeben“, even unchanged, sends every candidate the
   result again and a second Bescheid über endgültiges Nichtbestehen, and moves the release date the Widerspruchsfrist
   runs from – on release again, tell only those whose result changed, keep the first release date otherwise; confirm
   on reopening, saying so.
3. [Prüfungsausschuss as Gutachterin] thesis and audition sheets – #7 – blind marking is skipped for `correct_scores`:
   the chair sees the other Gutachten and grade before writing her own (37 theses) – blind everyone on the panel who
   has not marked, whatever office they hold.
4. [Gutachter:in] thesis page – #8 – no draft: text without a grade is refused, and the second Gutachten saved locks
   both without warning; the lock is only in the page, a request with the same grade still rewrites the locked text –
   „Entwurf speichern“, „Gutachten abgeben“ with a confirm naming the lock, the lock enforced on the server.
5. [examiner] marking sheet – #8 – any examiner releases; the confirm says neither how many are told, nor that a
   Bescheid über endgültiges Nichtbestehen goes out, nor who can reopen; release does not wait for the Protokoll –
   name all three; release after the Protokoll is in. Done: the confirm names all three and a Protokoll not yet
   booked; release does not wait for it while who sends the Protokoll is open (`docs/rules.md`, to confirm).
6. [student] Prüfungen – #1/#7 – exams of a Schwerpunkt not chosen are offered and accepted (71 students) – leave
   them out and refuse them.
7. [ASA] Rückmeldung – #8/#9 – „N ohne Rückmeldung exmatrikulieren“ acts on all at once without a preview, with reason
   7 also for those whose Zeugnis is issued (reason 1) or whose Prüfungsanspruch is lost (reason 8) – a preview grouped
   by reason, each with its right reason, then confirm.
8. [ASA] Rückmeldung, Kontoauszug einlesen – #8 – books and confirms straight away; any number in the Verwendungszweck
   equal to an unpaid Matrikelnummer counts; nothing booked can be taken back; unmatched bookings are only counted – a
   preview per booking, unmatched ones listed for booking by hand, „Zahlung zurücknehmen“.
9. [ASA, Prüfungsamt] Zahlungseingang, Semesterbeitrag, Protokoll, Rücktritt – #5 – an unreadable date becomes today,
   a future one is accepted; the Säumnisgebühr is reckoned from it; a mistyped Rücktritt date is reported as „Bitte das
   Attest hochladen …“ – keep these fields to past dates and refuse with the reason.
10. [admitted applicant] Meine Bewerbung, „Studienplatz angenommen“ – #7 – who moves up after the Rückmeldefrist is
    told to pay by a day already passed – the later of the Rückmeldefrist and acceptance plus some days, the same in
    the page and the letter.
11. [ASA] Studienakte, Fachwechsel – #8/#5 – one click ends the study and starts the new one, without a preview, a
    Bescheid or a way back; an unchanged Studiengang and Hauptfach is accepted – a preview, refuse the unchanged, send
    the Bescheid, a to-do for the recognition. Done but the to-do: recognition is on application (RPSO), as the
    Bescheid says.
12. [ASA] Studienakte, Urlaubssemester eintragen – #6/#8 – the Bescheid goes out at once, and only then is a broken rule
    named („Als Ausnahme eingetragen“); taking the leave back is one click and tells the student nothing – the rule note
    before, confirm, notify.
13. [dekanatsreferat] Module and Studiengang – #6 – changing credits, weights, rounding or attempts changes the grades of
    116 enrolled students and the Zeugnisse of 272 graduates without a word – name who is affected; „als neue
    PO-Version kopieren“. Done: who is affected. Open: copying needs a „gilt ab“ semester on the Studiengang first,
    since the newest version is what applicants start in at once.
14. [Prüfungsausschuss] Anerkennung – #8 – a recognised module leaves the choices and its correction page is linked from
    nowhere; the message prints „1.7“ – link each entry to its correction; the grade as „1,7“.
15. [Prüfungsamt] Noten aus Datei – #8 – „Wertungen von“ has no empty choice, so a file goes into the first examiner's
    column unless changed; offered before the Protokoll and refused only after the upload – „Bitte wählen“, required;
    only once the Protokoll is in.

## B. Applicants

1. [admitted applicant] Meine Bewerbung – #7 – once the place is offered nothing can be handed in, though the page says
   the Sprachnachweis or the HZB is missing; the Sprachnachweis shows as „freiwillig“ and vanishes at the deadline –
   keep what the Immatrikulation needs open until then, labelled „bis zur Immatrikulation“, with the HZB fields.
2. [admitted applicant] Annahme form – #5 – a refusal throws away what was typed and reports „Dieses Feld ist zwingend
   erforderlich.“ three times without saying where – `again()`/`sent()`, each error at its field.
3. [accepted applicant] start page – #6 – „Nichts offen“ while fee and insurer's report are outstanding – a to-do
   „Semesterbeitrag … überweisen“ with its day until the Immatrikulation.
4. [accepted applicant] „Für die Immatrikulation fehlt noch“ – #6 – the person's own steps and what others must do in
   one list; the letter names only fee and insurer – „Ihre Schritte“ apart from „wartet auf …“, all of it in the letter.
5. [applicant] Neue Bewerbung, Meine Bewerbung – #7/#8 – „Bis zur Frist können Sie alles ändern“, but programme,
   Wunschlehrer:in and Korrepetition cannot be changed and the programme is not even shown – show it with „ändern“ until
   the deadline; ask for the video's start times next to the video.
6. [applicant] Neue Bewerbung – #7/#6 – with no Eignungsprüfung open the form shows empty choices and no reason, while
   the login page invites to apply; Hauptfächer without an intake this term are missing without a word – „derzeit nicht
   möglich, nächste Frist …“, list the others with their term and deadline.
7. [applicant] video upload – #11 – a dropped connection leaves the button at „57 % hochgeladen“ for good; a server error
   reloads without a message – on failure, the button back and „Hochladen fehlgeschlagen“.
8. [applicant] Postfach → „Meine Bewerbung öffnen“ – #11 – it opens in the side panel, where uploads show no progress –
   open it as a page, or give panel uploads the progress too.
9. [applicant] Meine Bewerbung – #8 – no way to withdraw an application or give up an appointment; not coming ends in a
   rejection – „Bewerbung zurückziehen“, confirming that the appointment is freed.
10. [admitted applicant] Annahme form, Bisheriges Studium – #2/#3/#1 – „Letzter Abschluss“ lists 98 statistical
    categories, „Im Studienfach“ 265 subjects; for an HfM Bachelor going on to the Master all of it is on record – music
    subjects first, 96 set for a degree abroad, filled from one's own finished study.
11. [admitted applicant] Ablehnen – #8 – the confirm does not say it is final and the place goes down the Nachrückliste
    – say both.
12. [applicant] start page tile and badge – #11 – the tile shows any one application: „Abgelehnt“ beside one on the
    Nachrückliste, „Beworben“ for one invited, „Zugelassen“ for a lapsed offer – the furthest live one, in the page's
    words.
13. [applicant] Postfach, a Bescheid – #6 – the invitation says „für ein Visum vorlegen“; the printable letter is linked
    only from Meine Bewerbung – „Als Brief drucken“ on every message filed as a Bescheid.
14. [applicant] invitation – #13 – the place is „Festsaal (Fürstenhaus)“ with no street or map, also in the letter for
    visas – the building's address and a map link in the invitation, the page and the calendar entry. Open: rooms keep
    only a building's name; its address needs a place of its own in the data, filled from the Hochschule's.
15. [applicant] Mein Konto – #13 – the e-mail, which signs in and receives every Bescheid, can be changed by nobody and
    is never confirmed – change it with a confirmation link.
16. [applicant] search – #13 – nothing found for ASA, Studienplatz, annehmen, Semesterbeitrag, Immatrikulation,
    Krankenkasse – offices (name and shared address) findable for applicants; these words on Meine Bewerbung.

## C. Students

1. [student] Kammermusik – #11/#8 – a student in an ensemble sees „Teilnahmen 0“ and the form to register one, so a
   second ensemble can be made and its members told: the list is narrowed for everyone but the office, not only for
   coaches – narrow for coaches only; hide the form once in an ensemble.
2. [student] Ensemble anmelden – #5/#8 – members typed as free text; one unclear name drops everything typed; only the
   office dissolves an ensemble – find by typing, keep what was typed, let the student withdraw while registration runs.
3. [student] Prüfungen, angemeldet – #6 – which attempt it is (the last one!) shows only before registering; the
   withdrawal date only on hover; after it no word of Rücktritt mit Attest – Versuch (red when last) and „Abmelden bis“
   in the table; after that, how to withdraw with an attest.
4. [student] Studienakte, Rückmeldung blocked – #13 – „Versicherungsmeldung der Krankenkasse fehlt“ or „Sperre:
   Bibliotheksgebühren offen“ without what to do or whom to ask, though it ends in Exmatrikulation – per reason the
   remedy and a linked contact.
5. [student] Vorlesungsverzeichnis – #1/#2 – all 122 courses of the Studiengang in the 5th semester, 38 for modules
   already passed; one's own registrations not marked; „Nachmeldung anfragen“ for a passed module – mark „angemeldet“ and
   „bestanden“, open modules by Fachsemester first, „alle zeigen“.
6. [student] Meine Veranstaltungen – #11 – open requests (Platz, Nachmeldung) are missing – list them with „Zurückziehen“.
7. [student] course registration – #6 – a clash with the timetable and „zählt für …“ show only after registering – both
   next to the button.
8. [student] Abschlussarbeit, Urlaubssemester-Nachweis – #6 – no size limit beside the file field, refused after the
   whole upload; the attest field hides types the server takes – the limits and types of the Unterlagenart, as in
   admissions.
9. [student] Immatrikulationsbescheinigung – #7 – only for the current semester: after paying for the next one in
   January, no Bescheinigung or BAföG version for it until April – offer the coming semester once paid.
10. [student] Vertiefung wählen – #6 – the start page says „überfällig seit 1.10.“, the panel where one chooses says no
    date – the date there.
11. [student] Meine Leihinstrumente – #13 – return or extend: where, to whom? The Leihvertrag's conditions show to the
    office only – name and link the lending office, show the conditions to the borrower.

## D. Teachers and examiners

1. [teacher] „Krank? Alle eigenen Termine des Tages absagen“ – #8 – the pre-ticked list holds other coaches' ensemble
   rehearsals of co-taught courses; one click calls them off and tells their members – only her own ensembles' dates.
2. [teacher] Unterrichtszeiten – #8 – changing one row deletes every future date of that student in the course, make-up
   lessons included – replace the weekly series only, or name what goes.
3. [teacher] Unterrichtszeiten – #1 – only the room is checked: a lesson clashing with the teacher's own course in
   another building is accepted; a new row is 90 minutes whatever the plan says – check teacher and student as room
   booking does; the plan's minutes.
4. [coach] Kammermusik, „Wöchentlich planen“ – #8 – no ensemble choice: one coach's rehearsal lands in the timetables
   of all 213 students and 24 teachers – the ensemble field, one's own preselected.
5. [teacher] Teilnehmende eintragen – #1 – the module is the plan's lowest-semester one, so a 5th-semester student gets
   the 3rd semester's; one whose Studiengang the course does not count for is added with none – choose by Fachsemester
   as the class filling does; warn when none fits.
6. [juror] video stage, Meine Kommissionen – #6 – „Sichtung online, bis 2. November“ is the Bewerbungsschluss; the
   jury's own deadline (16.11.) and one's open count show only on the Sichtung page – both on the stage and the list.
7. [jury, examiner] candidates' sheet – #11 – the programme is cut at 42 characters, the rest only on hover, which
   tablets in the sitting cannot show – let it wrap, one work per line.
8. [teacher] course with many participants – #10 – results row by row, two selects each (94 rows) – „alle ohne Ergebnis
   bestanden“ and the file import exams have. Done: „alle ohne Ergebnis bestanden“ where no module is an exam; the
   file import stays with exams.
9. [Hauptfachlehrerin] Meine Klasse – #11 – the Einzelunterricht column takes any lesson class, so one missing from the
   Hauptfach class goes unflagged; for a semester in planning all rows show red – Hauptfach classes only; „nach der
   Rückmeldung“ while in planning.
10. [teacher] Meine Lehre – #11 – the SWS column shows the course's hours (1,5 for the Hauptfach class, which counts 18,0
    in the Deputat) – each course's SWS as the Deputat counts it.
11. [teacher] Anfragen – #8 – „Absagen“ next to „Zulassen“, without a confirm, sends the rejection and deletes the
    request – confirm naming the message.
12. [Korrepetitor] Zu begleiten – #1/#2 – no room, not which accompanist plays, two slots at the same time unflagged; the
    Einzeltermin offers all 244 applicants in one list – room and player columns, overlaps flagged, find by typing.
    Done: rooms, overlaps; a list that long is found by typing already. Open: which accompanist plays, not yet recorded.
13. [teacher] start page, „Termine planen“ – #4 – fires for a Praktikum that never has room dates and cannot be cleared –
    skip Lehrformen without dates, or „ohne feste Termine“.
14. [teacher] Meine Buchungen – #1 – twelve lesson series read „Hauptfach Violine · Klasse Wendt“ with no student –
    the student's name, as on the course page.
15. [Prüfungsausschuss chair as examiner] Prüfungen – #4 – all 216 exams; her own 37 theses found only by her name –
    „Nur meine“.

## E. Offices acting on many at once

1. [institut] Eignungsprüfung, „Termine vergeben und einladen“ – #8 – every applicant gets a time and the invitation
   (used for visas) in one click, without seeing the times or the end of the day; afterwards nobody can be moved – the
   times first, then send; move one with a new invitation.
2. [dekanat] Semesterwechsel – #9/#8 – the to-do „prüfen und veröffentlichen“ and the button come with no course taken
   over; the confirm names neither the called-off courses it deletes nor the teachers who have not answered – only once
   courses are taken over and teachers asked; the counts in the confirm; a link to the planned Vorlesungsverzeichnis.
3. [archiv] Aussonderung – #8 – „Nicht archivwürdig“ on up to 300 ticked files without a confirm, cut down the night
   after – confirm with the count and the night's deletion; taking it back until then.
4. [ASA] Studierende – #7 – „Alle ankreuzen“ ticks the 300 shown of 737; a message to all leaves the rest out without
   a word – „an alle N Treffer“.
5. [dekanatsreferat] Semesterwechsel, step 2 – #5/#8 – „Antwort bis“ not checked against „fertig bis“; the button that
   writes to every teacher gives no count – a proposed date before it; the count on the button.
6. [campus] Unterrichtsfreie Tage – #7 – a day added after the dates are booked leaves them standing – list them on save
   and offer to call them off, one message per person.
7. [veranstaltungsbuero] Tag absagen – #10 – only one's own dates; closing a hall for a day means calling off each
   booking – all bookings of chosen rooms for a day, one message per person.

## F. Offices, other

1. [campus] Semester neu – #8/#10/#5 – „In Planung“ unticked: a new semester's courses are open to students at once;
   all dates and fees blank; no check against neighbouring semesters – ticked; last year's dates a year on and the
   latest fees proposed; checked.
2. [Prüfungsamt] „Prüfungen ohne Termin“ – #7 – the to-do cannot be done: an exam gets a day only once someone is
   registered; it links to all 216 exams – book day and room before registration; „ohne Termin“ as a filter.
3. [Prüfungsausschuss] exam page – #7 – shown „Protokoll“ and „Rücktritt“ forms that refuse it – show them to whoever
   may use them.
4. [Prüfungsamt] Termine vergeben – #5 – any error reads „… alle Teilnehmenden haben schon Termine“, false, and the
   input is lost – the field's own error, input kept.
5. [ASA] newcomers' Semesterbeitrag – #10 – booked by hand one by one; the Verwendungszweck asked of applicants has no
   number the statement import could match – a reference number; the import matches admitted applicants too. Open:
   the reference's form is to agree with the Hochschule's bank account and the Studierendenwerk.
6. [ASA] Studienakte of an admitted applicant – #6 – „Immatrikulieren“ without what is missing, refused after the click;
   the term called „immatrikuliert“ – the missing items with their actions; the button once complete.
7. [ASA] Versandte Bescheide – #10/#9 – „per Post“ only a badge: no filter, no printing them all, no „gedruckt“; each
   audition's invitation a kind of its own – the filter, print all, mark printed; kinds by template. Open.
8. [ASA] Exmatrikulation after the Zeugnis – #1 – the reason left blank, though the to-do says why – „Abschluss des
   Studiums“ preselected.
9. [ASA] Studium bearbeiten – #4 – Hauptfachlehrer:in only among who taught that Hauptfach, so a new teacher cannot be
   chosen – those first, the rest under „Weitere“.
10. [ASA] Studierende filters – #1/#2 – Hauptfach not narrowed by Studiengang; Klasse lists all 69 teachers – narrow
    them.
11. [Prüfungsausschuss] Bestellte Prüfende – #10 – 60 appointments a year entered one by one – „aus dem Vorjahr
    übernehmen“ with a result per person, a to-do before the year.
12. [dekanat] Ermäßigungen – #10 – per semester, re-entered each time; SoSe 2027 has none yet – „bis Semester“, or carried
    over in the Semesterwechsel; the coming semester by default.
13. [institut] Eignungsprüfungen – #10 – 15 stages per intake re-created by hand each year – „wie im Vorjahr anlegen“,
    dates shifted. Open: an intake's stages, juries and links copied together.
14. [veranstaltungsbuero, institut] Leihinstrument – #5/#10 – a past return date is accepted and blocks the Rückmeldung
    at once; no „Verlängern“ – refuse past dates; extend.
15. [campus] Konten – #6 – a teacher made inactive without a word about 13 class students, 23 courses and 230 bookings –
    name what depends on them and where to reassign it.
16. [institut] Instrument, Raum anlegen – #1/#4 – all ten Institute offered, others refused after saving – one's own
    preset and first.
17. [campus] Ämter – #6 – the to-do „Amt ohne Mitglieder“ opens a form that cannot add members – add them there, or link
    Konten filtered for it.
18. [campus] Anmeldungen – #2 – a thousand rows, no filter by person or failures – both.
19. [veranstaltungsbuero] Veranstaltungen – #1 – opens on the calendar month: „Keine Veranstaltungen“ on 30.10. with nine
    in November – the coming weeks.

## G. Named, not reachable (#13, every role)

1. Hauptfachlehrer:in and Betreuung on the Studienakte, Lehrende on a course („Abmelden über die Lehrenden“), „nur über:
   Prüfungsamt“, „Den Nachweis sieht nur ASA … reichen Sie ihn dort ein“, „zuständig: ASA“ in Mein Konto, „Bitte wenden
   Sie sich an die Hochschule“ for a lapsed offer, „trägt Dekanatsreferat ein“ on course editing – `Office.holding()`
   returns bare names though offices have an address: link every person and office named to their e-mail.
2. [all] Passwort – #13/#7 – Mein Konto says „setzen Sie Ihr Passwort neu“, but nothing links to changing it, the search
   finds nothing, and the page is Django's admin page – „Passwort ändern“ in Mein Konto and the menu, in Campus's look.
