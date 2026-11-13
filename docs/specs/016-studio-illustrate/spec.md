# Spec 016: Paint illustrations from the studio

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/016-studio-illustrate`
**Depends on:** specs 004 (studio), 006 (idea to book), 008 (landscape illustrations), 009 (motion plans), 011 (blueprint), 014 (studio redesign)

---

## Problem Statement

A book made from an idea in the studio arrives with text and a blueprint but no pictures. Painting the pages, covers and motion plans is only possible from the command line (`video_pipeline.illustrate`), so the producer has to leave the studio, find the slug and run a command before any later stage makes sense. The producer wants the studio to carry a new book from idea to painted pages without the command line.

## Goals

- Creating a book from a draft starts painting its pictures automatically, in the studio.
- The book page has a Paint stage the producer can start, follow, stop and rerun like the other stages.
- The producer can see how many pictures are painted and which are missing.
- Script, voices and render stay manual, because they need the producer's review and approval.

## Non-Goals

- Starting the script, voices or render automatically.
- Changing how pictures are painted, checked or planned (spec 008, 009, 011 rules stay as they are).
- Choosing, retouching or repainting single pages from the studio.
- Running two jobs at once.

## Users and Context

**Primary user:** the producer, in the studio on the M1 Pro.
**Context:** after reviewing a draft and pressing Create book; also when returning to a book whose painting failed, was stopped, or whose blueprint art changed.

## User Stories

### Story 1: Paint automatically after Create book
As the producer, I want Create book to start painting so that a new book gets its pictures without another step.

- [ ] **AC-1.1** Given a ready draft and no running job When the producer presses Create book Then the book folder is created and a Paint job for that book is started, and the producer lands on the book page showing that job as running.
- [ ] **AC-1.2** Given the Paint job could not be started after the book was created (a job started in between) When Create book finishes Then the book still exists, the book page shows that pictures are missing and offers Paint, and no error removes the book.
- [ ] **AC-1.3** Given a job is running When the producer presses Create book Then the request is refused as today and the draft is kept.

### Story 2: Paint from the book page
As the producer, I want a Paint stage on the book page so that I can start or rerun painting.

- [ ] **AC-2.1** Given a book with missing pictures and no running job When the producer starts Paint Then a job runs the same command as `python -m video_pipeline.illustrate --book <slug>` and paints only the missing pictures.
- [ ] **AC-2.2** Given a book whose pictures all exist When the producer chooses Repaint all and confirms Then the job runs with `--force`; without the confirmation the request is refused with a message and nothing starts.
- [ ] **AC-2.3** Given a Paint job is running When the producer opens the book page Then the job's live log is shown like other stages, and Stop ends it; pictures already painted stay.
- [ ] **AC-2.4** Given a job (any book, any stage) is running When the producer starts Paint Then it is refused with the running job named.
- [ ] **AC-2.5** Given the painting tool cannot run (Codex missing or logged out) When Paint runs Then the job ends as failed and its log shows the reason.

### Story 3: See the pictures' state
As the producer, I want to see what is painted so that I know when the book is ready for the next stage.

- [ ] **AC-3.1** Given a book When the book page is shown Then the Paint stage shows painted/expected counts for story pages and covers (e.g. "7 of 10 pages, 0 of 2 covers") and names the missing files.
- [ ] **AC-3.2** Given every page and both covers exist When the book page is shown Then the Paint stage is marked done.
- [ ] **AC-3.3** Given the blueprint art changed after painting (spec 011 stale mark) When the book page is shown Then the Paint stage is marked stale and offers Repaint all.
- [ ] **AC-3.4** Given a book without a blueprint, or an existing book with all pictures When the book page is shown Then nothing about it changes except the Paint stage showing done or missing.

## Functional Requirements

- **FR-1** The studio must have a Paint stage per book that runs the existing illustrate CLI as a subprocess, under the studio's one-job-at-a-time rule.
- **FR-2** Create book must start a Paint job for the new book when no job is running; failure to start must not undo the book.
- **FR-3** Repaint all must require an explicit confirmation field.
- **FR-4** The Paint stage status must be derived from files on disk (pictures in `illustrations/`, the blueprint stale mark), not from job records alone.
- **FR-5** The studio must not start the script, voices or render stages automatically.

## Non-Functional Requirements

- Painting keeps running if the browser is closed; the studio's existing restart rules for orphaned jobs apply.
- Studio pages stay responsive while painting (the job is a separate process).

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Codex missing / not logged in | Job fails; log shows the reason; book kept. |
| One picture fails its checks | Job fails with the CLI's message; earlier pictures kept; Paint can be rerun. |
| Studio restarted mid-paint | Existing orphan handling: job marked interrupted; Paint can be rerun. |
| Repaint all without confirmation | Refused with a message; nothing starts. |

## Open Questions

None.

## Out of Scope

- Single-page repaint, picture selection or editing.
- Automatic script, voices or render.
