# Spec 004: Episode studio (local web interface)

**Version:** 1.1.0 (1.1.0, 2026-11-09: NFR Look allows subtle depth and small radii, MINOR; 1.0.1, 2026-11-09: NFR Look added at the producer's request, PATCH)
**Status:** Approved
**Created:** 2026-11-09
**Depends on:** specs 001 (episode script), 002 (voice synthesis), 003 (render)
**Replaces:** the planned one-command CLI/MCP wrapper (stage 4 in the PRD)

## Problem Statement

Producing an episode now takes three command-line stages with flags, two human gates (script review, audio approval) and a watch-through, plus copying a new book's files into the right places by hand. The producer has to remember the commands, the order, and where each output lands. A weekly or monthly release cadence needs this to be point-and-click.

## Goals

- Add a new book from the browser.
- See every book and exactly where its episode stands (script, voices, video), and what the next step is.
- Run each stage with one button and watch its progress live.
- Do both human gates in the browser: review/edit the script, listen and approve the audio.
- Watch the finished video in the browser.

## Non-Goals

- Access from other devices, accounts or passwords (this Mac only).
- Publishing to YouTube.
- Editing illustrations, voices or render settings in the browser.
- Running stages automatically one after another.
- Running two stages at the same time.
- Background music (separate spec).

## Users and Context

One producer on the M1 Pro, using a normal web browser on the same Mac. Stages are slow (script ≈ 1 min, voices ≈ 25 min, render ≈ 10 min) and memory-heavy, so only one runs at a time. The existing command-line tools keep working unchanged.

## User Stories

### Story 1: Open the studio
As the producer, I want to start the studio with one command and open it in my browser.

- **AC-1.1** One command starts the studio and prints the address to open; it is reachable only from this Mac.
- **AC-1.2** The home page lists every book the studio knows (the pilot included), with title, and for each stage its state: not started / running / done / needs review / approved / failed / out of date.
- **AC-1.3** For each book the page shows the single next action (e.g. "Generate script", "Review script", "Synthesize voices", "Listen and approve", "Render video", "Watch video").

### Story 2: Add a new book
As the producer, I want to add a book by uploading its data and pictures, so that I don't copy files by hand.

- **AC-2.1** I can upload a book data file plus its illustration images (pages and front/back covers); the studio validates them before saving anything.
- **AC-2.2** If the book data is invalid, or an illustration named in it (or a cover) is missing, nothing is saved and every problem is listed.
- **AC-2.3** A valid upload is stored in the new book's own folder; it then appears on the home page with "Generate script" as the next step.
- **AC-2.4** Uploading a book whose title matches an existing book is refused unless I confirm replacing it; replacing never touches another book.
- **AC-2.5** Optional intro/outro nook images can be uploaded with the book.

### Story 3: Run a stage and watch progress
As the producer, I want one button per stage with live progress, so I know what's happening.

- **AC-3.1** Each stage (generate script, synthesize voices, render video) has a button that is enabled only when its inputs are ready (script: always; voices: script reviewed; render: audio approved and still valid).
- **AC-3.2** While a stage runs, its page shows live progress output, elapsed time, and keeps doing so after a page reload.
- **AC-3.3** Only one stage runs at a time across all books; while one runs, every other stage button is disabled with a note saying what is running.
- **AC-3.4** When a stage ends, the page shows success or failure; on failure it shows the error message the tool produced.
- **AC-3.5** A running stage can be stopped; stopping leaves no partial outputs that a later run would trust (relies on each stage's existing atomic writes).
- **AC-3.6** Regenerating an existing script requires an explicit confirmation (it overwrites the hand-edited script); re-synthesis and re-render with "force" are available as separate, labelled options.

### Story 4: Review and edit the script
As the producer, I want to review the episode script in the browser and fix lines, so that review is quick.

- **AC-4.1** The script page shows the intro, every page (page text, its illustration thumbnail, its lines with speaker and role) and the outro.
- **AC-4.2** I can edit any line's text and save; saving validates the whole script with the same rules as the pipeline and, if invalid, saves nothing and shows each problem next to its line.
- **AC-4.3** A "Mark as reviewed" button records that this exact script was reviewed; any later edit clears the reviewed mark.
- **AC-4.4** Voice synthesis started from the studio uses the reviewed mark instead of the `--reviewed` flag and refuses if the script changed since it was marked.

### Story 5: Listen and approve
As the producer, I want to listen and approve in the browser.

- **AC-5.1** The audio page plays the full preview and each track (intro, every page, outro), and shows the review sheet's per-line measurements.
- **AC-5.2** An "Approve audio" button approves using the same rules as the approve command; if approval is refused, the reason is shown.
- **AC-5.3** The page shows the current audio status (pending review / approved) and when it was approved.

### Story 6: Watch the video
As the producer, I want to watch the finished video in the browser.

- **AC-6.1** Once rendered, the video page plays the video with seeking, shows its duration and file location.
- **AC-6.2** If the inputs changed since the video was rendered (so a re-render would change it), the page says the video is out of date.

## Functional Requirements

- **FR-1 Books.** The studio knows the pilot book in its current location plus every book added through it; each added book keeps its data and pictures in its own folder. Book identity is the slug of its title.
- **FR-2 Status.** Stage states are derived from the files the pipeline already writes (script, reviewed mark, audio status, video record), not from a separate database; out-of-date checks reuse each stage's own up-to-date logic.
- **FR-3 Jobs.** A stage runs as the same pipeline code the command line uses, in the background, one at a time; its output is captured for live display and kept after it finishes (last run per book and stage).
- **FR-4 Gates.** Script review mark (new), audio approval (spec 002) and the render gate (spec 003) are enforced the same way as on the command line; the studio cannot bypass them.
- **FR-5 Script editing.** Edits are validated with the pipeline's script validation and written atomically.
- **FR-6 Uploads.** Uploaded files are validated (book data, image files readable, size limits) and written atomically into the book's folder; file names from the upload cannot escape that folder.
- **FR-7 Media.** Audio and video files are served to the browser for playback, with seeking.

## Non-Functional Requirements

- **Security:** listens only on the local loopback address; no remote access, no secrets in pages; uploads cannot write outside their book's folder.
- **Responsiveness:** pages load in under 1 s while a stage is running; progress updates appear within 2 s of the tool printing them.
- **Look:** ultra-minimalist and clean: black and white only (one grey for secondary text and hairlines), no decoration, generous spacing between elements.
- **Depth (v1.1.0):** subtle depth on raised elements (sections, buttons, inputs, tables, the running banner): soft shadows in black at low opacity (at most 12%) and small corner radii (2 to 4px). Still black and white only: no other colours, no gradients, no icons.
- **Simplicity:** no build step for the web pages; starts with one command from the project's environment.
- **Reliability:** if the studio is restarted while a stage runs, the stage is reported as interrupted (not "running"), and nothing half-written is trusted (stage-level atomic writes).
- **Compatibility:** the command-line tools keep working unchanged, and books added through the studio can also be run from the command line.

## Error Scenarios

| Situation | Behaviour |
|---|---|
| Invalid upload | Nothing saved; list every problem (AC-2.2) |
| Duplicate title | Refuse unless replacement confirmed (AC-2.4) |
| Stage button pressed while another runs | Refused; say what is running (AC-3.3) |
| Stage fails | Show failure and the tool's error (AC-3.4) |
| Script edit invalid | Nothing saved; problems shown per line (AC-4.2) |
| Synthesis with unreviewed / changed script | Refused; ask to review (AC-4.4) |
| Approval refused | Show the reason (AC-5.2) |
| Studio restarted mid-stage | Stage shown as interrupted (NFR Reliability) |
| Port already in use | Studio stops at start-up and says so |

## Open Questions

None.

## Out of Scope

Remote access and accounts; publishing; editing pictures, voices or render settings; automatic chaining; concurrent stages; background music.
