# Spike 010: NumPy + OpenCV page animator on the M1 Pro (Phase 0)

**Date:** 2026-11-10
**ADR:** [010-layer-animation-renderer](../../adr/010-layer-animation-renderer.md)
**Code:** [spike_animate.py](spike_animate.py) (throwaway, kept)
**Verdict:** **PASS.** All three gates pass. Keep Option A (NumPy + OpenCV); no ModernGL fallback is needed.

## What was rendered

- Book `the-snail-mail-express`, **page 14**, the real section `page-14`: 1682 frames (56.07 s), built with `timeline.build_sections` from the real script and track timings, the same way `render_episode._load_inputs` does it. The section has a pan, a Q&A zoom at frame 1057 and 12 caption cards.
- Why page 14: it has the most layers (6). All 6 move: four sways, one breathe and the eye-stalk sways, each with depth parallax (depth 0.35–0.6). Its plan also already has `water_ripple` (box 100,777–708,1008, intensity 0.15) and `fireflies` (box 423,155–785,367, intensity 0.12, `#ffe8a1`).
- `rain` was added over the whole picture at intensity 0.5, which gives 3 effects at once (the plan maximum).
- **Worst case** (`--worst`): all three effects at intensity 1.0.
- Seeds: `sha256(f"{slug}:{page}:{index}:{type}")[:8]` → `np.random.default_rng`. No global RNG and no time-based randomness.

### Frame path (final configuration: `animated --dirty`)

1. **Picture resolution (1024², float32, premultiplied):**
   - Restore the dirty rects from the cut background.
   - Warp each moving layer into its transformed bbox (`cv2.warpAffine`, linear) and draw it "over" with cv2 arithmetic.
   - `water_ripple`: `cv2.remap` of its box, with a sine displacement that tapers to 0 over 24 px at the box edges.
   - Per rect: `cv2.addWeighted(original, 1−e, composite, e)` with the 1 s smoothstep ease, then `cv2.convertScaleAbs(alpha=255)` into a persistent uint8 picture. This picture starts as the original image.
2. **Particles on the uint8 picture:**
   - Draw particles after the lerp, with opacity × ease.
   - Rain: `cv2.polylines(..., LINE_AA, shift=4)` into a mask, then blend only the `np.nonzero` pixels.
   - Fireflies: a precomputed Gaussian sprite added per dot, with saturation.
3. **Resize and place:** `cv2.resize(..., INTER_LANCZOS4)` to 1080², then paste at x = 420 into a copy of the cached `compose_canvas` backdrop.
4. **Camera:** `cv2.warpAffine(INTER_LINEAR | WARP_INVERSE_MAP)` with the crop from `timeline.camera_at`. zoompan's integer/even clamping is emulated (see Facts).
5. **Encode:** rgb24 goes to `ffmpeg` through `Popen` stdin (a writer thread with a 3-frame queue). The graph is `section_filtergraph` with `scale×4 + zoompan` replaced by `_to_bt709_yuv(1920,1080)`, then the existing caption overlays and `_OUTPUT_FORMAT`, encoded with `ENCODE_ARGS`.

## Gate results

**Measurement conditions.** The machine was busy throughout: load average 7–16 on 8 cores, from another agent's pytest, the studio and a browser. All numbers are therefore pessimistic. Still and animated runs were interleaved so they are comparable.

| Gate | Target | Result | Verdict |
|---|---|---|---|
| 1. Speed, whole section | ≤ 0.85 s/s | **0.58–0.65 s/s** typical; 0.495 at lower load. Worst case 0.667 / 0.767 / 0.824 | PASS (worst case has a thin margin under heavy load) |
| 2. Peak memory | ≤ +3 GB over still | **+0.1–0.2 GB** (see below) | PASS |
| 3. Repeatability | identical frames | identical (hashes below) | PASS |
| Frame 0 vs still frame 0 | MAD ≤ 2/255 | **1.21/255** whole frame, 1.71 in the picture area (p99 7, max 32) | PASS |

### Speed

**Still path, same section** (spike `still` = `render_episode.section_command` on the same canvas and captions): 0.421, 0.426, 0.427, 0.440, 0.442, 0.464, 0.473, 0.539 s/s. Median about 0.44; ADR baseline about 0.42.

**Animated, final configuration** (all 1682 frames):

- With in-loop sha256 of every frame for the determinism check, which adds about 5.5 s per section: 0.643, 0.660, 0.662, 0.672, 0.677, 0.681 s/s.
- Without hashing: 0.644 (load 13.7) and 0.495 (an earlier, quieter run).
- Expected in production: about **0.55–0.65 s/s, 1.3–1.5× the still path.**
- At 1.5× on every page, a full episode with every page animated stays well within the NFR-1 2× budget, because intros, outros and transitions are unchanged.

**Breakdown** (run `final2`, seconds per section and ms per frame):

| Stage | s / section | ms / frame |
|---|---|---|
| composite (restore rects + 6 layers) | 7.9 | 4.7 |
| effects (ripple 1.1 ms, rain 3.0 ms, fireflies 0.1 ms) | 9.3 | 5.5 |
| lerp + to uint8 (rects only) | 1.7 | 1.0 |
| resize 1024 → 1080 (LANCZOS4) | 8.5 | 5.0 |
| camera warp 1920×1080 (linear) | 4.7 | 2.8 |
| write + hash (write alone ≈ 0.5 s) | 5.6 | 3.3 |
| **wall** | **37.9** | |

- FFmpeg runs at the same time. The writer thread was blocked about 6 s in total, so the Python producer and FFmpeg are close to balanced.
- FFmpeg-only floor: the same graph and encode fed a static rgb24 frame ran at 11.9 s, or 0.21 s/s. Real motion costs x264 more than that.
- CPU time: animated about 161 s user against 101 s for the still path.

### What each optimisation bought (same page, under similar load)

| Step | s/s | Note |
|---|---|---|
| Naive: full-frame float32 every frame, numpy "over" blend, effects in float | **1.156** | composite 24.2 s, effects 28.9 s: FAIL |
| + **dirty rects** (float work only in each moving layer's envelope and the ripple box; particles drawn on uint8 touching only covered pixels) | **0.792** | effects 28.9 → 7.7 s |
| + **cv2 arithmetic for the layer blend** (`split`/`merge`/`multiply`/`add`, not numpy broadcasting on strided views; 0.34 ms vs 5.7 ms for the willow layer) | **0.495–0.68** | composite 24.3 → 5.9–7.9 s. The biggest single win. |
| cv2 blend **without** dirty rects | 1.063 | Dirty rects are required. |
| Static-layer cache (no motion and depth 0 → baked into the base once) | n/a | Implemented; a no-op on page 14 (every layer moves). |
| Writer thread (queue of 3) vs a blocking write in the loop | ~5–10% | 0.625 without the thread in the same batch where others ran 0.57–0.59 |
| Resize LANCZOS4 → CUBIC → LINEAR | 0.569–0.589 vs ~0.6 | Resize 8.5 → 4.9 → 2.8 s, but the wall gain is in the noise. **Keep LANCZOS4** (closest to the still's Pillow LANCZOS). |
| Fused: one warp straight from 1024² to the output (no resize) | 0.569 | The warp doubles (2 full warps) and frame 0 is slightly worse. Not worth it. |
| "Front-res": resize only the dirty rects with `warpAffine(LANCZOS4)`, draw particles after the resize | **1.04–1.17** (worse) | `warpAffine` with LANCZOS4 is about 10× slower per pixel than `cv2.resize`. With LINEAR rects: 0.589; CUBIC: 0.642. Little gain and seam risk. Dropped. |
| Camera warp INTER_CUBIC instead of LINEAR | **0.865** (fails) | Warp 4.7 → 18.3 s, for a frame-0 gain of only 1.30 → 1.25 MAD before encoding. Keep LINEAR. |
| `cv2.setNumThreads(2 / 4)` | no real change | The macOS wheel uses **GCD** (see Facts): only 0/1 (serial) has an effect, and serial is ~5× slower. Leave the default. |

The optimisations that mattered: **dirty rects + uint8 particles**, and **cv2 arithmetic instead of numpy broadcasting**. Everything else is noise or worse.

### Memory

**Peak RSS by process** (`resource.getrusage`; `/usr/bin/time -l` agrees):

| Process | Still path | Animated |
|---|---|---|
| Python | 144 MB | 284–309 MB |
| ffmpeg | 705–810 MB | 606–737 MB |
| **Total** | **≈ 0.9 GB** | **≈ 1.0 GB** |

- Net change: **+0.1–0.2 GB**.
- ffmpeg is slightly *smaller* on the animated path because the 7680×4320 zoompan input is gone.
- The real render process also holds every track's samples, which is the same in both modes.

### Repeatability

| What | Runs | Result |
|---|---|---|
| sha256 of all 1682 rgb24 frames piped to FFmpeg, final defaults | 2 | `860bfaa8726b687aa9429c2987f7742b5e5d693faf2ea1a0d68c1499340ceef3` (identical) |
| sha256 of all 1682 rgb24 frames, first defaults | 4 | `eb44fcb90df7…` (identical) |
| sha256 of the decoded clip, final defaults | 2 | `fdf30790ff6c3015b7251a4091d4f1844178110d744cab7f93b488b29890772c` (identical) |
| sha256 of the `.mp4` files | 2 | `70756d424fc1c35fc902f8eb78072249eb5d95fdfb3308ef32090791649bfca6` (byte-identical; x264 is deterministic with threads) |
| Serial (`setNumThreads(1)`) vs GCD-parallel, 120 frames | 2 | `96087e6d…` (identical: OpenCV results don't depend on thread count) |

### Frame 0

| Comparison | MAD (/255) | Detail |
|---|---|---|
| Animated frame 0 vs still frame 0 (both decoded to rgb24 with ffmpeg) | **1.21** | Picture area 1.71, p99 7, max 32 |
| The spike's still clip vs the real `build/video/sections/page-14.mp4` | 0 | Identical; the baseline is the real section |
| Pre-encode cv2 frame 0 vs decoded still frame 0 | 1.30 (linear), 1.25 (cubic) | Best at offset (0, 0) in a ±0.5 px sub-pixel scan; ±0.25 px gives 1.66 |

- The camera geometry matches exactly: the best sub-pixel offset is (0, 0).
- The rest of the difference comes from zoompan's ×4-bicubic-then-sws-downscale resampling against cv2 bilinear, plus x264 noise in both clips (CRF 18). In short test clips (5–60 frames) the same comparison ranged 1.19–1.56, depending on how x264 coded the first I-frame.

## Producer's first look (spike author's notes; the producer still needs to watch)

- **Clip:** `page_14_animated.mp4` (plan intensities + rain 0.5). **Worst case:** `page_14_animated_worst.mp4`. Contact sheets of 6 frames (t = 0, 2, 8, 20, 34, 50 s): `page_14_contact.png` and `page_14_contact_worst.png`. Kept outside the repo; regenerate with `spike_animate.py`.
- Layer sway is gentle: no visible seams, holes or tearing at 1:1 crops. The ease-in from the still is invisible.
- **Rain:** the first defaults (opacity 0.35, length 34 px) were nearly invisible. The tuned values below read as light stylised rain: visible on dark areas, subtle on the light sky.
- **Fireflies:** good at 0.12 and lovely at 1.0.
- **Water ripple:**
  - The plan's 0.15 is subtle (0.75 px).
  - At 1.0 (5 px) the **frog**, which stands inside the water box, wobbles visibly.
  - This is a plan issue. Spec 009's planner should keep characters out of distortion boxes, or the renderer could mask out layer areas.
- **File size:** the animated clip is 20.9 MB against 12.1 MB for the still (+73%); the worst case is 31.7 MB. Rain and ripple are hard for x264, so the episode file will grow.

## Recommended per-effect defaults (for `config/render.json` → `animation.effects`)

**Units, at picture resolution (1024 px on the shorter side; scale by side/1024):**
- `count`: particles at intensity 1. Effective count = `round(count × intensity)`, minimum 1.
- `speed`: picture heights per second for falling or rising particles, or Hz / cycles per second for distortion.
- `size`: px (streak length, dot or flake size, wavelength or noise scale).
- `strength`: opacity 0–1 for particles and haze, px displacement for `water_ripple`, additive gain for `glow`.

| Effect | count | speed | size | strength | colour | Notes |
|---|---|---|---|---|---|---|
| `rain` | 260 | 1.6 | 40 | 0.6 | `#dfe6f0` | Tested. Slant 0.18; length ×U(0.6, 1); speed ×U(0.8, 1.2); opacity = strength × (0.5 + 0.5 × intensity); 1 px AA line. |
| `snow` | 160 | 0.12 | 3.0 | 0.85 | `#ffffff` | Soft dots (Gaussian sprite like fireflies), sideways wobble ±8 px at 0.3–0.6 Hz, size ×U(0.6, 1.4). |
| `fireflies` | 40 | 0.25 | 7.0 | 0.9 | `#ffe08a` | Tested. Wander radius = speed × 0.1 × side; pulse 0.25–0.6 Hz, squared sine; additive. |
| `falling_leaves` | 14 | 0.08 | 14 | 1.0 | `#c8862e` | Small tinted ellipse sprites, rotate ±40°/s, sway ±20 px at 0.2–0.4 Hz. |
| `water_ripple` | 0 | 0.5 | 38 | 4.0 | `#ffffff` (unused) | Tested at 5.0, which is a little strong at intensity 1, so **4.0** is recommended. Displacement px = strength × intensity; 24 px edge taper. |
| `smoke` | 6 | 0.03 | 80 | 0.35 | `#d9d4cc` | Rising puffs: blurred blobs, scale 1 → 2 and fade over their life; or a remap of a soft noise field. |
| `mist` | 0 | 0.01 | 220 | 0.25 | `#e8eef2` | Low-frequency noise band drifting sideways; alpha = strength × intensity × noise; static noise precomputed once. |
| `glow` | 0 | 0.2 | 60 | 0.25 | `#fff2c4` | Additive blurred soft disc or box; pulse 0.85–1.0 at `speed` Hz; precompute the blurred mask once. |

Animation block settings used: `ease_in_s` 1.0, `parallax_max_shift` 0.006 (6 px at depth 1 on 1024), `parallax_period_s` 9.0. Both read well.

## Facts Phase 2/4 implementers must know

1. **zoompan clamping, matched exactly.** zoompan works at the ×`upscale` size:
   - `cw = int(iw/z)`, `ch = int(ih/z)` (z clipped to [1, 10]);
   - `x = int(clip(cx·iw − iw/z/2, 0, iw − cw)) & ~1`, and likewise `y`. The `& ~1` is the yuv420p chroma alignment.
   - Divide by `upscale` to get 1× canvas pixels. The inverse map for `warpAffine` is `src = x + (u + 0.5)·cw/W − 0.5` (pixel centres).
   - A sub-pixel scan confirms (0, 0) is the best offset. See `zoompan_crop` and `camera_matrix` in the spike.
   - Using `camera_at` directly, without the int and even rounding, would put the camera up to about 0.5 px away from the still.
2. **Colour handling.**
   - Pipe **rgb24** (full range by definition) and convert in the graph with the existing `_to_bt709_yuv(W, H)` (`out_color_matrix=bt709:out_range=tv,format=yuv420p`), then the same overlays and `_OUTPUT_FORMAT` (setparams tags).
   - ffprobe of the animated clip shows `yuv420p, tv, bt709`, the same as the still.
   - Do not add `-color_range` to the rawvideo input.
3. **OpenCV threads on macOS.**
   - The wheel's parallel framework is **GCD**. `setNumThreads(n > 1)` is ignored, `setNumThreads(0 or 1)` makes it serial (resize 12 ms vs 2.6 ms), and `getNumThreads()` still reports 8 after `setNumThreads(1)`.
   - Don't tune it, and don't use it as a cache-key input; results are thread-independent.
4. **Numpy broadcasting on strided views is the hot trap.** `region *= 1 - warped[..., 3:4]` on a `dst[y0:y1, x0:x1]` view cost 5.7 ms; cv2 `split`/`merge`/`multiply`/`add` on contiguous planes cost 0.34 ms. Use cv2 for every per-pixel blend.
5. **The background equals the original outside the layer boxes.** Verified on page 14: max diff 0 outside the union of layer bboxes.
   - The dirty-rect design depends on this. The persistent uint8 picture starts as the original, and only the moving layers' **section envelopes** (the union over all frames of each transformed bbox, precomputed) and the distortion boxes are recomputed.
   - Frame 0 is therefore exactly the original picture.
   - Phase 2 should check this per page when it loads the cut (cheap). If a cut ever differs outside the boxes, fall back to full-frame compositing.
6. **Ease and first frame.**
   - Lerp only inside the rects with `addWeighted(original, 1−e, comp, e)`, non-destructively (rects overlap), then `convertScaleAbs(alpha=255)`, which rounds and saturates. Particles draw after the lerp with opacity × e.
   - At e = 0 the picture is bit-identical to the original. The remaining frame-0 difference is only the cv2 resize/warp against Pillow + zoompan, plus encoding.
7. **Draw particles on uint8, not on a full-frame float buffer.** Full-picture rain on a float buffer was the single largest cost in the naive version (about 17 ms/frame). Drawing on uint8 and touching only covered pixels costs about 3 ms.
8. **Pipe hygiene.** `Popen([...], stdin=PIPE, stderr=PIPE)` with `-loglevel error` and a writer thread. Close stdin, then read stderr, then `wait()`. The frame count comes from `-frames:v section.frames`; ffprobe confirmed 1682 frames, the same as the still.
9. **Don't hash frames in the production loop.** sha256 of 6 MB per frame costs about 3 ms per frame (~0.1 s/s). Keep it for tests only.
10. **`uv add` held the uv lock for about 7 minutes** while it downloaded the 44 MB wheel, which blocks other agents' `uv run`.
