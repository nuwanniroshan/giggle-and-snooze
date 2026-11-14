# PRD: AI artifact cleanup with SAM and LaMa

**Date:** 2026-11-14
**Feature:** 026-artifact-cleanup

## Problem

Painted pages carry AI artifacts (extra fingers, stray blobs, smudges, garbled marks). Today the only fix is repainting the whole page, which changes everything.

## Users & Context

The book maker in the studio or CLI. Touches `illustrate.py` (repaint, `.bak`), `cut_layers.py` (SAM segmenter, LaMa inpainter, existing interfaces), the Codex agent runner and MCP save tools, and the studio picture view.

## Goals

- `illustrate --cleanup` asks Codex to flag artifact regions (box, points, reason), SAM masks each region and LaMa fills it.
- The cleaned picture replaces `page_NN.png` immediately and the old one is kept as `.bak`. The studio shows before and after. Accept only dismisses the review and keeps the `.bak`; Revert restores the exact previous bytes and removes the `.bak`.
- Confirmation is asked only when a `.bak` already exists (a prior repaint or cleanup), because it would be overwritten; this is intended.
- Safety caps: oversized or failing regions are skipped, never half-applied, and the log lists every skip.

## Non-Goals

- Manual point clicking in the studio.
- Style changes, upscaling or recolouring beyond the filled regions.
- Covers: `--cleanup` applies to story pages only (titled covers carry painted lettering LaMa could damage).

## Success Criteria

- On a page with a flagged artifact, the region is filled and pixels outside the masks are unchanged.
- A region over the size cap is skipped and reported.
- Revert restores the exact previous bytes and removes the `.bak`.
- Right after cleanup the page's plan, cuts and rendered sections show stale through the picture sha256 check, with the refresh command, exactly as after a repaint. The script, its hash and approvals are not invalidated.
- Unit tests with fake Codex, Segmenter and Inpainter cover region validation, caps and compositing.

## Architecture Decisions

- [Where a cleaned picture lives](../../adr/026-cleaned-picture-overwrite.md) — overwrite `page_NN.png` and keep `.bak`, so every edition and the video see one picture.

## Out of Scope

Human review of Codex's flagged regions before the fill, automatic re-planning after cleanup, batch cleanup across a book beyond a page loop.
