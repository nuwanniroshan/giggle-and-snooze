# PRD: Animated story pages in the video

**Date:** 2026-11-10
**Feature:** 010-animate-pages

## Problem

Spec 009 gives every story page a motion plan and cut layers, but the video still shows a still picture. The producer wants each page to move gently: layers sway and bob, near layers drift against far ones (parallax), and rain, water, smoke and similar effects play. All of it is generated locally by a script and repeatable.

## Users & Context

**User:** the producer, rendering with `render_episode` (CLI) or the studio on the M1 Pro (16 GB).

**Depends on:** spec 009 (plan + `build/layers/page_NN/` with `layers.json`, RGBA layers, `background.png`).

**Touches:**
- Spec 003 render: `timeline.py`, `frames.py`, `render_episode.py`, `config/render.json`.
- Spec 001 episode script: an optional per-page override.
- Spec 004 studio.
- Spec 008 (square or 16:9 pictures).

## Goals

- **Animate each page.** Each story page section is rendered as an animated clip of the picture: background + layers composited by z-order. Each layer:
  - applies its planned motions around its pivot;
  - shifts slightly with depth (parallax: near layers move more, as a gentle periodic drift);
  - shows the planned effects (`rain`, `snow`, `fireflies`, `falling_leaves`, `water_ripple`, `smoke`, `mist`, `glow`), generated procedurally in its region.
- **Exact length, smooth start.** The clip has exactly the section's frame count. Motion starts from the at-rest picture, so the first frame matches the still. Transitions are built from the clip's real first and last frames, so there is no jump.
- **Existing render is kept.** The camera moves (push, pan, Q&A zoom targets), the backdrop for square pictures, the captions, the transitions, the ambience and the audio keep their spec 003, 007 and 008 meaning and limits. They run on the animated clip instead of the still (the camera works per frame).
- **Repeatable.** Every random element uses a seed derived from the book, the page and the effect, so the same inputs give identical frames.
- **Settings.** In `config/render.json`, an `animation` block holds:
  - on/off;
  - a global intensity multiplier;
  - the parallax strength and period;
  - per-effect defaults such as particle counts and speeds.

  Animation is **on** by default (pages with no plan simply stay still).

  Each page in `episode_script.json` can have an optional `animation` override: an intensity number, or `false` for a still page. This override is excluded from the script hash (as `ambience` is), so changing it never invalidates the voice approval or the review mark.
- **Geometry from the plan.** z-order, depth, pivot, motions and effects come from the spec 009 plan; the layer manifest only supplies the cut files.
- **Graceful fallback.** A page with no plan, no cut layers, a stale plan, or a manifest whose cut hash doesn't match the plan's current cut fields renders as a still, as today. The render log and the studio name the page and the reason (for example "re-run cut_layers").
- **Caching.** A section's cache key includes the plan hash, the layer manifest hash, the animation settings and the renderer version. Unchanged pages are reused. Turning animation off (or a still-fallback page) yields today's cache keys exactly: `RENDER_VERSION` and `timeline.section_key` stay unchanged for still sections, and the animation fields (settings plus the page's override) are added only to animated sections' keys, so a page with override `false` keeps today's key.
- **Script hash.** `file_hash.script_sha256` is generalised from the single `ambience` key to a set of excluded per-block keys (`ambience`, `animation`). The raw-bytes shortcut still applies when none of them is present, so existing approvals stay valid. `contract` validates `animation` (a number in the allowed range, or `false`).
- **Preview.** A preview command renders one full page section (animation + camera + captions) so the producer can judge it quickly; the studio can start it and play the result.

## Non-Goals

- Producing plans or cutting layers (spec 009).
- Animating the intro/outro nook scenes or the covers.
- Character lip-sync, blinks, or new painted frames.
- Physically accurate simulation; effects are stylised and gentle.

## Success Criteria

- On the pilot book, every page with a plan shows its planned motions and effects in the preview. The producer judges the motion gentle, with no visible seams, holes or tearing at normal viewing.
- No jump into or out of any page section or transition.
- A full pilot episode renders in at most about 2× today's time (8.2 min → ≤ ~16.5 min), with up to 10% overrun accepted. Past that, lower the defaults or move hot effects to the GPU (ADR-010).
- Peak memory of the render process stays ≤ 3 GB above today's.
- Two renders with the same inputs give identical frames.
- Animation off (globally, or `false` for a page) is byte-identical to today's output for those sections, with unchanged cache keys.

## Architecture Decisions

- [Local renderer for layer motion and effects](../../adr/010-layer-animation-renderer.md) — NumPy + OpenCV CPU compositor, with procedural particles and distortion effects piped to FFmpeg, gated by a spike. Fallback: move hot effects to ModernGL shaders behind the same interface.

Inline decisions (not ADR-worthy):
- **Integration point:** the animator renders the bare picture area (not the backdrop canvas) as a clip. The section render composites it over the existing blurred backdrop (square) or uses it full-frame (16:9), then applies the camera and captions. The spike measures this whole path.
- **Effect vocabulary** is shared with spec 009's plan schema (one definition in code).

## Out of Scope

- Plan generation and layer cutting (spec 009).
- Background music (separate spec).
- Studio controls for tuning motion (the producer edits the plan JSON or the config).
- GPU shaders unless the spike shows the CPU path is too slow.
