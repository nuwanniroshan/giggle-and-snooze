# Spec 003: Render the episode video

**Version:** 1.0.0
**Status:** Approved
**Created:** 2026-11-09
**Depends on:** spec 001 (episode script), spec 002 (voice synthesis; audio must be approved)

## Problem Statement

After spec 002, a book has an approved, spoken episode: an intro track, one track per page and an outro track, each with per-line timing. Nothing yet turns that audio and the book's illustrations into the 16:9 read-aloud video the channel publishes. Doing it by hand in an editor takes hours per book and breaks the "new book, no hand-tuning" principle.

## Goals

- One command turns an approved episode into a single YouTube-ready 1920×1080 video.
- Every page fills the frame with its illustration, gently animated while it is read, with on-screen captions of what is being said.
- The kids' Q&A visibly draws attention to the detail they talk about.
- Page turns are marked by a transition and a page-turn sound.
- The intro and outro frame the reading with a cozy scene, without requiring custom art.
- Re-running with nothing changed costs almost nothing.

## Non-Goals

- Background music (handled in a separate, later spec).
- Uploading or publishing to YouTube; titles, descriptions, thumbnails.
- A separate captions/subtitle file.
- Vertical (9:16) or other formats.
- On-screen avatars or speech bubbles.
- Generating reading-nook artwork.

## Users and Context

The producer (one person) runs the pipeline on an Apple Silicon M1 Pro with 16 GB, after approving the voice audio. They watch the result before publishing. Viewers are young children (about 3–8) and their parents, often on a TV or tablet.

## User Stories

### Story 1: Render an approved episode
As the producer, I want one command to render the approved episode into a video, so that I get a publishable file without an editor.

- **AC-1.1** Given approved audio for a book, when I run the render, then one video file is written under the book's build folder, named from the book's metadata, and the command exits 0.
- **AC-1.2** The video is 1920×1080, 30 frames per second, with stereo audio, in a format YouTube accepts directly (H.264 video in 8-bit 4:2:0, AAC audio, metadata at the start of the file, BT.709 colour).
- **AC-1.3** The video contains, in order: the intro, every page in page-number order, and the outro. Each section's spoken audio is the approved track for that section, unaltered except for level and placement.
- **AC-1.4** The total duration equals the sum of the section durations plus the transitions, within 0.1 s, and the audio and video streams end within 0.1 s of each other.
- **AC-1.5** Another book with its own approved audio renders with no code or config changes.

### Story 2: Refuse unapproved or changed audio
As the producer, I want the render to refuse audio I haven't approved, so that nothing unreviewed is ever published.

- **AC-2.1** If the voice status is not "approved", the render stops before producing anything, says the audio needs approval, and names the approve command.
- **AC-2.2** If the episode script or the audio changed after approval (the approval would no longer hold), the render stops and says to re-run synthesis and approve again.
- **AC-2.3** If the audio folder or any track it lists is missing, the render stops and names what is missing.

### Story 3: Pages fill the frame and move
As a viewer, I want each page's picture to fill the screen and move gently, so that the reading feels alive.

- **AC-3.1** Each page section shows that page's whole illustration, at full frame height, centred, over a softened, darker version of the same illustration filling the rest of the 16:9 frame.
- **AC-3.2** While narration plays, the picture moves according to the page's narration motion from the episode script (slow push-in, slow pull-out, pan left, pan right, or static). Motion is smooth: no frame jumps back or stutters (each frame's zoom and position change monotonically within a motion).
- **AC-3.3** On pages with kid Q&A, from the start of the first Q&A line the camera eases toward the page's Q&A zoom target (its x, y within the illustration and its zoom level) and holds there until the page ends.
- **AC-3.4** Each page section lasts as long as its audio track plus a short lead-in before the first line and a short hold after the last line.

### Story 4: Captions
As a parent or child, I want to read along with what is being said, so that the video supports early reading.

- **AC-4.1** Every spoken line is shown on screen as a caption while it is spoken, and no caption is shown when nothing is being said.
- **AC-4.2** Captions show the line's original words from the script, with emotion tags removed and no pronunciation respellings.
- **AC-4.3** A caption never exceeds two lines of text and stays inside the frame with a margin; longer lines are split into consecutive caption cards in reading order, each shown for a share of the line's time proportional to its words.
- **AC-4.4** Captions are legible over any illustration: light text on a dark translucent band at the bottom of the frame, text at least 40 px tall at 1080p.

### Story 5: Page turns
As a viewer, I want a clear page turn between pages, so that I can follow the book.

- **AC-5.1** Between consecutive sections there is a visual transition of fixed length, and a page-turn sound plays during it.
- **AC-5.2** No spoken audio overlaps a transition.
- **AC-5.3** If the producer has supplied a page-turn sound file in the shared audio-effects folder, it is used; otherwise a page-turn sound is produced locally and reused.

### Story 6: Intro and outro scenes
As a viewer, I want a cozy opening and closing, so that the episode feels like story time.

- **AC-6.1** The intro shows a reading-nook intro image if one is supplied for this book, else a shared one, else the book's front cover over a softened backdrop; the camera pushes in slowly while the intro audio plays.
- **AC-6.2** Before the outro, the book's back cover is shown briefly (the book closes) with a page-turn transition.
- **AC-6.3** The outro shows a reading-nook outro image if one is supplied for this book, else a shared one, else the back cover over a softened backdrop; the camera pulls back slowly and the picture fades out at the end.
- **AC-6.4** Intro and outro captions follow Story 4.

### Story 7: Fast, safe re-runs
As the producer, I want re-renders to reuse unchanged work, so that small edits are quick.

- **AC-7.1** Re-running with no changed inputs produces no new rendering and reports that the video is up to date.
- **AC-7.2** Changing one page's inputs (its audio, illustration, camera settings or caption text) re-renders only that page's section, then re-assembles the video.
- **AC-7.3** A `--force` option re-renders every section.
- **AC-7.4** An interrupted render never leaves a partial video at the final path, and a partial section is never reused.

## Functional Requirements

- **FR-1 Inputs.** Book data (metadata, pages, illustration files), the episode script (camera settings, cast, text), the approved voice output (status, tracks and their timing), optional nook images, an optional page-turn sound, and a render configuration (frame size, rate, lead-in/hold, transition length, caption style, motion strength, audio levels). Nothing book-specific is hardcoded.
- **FR-2 Approval gate.** Render only when the voice status is "approved" and still valid for the current script and audio (Story 2).
- **FR-3 Sections.** Build sections intro, page 1…N, back-cover beat, outro, each with its picture, motion, captions and audio placement.
- **FR-4 Motion.** Narration motions and Q&A zoom per Story 3; intro push-in and outro pull-back with fade-out per Story 6. Motion is derived only from the script's camera settings and timing data.
- **FR-5 Captions.** Per Story 4.
- **FR-6 Transitions and page-turn sound.** Per Story 5.
- **FR-7 Assembly.** Join sections and transitions into one video with one continuous audio track; levels normalised so speech peaks don't clip.
- **FR-8 Reuse.** Each section's identity covers everything that affects it (picture file contents, timing, text, motion, render configuration); unchanged sections are reused, the final video is reused when nothing changed, all writes are atomic (Story 7).
- **FR-9 Report.** On success, print the video path, its duration, how many sections were rendered vs reused, and a reminder to watch it before publishing.

## Non-Functional Requirements

- **Performance:** a full first render of the pilot (about 15 minutes of video) finishes in ≤ 15 minutes on the M1 Pro 16 GB; a no-change re-run in < 10 s.
- **Memory:** peak memory stays under 4 GB; sections are rendered one at a time.
- **Cost:** no network use and no paid services.
- **Quality:** the producer watches the full video before publishing (a human gate, outside the tool).
- **Reliability:** every failure exits non-zero with a message naming the cause (and the section where relevant).

## Error Scenarios

| Situation | Behaviour |
|---|---|
| Audio not approved | Stop; say approval is needed; name the approve command (AC-2.1) |
| Script or audio changed since approval | Stop; say to re-run synthesis and approve (AC-2.2) |
| Audio folder, a track or its timing missing | Stop; name the missing item (AC-2.3) |
| Illustration or cover file missing | Stop; name the page and file |
| Supplied nook image or page-turn sound unreadable | Stop; name the file (don't silently fall back) |
| Video tool missing or fails | Stop; show the tool's error and the section |
| Disk full | Stop; name the output folder |
| Interrupted | No partial final video; next run redoes incomplete sections (AC-7.4) |

## Open Questions

None.

## Out of Scope

Background music; publishing and YouTube metadata; thumbnails; subtitle files; 9:16 and other formats; avatars; generating nook art.
