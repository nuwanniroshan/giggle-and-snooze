# Spec 026: AI artifact cleanup with SAM and LaMa

**Status:** Approved
**Created:** 2026-11-14
**Branch:** `feat/026-artifact-cleanup`
**Depends on:** specs 009 (SAM + LaMa runtime), 016/019 (illustrate, repaint, `.bak`), 004/014 (studio)
**PRD:** [prd.md](prd.md) · **ADR:** [026-cleaned-picture-overwrite](../../adr/026-cleaned-picture-overwrite.md)

---

## Problem Statement

Painted story pages carry AI artifacts (extra fingers, stray blobs, smudges, garbled marks). The only fix today is repainting the whole page, which changes everything on it.

## Goals

- Remove flagged artifacts from a page's picture while leaving every other pixel unchanged.
- Keep the previous picture so the producer can compare and revert.
- Never apply a fix that is too large or failed.

## Non-Goals

- Manual point selection in the studio.
- Style changes, upscaling or recolouring beyond the filled regions.
- Covers (titled covers carry painted lettering the fill could damage).

## Users and Context

**Primary user:** the producer, in the studio or terminal, on this Mac.
**Usage context:** after a page is painted, when its picture shows an artifact.
**Mental model:** "Find the glitch, erase just that, show me before and after."

## User Stories

### Story 1: Find and fill artifacts

- [ ] **AC-1.1** Given a painted page When the producer runs cleanup Then Codex, shown the picture, lists artifact regions, each with a box, up to a few points and a short reason; a picture with no artifacts is reported "nothing to clean" and left unchanged.
- [ ] **AC-1.2** Given listed regions When cleanup continues Then each region is cut into a mask from its box and points, and the masks are grown by a configured margin and filled from the surrounding picture.
- [ ] **AC-1.3** Given a finished fill When the picture is saved Then pixels outside the grown masks are identical to the original.
- [ ] **AC-1.4** Given a region whose mask is empty, or covers more than the configured share of the picture, or whose segmentation or fill fails When cleanup runs Then that region is skipped, not partly applied, and the log lists each skipped region with its reason; the other regions still apply.
- [ ] **AC-1.5** Given Codex output When it is saved Then it is validated (boxes and points inside the picture, region count and reason length limited) with up to 3 attempts, each refused with every problem listed; after 3 failures nothing changes.
- [ ] **AC-1.6** Given every region was skipped When cleanup ends Then the picture is unchanged, no `.bak` is made and the run reports failure.

### Story 2: Keep, compare, revert

- [ ] **AC-2.1** Given an applied cleanup When it finishes Then the cleaned picture replaces `page_NN.png` immediately and the previous bytes are kept as `page_NN.png.bak`.
- [ ] **AC-2.2** Given a `.bak` already exists (from a repaint or cleanup) When cleanup would overwrite it Then the studio asks for confirmation; the command line requires `--yes`.
- [ ] **AC-2.3** Given a cleaned page When the producer opens it in the studio Then before and after are shown with the skipped-region list, with Accept and Revert.
- [ ] **AC-2.4** Given Accept When pressed Then the review is dismissed and the `.bak` is kept.
- [ ] **AC-2.5** Given Revert When pressed Then `page_NN.png` is restored to its exact previous bytes and the `.bak` is removed.

### Story 3: Downstream effects

- [ ] **AC-3.1** Given a cleanup applied or reverted When the studio and logs report the page Then its plan, cuts and rendered sections show stale through the picture hash, with the refresh command, as after a repaint.
- [ ] **AC-3.2** Given a cleanup When it finishes Then `episode_script.json`, its hash, the review mark and the voice approval are unchanged.
- [ ] **AC-3.3** Given a cover name When cleanup is requested Then it is refused with a message that only story pages are supported.

### Story 4: Run it

- [ ] **AC-4.1** Given a book When the producer runs `illustrate --book <slug> --page N --cleanup` Then one page is cleaned; without `--page`, an error asks for a page.
- [ ] **AC-4.2** Given the studio picture view When the producer starts cleanup Then it runs as a stage job with log and stop, and the models are loaded only for that job.

## Functional Requirements

- **FR-1** Cleanup is an `illustrate` mode (`--cleanup`) for story pages, reusing the cut stage's segmenter and fill interfaces and the repaint `.bak` convention.
- **FR-2** Codex region flagging goes through a validating save tool (MCP) with 3 attempts; region data: box, 0–4 include points, reason.
- **FR-3** Region masks are built like layer masks (box plus points, cleaned, grown); settings (margin, max share of picture, max regions) live in config.
- **FR-4** The result composites filled pixels only inside the grown masks; the rest is copied from the original.
- **FR-5** A cleanup writes a record next to the picture (regions applied and skipped, date) for the studio view.
- **FR-6** The picture is written atomically; the `.bak` is written before the picture and only when at least one region applied.

## Non-Functional Requirements

- **NFR-1 Memory:** fits 16 GB; models loaded one after another within the job.
- **NFR-2 Testability:** region validation, mask caps, compositing and `.bak` handling are pure or use fake Codex, segmenter and fill; real models in the opt-in `models` marker.
- **NFR-3 Safety:** Codex text never reaches a shell or file name; no network beyond Codex.
- **NFR-4 Fidelity:** unchanged pixels are bit-identical; picture size and format are unchanged.

## Error Scenarios

| Scenario | Behaviour |
|---|---|
| Models missing | Job fails with the download command |
| Codex fails or refuses 3 times | Nothing changes; failure with the last problem list |
| Region too large | Skipped, listed |
| Write fails midway | Original picture intact (atomic write) |
| Revert with no `.bak` | Refused with a message |

## Open Questions

- None.

## Out of Scope (Future Considerations)

Human review of flagged regions before the fill, automatic re-planning after cleanup, a one-command cleanup across a whole book, cover cleanup.
