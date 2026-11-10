# ADR-009-layer-segmentation-runtime: Local runtime for cutting layers (masks + background fill)

**Status:** Accepted
**Date:** 2026-11-10
**Feature:** 009-animation-plan-layers

## Context

Each picture's motion plan (written by the AI when the picture is painted) names the layers to animate. For each layer it gives a rough box and optional points in picture pixels. A local script must turn these into:

1. a clean mask per layer (cartoon art: bold shapes, soft painted edges, characters, props, foliage, water), and
2. a background with the holes behind the cutouts filled, so moving a layer never reveals a blank or duplicated area.

Constraints from the PRD:
- Runs locally on the M1 Pro (16 GB), offline after a one-time download. No local language model.
- Prefer existing libraries.
- Weights allow commercial use.
- Seconds per page, not minutes, and the result is cached.
- Runs as its own stage, one at a time, like voice synthesis.

Two sub-decisions: the **segmentation** model and runtime, and the **inpainting** model and runtime.

## Options Considered

### Segmentation

#### Option S1: SAM 2.1 via Apple's Core ML conversions (`apple/coreml-sam2.1-*`, Apache-2.0) + `coremltools`

**Pros:**
- Official Apple conversions (image encoder, prompt encoder and mask decoder) that run on the Neural Engine or GPU.
- Box and point prompts are supported.
- Small (tiny, small and base-plus sizes), fast, no torch.

**Cons:**
- macOS only (fine: the producer's machine is the target).
- We write the small pre- and post-processing glue (resize to 1024, prompt coordinate scaling, mask upsampling).
- `coremltools` prediction API versions must be pinned.

#### Option S2: Official `sam2` package (PyTorch, MPS)

**Pros:** the reference implementation with the most examples; `SAM2ImagePredictor` takes boxes and points directly.
**Cons:** pulls in torch (large install). MPS support has had gaps (some ops fall back to CPU). Heavier memory.

#### Option S3: `mlx-sam` (community MLX port)

**Pros:** MLX is already in the venv (through `mlx-audio`), and it supports box and point prompts plus quantised variants.
**Cons:** community-maintained, young, and with less certainty about upkeep and accuracy parity.

#### Option S4: rembg / BiRefNet (salient-object matting, onnxruntime)

**Pros:** very clean edges for one main subject; ONNX.
**Cons:** no prompts. It finds "the" subject, not a named layer such as "left antenna" or "front leaves", so it can't cut several planned layers.

### Inpainting

#### Option I1: LaMa (big-lama) ONNX on onnxruntime (already in the venv)

**Pros:** strong at filling large holes with plausible texture; ~1 s per 512² tile on an M1 Pro reported; Apache-2.0; no new runtime.
**Cons:** fixed 512×512 input, so crop around the hole, resize and paste back. It can blur fine painted texture.

#### Option I2: Diffusion inpainting (SD-based)

**Pros:** higher-fidelity fills.
**Cons:** large models, slow on 16 GB, non-deterministic, and likely to change the art style. That conflicts with "seconds per page" and repeatability.

#### Option I3: OpenCV `inpaint` (Telea / Navier-Stokes)

**Pros:** zero new dependencies, instant.
**Cons:** smears across large holes. It is only acceptable for thin masks.

## Decision

**Segmentation: Option S1 (SAM 2.1 Core ML, small model by default)**, because it is an official, Apache-2.0, prompt-driven segmenter that runs fast on Apple hardware without adding torch. That meets the "local, seconds per page, existing library" goals. **Inpainting: Option I1 (LaMa ONNX)**, because it fills large holes well using onnxruntime, which the project already has. **OpenCV inpaint (I3)** is the fallback for thin masks or a missing model.

Implementation **starts with a spike** on three pilot pages with planned boxes. All of these must hold:

1. Mask quality: in the studio overlay, the producer judges each planned layer cleanly cut (no missing limbs, no large background chunks).
2. ≤ 10 s per page for all layers, plus the fill, after the models are loaded.
3. Peak memory ≤ 4 GB.
4. Runs offline from a subprocess.

If S1 fails 1–4, try **S3 (mlx-sam)**, then **S2 (official sam2)**, recording the switch by superseding this ADR.

## Consequences

- The licence of the exact LaMa ONNX conversion that is pinned must be checked and recorded (the original big-lama weights are Apache-2.0, but third-party exports can carry their own terms). If its terms are unclear, export the ONNX ourselves from the original weights.
- New dependencies: `coremltools` and the pinned model files (downloaded once by a `download` command, like voices). LaMa ONNX is downloaded the same way.
- The masks are only as good as the plan's boxes. Box accuracy from the image model is a known risk, so the plan stays hand-editable and the studio shows the boxes and masks for checking.
- Layers and the filled background are cached by picture hash, cut-field hash (layer names, boxes, points) and model ids, so spec 010's animator only reads files and doesn't depend on these runtimes.
- If superseded, only the cut step changes. The plan format and the layer outputs stay the same.
