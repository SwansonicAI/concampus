# Friction: what makes a page feel wrong

The issues fixed in the review rounds share a small number of characteristics. None of them is a crash; each makes a
person think, guess, repeat or recover where the system could have done that for them. Below, each pattern with the
question that finds it, what we fixed, and the usual fix. For the site-wide sweep: go role by role (applicant, student,
teacher, each office), open every page with a list, form or action, and ask the questions.

## 1. The system knows, the person guesses

**Ask:** does the page ask for, or offer everything instead of, something the system already knows: the person's
Hauptfach, semester, schedule, earlier answers, earlier uploads?

**Fixed:** room booking ignored a lecture in another building right before; teachers not narrowed to the Hauptfach;
the semester list went back to 2020 for a student who began in 2024; the same video uploaded twice.

**Fix:** order, narrow and default by what we know; offer to reuse what is there.

## 2. One long list instead of a few short questions

**Ask:** is there a list of more than about 30 entries, or one that mixes kinds (Germany and abroad, every
Studiengang with every Hauptfach)?

**Fixed:** 931 Hochschulen and states, 645 Kreise and states, every Eignungsprüfung in one dropdown.

**Fix:** first the question that splits (Hauptfach, Germany or abroad), then a short list; type-to-find where it stays
long; the person's own or the most frequent answers first.

## 3. Asked in the administration's terms

**Ask:** must the person know an official category to answer (Kreis, Hochschule der Ersteinschreibung, Studienart,
module codes)?

**Fixed:** "Zuerst studiert: in Deutschland / im Ausland" before the list; Studiengang shown under the Hauptfach.
Open: the Kreis of the HZB (item 93).

**Fix:** ask in the person's words (town, instrument, "where did you first study?") and translate to the category.

## 4. Narrowed with no way out

**Ask:** does a filter or default stop a legitimate exception?

**Fixed:** only the teachers of the chosen Hauptfach.

**Fix:** keep the narrowing, add "show the rest".

## 5. Input that accepts what cannot be right; errors that do not say why

**Ask:** can the field take letters, impossible or future dates, shortcuts meant for staff ("morgen") in a public
form? Does the error say what is wrong and how to write it, in German?

**Fixed:** an HZB date in the future, "31.02.", the browser's English "required" message.

**Fix:** keep typing to what the field can hold; check on the server with a reason; show the same reason while typing.

## 6. The rule shows after the act, or elsewhere

**Ask:** does the person learn a limit, cost, deadline or consequence only after acting, or only on another page?

**Fixed:** the quota rule only on "Meine Buchungen"; upload limits only after choosing a file; what a Vertiefung commits
to (modules, share of the Gesamtnote); Fristen without saying where they come from.

**Fix:** state the rule, limit or consequence next to the control, in numbers.

## 7. A promise the system cannot keep

**Ask:** does what the page offers work end to end, at its extremes: the largest file, the oldest semester, the last
day?

**Fixed:** a 1.5 GB video against a 30-second server limit; room booking offered on a past course; registration
"leer: sofort" opening courses nobody had checked.

**Fix:** test the edge; offer only what works.

## 8. The wrong thing is easiest; no way back

**Ask:** is the default to do many things at once (opt out of each)? Does one click do something lasting? Can a
mistake be undone?

**Fixed:** 252 courses pre-ticked, one-off ones included; booking in one click; copied courses that could not be
deleted.

**Fix:** default to the safe or usual choice; confirm a lasting act by naming what happens; give a way back (delete a
draft, call off, undo).

## 9. A process squeezed into one button

**Ask:** does the real work have stages, people and deadlines that the system turns into one action?

**Fixed:** the Semesterwechsel (prepare, confirm, check, publish); the paper Protokoll booked by the day it arrived.

**Fix:** states (draft, confirmed, published), who acts at each, to-dos with due dates, checks before the last step.

## 10. Work repeated by hand

**Ask:** is the same entry made many times: per course, per student, per application?

**Fixed:** about 250 courses scheduled one by one; one video per application.

**Fix:** a bulk action with a result per item; last time's values as the proposal.

## 11. Stale, silent or cut-off

**Ask:** after an action, does every count, badge and message update? Is there feedback while waiting? Is any text cut
off?

**Fixed:** the Postfach count, an old "Nichts gefunden", no feedback after choosing a demo profile, room descriptions,
short bookings and 30-minute dates cut off.

**Fix:** refresh what changed, show that something is happening, give text room or let it wrap.

## 12. Fields without hierarchy

**Ask:** does a form of more than six fields run in one column, with related fields apart and nothing grouped?

**Fixed:** every form, the course edit page first.

**Fix:** groups under headings, related short fields side by side (`layout` on the form).

## 13. Told to ask someone who cannot be found

**Ask:** where a page names a person or office to contact, can they be reached in one step?

**Fixed:** "Prüfungsamt" found nothing in search; the Hauptfachlehrer:in for advising had no address.

**Fix:** link the name to its e-mail where it is mentioned; make people and offices searchable.

## Recording the sweep

One line per finding: page and role, pattern number, what happens, the fix. Fix first what many people meet often
(applicants, students) and what costs most when it goes wrong (deadlines, Bescheide, grades).
