# Links and audio in applications, 16 November 2026

To build. What an application can hold today and what it needs, chiefly for composition; noted while reworking the
jury's pages (`docs/layout.md`).

## Today

- Applicants upload files only: `admissions.views.applicant.upload` needs a file. A document can hold a link
  (`students.models.Document.url`) and the jury's pages show one, but nothing lets an applicant hand one in; only a
  video taken over from another application keeps its link.
- In the page play: videos (.mp4, .mov, .webm, .ogg …) and links straight to a video file (`Document.playable`).
  PDFs and photos open in the browser; everything else downloads, audio included.
- The Content-Security-Policy lets media come only from ConCampus's own files.

## Needed

1. **Links.** A document type says whether a link may stand in for a file (YouTube, Vimeo, SoundCloud, a cloud
   folder). The applicant pastes it; it is checked to be https and kept with the day it came in.
   - Not embedded: YouTube and the like would send the jury's data to Google and the like (consent under the GDPR)
     and need the Content-Security-Policy opened. The link opens in a new tab; a link straight to a video or audio
     file still plays in the page.
   - What a link holds can change after the Frist; an upload cannot. The note to applicants says it must not, and
     the Eignungsprüfungsordnung decides whether links count at all.
   - In the Sichtung the times in the programme jump through an uploaded video only.
2. **Audio in the page.** .mp3, .m4a, .wav, .flac, .ogg/.opus play in an audio player, in the Sichtung and on the
   applicant's card, as videos do (`admissions/_document.html`).
3. **Several kinds in one submission.** For composition: scores as PDF, recordings as audio or video, links; one
   document type with `max_files` > 1 and these file types, or one type each.

## What stays

- Instrumentalists and singers hand in a video, never audio alone: the commission sees who plays. Their video types
  accept video files (and links, once 1. is built), no audio.
- Every file keeps being checked by its content (`students.models.genuine`) and served sandboxed
  (`docs/measures.md`).

## Open

- Which subjects may hand in links, and whether the Hochschule's rules allow them for a video round.
- Whether a Mappe (composition, music theory) is judged online like a video round, before the stage in person.
