# Spec 025: Grounded camera targets and caption placement

**Status:** Approved
**Created:** 2026-11-14
**Branch:** `feat/025-grounded-camera-captions`
**Depends on:** specs 001 (episode script), 003 (render), 009 (plan + layers), 010 (animate), 004/014 (studio)
**PRD:** [prd.md](prd.md)

---

## Problem Statement

The Q&A camera zoom uses a position the script writer guessed from the page's prompt text, so it often misses the subject the kids discuss. Captions always sit in a fixed bottom band and can cover the subject or the action.

## Goals

- The Q&A camera centres on, and keeps in frame, the real picture region of the subject the Q&A names.
- Each page's caption band sits at the top or bottom, whichever covers less of the picture's subjects.
- This works on books that are already reviewed and approved, with no re-review and no new voice approval.

## Non-Goals

- Free-position captions or caption restyling.
- Locating subjects on pages with no Q&A and no cut layers.
- New models, or changes to still-versus-animated rendering.

## Users and Context

**Primary user:** the producer, in the studio or terminal, on this Mac, after the pictures are painted and cut and before (or after) rendering.
**Usage context:** one grounding run per book; re-run for a page whose picture changed.
**Mental model:** "Aim the camera at what they're talking about; keep the words off the picture."

## User Stories

### Story 1: Name and locate the Q&A subject

**As a** producer **I want** the camera target found from the real picture **So that** the Q&A zoom lands on the subject.

- [ ] **AC-1.1** Given a page with a Q&A, a current plan and cut layers When grounding runs Then the page's camera file records the subject name, its bounding box and a target (x, y, zoom) whose centre lies inside that box.
- [ ] **AC-1.2** Given the named subject is a plan layer When grounding runs Then that layer's cut mask defines the box and no new segmentation runs for it.
- [ ] **AC-1.3** Given the named subject is not a plan layer When grounding runs Then the subject's phrase and a box on the picture are segmented once and the resulting mask defines the box.
- [ ] **AC-1.4** Given a subject box When the zoom is chosen Then the whole box stays inside the frame and the zoom is within the existing 1–2 range, reduced if the box is too large.
- [ ] **AC-1.5** Given a page with no Q&A When grounding runs Then no subject is named and the page's target is left unset.
- [ ] **AC-1.6** Given naming or segmentation fails, or the mask is empty or covers over the configured share of the picture When grounding runs Then the page records the reason and no target, and the other pages continue.

### Story 2: Choose the caption band

- [ ] **AC-2.1** Given a page with masks (the subject mask plus all cut layer masks) When grounding runs Then the band with the smaller share of mask pixels is recorded as `top` or `bottom`.
- [ ] **AC-2.2** Given equal coverage, or a page with no masks When the band is chosen Then it is `bottom`.
- [ ] **AC-2.3** Given a page's band When captions are drawn on that page Then every caption card of the section uses that band, with the same style, size and margins as today.
- [ ] **AC-2.4** Given the intro and outro sections When they render Then captions stay at the bottom.

### Story 3: Use it in render without disturbing approvals

- [ ] **AC-3.1** Given a camera file whose picture sha256 matches the page picture When the page renders Then its Q&A camera uses the grounded target in place of `ffmpeg_camera.qa_zoom_target`, and captions use the recorded band.
- [ ] **AC-3.2** Given no camera file, a stale one (picture changed) or a page whose grounding failed When the page renders Then the old guessed target and bottom band are used, and the log names the reason and the fix command.
- [ ] **AC-3.3** Given grounding is run on an approved book When it finishes Then `episode_script.json`, its hash, the review mark and the voice approval are byte-for-byte unchanged.
- [ ] **AC-3.4** Given a page's camera file changes When sections render Then only that page's section is re-rendered; pages whose camera and band are unchanged keep their cached sections.
- [ ] **AC-3.5** Given a book with no camera files When it renders Then output and cache keys equal today's.

### Story 4: Run and inspect it

- [ ] **AC-4.1** Given a book When the producer runs the grounding command with an optional page and a force option Then pages with a current camera file are skipped unless forced.
- [ ] **AC-4.2** Given the studio Animation view of a page When a camera file exists Then it shows the subject name, box, target and band, and the failure reason if any.
- [ ] **AC-4.3** Given the studio book page When the producer starts grounding Then it runs as a stage job like the other stages, with log and stop.
- [ ] **AC-4.4** Given a hand-edited camera file When it is saved Then it is validated (target and box in range, band `top` or `bottom`) and an invalid file is ignored with the reason in the log.

## Functional Requirements

- **FR-1** Grounding is its own stage and CLI (`video_pipeline.ground_camera --book <slug> [--page N] [--force]`). It requires cut layers for pages whose subject is a plan layer and runs the segmenter on its own (never alongside another model stage).
- **FR-2** Per page with a Q&A, Codex, given the picture, the page's Q&A lines and the plan layer names, returns a subject: a layer name, or a short phrase with a pixel box. The result is accepted only through a validating save tool (layer name must exist; box inside the picture; phrase length limited) with up to 3 attempts, each refused with every problem listed.
- **FR-3** The camera file is `books/<slug>/camera/page_NN.json` (committed, hand-editable): picture sha256, subject, box, target (x, y, zoom), band, failure reason. A file is current only when its sha256 equals the picture's.
- **FR-4** Zoom for a box is the largest zoom within 1–2 that keeps the box (plus a configured margin) in frame; the target centre is the box centre clamped so the crop stays in the picture. Settings live in config.
- **FR-5** Band choice scores the share of mask pixels whose rows fall in each band's area; the score ignores masks smaller than a configured share.
- **FR-6** Render and the page preview read the camera file; the caption band and the grounded target enter the section cache key only when a current camera file exists.
- **FR-7** Grounding never writes `episode_script.json`.

## Non-Functional Requirements

- **NFR-1 Memory:** fits the 16 GB host; one model loaded at a time; segmentation only for non-layer subjects.
- **NFR-2 Determinism:** the same picture, masks, subject and config give the same file (the subject choice itself is Codex's and is stored, not regenerated).
- **NFR-3 Testability:** target math, band scoring, staleness and file validation are pure and unit-tested without models; the segmenter and Codex are faked; real models in the opt-in `models` marker.
- **NFR-4 Safety:** the subject phrase never reaches a shell or file name; Codex output is validated before saving.

## Error Scenarios

| Scenario | Behaviour |
|---|---|
| Layers not cut for a layer subject | Page records the reason ("cut layers missing") and the fix command |
| Codex names an unknown layer | Refused with the layer list; retried up to 3 times, then page fails |
| Empty or oversized mask | Page fails with reason; old behaviour at render |
| Picture changed after grounding | File is stale; render falls back and says so |
| Invalid hand edit | File ignored, reason logged |

## Open Questions

- None.

## Out of Scope (Future Considerations)

Per-card caption placement, detail or edge based band scoring, camera targets for narration pages, grounding during autopilot (a later spec may add it as a stage).
