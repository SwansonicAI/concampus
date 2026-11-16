# To-dos, 16 November 2026

Every to-do of the start page followed to its end, as the person it is for would: does the page it opens show what it
is about, can it be done there with that person's rights, and does doing it make it go away. The to-dos of 187 people,
a few of every set of roles, opened as them (all answered without an error), then each kind followed in the code and on
the seeded data, its action done in a transaction rolled back after. One line each: role and to-do, pattern of
`docs/friction.md`, what happens, the fix. Grouped by how badly it fails: what cannot be done at all first.

Status: fixed on 16 November 2026, except where an item says „Open“.

## A. Cannot be done

1. [student] „N Module noch ohne Lehrveranstaltung“ – #7 – a course counting for two modules, taken for one, is
   offered again for the other, where „Anmelden“ answers „Sie haben schon einen Platz“; three students stuck for good
   – a course attended or asked for is offered for no other module.
2. [Konten] „Änderungen der Berechtigungen bestätigen“ – #7 – a right given to an office one belongs to is counted and
   shows „Bestätigen“, which answers 404; where everyone who gives rights is in it, nobody can – count and offer only
   what one may confirm. Open: say so where nobody is left to confirm.
3. [teacher] „Termine planen“ – #7 – a course taught one to one (Lehrform Einzelunterricht) can only get one time for
   all 25, every room „zu klein“ – leave such courses out. Open: a timetable per student, as lesson classes have.
4. [Dekanatsreferat] „Semesterwechsel: N übernehmen“ – #7 – the courses of a teacher whose contract ends are left
   unticked and cannot be dropped; the count never reaches 0 – count only courses someone still teaches.
5. [examiner] „Ihre Wertung fehlt“ – #7 – counts who withdrew with good reason or has a result; cleared only by a mark
   that means nothing – count as the sheet does (`panel.due`).
6. [Veranstaltungsbüro] „Zurückfordern: Instrument“ – #7 – no way to ask it back: „Erinnern“ only once overdue, no
   address – „Zurückfordern“: due in 14 days, the borrower told; then theirs to return.
7. [student] „Abschlussarbeit abgeben“ – #7 – after the deadline the upload is gone, the to-do stays – only while the
   upload is open; after it the Prüfungsamt's „nicht abgegeben“.
8. [ASA, student] Rückmeldung – #7 – a student with a Zeugnis is asked to pay 341 €; the office's count holds those
   who leave on other ground and never reaches 0; after the Nachfrist it still says „Zahlungen ausstehend“ – leave
   them out, open `?stand=offen`, after the Nachfrist „ohne Rückmeldung exmatrikulieren“.
9. [Prüfungsausschuss] „Anerkennen“ – #7 – once the student has left, the page answers 404 – only for who still
   studies.

## B. Leads to the wrong decision

1. [ASA] „Bewerbungen entscheiden“ – #8 – comes once the Hauptfach stage is closed, though Klavier and Musiktheorie
   follow; the page advises „Studienplatz anbieten“ and „Wie empfohlen“ sends the Bescheide – decided only once every
   stage a passed one leads to is passed.
2. [ASA] „Bewerbungen entscheiden“ – #11 – counts who the page says wait for the others in their intake – count as
   the page does (`recommendations()`).
3. [Institut] „Eignungsprüfungen fürs nächste WiSe einrichten“ – #8 – opens the nearest semester, SoSe; one click
   copies the WiSe stages into it – open the copy from the semester the to-do is built from, into the one a year on.

## C. Opens the wrong page

1. [Institut] „Kommission zu klein“ – #1 – opens the Bewerbungen, which say nothing of commissions – the Eignungsprüfung
   itself, or the semester's list of what is to do; „Kommission ergänzen“.
2. [Konten] „Amt ohne Mitglieder“ – #1 – opens all 146 accounts; a member entered waits for a second person unsaid –
   the office's form, naming who waits; no to-do while someone does.
3. [teacher] „Klasse: N ohne Einzelunterricht“ – #1 – no verb; the red row does not lead to the class – „Einzelunterricht
   eintragen“; each row a link into the class, the Matrikelnummer filled in.
4. [Prüfungsamt] „Kommission prüfen“ – #4 – counts every semester, the list shows the current one – link the semester.
5. [Personalverwaltung] „ohne Lehrverpflichtung im SoSe“ – #4 – the list shows the current semester – the list follows
   `?semester=`.

## D. Nothing to do

1. [Prüfungsausschuss] „Zeugnis seit über 90 Tagen überfällig“ – #13 – only the Prüfungsamt issues it; the page says
   neither that nor whom – the page names who issues it, „Erinnern“ once a week, the to-do resting meanwhile.
2. [Prüfungsamt] „N Anmeldungen im letzten Versuch“ – #9 – no action; stays until release – dropped: the exam shows
   it, „Kommission prüfen“ asks for the stricter panel.
3. [Dekanatsreferat] „über der Lehrverpflichtung“ – #9 – the semester under way, too late to change – the one being
   planned.
4. [teacher] „empfohlen X, wartet auf …“ – #9 – the choice is the student's, who has the to-do – dropped.
5. [Institut] „Schwerpunkt nicht gewählt“ – #9 – the office cannot record it – only for who records it.
6. [ASA] „Immatrikulation: 1 wartet auf …“ – #9/#11 – counts reasons, not people; two are the office's own step –
   „Hochschulzugänge aus dem Ausland prüfen“; „erinnern“ sends an applicant only their own steps.
7. [Personalverwaltung] „befristet bis …“ – #9 – a fact, no verb – „verlängern oder ausscheiden lassen“.
8. [applicant] „Eignungsprüfung am …“ – #11 – on the list for two months – in its week.

## E. Smaller

1. [applicant] „nachreichen“ – #6 – asks for what the page says is due only „bis zur Immatrikulation“ – leave it out.
2. [Prüfungsamt] thesis sheet – #11 – a Vermerk saved reads „Gutachten abgegeben.“ – „Ergebnisse gespeichert.“
3. [teacher] „Anfragen entscheiden“, ensembles' wishes – #11 – only the semester under way, while students register for
   the next – both semesters.

## F. Open

1. Demo data – 28 applications (Konzertexamen, Promotion) without an Eignungsprüfung never reach „entscheiden“; 23
   practical exams not marked oral fall due after six weeks, not seven days – `seed.py`, at the next seed.
2. [Ersatz] „Ihre Wertung als Ersatz fehlt“ – no due date, and before the exam is held.
3. [third Gutachter:in] the due date counts from the hand-in, not from the appointment.
4. [Prüfungsamt] „Kommission prüfen“ – the list does not say why a panel falls short.
5. [Institut] „Ensembles ohne Betreuung zuteilen“ – only the semester under way.
6. [Veranstaltungsbüro] „Programm fehlt“ – the organiser is not reminded.
