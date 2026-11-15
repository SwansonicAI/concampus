# Layout audit, 16 November 2026

Seen in the browser as Prüfungsamt (Thomas Albrecht) and Lehrende (Prof. Wendt), one list and one detail page of each
kind. Not seen: students, applicants, the other offices. Open: everything below; the deeper problem found with it,
that a person two or three pages down loses where they are, is being worked on first.

What the clean pages share: each thing shown once, one line per row. What the sloppy ones share: rows of three to five
lines, the same data in more than one place, rules written out under the form.

## A. Lists that take a screen for six rows

1. [Lehrende] Videostufe Violine (`/aufnahme/pruefung/3144/`) – 42 Bewerbende, about 4 visible: the Repertoire wraps
   to 4–5 lines per row – one line, the rest on demand.
2. [Lehrende] same page – the 26 still to rate sit among 16 already locked, found only by an empty box – open first,
   or a filter.
3. [Lehrende] same page – the bar under the table (Speichern and two lines of rules) covers about 100px – rules into a
   tooltip.
4. [Lehrende] same page – the facts above sit in a narrow column, right half empty; „Sichtung“ wraps to 3 lines and
   the date that matters (Wertungen bis 16.11.) is inside the sentence – the deadline as a mark beside the title.
5. [Lehrende] same page – „·“ for a missing value; names wrap („Baumgartner, / Lukas“); „Violine · B.Mus. ·“ ends
   in a dot before the Video link on the next line.
6. [Prüfungsamt] Prüfungen – 227 exams, about 6 per screen: every title repeats „Künstlerische Präsentation I ·
   Künstlerisch-praktische Prüfung (10 Min.) ·“, the instrument last on line 3 – group by module, the row shows
   instrument and length.
7. [Prüfungsamt] same list – Kommission wraps to 3 lines, Datum to 2; Studiengang smaller than the other columns;
   Datum mostly „–“ (47 without a date).
8. [Prüfungsamt] Studierende – names wrap („Ackermann, / Emilia“) while Matrikelnr. has room to spare.

## B. The same thing shown more than once

1. [Lehrende] Hauptfach Violine · Klasse Wendt – the 11 students three times: Teilnehmende (order of sign-up,
   „Nachname, Vorname“), Nächste Termine (by time, „Vorname Nachname“), Unterrichtszeiten (alphabetical) – one table:
   name, weekly time, room, next date.
2. [Lehrende] same page – Status says „Angemeldet“ 11 times under „11 angemeldet“; the e-mail column, all green links,
   is the loudest and least important.
3. [Lehrende] same page – the course's facts (Lehrende, Umfang, Platzvergabe) below the students; „Anrechenbar für“ a
   block of tiny module codes; every room dropdown repeats „· Unterrichtsraum Violine · 6 Pl.“
4. [Prüfungsamt] Studierende – Status all „Immatrikuliert“ with the filter on Immatrikuliert.
5. [Prüfungsamt] student record – Semester: 7 rows of „immatrikuliert“ – one line, „immatrikuliert seit WiSe
   2023/24“.

## C. Details

1. [Lehrende] Hauptfach Violine – „Nächste Termine“ a green heading; every other section black with the green bar.
2. [Prüfungsamt] Start, Zu erledigen – three wordings („Zeugnis ausstellen: X“, „1 Prüfung: Kommission prüfen“, „…:
   1 Anmeldung im letzten Versuch“); the area column moves with whether there is a date.
3. [Prüfungsamt] student record – „7 / 6 Fachsemester“ a full green bar though over the Regelstudienzeit; „Verlauf“
   loose between the name and the line under it.
4. [Prüfungsamt] exam page – the examiners' marks aligned differently (Albers and Keller right, Hallmann centred);
   „vorläufig 2,0 (2 von 3 Wertungen)“ small and grey beside a normal „offen“; the rules as a paragraph under
   Speichern.
