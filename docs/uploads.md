# Safer uploads, 16 November 2026

To build, once the jury's pages are done. What protects ConCampus and its users from a harmful file today, what is
decided to add, and why not more.

## Today

- Only a few types per kind of document: PDF and photos by default, video where a video round asks for one
  (`DocumentType.file_types`). No Office files, no archives.
- Every file has to be what its name says, by its first bytes: an .exe renamed .pdf is refused
  (`students.models.genuine`).
- Stored under a random name; opened only by who may (`admissions.views.applicant.document`).
- Served sandboxed (`Content-Security-Policy: sandbox`), never as a type guessed from a name; only PDFs and photos
  open in the browser's own viewer, anything else downloads (`docs/measures.md`).

## Decided

1. **Disarm every uploaded PDF**, before it is stored, with pikepdf (qpdf):
   - remove JavaScript (the names tree, in actions and annotations), actions on opening (`/OpenAction`, `/AA`),
     embedded files (`/EmbeddedFiles`, file attachments), forms (`/AcroForm`, XFA), multimedia (`/RichMedia`),
     actions that start a program or open another file (`/Launch`, `/GoToR`, `/GoToE`);
   - remove every link (`/Link` annotations with `/URI`), so nothing in a PDF is clickable; the text of an address
     stays text. Links an applicant means the jury to follow are handed in as links (`docs/media.md`);
   - keep text, fonts and vector graphics: scores stay sharp, text searchable and readable by screen readers;
   - run it apart from the web process, with a time and memory limit; a PDF it cannot read is refused, not stored as
     it came.
2. **Scan every upload with ClamAV**, later: the official `clamav/clamav` container in `compose.yaml`, ConCampus
   streaming each file to it (clamd INSTREAM) before storing. Free, runs on our server, nothing leaves it but the
   signature updates it fetches (freshclam). About 1–1.5 GB RAM.
   - A hit is not lost silently: the file goes to quarantine, the office sees it and can release it.
   - Optional heuristics that cause most false alarms stay off (`AlertEncrypted`, PUA), or are allowlisted per
     signature.
   - Connected as "a scanner" (host and port in the settings), so a central scanner of the Hochschule's IT can stand in.

## Not chosen

- **PDFs drawn as images.** Removes most, as fonts and images are drawn anew, but moves the risk to a renderer on the
  server, loses searchable text and accessibility, and makes vector scores pixels and files large.
- **A scan by an outside service** (VirusTotal and the like): applicants' documents would leave the Hochschule.

## What stays

No measure removes all risk: a PDF can still attack a reader through a malformed font or image (as FORCEDENTRY did,
2021), and its text can still deceive. The layers above together are what is realistic; staff computers and browsers
kept up to date are the Hochschule IT's part.

## Open

- ClamAV down: refuse uploads (safe, but may block applicants near a Frist) or accept them marked „ungeprüft“ and
  scan later.
- Keep the original of a disarmed PDF, for the office only, or the disarmed copy alone.
- Whether HfM's IT runs a central scanner ConCampus should use instead.
