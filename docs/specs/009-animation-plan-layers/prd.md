# PRD: Motion plan and layer cutting for story pages

**Date:** 2026-11-10
**Feature:** 009-animation-plan-layers

## Problem

In the storytime video every story page is a still picture, moved only by the FFmpeg camera. The producer wants the pictures themselves to move: characters swaying, antennae wiggling, leaves drifting, rain, water and smoke. Regenerating art or video with AI for every frame is slow, costly and not repeatable, and a local language model is too slow on this machine. So the AI should only **plan** the animation, once, when the picture is painted. Local Python then **cuts** the picture into layers (this spec) and **animates** them (spec 010).

## Users & Context

**User:** the producer, on the M1 Pro (16 GB), using the CLI and the studio.

**Touches:**
- `video_pipeline/illustrate.py` (spec 006: Codex paints pictures).
- The book folder layout (spec 005).
- The studio (spec 004).
- Spec 008 (pictures may be square or 16:9).

**Feeds:** spec 010 (animator and render), which consumes the plan and the cut layers.

**Approach agreed in brainstorm:**
1. The AI motion plan is written right after each picture is painted.
2. Layer boxes from the plan are cut locally with SAM 2.
3. The holes are filled with LaMa.
4. The producer checks plans and layers in the studio before anything moves.

(The depth-parallax-only approach was rejected; see ADR-009-depth-parallax-engine.)

## Goals

- **Motion plan per picture.** For every story page picture, a hand-editable JSON plan `books/<slug>/animation/page_NN.json`, committed like `episode/`. It lists:
  - **layers:** name, a short description, a box (and optional include and exclude points) in picture pixels, z-order, depth (0 = far … 1 = near, used for parallax), pivot point;
  - **motions** per layer, from a fixed vocabulary: `sway`, `bob`, `rotate`, `breathe`, `shift`, each with amplitude, period and phase;
  - **effects**, from a fixed vocabulary: `rain`, `snow`, `fireflies`, `falling_leaves`, `water_ripple`, `smoke`, `mist`, `glow`, each with an optional region (box or layer name), intensity and colour hints.
  - **picture identity:** the sha256, width and height of the picture the plan was made for.
- **Validated plans.** Every plan is validated against one schema (pydantic) on save and on load, and fails fast with the file and field name.
  - Values are clamped to gentle limits set in config: maximum rotation, maximum shift as a fraction of the picture, minimum period, maximum layer count (such as 8) and maximum effects (such as 3).
  - Unknown motion or effect names are rejected.
  - Every box, point and pivot must lie inside the picture's width and height; the validating tool is given the real size and rejects anything outside it, which catches coordinates returned at the wrong scale.
  - A plan whose recorded picture sha256 doesn't match the current picture is **stale**. The CLI and the studio show it as stale, and `cut_layers` refuses a stale plan.
- **Planned when painted.** `illustrate` writes the plan straight after it paints each **page** picture (not covers). Codex is given the painted picture (attached with `-i`; this needs an image option added to the MCP-tool Codex runner in `agents.py`), the page's text and illustration prompt, and the schema, and saves through a validating tool (as `save_episode_script` does).
  - If planning fails after a successful paint, the picture is kept, the failure is reported, and `plan` retries it later.
  - Repainting a picture (`illustrate --force`) makes its old plan stale and writes a new plan for the new picture. The previous plan file is kept as `page_NN.json.bak`, so hand edits are not lost.
- **Plan existing pictures.** A `plan` command (CLI and studio) writes plans for existing pictures, such as both current books. Existing plans are kept unless forced.
- **Cut layers locally.** `cut_layers --book <slug>` turns each plan into:
  - one RGBA PNG per layer (SAM 2 mask from the box and points, with a slightly feathered edge);
  - `background.png`, the picture with every cut-out area filled (LaMa);
  - a `layers.json` manifest (layer files, offsets, sizes, and the cut hash).

  These go in `books/<slug>/build/layers/page_NN/`. The work runs offline after a one-time `download` command. It is cached by picture hash + a hash of the **cut-relevant plan fields only** (layer names, boxes, points) + model ids, so unchanged pages are skipped and editing a motion or effect never re-runs SAM or LaMa. The manifest records this cut hash.
  - The manifest holds only cut results (files, offsets, sizes). z-order, depth, pivot, motions and effects are always read from the plan at render time, so the plan is the one source of truth.
- **Studio check.** Per page, show the picture with the plan's boxes and the cut masks overlaid, the filled background, and the layer list with motions and effects. Plan and cut can be started from the studio, one job at a time.
- **Square and 16:9.** Both work (all coordinates are in picture pixels).

## Non-Goals

- Rendering any motion or effect (spec 010).
- A visual plan editor in the studio (the producer edits the JSON; the studio only displays it).
- Cutting the intro/outro nook scenes and the covers.
- Asking the image generator to paint separate layer images.
- Local language or vision models.

## Success Criteria

- Every story page picture of both current books has a plan that validates, produced by the `plan` command with no hand-editing required to pass validation.
- On the pilot book, the producer judges at least 80% of planned layers cleanly cut in the studio overlay: no missing body parts, no big background chunks. Every remaining layer can be fixed by editing its box or points in the plan and re-running `cut_layers`.
- With all layers removed, the filled background shows no blank or obviously duplicated areas at normal viewing.
- `cut_layers` takes ≤ 10 s per page after model load and ≤ 4 GB peak memory, and a second run with unchanged inputs does no model work.
- Newly painted books get plans automatically during `illustrate`, with no extra step.
- Missing models, or running offline before `download`, fails with a clear message naming the command. An invalid plan fails with the file and field.

## Architecture Decisions

- [Local runtime for cutting layers](../../adr/009-layer-segmentation-runtime.md) — SAM 2.1 via Apple's Core ML conversions (box and point prompts) plus LaMa ONNX on onnxruntime for the background fill, each gated by a spike. Fallbacks: mlx-sam, then the official sam2; OpenCV inpaint for thin masks.
- [Engine for depth parallax on story pages](../../adr/009-depth-parallax-engine.md) — **Rejected**: depth-only parallax can't give the planned layer motion and effects the producer wants.

Inline decisions (not ADR-worthy):
- **Plan provider:** Codex through the ChatGPT app, as in the other stages (no new provider). Codex sees the painted picture and returns the plan through a validating MCP tool; no fallback between providers.
- **Plan location:** `books/<slug>/animation/` (committed, hand-editable), next to `episode/`. Cut layers are generated output in `build/layers/` (git-ignored).
- **One effect and motion vocabulary**, defined once in code and shared with spec 010's renderer, so a plan can never name something the renderer can't draw.
- **Process isolation:** `cut_layers` runs as its own stage (subprocess), so Core ML and onnxruntime memory never overlaps voice synthesis or rendering.

## Out of Scope

- Animating layers, parallax and effects, and putting them in the video: spec 010.
- Blinks or extra painted frames (for example closed eyes) — possible later.
- Depth estimation models (rejected direction).
- Cloud segmentation or layer-decomposition services (Qwen-Image-Layered via an API).
- Background music (separate spec).
