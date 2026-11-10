# Spike report: SAM 2.1 (Core ML) and LaMa (ONNX) on the M1 Pro (Phase 0, ADR-009 gate)

Date: 2026-11-10. Script: `docs/specs/009-animation-plan-layers/spike_cut.py` (throwaway, kept for the record). Proposed config: `config/animation.json (models block)` (also at the end of this file).

## Verdict (gate)

| Criterion (ADR-009) | Result |
|---|---|
| Runs offline from a subprocess | PASS. Models load from local files, no network call (the downloads happened earlier, once). |
| <= 10 s per page, all layers plus fill, after load | PASS with `CPU_AND_GPU`: about 3.0 s per page (encoder 0.24 s, 4 prompts + decoders about 0.4 s, fill about 2.4 s). The first page of a fresh process is about 3.4-3.6 s. |
| Peak memory <= 4 GB | PASS. Peak RSS 0.9-1.0 GB for the whole process (1.3 GB with `CPU_ONLY`). |
| Bit-exact across runs | PASS. Two separate processes give identical sha256 for every mask and every background (also pass 1 against pass 2 inside one process). Not identical between `CPU_AND_GPU` and `CPU_ONLY` (float differences), so the compute unit is part of the model id for the cut hash. |
| Masks a human would accept | PASS (producer, 2026-11-10: "good enough, continue"). My own read of the overlays is in "Mask quality" below: 8 of 12 layers look cleanly cut, the 3 snails need occlusion handling, and Clara on page 12 loses part of the scroll. |

Core ML SAM 2.1 does work, but **not with `ComputeUnit.ALL` or `CPU_AND_NE`** (see "Compute units"). That is the main finding.

## Environment

- Apple M1 Pro, 16 GB, macOS 26.5.2 (arm64), Python 3.12.13 (uv venv).
- `coremltools` 8.3.0 (the newest 8.x; installs fine on Python 3.12), `onnxruntime` 1.31.1 (providers: CoreML, Azure, CPU), numpy 2.5.4, Pillow 13.0.0.
- `coremltools` prints "You will not be able to run predict()... weight.bin doesn't exist" for packages read straight from the Hugging Face cache. The cache holds symlinks into `blobs/`, and Core ML's compiler fails on symlinked packages. The spike copies the three `.mlpackage` folders with `cp -RL`. The download command must store real files (copy or `local_dir`, not the symlinked cache).
- Xcode is not installed, so `xcrun coremlcompiler` is unavailable. `coremltools` compiles an `.mlpackage` at load time (0.1-0.7 s on CPU/GPU).

## Models, revisions and licences (NFR-6)

| Role | Repo | Revision (commit SHA) | Files | Size | Licence | Source |
|---|---|---|---|---|---|---|
| Segmenter | `apple/coreml-sam2.1-small` | `883f5787eb0be35ce6965907a8bc1f5320a5a02e` | `SAM2_1SmallImageEncoderFLOAT16.mlpackage`, `SAM2_1SmallPromptEncoderFLOAT16.mlpackage`, `SAM2_1SmallMaskDecoderFLOAT16.mlpackage` | 78 + 2 + 9.8 MB | Apache-2.0 (model card front matter `license: apache-2.0`) | https://huggingface.co/apple/coreml-sam2.1-small |
| Fill | `Carve/LaMa-ONNX` | `c3c0c9e468934d62e79c329e35d82dd09ff8c444` | `lama_fp32.onnx` | 198 MB | Apache-2.0 (model card front matter `license: apache-2.0`) | https://huggingface.co/Carve/LaMa-ONNX |

Licence chain checked on GitHub (SPDX via the API): the SAM 2.1 weights come from `facebookresearch/sam2` (Apache-2.0), converted with the `huggingface/segment-anything-2` fork, branch `coreml-conversion` (the model card names it). LaMa comes from `advimman/lama` (Apache-2.0); Carve's export code is `Carve-Photos/lama` (a fork, Apache-2.0). The Carve model card states Apache-2.0 and says the export is "identical" to the original model, so the ADR consequence (check a third-party export's terms) is satisfied: no extra terms. The card describes `lama_fp32.onnx` as the recommended file, exported with `torch.onnx.export`, opset 17, fixed 512x512 input. SAM2 Studio (the reference Swift app, https://github.com/huggingface/sam2-studio, Apache-2.0) was read for the pre- and post-processing below.

## Exact I/O (read from the models)

### Core ML (read with `ct.models.MLModel(...).get_spec().description`)

`SAM2_1SmallImageEncoderFLOAT16.mlpackage`
- input `image`: image type, 1024 x 1024, RGB (pass a PIL RGB image to `predict`).
- outputs (float16 tensors, returned as float32 arrays): `image_embedding` [1,256,64,64], `feats_s0` [1,32,256,256], `feats_s1` [1,64,128,128].

`SAM2_1SmallPromptEncoderFLOAT16.mlpackage`
- inputs: `points` [1,N,2] (pass float32), `labels` [1,N] (pass int32); N is flexible, 1..16.
- outputs: `sparse_embeddings` [1,N+1,256] (an extra padding point is appended; a box alone, N=2, gives [1,3,256]), `dense_embeddings` [1,256,64,64].

`SAM2_1SmallMaskDecoderFLOAT16.mlpackage`
- inputs: `image_embedding` [1,256,64,64], `sparse_embedding` [1,M,256] with M in the enumerated set 2..15, `dense_embedding` [1,256,64,64], `feats_s0` [1,32,256,256], `feats_s1` [1,64,128,128].
- outputs: `low_res_masks` [1,3,256,256] (float logits, positive = inside), `scores` [1,3] (predicted IoU, 0..1).
- Consequence: at most 14 prompt points per layer (the box counts as two), because M = N + 1 <= 15.

### ONNX (`lama_fp32.onnx`, read with `InferenceSession`)

- inputs: `image` ['batch',3,512,512] float32; `mask` ['batch',1,512,512] float32.
- output: `output` ['batch',3,512,512] float32.
- Checked by running it: `image` is RGB scaled 0..1; `mask` is 1 where the hole is; `output` is RGB scaled **0..255** (min 0.0, max 255.0 on a test), and pixels outside the mask come back unchanged (max difference 0.0).

## Pre- and post-processing (verified against SAM2 Studio and by running)

- Image: the encoder contains the scale and bias ops (mean/std), so give it a plain 8-bit RGB image stretched to 1024 x 1024 (no aspect-preserving pad; the pilot pictures are square).
- Prompt coordinates are in the 1024 x 1024 space (x * 1024 / picture width, y * 1024 / picture height). Labels: 0 exclude (background), 1 include (foreground), 2 box top-left, 3 box bottom-right. A box is two points with labels 2 and 3, and include/exclude points are appended after them.
- Decoder returns three candidate masks. SAM2 Studio takes the argmax of `scores`. Upsample the 256 x 256 logits to the picture size (bilinear, PIL mode `F`) and threshold at 0.
- LaMa: crop a square around each hole region with margin (never smaller than 512 px, never larger than the picture), resize to 512, run, resize back, and paste only the pixels inside the (dilated) hole. Regions are merged when they touch after growing by the margin; on all three pilot pages that merged everything into one region (about the whole picture, so it is resized 2x down and back).

## Compute units (the important finding)

| Compute units | Load | Encoder output | Result |
|---|---|---|---|
| `ALL` | **368 s** every process (ANE compile of a fresh temp model each time, no cache hit) | `image_embedding` range -5780..4392 (overflow) | **Broken**: scores about 1e-6, masks are tiny noise (snail 0.14% of the picture). Pages came out at about 4.5 s, but with garbage masks. |
| `CPU_AND_NE` | 324 s | range -5596..4468 | **Broken**, same as `ALL`. The Neural Engine's float16 path overflows in the encoder. |
| `CPU_AND_GPU` | **5.7-6.3 s** for all three models | range -2.7..2.4 | **Good**: scores 0.9+ for good masks. Deterministic, about 3.0 s per page, peak 0.9-1.0 GB. **Chosen.** |
| `CPU_ONLY` | 5.4-5.6 s | range -2.7..2.4 | Good and deterministic, 3.2 s per page, peak 1.3 GB. The fallback. |

The `E5RT encountered an STL exception... conv_transpose output_shape` line printed at exit is harmless noise from the decoder's flexible shapes.

LaMa on onnxruntime: `CPUExecutionProvider` about 2.4 s per page (one 512 tile). The `CoreMLExecutionProvider` took 44-70 s per page (and 23 s to load) with 1.6 GB peak, so it is rejected. Choice: CPU.

## Timings (CPU_AND_GPU, ONNX on CPU, 3 pilot pages x 4 layers, `--select large --clean`)

| | Page 1 | Page 5 | Page 12 |
|---|---|---|---|
| encoder (once per page) | 0.23 s | 0.23 s | 0.23 s |
| 4 x prompt + decoder + upsample + clean | 0.38 s | 0.37 s | 0.43 s |
| fill (dilate, merge, LaMa tile, paste) | 2.4 s | 2.4 s | 2.5 s |
| total per page | 2.96 s | 3.00 s | 3.13 s |

- Two fresh processes: totals 9.64 s and 9.37 s for the first pass over three pages (the first page is about 0.4-0.6 s slower: warm-up), 9.12 s and 9.14 s for the second pass.
- Model load: 5.7-6.3 s (Core ML 0.2 s for the prompt and decoder, about 1 s for the encoder, the rest is ONNX session creation of the 198 MB file and imports). That is outside the 10 s per-page budget and is paid once per `cut_layers` process.
- Peak RSS (`resource.getrusage`, bytes on macOS): 0.92 GB and 1.00 GB for the whole process (0.5-0.6 GB right after load).
- Time per layer is small: the cost is the fill. Layers add about 0.1 s each, so 8 layers per page stay near 3.5 s.

## Determinism

- `CPU_AND_GPU`: run 1 and run 2 (separate processes) match bit for bit in all 12 masks and all 3 backgrounds; pass 1 and pass 2 inside one process also match.
- `CPU_ONLY`: same, separately.
- `CPU_AND_GPU` and `CPU_ONLY` differ from each other. So the compute unit must be part of the model id in the cut hash (FR-9), and the adapter must always use the configured unit.

## Mask quality (my own read; the producer's verdict is below)

Method: boxes written by hand after looking at each picture (4 per page, in `spike_cut.py`), best of three masks, upsampled and thresholded at 0.

Findings, in the order I would act on them:

1. **Picking the highest-score mask is not enough for big objects.** The snail's best-score mask was a patchy "part" mask (score 0.940) while a near-complete mask scored 0.929. Rule used for the final run: the largest mask among those within 0.05 of the best score. Then a small clean-up (close gaps 7 px, fill interior holes; 0.1 s per page in numpy) turns the patchy snails into solid shapes.
2. Small and medium objects are good from a box alone: acorn mailbox, front mushroom, fern, lily pad, book, leaf sprig, and Clara (see list below). Edges are soft and clean enough for a 2 px feather.
3. Occlusion: the snail's mask also takes Clara's torso when Clara sits on it (pages 1 and 5) and drops her where it does not. The plan needs z-order-aware handling (for example subtract the nearer layer from the farther one, or exclude points on Clara) and the fill must cover both. Include and exclude points on the snail did not clearly help on this model (`--points` run), so boxes plus the rule above are the baseline.
4. Thin or detached bits stay behind in the background fill: Clara's magnifying glass on page 5, the snail's eye tips on page 12, the thread on page 5, some hair strands. Growing the hole (dilate 6 px used) to 12-16 px, or a second pass that dilates only the fill hole and not the layer, will likely fix most of them.
5. The fill of a very large hole (snail plus Clara, about 35% of the picture) is a smooth blurry plausible landscape, not a detailed background. That is fine for a background that is mostly covered by the animated layers, but it shows if the snail moves far. Clara alone or small props fill well.
6. The merge of regions into one big tile means the 512 tile sees the whole picture at half resolution. Small holes (mailbox, mushroom) are better filled from their own crops (the merge uses a 96 px gap; a smaller gap would give more tiles at about 2.4 s each, still within budget for up to about 3 tiles).

My read of the cutouts per layer (final run, `--select large --clean`):

| Page | Layer | Looks |
|---|---|---|
| 1 | clara | clean (book and hands included) |
| 1 | snail | good shape, includes Clara's lower body (occlusion) |
| 1 | acorn_mailbox | clean |
| 1 | front_mushroom | clean |
| 5 | clara | clean, but the magnifier is missing (stays in the background) |
| 5 | snail | good, includes Clara's skirt (occlusion), eye tips partly cut |
| 5 | fern_left | clean, a few stray specks |
| 5 | lily_pad | clean |
| 12 | clara | good body, scroll and hands partly missing |
| 12 | snail | solid, includes some of the cottage roof edge fringe, eye tips missing |
| 12 | book | clean |
| 12 | leaf_sprig_right | clean |

That is 8 of 12 clean (counting Clara on page 5 as clean apart from the magnifier), 3 snails acceptable only after occlusion handling, and Clara on page 12 not clean. NFR-7 asks for 80% (about 10 of 12); this is below it today, so the producer's judgement and some tuning (hole growth, occlusion subtraction, point or box edits) decide the gate.

## Producer verdict on masks: accepted (2026-11-10)

The producer judged the final-configuration masks good enough to build on. The 80% clean-layer target (NFR-7) is still checked on the whole pilot book in Phase 6, after occlusion handling (front-most layer owns each pixel) is in the cutter.

Overlay and cutout files (not committed; in the session scratchpad). Each overlay is the picture with each layer tinted in its own colour and the box outline; each cutout sheet shows every layer on a checkerboard; each background is the LaMa fill.

Final configuration (`CPU_AND_GPU`, `--select large --clean`):
- `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/final_run1/overlay_page_01.png`, `overlay_page_05.png`, `overlay_page_12.png`
- `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/final_run1/cutouts_page_01.png`, `cutouts_page_05.png`, `cutouts_page_12.png`
- `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/final_run1/background_page_01.png`, `background_page_05.png`, `background_page_12.png`
- `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/final_run1/hole_page_NN.png` (the dilated union that was filled), `result.json` (timings, scores, hashes)
- `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/final_run2/` is the second process (identical hashes).

Comparison runs:
- Box only, best score (the first run): `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/gpu_cpu_run1/` (shows the patchy snail).
- Largest mask, no clean-up: `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/box_large/`.
- Largest mask plus include/exclude points: `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/pts_large/`.
- `CPU_ONLY`: `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/cpuonly_run1/`, `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/cpuonly_run2/`.
- LaMa on the CoreML execution provider: `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/gpu_coremlep/`.
- `ComputeUnit.ALL` (garbage masks): `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike/all_cpu_a/`.

(`/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike` = `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/11701621-30a1-48a5-8636-6ba824a75caf/scratchpad/spike`)

## What Phase 4 needs from this

- Config `models` block below. The adapter must copy the `.mlpackage` folders out of the Hugging Face cache as real files (FR-11, AC-4.9).
- Always load with `compute_units=CPU_AND_GPU`; never `ALL`. Add a self-check after the encoder (embedding range within about +-10, best score of a trivial box above 0.5) so a bad compute unit fails loudly rather than giving garbage masks.
- Mask rule: largest mask within 0.05 of the best score, then close 7 px and fill interior holes (crop to the bbox first; run it in numpy).
- Fill: dilate the union by 6 px or more (12-16 recommended, see finding 4), crop around each merged region, resize to 512, run, paste inside the hole only.
- The compute unit and the rule parameters belong in the model id for the cut hash.
- Not verified: other sizes of SAM 2.1 (tiny, base-plus), ONNX LaMa at other resolutions, behaviour with more than 4 layers per page, non-square pictures.

## Proposed `models` block

```json
{
  "segmenter": {
    "repo": "apple/coreml-sam2.1-small",
    "revision": "883f5787eb0be35ce6965907a8bc1f5320a5a02e",
    "encoder": "SAM2_1SmallImageEncoderFLOAT16.mlpackage",
    "prompt_encoder": "SAM2_1SmallPromptEncoderFLOAT16.mlpackage",
    "decoder": "SAM2_1SmallMaskDecoderFLOAT16.mlpackage",
    "size": 1024,
    "compute_units": "CPU_AND_GPU",
    "io": {
      "encoder": {
        "input": {"name": "image", "kind": "rgb_image", "size": [1024, 1024]},
        "outputs": ["image_embedding", "feats_s0", "feats_s1"],
        "shapes": {"image_embedding": [1, 256, 64, 64], "feats_s0": [1, 32, 256, 256], "feats_s1": [1, 64, 128, 128]}
      },
      "prompt_encoder": {
        "inputs": {"points": {"dtype": "float32", "shape": "[1, N, 2]"}, "labels": {"dtype": "int32", "shape": "[1, N]"}},
        "outputs": ["sparse_embeddings", "dense_embeddings"],
        "max_points": 14
      },
      "decoder": {
        "inputs": ["image_embedding", "sparse_embedding", "dense_embedding", "feats_s0", "feats_s1"],
        "outputs": ["low_res_masks", "scores"],
        "shapes": {"sparse_embedding": "[1, N+1, 256], N+1 in 2..15", "low_res_masks": [1, 3, 256, 256], "scores": [1, 3]}
      }
    },
    "prompt": {
      "coordinate_space": "1024x1024 (picture pixels * 1024 / picture size, x and y separately)",
      "labels": {"exclude": 0, "include": 1, "box_origin": 2, "box_end": 3}
    },
    "preprocess": {"normalization": "inside the encoder (scale and bias ops); pass a plain 8-bit RGB image", "resize": "stretch to 1024x1024, bilinear"},
    "postprocess": {
      "mask_logits": "low_res_masks[0, i], 256x256, float logits",
      "upsample": "bilinear to picture size, then threshold at 0.0",
      "select": "largest mask among those with score >= best score - 0.05",
      "clean": {"close_px": 7, "fill_interior_holes": true}
    },
    "license": "Apache-2.0",
    "source": "https://huggingface.co/apple/coreml-sam2.1-small"
  },
  "fill": {
    "repo": "Carve/LaMa-ONNX",
    "revision": "c3c0c9e468934d62e79c329e35d82dd09ff8c444",
    "file": "lama_fp32.onnx",
    "size": 512,
    "providers": ["CPUExecutionProvider"],
    "io": {
      "inputs": {
        "image": {"dtype": "float32", "shape": "[batch, 3, 512, 512]", "range": "0..1 RGB"},
        "mask": {"dtype": "float32", "shape": "[batch, 1, 512, 512]", "range": "1 = hole, 0 = keep"}
      },
      "outputs": {"output": {"dtype": "float32", "shape": "[batch, 3, 512, 512]", "range": "0..255 RGB"}}
    },
    "tile": {"dilate_px": 6, "margin_px": 96, "min_crop_px": 512, "paste": "only inside the hole"},
    "license": "Apache-2.0",
    "source": "https://huggingface.co/Carve/LaMa-ONNX"
  }
}
```
