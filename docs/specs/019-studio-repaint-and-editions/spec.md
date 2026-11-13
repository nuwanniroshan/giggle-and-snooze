# Spec 019: Repaint one page and build the editions from the studio

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/019-studio-repaint-and-editions`
**Depends on:** specs 004, 009, 010, 014, 016

---

## Problem Statement

When one picture is wrong the studio can only repaint every picture, which costs time and quota and changes good pages. The PDF, EPUB, KDP files and flipbook can only be built from the command line, so a book made in the studio is never finished there.

## Goals

- Repaint a single page from the studio; its motion plan and layers follow.
- Build every edition from the studio and download each file there.

## Non-Goals

- Editing the picture prompt in a rich editor (a plain text box is enough).
- Publishing or uploading editions anywhere.

## Users and Context

**Primary user:** the producer in the studio.

## User Stories

### Story 1: Repaint one page
- [ ] **AC-1.1** Given a book's page view in the studio When the producer presses Repaint page and confirms Then a job repaints only that page's picture (the old picture is kept as `page_NN.png.bak`), writes a fresh motion plan for it, and cuts its layers; other pages are not touched.
- [ ] **AC-1.2** Given the page view When the producer edits the page's picture prompt and saves Then `book.json` stores it (validated as other book data), before or without repainting.
- [ ] **AC-1.3** Given Repaint page without the confirm field When submitted Then it is refused and nothing starts.
- [ ] **AC-1.4** Given a cover When the producer repaints it Then only that cover is repainted (no plan or cut).
- [ ] **AC-1.5** Given a repaint finished When the video stage is shown Then it is out of date (the existing render check sees the changed picture).
- [ ] **AC-1.6** Given any job running When Repaint page is pressed Then it is refused naming the running job.

### Story 2: Build editions
- [ ] **AC-2.1** Given a book with every picture When the producer starts Build book Then a job runs the book builder and the book page lists each produced file with a download link and its size.
- [ ] **AC-2.2** Given pictures are missing When the book page is shown Then Build book is blocked with the missing files named.
- [ ] **AC-2.3** Given the editions were built and then `book.json` or a picture changed When the book page is shown Then the editions are marked out of date.
- [ ] **AC-2.4** Given the flipbook When it is opened from the studio Then its pictures load (served from the book's files).

## Functional Requirements

- **FR-1** The illustrate CLI must accept `--page N` (or `--cover front|back`) to repaint one picture, keeping the old file as `.bak`.
- **FR-2** The studio must run repaint, plan and cut for one page as one job, under the one-job rule.
- **FR-3** The studio must have a Build book stage with status derived from files (built / out of date / not built / blocked).
- **FR-4** Download links must be limited to the book's `build/` edition files.

## Non-Functional Requirements

- Repainting one page costs one picture request.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Repaint fails | Job fails, old picture restored from `.bak`, log shows why. |
| Build fails | Job fails with the builder's message; earlier files kept. |

## Open Questions

None.

## Out of Scope

- Uploading to KDP or other stores.
