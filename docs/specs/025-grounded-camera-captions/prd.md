# PRD: Grounded camera targets and caption placement

**Date:** 2026-11-14
**Feature:** 025-grounded-camera-captions

## Problem

The Q&A camera zooms to an (x, y, zoom) the script writer guessed from the page's prompt text, so it often misses the subject the kids are talking about. Captions are a fixed bottom band that can cover the subject or the action.

## Users & Context

The book maker running the studio or the CLI for the storytime video. Touches `episode.py` (`zoom_target`), `timeline.py` (camera, captions, cache keys), `frames.py` (caption drawing), spec 009 plan layers and masks, and the SAM runtime in `cut_layers.py`.

## Goals

- A separate stage, `video_pipeline.ground_camera --book <slug> [--page N] [--force]`, runs after `cut_layers` (SAM runs alone, sequential, like the other model stages) and writes `books/<slug>/camera/page_NN.json` (committed, hand-editable): subject name, subject bbox, grounded Q&A target (x, y, zoom) and caption band (`top` or `bottom`), keyed by the picture sha256.
- **Subject source:** for pages with a Q&A, Codex (picture attached, plan layer names listed, saved through a validating MCP save tool) names the Q&A subject, either as an existing plan layer name or as a short phrase plus a box on the picture.
- **Matching rule:** a layer name uses that layer's mask. A phrase plus box runs SAM once with the box as prompt through the existing `Segmenter` interface. The target centres on the mask bbox, with zoom chosen to keep the bbox in frame, clamped to the existing 1–2 range.
- **Caption band:** per page, score each band by the share of mask pixels under it; masks are the Q&A subject mask when present plus all cut plan-layer masks when the page is cut. The band with less coverage wins, ties and pages with no masks keep `bottom`. No separate detail measure.
- Render prefers the grounded file over `ffmpeg_camera.qa_zoom_target` when its picture sha256 matches; the camera file feeds section cache keys, nothing else changes them.
- The script, its hash, review marks and voice approvals are untouched, so already-approved books gain grounded cameras without re-review.

## Non-Goals

- Free-position captions or caption restyling.
- A SAM pass over every page independent of a named subject.
- New models or changes to still-versus-animated rendering.

## Success Criteria

- On pilot pages with a Q&A, the target centre lies inside the named subject's mask bbox and the bbox stays in frame.
- No caption band covers more mask pixels than the opposite band would.
- With no grounded file, a stale one, or a failed grounding, render uses the old guessed target and `bottom` band and the log says why.
- Running the stage does not change `episode_script.json` or invalidate review or voice approval.
- Unit tests cover mask-to-target math, band scoring and file staleness without real models; unchanged pages keep their cache keys.

## Architecture Decisions

None need an ADR. The stage and file are new but additive and easy to remove; logic is pure functions, SAM stays behind `Segmenter`, and the separate file avoids touching the script hash.

## Out of Scope

Moving caption position within a band, per-card placement, saliency-driven camera on narration-only pages, band choice from a detail or edge measure.
