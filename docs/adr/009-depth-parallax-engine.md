# ADR-009-depth-parallax-engine: Engine for depth parallax on story pages

**Status:** Rejected (2026-11-10, producer: depth parallax alone won't give the planned layer motion and effects wanted; replaced by AI-planned layers, see 009-layer-segmentation-runtime and 010-layer-animation-renderer)
**Date:** 2026-11-10
**Feature:** 009-animation-plan-layers (originally 009-page-parallax, dropped)

## Context

Story pages in the video are still pictures moved only by the FFmpeg camera (push, pan, Q&A zoom). The producer wants the pictures themselves to feel alive. The first step is 2.5D depth parallax: estimate depth for each picture, then move near and far parts of the picture by different amounts. Constraints from the PRD:

- Runs locally on the M1 Pro (16 GB), offline after a one-time model download, repeatable for the same inputs. No local language model; no cloud video generation.
- Works on every existing picture (square 1024×1024 and the new 16:9 pictures from spec 008) with no per-page manual preparation.
- Prefer an existing, maintained library over building our own.
- A full episode renders in at most about twice today's time (pilot: 8.2 min for a 15:15 episode).
- Model weights must allow commercial use (YouTube).

## Options Considered

### Option A: DepthFlow (BrokenSource, PyPI `depthflow` 1.0.1, Aug 2026)

Python tool that turns an image into a parallax video: a GLSL ray-marching shader on the GPU (ShaderFlow/OpenGL), frames piped to FFmpeg. Bundles depth estimators, with Depth Anything V2 as the default.

**Pros:**
- Ready-made, actively released, scriptable from Python (`DepthScene` with an `update()` hook for camera state over normalised time).
- Ray-marching handles the area behind near objects better than a plain pixel warp (fewer stretched edges).
- Any resolution and fps, supersampling, depth caching built in.
- Depth Anything V2 Small is Apache-2.0.

**Cons:**
- AGPL-3.0. We only run it locally and publish videos, not the software, so this is acceptable, but the code must never be shipped to others as part of a hosted service without honouring the AGPL.
- Pulls in torch and transformers for the estimator, plus an OpenGL stack. That is a large dependency footprint.
- Its docs say nothing about macOS or Apple Silicon. macOS OpenGL is deprecated (capped at 4.1), and headless rendering under a studio subprocess is untested.
- The 1.0 API is new: animation presets are "planned", so we write our own `update()` motion.

### Option B: Own warp: Depth Anything V2 Small (ONNX) + numpy remap

Estimate depth with the ONNX export of Depth Anything V2 Small through onnxruntime (already in the venv), then render each frame by displacing pixels by depth × camera offset (numpy backward warp), piping raw frames to FFmpeg.

**Pros:**
- Small footprint: no torch, no OpenGL. Fully under our control and unit-testable.
- Licence: Apache-2.0 model, our own code.

**Cons:**
- We build and maintain the renderer ourselves, against the stated preference.
- A plain warp smears or tears at depth edges unless motion is kept very small. Fixing that means inpainting or layered depth, which is more code.
- CPU per-frame warp at 1080p must be measured to see whether it fits the time budget.

### Option C: Layer decomposition (Qwen-Image-Layered or SAM 2 + LaMa) + cutout animation

Split each picture into RGBA layers (Qwen-Image-Layered, Apache-2.0; or SAM 2 masks with LaMa inpainting), then move layers in Python.

**Pros:**
- Enables true character motion (bob, sway) later.

**Cons:**
- Qwen-Image-Layered is a ~20B diffusion model: too large for 16 GB locally, and cloud-only means a paid API per picture.
- SAM 2 + LaMa needs a per-page plan of what to cut, and edge and seam quality varies.
- Much more than this step needs. This is the planned follow-up if parallax looks too weak, not the first step.

### Option D: Cloud image-to-video (Veo, Sora, Kling and similar)

**Pros:** The richest motion.
**Cons:** Not repeatable, characters and art style drift, paid per clip, and it conflicts with "animate locally with a script".

## Decision

**We chose Option A (DepthFlow)**, because it is an existing, maintained library that produces higher-quality parallax than a plain warp (PRD goal: pages feel alive without visible artefacts), it uses the Apache-2.0 Depth Anything V2 Small model, and it is scriptable for repeatable batch renders.

Because its macOS support is undocumented, implementation **starts with a spike**. It renders one pilot page section end to end (depth → DepthFlow clip → backdrop + camera + captions) at 1920×1080 / 30 fps on the M1 Pro, started as a subprocess with no terminal or window (as the studio does). Pass criteria, all required:

1. It runs headless from a subprocess on macOS, with no window and no user interaction.
2. The whole section pipeline takes **≤ 0.85 s of wall time per second of video** (today ~0.42 s/s; 2× budget), and depth estimation takes ≤ 10 s per picture.
3. Peak memory of the parallax process is **≤ 6 GB**.
4. Looking at the clip, the producer sees no tearing or smeared edges at the configured strength.

If any criterion fails, we switch to **Option B**, keeping the same interface (picture + depth + settings → clip). That switch is recorded by superseding this ADR. If Option B also fails the quality criterion at a strength the producer finds worthwhile, stop and revisit the approach with the producer (most likely moving to cutout layers, Option C) instead of shipping weak parallax.

## Consequences

- Easier: per-page parallax with no manual preparation, so depth maps can be reused later (for example to decide where cutout layers go in Option C).
- Harder: dependency weight (torch, OpenGL). DepthFlow and its estimator run in their own subprocess. Their packages go in an optional `parallax` dependency group, pinned in `uv.lock`, so memory for voice synthesis and the base install are not affected.
- The parallax engine must sit behind a small interface (`picture, depth, settings, duration → clip`) so Option B can replace it without touching the render timeline.
- AGPL: fine for local production use. Revisit if the studio is ever offered as a hosted service.
- If superseded, the depth cache format (greyscale PNG keyed by picture hash + model) should stay, so Options B and C can reuse it.
