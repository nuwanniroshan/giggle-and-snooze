# ADR-010-layer-animation-renderer: Local renderer for layer motion and effects

**Status:** Accepted
**Date:** 2026-11-10
**Feature:** 010-animate-pages

## Context

Spec 009 produces, per story page, a motion plan plus cut RGBA layers and a filled background. Spec 010 must turn them into an animated clip for each page section of the video:

- layer motion: sway, bob, rotate, breathe, shift around a pivot;
- parallax between layers by planned depth;
- procedural effects generated locally: rain, snow, fireflies, falling leaves, water ripple, smoke or mist, glow.

Constraints from the PRD:
- Local and offline.
- Repeatable: same inputs give the same frames.
- Unit-testable pure logic, as the project rules require.
- Fits 16 GB.
- A full episode renders in at most about 2× today's time (pilot: 8.2 min for 15:15).
- Prefer existing libraries.

No existing Python library covers cutout animation plus rain, smoke and water together, so the choice is about which rendering foundation we build the small animator on.

## Options Considered

### Option A: NumPy + OpenCV (CPU)

OpenCV `warpAffine` for layer transforms and `remap` with procedural noise for water and smoke distortion. Our own particle systems for rain, snow, fireflies and leaves are drawn with NumPy and OpenCV and alpha-composited with NumPy. Raw frames are piped to FFmpeg.

**Pros:**
- Same stack as today (NumPy, Pillow, FFmpeg); OpenCV is a small, mature, permissive (Apache-2.0) wheel.
- Deterministic with seeded RNGs, so frames repeat exactly.
- Motion and particle maths are pure functions, which are easy to unit-test.
- No GPU or OpenGL context problems in a headless studio subprocess.

**Cons:**
- CPU-bound: per-frame compositing at 1080p must be measured, with mitigation by rendering at picture resolution, caching static layers and vectorising particles.
- Water and smoke look simpler than shader versions.

### Option B: ModernGL (GLSL shaders, offscreen)

**Pros:** fastest; shader water and smoke look best.
**Cons:**
- Shader code is harder to test and maintain.
- macOS OpenGL is deprecated (capped at 4.1), and an offscreen context from a subprocess is a risk.
- GPU float differences can break exact repeatability.

### Option C: Blender headless (bpy)

**Pros:** full particle, smoke and compositing systems.
**Cons:**
- A separate large app (GPL), slow start and render, and the cutout workflow is not native.
- Reported Metal render crashes on Apple Silicon.
- The hardest to test.

### Option D: skia-python (SkSL runtime effects)

**Pros:** fast 2D compositing with shader effects, CPU or GPU.
**Cons:** less common in Python, runtime-effect support is recent (m132), and it is a new stack for the team. The effects are still our own code.

## Decision

**We chose Option A (NumPy + OpenCV)**, because it meets the repeatability and unit-test requirements, fits the existing stack and headless studio, and needs only one small, mature extra library (`opencv-python-headless`).

Implementation **starts with a spike**: one pilot page section at the plan maximum of effects (3: for example rain, water ripple and fireflies), with 4 layers and parallax, rendered at 1920×1080 / 30 fps, then piped into the existing camera, caption and section path. Pass criteria:

1. The whole section pipeline takes ≤ 0.85 s of wall time per second of video (today ~0.42 s/s, so 2× budget).
2. Peak memory of the render process ≤ 3 GB above today's render.
3. Two runs give identical frames.

If (1) fails after reasonable optimisation (picture-resolution rendering, static-layer caching, vectorised particles), move only the hot effects to **Option B (ModernGL)** behind the same effect interface, recording this by superseding this ADR.

## Consequences

- New dependency: `opencv-python-headless` (pinned).
- The animator is a set of pure functions (`motion(t) → transform`, `particles(seed, t) → sprites`, `distort(region, t) → map`), plus a thin frame loop. Effects are pluggable by name, so the plan schema (spec 009) and the renderer share one effect vocabulary.
- Effects stay deliberately simple and stylised; that suits picture-book art.
- The FFmpeg camera, captions and transitions (spec 003) consume the animated clip instead of the still picture. Per-frame camera cost is new and is part of the spike's measurement.
