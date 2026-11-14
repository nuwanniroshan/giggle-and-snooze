# ADR-026-cleaned-picture-overwrite: Where a cleaned picture lives

**Status:** Accepted
**Date:** 2026-11-14
**Feature:** 026-artifact-cleanup

## Context

Spec 026 removes AI artifacts (extra fingers, stray blobs, garbled marks) from a painted page by flagging regions with Codex, masking them with SAM and filling them with LaMa. The result must reach every consumer: the print and digital editions, EPUB, the web reader, the studio, and the video (plan, cut layers, animation). Today `illustrate` repaints a picture by overwriting `page_NN.png` and keeping the old one as `.bak`, and every consumer is keyed by the picture's sha256, so a changed picture marks plan, cuts and script stale with a refresh command (spec 009, 019). The decision is hard to reverse because every consumer would depend on it.

## Options Considered

### Option A: Overwrite `page_NN.png`, keep `.bak`

**Pros:** One picture for every consumer, nothing new to teach editions or the renderer; reuses the sha256 stale checks and the `.bak` revert already used by repaint; matches `illustrate` behaviour.
**Cons:** A cleanup stales that page's plan, cuts and animation, so they must be refreshed; only one `.bak` generation is kept.

### Option B: Store `page_NN.clean.png` and apply it at build and render time

**Pros:** The original is never touched; cleanup can be toggled per consumer.
**Cons:** Every edition builder, the reader, the studio and the renderer must choose between two pictures; two pictures per page can drift out of sync with the plan; double storage.

## Decision

**We chose Option A** because the PRD requires that all editions and the video show the same cleaned picture, and Option A reaches them with the existing sha256 staleness and `.bak` revert, with no change to any consumer.

## Consequences

Running a cleanup invalidates that page's plan, cuts and rendered sections exactly as a repaint does, at cleanup time (not at Accept); the studio shows the refresh command. Revert restores the `.bak` (and removes it) and the same staleness applies. A second cleanup overwrites the single `.bak`, so the studio asks for confirmation if a `.bak` exists (intended: a repainted page already has one). If this ADR is superseded, every consumer must learn to pick between pictures.
