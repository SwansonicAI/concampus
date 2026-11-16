# To do

Ideas kept for later, each with why and where it would go. What the domain still lacks is in `docs/gaps.md`, what
waits on others in `docs/waiting.md`.

## Names from what exists

When a title is typed (a Lehrveranstaltung, a Prüfung, a stage of an Eignungsprüfung), offer the titles already in
use as one types: „Eignungsprüfung Hauptfach …“, „Videostufe …“, „Gehörbildung I“. The same thing then carries the
same name across semesters, Hauptfächer and offices, and what finds things by name keeps finding them: lists and
search, and the taking over of Eignungsprüfungen a year on, which matches stages by their title.

- Where: the title fields of the forms for Lehrveranstaltungen, Prüfungen and Eignungsprüfungen; the lookup behind
  `datalist[data-suggest]` in `app.js` is a start.
- Open: only suggest, or choose from the list with a new name as the exception.

## Writing to one another

The Postfach only receives: ConCampus writes, the person reads (`core.Message`); a list can be written to from
Studierende › Listen. Nobody can answer, nor write to a commission, a teacher or the office. A jury finding a day, an
applicant asking about a Termin, a teacher moving a lesson all go to e-mail and leave ConCampus without a trace.

- Where: a reply under a message, and „Schreiben“ on a person, a commission or a Lehrveranstaltung; one thread per
  matter, linked to it (the step, the Termin, the course), mailed as now.
- Open: who may write to whom (applicants only to the office?); whether replies by e-mail come back in.
