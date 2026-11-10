# Implementation Plan: Spec 010 – animated story pages in the video

**Spec:** docs/specs/010-animate-pages/spec.md (Approved)
**Decision:** docs/adr/010-layer-animation-renderer.md (Accepted: NumPy + OpenCV CPU, spike-gated)
**Saved to:** `docs/specs/010-animate-pages/plan.md` (after approval)

## Context

Spec 009 gives each story page a motion plan and cut layers (`books/<slug>/animation/page_NN.json`, `build/layers/page_NN/`). The video still shows each page as a still picture. Spec 010 renders each eligible page as an animated picture inside its existing section. The camera, captions, transitions and audio stay the same. With animation off, or for a page with no plan, the output is byte-identical to today's.

What the code has today:
- **Section clips (`render_episode.py`):**
  - `_render_section` writes a 1920×1080 canvas PNG (`frames.compose_canvas`: blurred backdrop plus the picture `ImageOps.contain`'d and centred).
  - FFmpeg then runs `scale ×4 → zoompan d=frames` on that one still, overlays the caption PNGs (`overlay … enable=between`) and encodes exactly `section.frames` frames.
  - `section_key` (timeline.py:659) hashes the picture sha, frames, captions, camera, backdrop, caption style and `_frame_settings` (with `RENDER_VERSION = 3`).
- **Transitions** grab the real last and first frames of the clips (`frame_grab_command`), so AC-2.2 needs no change.
- **`timeline.camera_at(cam, frame) -> (z, cx, cy)`** is the exact pure-Python twin of `zoompan_expr`.
- **`_load_inputs`** computes every key and is shared by `render_video` and `render_check_detail`. Status (FR-14) follows from the keys.
- **Spec 009 helpers to reuse:**
  - `motion_plan.plan_state`, `load_limits`, `MotionPlan`, `EFFECTS`, `MOTIONS`;
  - `cut_layers.read_config`, `cut_hash`, `model_ids`, `layers_dir`, `MANIFEST`;
  - studio `books._cut_settings` / `_cut_state` (move the shared "cut matches the plan" logic into `cut_layers.cut_state(folder, plan, config) -> str` and use it from both places).
- **Hash and contract:**
  - `file_hash.script_sha256` excludes only `ambience`;
  - `contract._page_errors` / `_bookend_errors` are where an `animation` check goes.
- **Not installed:** OpenCV (`opencv-python-headless`) is not in the venv.

**Key design choice (the camera on animated sections).**
- zoompan on a moving input would need `d=1` and a ×4 upscale of every frame (~33 Mpx per frame). That is likely too slow for the 0.85 s/s gate.
- So animated sections apply the camera on the CPU instead: `cv2.warpAffine` with the same `timeline.camera_at` path, which has sub-pixel precision and needs no upscale. Finished 1920×1080 frames are piped (rawvideo) into FFmpeg, which only overlays the captions and encodes, using the same `ENCODE_ARGS` and colour tagging.
- Still sections keep today's zoompan path, keys and bytes.
- The spike (Phase 0) measures this. If it fails the gate, ADR-010's fallback applies (ModernGL for the hot effects), recorded by superseding the ADR.

**First frame = still (AC-1.4).**
- The cut's outward feather means "background + layers at rest" differs slightly from the original along layer edges.
- So the animated picture area at time t is `lerp(original, composite_t, e(t))`, with the ease-in `e` going 0 → 1 over `ease_in_s` (motion amplitudes are scaled by `e` too). Frame 0 is therefore the original picture exactly.

## Global constraints

- **Code layout:**
  - All animation maths (motions, parallax, ease, seeds, effects, compositor, camera warp) is pure in `video_pipeline/animate.py` and unit-tested on tiny arrays.
  - Eligibility and keys go in `animate.py`; rendering glue goes in `render_episode.py`.
- **One vocabulary:** `motion_plan.MOTIONS` / `EFFECTS`; the config's per-effect defaults must cover exactly `EFFECTS`.
- **Seeds:** `int.from_bytes(sha256(f"{slug}:{page}:{index}:{type}").digest()[:8])` → `np.random.default_rng(seed)`. Never global RNG, never time.
- **Stills unchanged:** `RENDER_VERSION` and `section_key` stay unchanged for still sections. Animated key = `_hash({"still": section_key(...), "plan": sha256(plan file bytes), "manifest": sha256(layers.json bytes), "settings": asdict(cfg.animation), "scale": scale, "animation_version": ANIMATION_VERSION})`.
- **No new subprocess shell use:** frames are piped with `subprocess.Popen([...], stdin=PIPE)` (argument list); stderr is read on failure.
- **Memory:** one section at a time. Layer RGBA arrays are float32 at picture resolution (≤ 8 layers × 1024² × 4 × 4 B ≈ 134 MB). Frames are streamed, never held in memory.
- **Tests:** unit tests use no real FFmpeg (the `FakeFrameRunner` consumes the frames). Real FFmpeg tests carry the `ffmpeg` marker. Tools: Ruff, pathlib, type hints, atomic JSON.
- **New dependency:** `opencv-python-headless` (pinned, ADR-010).

Components (Simplicity Gate: 3):
1. `video_pipeline/animate.py` (pure animator plus eligibility).
2. Render integration (`timeline.py` settings, `render_episode.py` animated path and preview, `contract.py`, `file_hash.py`, `config/render.json`).
3. Studio additions.

---

## Phase 0: Spike – ADR-010 gate on the M1 Pro

**Implements:** NFR-1, NFR-2, NFR-3 (gate) | **Files:** `pyproject.toml` (+`opencv-python-headless`), `docs/specs/010-animate-pages/spike.md`, `docs/specs/010-animate-pages/spike_animate.py` (throwaway, kept)

Prove the CPU path before building on it.
- `uv add "opencv-python-headless>=4.10,<5"`.
- `spike_animate.py`:
  - Load pilot page 1's plan and cut.
  - Composite 4 layers with sway/bob/breathe plus depth parallax, and 3 effects: rain (particles), water_ripple (`cv2.remap` with a sine/noise displacement map) and fireflies (additive soft dots).
  - Composite at picture resolution, `contain` it into the cached canvas backdrop, apply the camera with `cv2.warpAffine` from `timeline.camera_at`, and pipe rgb24 to FFmpeg with the existing caption overlays and `ENCODE_ARGS`.
  - Render one real page section (its frame count from the pilot's tracks).
- **Measure:**
  - wall seconds per video second for the whole section path;
  - peak RSS (`resource.getrusage`) against today's render of the same section;
  - sha256 of two runs' decoded frames (identical);
  - frame 0 against today's still section frame 0 (mean absolute difference ≤ 2/255).
- **Gate:** ≤ 0.85 s/s, ≤ 3 GB above today, identical frames. If it fails after the listed optimisations (picture-resolution compositing, caching static layers and the backdrop, vectorised particles, `cv2.setNumThreads` tuning), stop and supersede ADR-010 with ModernGL for the hot effects.
- Record timings, memory, the hashes and the producer's first look at the clip in `spike.md`.
- Commit: `chore(animation): spike the NumPy + OpenCV page animator on the M1 Pro (Phase 0)`

## Phase 1: Settings, page override and script hash

**Implements:** FR-8, FR-9, FR-10 | **Satisfies:** AC-3.1 (config half), AC-3.5, AC-3.6 | **Files:** `config/render.json`, `video_pipeline/timeline.py`, `video_pipeline/contract.py`, `video_pipeline/file_hash.py`, tests `test_timeline.py`, `test_contract.py`, `test_file_hash.py`

```python
# timeline.py
@dataclass(frozen=True)
class EffectDefaults:
    count: int = 0; speed: float = 0.0; size: float = 0.0; strength: float = 0.0; colour: str = "#ffffff"

@dataclass(frozen=True)
class AnimationSettings:
    enabled: bool = True
    intensity: float = 1.0
    ease_in_s: float = 1.0
    parallax_max_shift: float = 0.006      # fraction of the picture's shorter side
    parallax_period_s: float = 9.0
    override_min: float = 0.25
    override_max: float = 2.0
    effects: dict[str, EffectDefaults] = field(default_factory=dict)
# RenderConfig gains `animation: AnimationSettings = AnimationSettings()`; load_render_config parses
# the block (missing block → defaults) and raises ConfigError naming e.g. "animation.effects.smog"
# for an effect not in motion_plan.EFFECTS, a missing effect, or intensity/override range ≤ 0.
```
`config/render.json` gains the `animation` block with defaults for all 8 effects (counts, speeds, sizes, strengths, colours; tuned values come from the spike).

```python
# contract.py – called from _page_errors; _bookend_errors rejects any "animation" key
def _animation_errors(where: str, block: dict, lo: float = 0.25, hi: float = 2.0) -> list[str]:
    if "animation" not in block:
        return []
    v = block["animation"]
    if v is False or (isinstance(v, int | float) and not isinstance(v, bool) and lo <= v <= hi):
        return []
    return [f"{where}: animation must be false or a number from {lo} to {hi} (got {v!r})"]
```
`validate_script` keeps its signature. `render_episode` re-checks every page's override against `cfg.animation.override_min/max` and raises `RenderError` naming the page if the config narrows the range.

```python
# file_hash.py
EXCLUDED_BLOCK_KEYS = ("ambience", "animation")   # per-block keys that never change approvals
...
if not any(k in b for b in blocks for k in EXCLUDED_BLOCK_KEYS):
    return sha256_file(path)                      # raw-bytes shortcut, unchanged
for block in blocks:
    for k in EXCLUDED_BLOCK_KEYS:
        block.pop(k, None)
```
Tests first:
- **Config:** loading with no `animation` block gives defaults; an unknown effect or a missing effect raises a `ConfigError` naming the field.
- **Contract:** `animation: 1.5` and `false` pass; `0`, `3`, `"high"` and `true` fail naming the page; an intro `animation` key fails.
- **Script hash:**
  - adding, changing or removing a page `animation` gives the same hash as without it;
  - a script with neither key hashes as the raw bytes;
  - `ambience` behaviour is unchanged;
  - an approved fixture stays approved after adding `animation: false` (through `synthesize_voices.approval_problems`).
- Commit: `feat(animation): animation settings, page override and script hash (Phase 1)`

## Phase 2: The animator – motions, parallax, effects, compositor (pure)

**Implements:** FR-1–FR-7 | **Satisfies:** AC-1.1–1.5, AC-3.2, AC-3.3, AC-5.4 (unit level) | **Files:** `video_pipeline/animate.py`, `tests/test_animate.py`

```python
# animate.py (core API)
ANIMATION_VERSION = 1

def ease(t: float, ease_in_s: float) -> float:            # smoothstep 0→1; 1 when ease_in_s == 0
def seed_for(slug: str, page: int, index: int, kind: str) -> int:
def layer_matrix(layer: Layer, t: float, short_side: int, e: float, scale: float,
                 parallax: tuple[float, float]) -> np.ndarray:   # 2x3 affine, picture pixels
    # sway: rot ±A·sin(2π(t/P+φ)); rotate: A·(1−cos(2π(t/P+φ)))/2 ≥ 0; bob: dy=A·short·sin(...);
    # shift: (dx,dy)=normalised(direction)·A·short·sin(...); breathe: s=1+A·sin(...);
    # rotations/scale around the pivot; parallax dx = depth·max_shift·short·sin(2πt/period);
    # every amplitude × e × scale. Composed in the order breathe → rotate/sway → bob/shift → parallax.

@dataclass
class PageAnimation:                     # everything needed to draw one page, loaded once per section
    original: np.ndarray                 # H×W×3 float32 0..1
    background: np.ndarray               # H×W×3
    layers: list[tuple[Layer, np.ndarray, tuple[int, int]]]   # plan layer, RGBA float32, offset; ascending z, plan order for ties
    effects: list["EffectState"]
    settings: AnimationSettings
    scale: float
    fps: int

def load_page_animation(plan: MotionPlan, layers_folder: Path, picture: Path,
                        settings: AnimationSettings, scale: float, slug: str, fps: int) -> PageAnimation:
def render_frame(anim: PageAnimation, frame: int) -> np.ndarray:   # H×W×3 uint8, picture area
    t = frame / anim.fps; e = ease(t, anim.settings.ease_in_s)
    out = anim.background.copy()
    for layer, rgba, offset in anim.layers:   # cv2.warpAffine(rgba, M, (W,H), INTER_LINEAR, BORDER_CONSTANT 0) then alpha-over
    ...                                       # effects (water_ripple remaps the composite in its region first, then overlay effects)
    out = anim.original * (1 - e) + out * e   # frame 0 == original exactly
    return (out.clip(0, 1) * 255 + 0.5).astype(np.uint8)
```
Effects (`EffectState` per plan effect, with a seeded `rng`; region = box, layer bounds transformed by that layer's matrix and masked by its alpha, or the whole picture; intensity = `min(1, plan.intensity × scale)` and defaults from settings):
- `rain`: N = count × intensity streaks with fixed seeded start positions, falling at speed, wrapping inside the region; drawn as anti-aliased lines (`cv2.line`, LINE_AA) with the colour at 0.5 alpha.
- `snow`: soft dots with a sine drift.
- `fireflies`: additive Gaussian dots whose brightness pulses.
- `falling_leaves`: small rotated ellipses tumbling down.
- `water_ripple`: `cv2.remap` with dy = strength·sin(2π(y/λ + t·speed)) inside the region.
- `smoke` / `mist`: a seeded value-noise field (2 octaves, upscaled once per section) scrolled over t, alpha-blended in the colour.
- `glow`: a pulsing Gaussian-blurred brightening of the region or layer.

Tests first (picture 64×48, layers as solid squares):
- `ease(0) == 0`, `ease(≥ease_in) == 1`; every motion's matrix at e=0 is the identity (AC-1.4).
- Sway with A=4 rotates the pivot-relative corner by 4° at the period's quarter; `rotate` never goes negative; bob and shift move by A·short; breathe scales around the pivot; scale=2 doubles the offsets (AC-3.2/3.3).
- Parallax: depth 0 gives no offset; depth 1 gives max_shift·short at the peak; the background never moves (AC-1.2).
- `render_frame(anim, 0)` equals the original picture exactly (AC-1.4).
- With a layer moving to the picture edge, the output keeps its shape H×W with no NaN or transparency, and pixels outside the picture are never written (the function returns only the picture area) (AC-1.5).
- z-order: an overlapping higher-z layer's colour wins (AC-1.1).
- Each effect changes pixels only inside its region (box, or the moved layer's area) and does nothing at intensity 0 (AC-1.3).
- `seed_for` is stable; two `load_page_animation` + `render_frame(…, 37)` runs are byte-equal (AC-5.4).
- Every name in `EFFECTS` has an implementation (one registry dict).
- Commit: `feat(animation): pure page animator – motions, parallax, effects (Phase 2)`

## Phase 3: Eligibility and cache keys

**Implements:** FR-11, FR-13, FR-14 | **Satisfies:** AC-3.4, AC-4.1, AC-4.2, AC-5.2, AC-5.3, AC-5.5 | **Files:** `video_pipeline/animate.py`, `video_pipeline/cut_layers.py` (`cut_state`), `video_pipeline/studio/books.py` (use it), `video_pipeline/render_episode.py` (`_load_inputs`), tests `test_animate.py`, `test_render_cli.py`, `test_studio_books.py`

```python
# cut_layers.py – shared by studio and render
def cut_state(folder: BookFolder, plan: MotionPlan, config: dict) -> tuple[str, str]:
    """("cut", "") when layers.json matches the plan's current cut hash and every file exists,
    else ("needs cut", reason)."""

# animate.py
@dataclass(frozen=True)
class PageEligibility:
    animated: bool
    reason: str = ""                  # e.g. "stale plan – re-run python -m video_pipeline.motion_plan --book s --page 4"
    plan: MotionPlan | None = None
    scale: float = 1.0
    plan_sha: str = ""
    manifest_sha: str = ""

def page_eligibility(folder: BookFolder, page: dict, script_page: dict, settings: AnimationSettings,
                     root: Path) -> PageEligibility:
    # order: animation off → "animation off"; override false → "animation false in the script";
    # plan_state none/stale/invalid/no_picture → reason + fixing command; cut_state != "cut" →
    # "needs cut – re-run python -m video_pipeline.cut_layers --book s"; config unreadable → reason.

def animated_key(still_key: str, el: PageEligibility, settings: AnimationSettings) -> str:
```
In `render_episode._load_inputs`, for each page section compute `page_eligibility`. The key is `animated_key(section_key(...), el, cfg.animation)` when animated, else `section_key(...)` unchanged. Store `eligibility: dict[str, PageEligibility]` on `_Inputs` and log `page NN: still – <reason>` once per page for a non-trivial reason (a book with no `animation/` folder logs nothing). `render_check_detail` follows through `_final_key`.

Tests first (`tests/animation_helpers.py` fakes plus `render_helpers.make_world`):
- Each still reason (no plan, stale, invalid, needs cut, missing layer file, off, false) gives `animated=False` with the right command in the reason.
- A book without `animation/` gives keys equal to a run on the pre-010 code path (AC-4.2). Assert against `section_key` directly, and assert the `RENDER_VERSION == 3` test still holds.
- Override `false` gives exactly `section_key` (AC-3.4).
- Editing a motion changes only that page's key (and its two transition keys); changing `cfg.animation` changes only animated keys (AC-5.2, AC-5.3).
- `render_check_detail` reports `out_of_date` after a plan edit on an animated page (AC-5.5).
- The studio `page_animation` uses `cut_state` (existing tests still pass).
- Commit: `feat(animation): page eligibility and animated cache keys (Phase 3)`

## Phase 4: Render animated sections

**Implements:** FR-12 | **Satisfies:** AC-1.1–1.6, AC-2.1–2.4, AC-5.1, AC-5.4 | **Files:** `video_pipeline/animate.py` (camera warp), `video_pipeline/frames.py` (backdrop split), `video_pipeline/render_episode.py`, tests `test_animate.py`, `test_render_ffmpeg.py`, `test_render_cli.py`, `test_render_real.py`

```python
# frames.py – split compose_canvas so both paths share it (the still path's bytes are unchanged)
def canvas_parts(picture: Image.Image, cfg) -> tuple[Image.Image, tuple[int, int], tuple[int, int]]:
    """(blurred darkened backdrop W×H, contain size, paste offset) – what compose_canvas uses."""

# animate.py
def camera_matrix(cam: Camera, frame: int, width: int, height: int) -> np.ndarray:
    """2×3 affine mapping the canvas to the output for timeline.camera_at(cam, frame): the view of
    width/zoom × height/zoom centred at (cx·W, cy·H), clamped as zoompan clamps."""
def canvas_frames(anim: PageAnimation, backdrop: np.ndarray, size, offset, cam: Camera,
                  frames: int, width: int, height: int) -> Iterator[bytes]:
    for n in range(frames):
        pic = cv2.resize(render_frame(anim, n), size, interpolation=cv2.INTER_LANCZOS4)
        canvas = backdrop.copy(); canvas[oy:oy+h, ox:ox+w] = pic
        yield cv2.warpAffine(canvas, camera_matrix(cam, n, width, height), (width, height),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).tobytes()

# render_episode.py
FrameRunner = Callable[[list[str], Iterable[bytes], str], None]
def run_ffmpeg_frames(cmd: list[str], frames: Iterable[bytes], what: str) -> None:
    """Popen(cmd, stdin=PIPE, stderr=PIPE); write each frame; close; on non-zero exit raise
    RenderError with the stderr tail (as run_ffmpeg does)."""
def animated_section_filtergraph(section: Section, caption_count: int, cfg: RenderConfig) -> str:
    # [0:v]format=yuv420p via scale=out_color_matrix=bt709:out_range=tv[base], then the same
    # caption overlay chain and the same outro fade/setparams tail as section_filtergraph
def animated_section_command(section, caption_pngs, script_path, out_path, cfg) -> list[str]:
    # ["-f","rawvideo","-pix_fmt","rgb24","-s",f"{w}x{h}","-framerate",fps,"-i","pipe:0", *captions,
    #  "-filter_complex_script", script, "-map","[vout]","-frames:v",frames, *ENCODE_ARGS(cfg), out]
```
- `_render_section` branches on `inputs.eligibility[section.name].animated`:
  - **Animated:** load `PageAnimation`, build the frames iterator and run through `frame_run` (default `run_ffmpeg_frames`) with the same `.tmp.mp4` → `os.replace` pattern.
  - **Still:** unchanged.
- A layer file that fails to load at render time falls back to still and logs it. That changes the key only on the next `_load_inputs`, so the fallback is written with the still key's meta.
- `render_video(..., frame_run=run_ffmpeg_frames)` and `main(..., frame_run=None)` are injectable for tests.
- Fix the stray `args.build_dir` reference in `main` (render_episode.py:902) while there.

Tests first:
- `camera_matrix` maps the canvas centre to the output centre for a static camera at z=1, and at a push camera's frame n it matches `camera_at` (the view rectangle's corners).
- `animated_section_command`: the first input is `pipe:0` rawvideo of the right size; captions are inputs 1..K; `-frames:v` equals `section.frames` (AC-1.6).
- With a `FakeFrameRunner`, the animated page consumes exactly `frames` frames of `w·h·3` bytes; still sections still go through `run`; transitions still use the frame grabs (AC-2.2); the audio master is byte-equal with animation on and off (AC-2.3).
- With animation off, every runner call and every key equals today's (AC-2.4).
- A second run renders nothing (AC-5.1).
- `ffmpeg`-marked (`test_render_real.py`, tiny world 320×180 at 10 fps):
  - an animated page's decoded frame 0 is within mean absolute difference ≤ 3/255 of the still render's frame 0 (AC-1.4, encoder tolerance);
  - frame counts are equal (AC-1.6);
  - two renders after clearing the cache give equal decoded frames (AC-5.4);
  - backdrop pixels outside the picture area of a static-camera frame equal the still's (AC-1.5).
- Commit: `feat(animation): render animated page sections (Phase 4)`

## Phase 5: Preview and studio

**Implements:** FR-15, FR-16 | **Satisfies:** AC-4.3, AC-6.1–6.3, AC-3.1 (studio status) | **Files:** `video_pipeline/render_episode.py`, `video_pipeline/studio/{books,jobs,app}.py`, `templates/animation.html`, `templates/animation_page.html`, tests `test_render_cli.py`, `test_studio_*.py`

- **`render_preview(folder, root, page, *, run, frame_run) -> PreviewResult(path, animated, reason)`:**
  - No approval gate. It loads the script, config and only `page-NN`'s track; missing track → `RenderError("no voice track for page N – run python -m video_pipeline.synthesize_voices --book s --reviewed")`.
  - It builds that one section via `build_sections`, renders it through the same `_render_section` (same cache dir and key, so a later full render reuses it), muxes the page track (`mux_command`) into `build/video/preview/page_NN.mp4`, and prints the path and `animated` or `still – <reason>`.
- **CLI:** `--preview-page N`.
- **Studio:**
  - `jobs.stage_command(..., page: int | None = None)` adds `--preview-page N` for stage `preview` (`STAGE_MODULES["preview"] = "video_pipeline.render_episode"`). `CommandFor` and `_start_stage` pass `page` through, and so does the test fake `Commands`.
  - Route: `POST /books/{slug}/animation/{n}/preview` (same-origin check; refused when busy).
  - `gate` for preview needs the page track to exist, not approval.
  - Media kind `preview_video` serves only `build/video/preview/page_NN.mp4`, with Range support (reuse the video kind's handler).
  - `books.page_animation` rows gain `will_animate` / `still_reason` from `animate.page_eligibility`.
  - Templates: the page table shows "animated" or "still: reason"; the page view has a Preview button and a `<video>` when the file exists.

Tests first:
- **Preview CLI with fakes:**
  - it writes `build/video/preview/page_04.mp4`;
  - it works without approval;
  - a missing track exits 1 naming the voices command;
  - a following full render reuses the page section (the `what` list shows it isn't re-rendered).
- **Studio:**
  - the preview job's command includes `--preview-page 4`;
  - it is refused when busy;
  - a cross-site POST gets 403;
  - the page view shows "still: stale plan …" for a stale plan and a player when the preview exists;
  - media traversal gets 404.
- Browser check in the studio (pilot book): start a preview, play it.
- Commit: `feat(animation): page preview from the CLI and the studio (Phase 5)`

## Phase 6: Integration verification

- `uv run pytest` and `uv run ruff check video_pipeline tests` pass; `uv run pytest -m ffmpeg` passes.
- **Pilot, full render with every page animated:**
  - record the total time (`/usr/bin/time -l`) against today's 8.2 min (target ≤ 16.5 min, ≤ 10% over accepted) and peak RSS against today's run (≤ +3 GB);
  - a re-run reuses everything;
  - a second clean render gives an identical video sha256 (NFR-3).
- **Byte-identical checks:**
  - with `animation.enabled: false`, the pilot render equals today's `master` render byte for byte, with the same section keys (AC-2.4);
  - `a-pocket-full-of-plops` (no plans) renders identically too (AC-4.2).
- **Producer review (NFR-6):** watch the pilot video and previews: gentle motion, no seams, holes or tearing, no jumps. Tune the effect defaults in `config/render.json` if asked.
- **Docs:** update `CLAUDE.md` (stage 3 line plus the preview command) and the tech-stack steering file (OpenCV).
- `sdd-superpowers:sdd-review` (Mode B), then commit `feat: complete spec 010 animated story pages`.

## Coverage map

| FR / AC | Phase |
|---|---|
| FR-8, FR-9, FR-10; AC-3.5, AC-3.6 | 1 |
| FR-1–FR-7; AC-1.1–1.5, AC-3.2, AC-3.3 | 2 (units), 4 (end to end) |
| FR-11, FR-13, FR-14; AC-3.4, AC-4.1, AC-4.2, AC-5.2, AC-5.3, AC-5.5 | 3 |
| FR-12; AC-1.6, AC-2.1–2.4, AC-5.1, AC-5.4 | 4 |
| FR-15, FR-16; AC-4.3, AC-6.1–6.3, AC-3.1 | 1, 5 |
| NFR-1–6 | 0, 6 |

**Gates:**
- **Simplicity:** 3 components, plus one new dependency (ADR-010).
- **Anti-abstraction:** plain functions and one dataclass per concept; there is no effect class hierarchy, just a dict of draw functions.
- **Integration-first:** settings, contract and keys come before the renderer.
