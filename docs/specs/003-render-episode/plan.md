# Implementation Plan: Render the episode video

**Spec:** docs/specs/003-render-episode/spec.md (v1.0.0, Approved)
**Branch:** feat/003-render-episode
**Created:** 2026-11-09

## Context

Inputs already exist: `book_data.json` (pages with `illustration_file`, square 1024×1024 PNGs in `assets/illustrations/`, plus `cover_front.png` / `cover_back.png`), `episodes/<slug>/episode_script.json` (per page `ffmpeg_camera.narration_motion` ∈ `slow_push_in|slow_pull_out|pan_left|pan_right|static`, optional `qa_zoom_target {x,y,zoom}`), and spec 002 output `build/<slug>/audio/` (`status.json`, `preview.json`, `tracks/<name>.wav` mono 24 kHz + `tracks/<name>.json` timing with per-line `start`, `end`, `text`, `role`, `speaker`).

**Feasibility checks (2026-11-09, this Mac, FFmpeg 8.1.2):**
- `zoompan` on a 1920×1080 canvas upscaled 4× renders 30 s in 12.3 s (2×: 8.3 s) → a 15-minute episode ≈ 6 min. Use 4× (smoother).
- FFmpeg has `zoompan`, `xfade`, `overlay` but **no `drawtext`** (no libfreetype) → captions are Pillow-rendered transparent PNGs overlaid with `enable='between(t,a,b)'`.

**Design (three modules, plus config):**
- `video_pipeline/timeline.py` — pure: render config, caption splitting, section list with frame-exact durations, camera math (`camera_at`) and the matching `zoompan` expression builder, section identity keys. Fully unit-tested, no I/O beyond reading JSON.
- `video_pipeline/frames.py` — Pillow + numpy: compose the 16:9 canvas for a picture, render caption PNGs, generate the page-turn sound, mix the master audio track.
- `video_pipeline/render_episode.py` — FFmpeg command builders (pure, tested) + runner, approval gate, caching, CLI.

**Assembly strategy (follows `.claude/rules/ffmpeg.md`: render segments, then concat):**
1. Each section (intro, page-NN, back-cover beat, outro) → its own silent H.264 clip with an exact frame count: canvas PNG → `scale=4*W:-1,zoompan=...:d=N:s=WxH:fps=F` → caption overlays → (outro: `fade=out`) → `format=yuv420p`.
2. Each transition → a clip of fixed length made from the last frame of section A and the first frame of section B with `xfade` (`transition` from config, default `slideleft`).
3. All clips share encode settings → joined with the concat demuxer and `-c copy`.
4. Master audio built in numpy at the voice sample rate: silence, each section's track placed at `section_start + lead_in`, the page-turn sound at each transition start; peak-limited to −1 dBFS; padded to exactly the video length.
5. Mux: `-c:v copy -c:a aac -b:a 192k -ar 48000 -ac 2 -movflags +faststart`, BT.709 tags set at encode.

## Global Constraints

- Output: `build/<slug>/video/<slug>.mp4` (+ `<slug>.json` render record). Work files: `build/<slug>/video/sections/<name>.mp4|.json`, `transitions/<a>__<b>.mp4|.json`, `frames/` (canvas/caption PNGs), `audio/master.wav`. Shared generated sound: `build/sfx/page_turn.wav`.
- Section names: `intro`, `page-PP`, `back-cover`, `outro`.
- Durations are whole frames: `frames = ceil((lead_in + track_duration + hold) * fps)`; the back-cover beat is `round(back_cover_seconds * fps)`; transitions `round(transition_seconds * fps)`. The total = Σ section frames + Σ transition frames.
- Voice line `L` of a section starts at `lead_in + L.start` within the section.
- Nook images: `episodes/<slug>/intro_nook.png` / `outro_nook.png` (per book), else `assets/nook/intro_nook.png` / `outro_nook.png` (shared), else the cover. A supplied file that can't be opened is an error, not a fallback.
- Page-turn sound: `assets/audio_sfx/page_turn.wav` if present (decoded via ffmpeg to the voice sample rate, mono), else generated.
- Canvas: picture scaled to frame height (1080×1080), centred; backdrop = the same picture scaled to cover 1920×1080, Gaussian-blurred, darkened.
- Camera coordinates are canvas fractions; a Q&A target (illustration fractions) maps to canvas as `cx = (left + x*side)/W`, `cy = y`, where `left = (W - H)/2`, `side = H`.
- Every JSON and media write is atomic (tmp then `os.replace`). Every FFmpeg call: argument list, `check=True`, `capture_output=True`; errors re-raised as `RenderError` with the section name and stderr tail.
- Approval gate reuses spec 002: status `approved`, `sha256(script) == status.script_sha256`, `current_preview_key == status.preview_key`, `settings_hash == status.settings_sha256`.
- Pytest marker `ffmpeg` (exists) for tests that run the real binary; excluded by default.

### `config/render.json`
```json
{
  "width": 1920, "height": 1080, "fps": 30, "upscale": 4,
  "lead_in": 0.6, "hold": 1.0, "transition": 0.8, "transition_style": "slideleft",
  "back_cover_seconds": 2.5,
  "motion": {"push_zoom": 1.12, "pan_zoom": 1.12, "pan_travel": 0.06, "qa_ease": 1.2, "intro_zoom": 1.15, "outro_zoom": 1.15, "outro_fade": 1.5},
  "backdrop": {"blur": 30, "darken": 0.55},
  "captions": {"font_size": 48, "max_chars": 44, "max_lines": 2, "band_opacity": 0.6, "margin": 48, "padding": 24, "font": "/System/Library/Fonts/Supplemental/Georgia.ttf", "fallback_font": "/System/Library/Fonts/Supplemental/Times New Roman.ttf"},
  "audio": {"peak_dbfs": -1.0, "sfx_gain_db": -6.0},
  "video": {"crf": 18, "preset": "medium", "audio_bitrate": "192k", "audio_rate": 48000}
}
```

---

## Phase 1: Timeline, captions and camera math (pure)

**Implements:** FR-1 (config), FR-3, FR-4, FR-5, FR-8 (keys) | **Satisfies:** AC-1.3, AC-1.4, AC-3.2–3.4, AC-4.1–4.3, AC-5.2, AC-6.1/6.3 (motion), AC-7.2 (key scope)
**Files:** `config/render.json`, `video_pipeline/timeline.py`, `tests/test_timeline.py`

**Interfaces produced:**
- `RenderConfig` (frozen dataclass mirroring the JSON; nested `motion`, `backdrop`, `captions`, `audio`, `video` as dicts or small dataclasses) and `load_render_config(path) -> RenderConfig` (ConfigError naming missing keys).
- `strip_tags(text) -> str` — removes `<...>` tags (allowed emotion tags and any other markup) and collapses whitespace.
- `CaptionCard(text: str, start: float, end: float)`; `caption_cards(text, start, end, max_chars, max_lines) -> list[CaptionCard]` — greedy word wrap into lines ≤ `max_chars` (a single word longer than max_chars gets its own line), group `max_lines` lines per card, split `[start, end]` proportionally to each card's word count; cards are contiguous and ordered; empty text → `[]`.
- `Camera(kind: str, frames: int, start: tuple[z, cx, cy], end: tuple[z, cx, cy], qa_frame: int | None, qa_target: tuple[z, cx, cy] | None, ease_frames: int, fade_out_frames: int)`.
- `narration_camera(motion, cfg) -> (start, end)`: `static` → (1,0.5,0.5)→(1,0.5,0.5); `slow_push_in` → z 1→push_zoom; `slow_pull_out` → push_zoom→1; `pan_left` → z=pan_zoom, cx 0.5+pan_travel→0.5−pan_travel; `pan_right` the reverse; cy 0.5.
- `smoothstep(p)`; `camera_at(cam, frame) -> (z, cx, cy)`: before `qa_frame` (or for all frames if none): interpolate start→end with `smoothstep(frame / max(1, (qa_frame or frames) - 1))` clamped to [0,1]; from `qa_frame`: `q = smoothstep(clamp((frame - qa_frame)/ease_frames))`, interpolate from the narration end value to `qa_target`. Then clamp `cx` to `[1/(2z), 1 - 1/(2z)]` and `cy` likewise (keeps the crop inside the canvas).
- `zoompan_expr(cam) -> tuple[str, str, str]` — the `z`, `x`, `y` expressions for FFmpeg's zoompan using `on` (output frame index) that compute exactly what `camera_at` computes (same smoothstep, same clamping; x = `cx*iw - iw/zoom/2`, y likewise). Expressions must not contain unescaped `,` issues: build them with `if()`, `lt()`, `clip()`, `min()`, `max()` and pass the whole filter via `-filter_complex_script` in Phase 3, so commas inside expressions are quoted with `'...'`.
- `qa_target_on_canvas(target, cfg) -> (z, cx, cy)`.
- `Section(name, kind: 'intro'|'page'|'beat'|'outro', picture: str (logical id: 'page_03', 'cover_front', 'cover_back', 'intro_nook', 'outro_nook'), frames: int, track: str | None, captions: list[CaptionCard], camera: Camera)`.
- `build_sections(script, timings: dict[str, dict], cfg, pictures: dict[str, str]) -> list[Section]` — `timings` maps track name → timing JSON; `pictures` maps section name → logical picture id (decided by Phase 4's resolver). Order: intro, page-01…page-NN (by `page_number`), back-cover, outro. Page camera: narration motion; if the page has Q&A lines, `qa_frame = round((lead_in + start_of_first_non-narration_line) * fps)`, target from `qa_zoom_target`. Intro camera: z 1→intro_zoom, centre. Outro camera: z outro_zoom→1 with `fade_out_frames = round(outro_fade * fps)`. Beat: static, no captions, no track. Captions: for each timing line, `caption_cards(strip_tags(line.text), lead_in + line.start, lead_in + line.end, ...)`.
- `section_times(sections, cfg) -> list[tuple[float, float]]` (start seconds of each section in the final video, accounting for transitions between consecutive sections) and `total_frames(sections, cfg) -> int`.
- `section_key(section, picture_sha: str, cfg) -> str` — sha256[:32] of JSON of everything that affects the clip: picture sha, frames, captions, camera, width/height/fps/upscale, backdrop, captions style, video encode settings. `transition_key(key_a, key_b, cfg) -> str`.

**Tests (`tests/test_timeline.py`)** — write first, see them fail (`ImportError`), then implement:
- config loads from `config/render.json`; missing key → ConfigError naming it.
- `strip_tags("<giggle> Hi <b>there</b>!") == "Hi there!"`.
- caption_cards: short text → 1 card spanning [start,end]; 60-word text with max_chars 44/max_lines 2 → several cards, each ≤2 lines each ≤44 chars (except over-long single words), contiguous, first.start == start, last.end == end, durations proportional to word counts (±1e-6); empty → [].
- camera_at for each narration motion: frame 0 equals start, last frame equals end, z/cx monotonic across frames (AC-3.2); static constant.
- Q&A: before qa_frame matches narration; at qa_frame + ease_frames and after equals the target (clamped); monotonic during ease (AC-3.3).
- clamping: target cx=0.0 with z=1.3 yields cx = 1/(2*1.3).
- qa_target_on_canvas maps x=0 → 420/1920, x=1 → 1500/1920.
- zoompan_expr: evaluate the produced expressions with a tiny test-side evaluator (replace FFmpeg functions `if, lt, gte, clip, min, max` with Python equivalents and `on`, `iw`, `ih`, `zoom` with values) at several frames and compare to `camera_at` (abs 1e-6). This keeps the FFmpeg expression and the Python model provably identical.
- build_sections with the mini book script (`tests/voice_helpers.make_script(MINI_BOOK)`) and synthetic timings: order and names; page frames = ceil((lead_in + duration + hold)*fps); captions cover each line's span shifted by lead_in; Q&A pages have qa_frame at the first non-narration line; back-cover beat frames; outro fade frames (AC-1.3, AC-3.4, AC-4.1, AC-6.x).
- section_times/total_frames consistent: total = Σ section frames + (n−1) transition frames; no section starts inside a transition (AC-5.2, AC-1.4).
- section_key changes when picture sha, a caption, the motion, a timing, or a render setting changes; identical inputs → identical key (AC-7.2).

**Commit:** `feat(render): Phase 1: timeline, captions and camera math`

---

## Phase 2: Canvas, caption images, page-turn sound and master audio

**Implements:** FR-3 (picture), FR-5 (caption look), FR-6 (sound), FR-7 (audio) | **Satisfies:** AC-3.1, AC-4.4, AC-5.3, AC-1.3 (audio unaltered), AC-1.4 (lengths)
**Files:** `video_pipeline/frames.py`, `tests/test_frames.py`
**Consumes:** Phase 1 `RenderConfig`, `CaptionCard`, `Section`, `section_times`; spec 002 `write_wav_atomic`.

**Interfaces produced:**
- `compose_canvas(picture_path, cfg, out_path) -> None` — RGB 1920×1080 PNG: picture scaled with LANCZOS to `height×height` and pasted centred; backdrop = picture `ImageOps.fit` to W×H, `GaussianBlur(blur)`, multiplied by `darken`. Opens with `with Image.open(p) as im: im.convert("RGB")`; raises `PictureError(path)` if unreadable. Atomic write.
- `render_caption(text, cfg, out_path) -> None` — RGBA W×H PNG, fully transparent except a bottom band: rounded rect with alpha `band_opacity*255`, black, spanning the text block + padding, centred horizontally, bottom margin `margin`; white text lines centred, font from `captions.font` (fallback font, then `ImageFont.load_default(size)`), size `font_size`. Lines are produced by the same wrapper as Phase 1 (`timeline.wrap_lines(text, max_chars)` — expose it in Phase 1 if not already).
- `page_turn_sound(sample_rate, seed=0) -> np.ndarray` — ~0.6 s deterministic float32 mono: two or three short band-limited noise bursts (paper rustle) with fast attack / exponential decay, peak ≈ 0.5.
- `load_page_turn(assets_dir, sample_rate, cache_path) -> np.ndarray` — supplied `assets/audio_sfx/page_turn.wav` decoded with `ffmpeg -i f -f f32le -ac 1 -ar sr pipe:1` (raise `SoundError(path)` on failure); else the generated sound cached at `cache_path` (written once with `write_wav_atomic`).
- `mix_master(sections, tracks: dict[str, np.ndarray], page_turn, cfg, sample_rate) -> np.ndarray` — length exactly `round(total_frames/fps * sample_rate)`; each section's track added at `section_start + lead_in`; page-turn (scaled by `sfx_gain_db`) at each transition start; if peak exceeds `peak_dbfs`, scale the whole mix down (speech is never altered otherwise).

**Tests (`tests/test_frames.py`)** — failing first:
- compose_canvas on a synthetic 1024² image (solid red with a white square): output size 1920×1080 RGB; the centre column region 420..1500 equals the resized picture (sample pixels); backdrop pixels darker than the picture's (AC-3.1); unreadable file → PictureError naming it.
- render_caption: RGBA 1920×1080; top half fully transparent; band alpha ≈ band_opacity*255 below; no opaque pixel within `margin` of the bottom or sides; a long text wraps to ≤ max_lines (AC-4.3, AC-4.4). Text height ≥ 40 px: measure the bounding box of white pixels per line.
- page_turn_sound: deterministic for a seed, duration 0.4–0.8 s, peak ≤ 0.6, ends near silence.
- load_page_turn: no supplied file → generated + cached file created, second call reads the cache; supplied unreadable file → SoundError (ffmpeg call monkeypatched to fail).
- mix_master: length matches total frames exactly; a track's samples appear unaltered at the expected offset when no limiting is needed (AC-1.3); a page-turn burst starts at each transition start and no voice sample lies inside a transition window (AC-5.2); a loud mix is scaled to the peak limit.

**Commit:** `feat(render): Phase 2: canvas, caption images, page-turn sound and master audio`

---

## Phase 3: FFmpeg section, transition, join and mux

**Implements:** FR-4, FR-6, FR-7 | **Satisfies:** AC-1.2, AC-1.4, AC-3.2, AC-5.1, AC-6.3 (fade)
**Files:** `video_pipeline/render_episode.py` (FFmpeg part), `tests/test_render_ffmpeg.py`, `tests/test_render_real.py`
**Consumes:** Phases 1–2.

**Interfaces produced:**
- `RenderError(Exception)`.
- `ENCODE_ARGS(cfg) -> list[str]` — `-c:v libx264 -preset P -crf C -pix_fmt yuv420p -r FPS -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv -video_track_timescale 15360 -an`.
- `section_filtergraph(section, caption_count, cfg) -> str` — pure: `[0:v]scale=UP*W:-1,zoompan=z='..':x='..':y='..':d=N:s=WxH:fps=F[base]`, then one `overlay=enable='between(t,a,b)'` per caption input `[i:v]` chaining to `[vK]`, then `fade=t=out:st=..:d=..` for the outro, ending `format=yuv420p[vout]`. Frame count enforced with `-frames:v N`.
- `section_command(section, canvas_png, caption_pngs, script_path, out_path, cfg) -> list[str]` — `ffmpeg -y -loglevel error -loop 1 -framerate F -i canvas` + `-loop 1 -framerate F -i cap_i` per caption + `-filter_complex_script script_path -map [vout] -frames:v N` + ENCODE_ARGS + out.
- `transition_command(last_png, first_png, out_path, cfg) -> list[str]` — two looped stills each `-t T`, `xfade=transition=STYLE:duration=T:offset=0`, `-frames:v round(T*fps)`, ENCODE_ARGS.
- `frame_grab_command(video, out_png, last: bool) -> list[str]` — first frame (`-frames:v 1`) or last frame (`-sseof -0.1 ... -update 1`).
- `concat_command(list_file, out_path) -> list[str]` — `-f concat -safe 0 -i list -c copy`.
- `mux_command(video, wav, out_path, cfg) -> list[str]` — `-i video -i wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a B -ar R -ac 2 -movflags +faststart -shortest`.
- `run_ffmpeg(cmd, what: str) -> None` — `subprocess.run(cmd, check=True, capture_output=True, text=True)`; FileNotFoundError → RenderError("ffmpeg not found; brew install ffmpeg"); CalledProcessError → RenderError(f"{what}: {stderr tail}").
- `probe(path) -> dict` — `ffprobe -v error -show_streams -show_format -of json`.

**Tests:**
- `tests/test_render_ffmpeg.py` (pure, failing first): filtergraph contains zoompan with `d=N`, `s=1920x1080`, `fps=30`, one `overlay` per caption with the right `between(t,a,b)` times, `fade=t=out` only for the outro, ends with `[vout]`; commands are lists starting with `ffmpeg`, contain `-filter_complex_script`, `-frames:v N`, `-pix_fmt yuv420p`, BT.709 tags, `+faststart` in mux; run_ffmpeg maps missing binary and failures to RenderError with the `what` text.
- `tests/test_render_real.py` (`@pytest.mark.ffmpeg`): render a tiny 2-section episode at 320×180, fps 10, upscale 2 from synthetic pictures/captions/tones (via the Phase 4 pipeline function if available, else directly through these commands + `mix_master`) and ffprobe it: 1 video stream 320×180 h264 yuv420p, 1 aac stereo 48 kHz stream, frame count = total_frames, audio/video durations within 0.1 s (AC-1.2, AC-1.4); a mid-transition frame differs from both neighbours' frames.

**Commit:** `feat(render): Phase 3: FFmpeg section, transition, join and mux`

---

## Phase 4: Approval gate, pictures, caching and CLI

**Implements:** FR-1, FR-2, FR-8, FR-9, Error Scenarios | **Satisfies:** AC-1.1, AC-1.5, AC-2.1–2.3, AC-6.1–6.3 (picture choice), AC-7.1–7.4
**Files:** `video_pipeline/render_episode.py` (append), `video_pipeline/synthesize_voices.py` (small: `approval_problems`), `tests/test_render_cli.py`, `tests/render_helpers.py`

**Interfaces produced:**
- In `synthesize_voices.py`: `approval_problems(paths, script_path, settings_sha) -> list[str]` returning human messages (not approved → mentions `synthesize_voices approve`; script changed / settings changed / preview changed → "re-run synthesis and approve again"; no status → "run synthesis first"). `approve` keeps its behaviour (may reuse the helper).
- `VideoPaths(root)` with `sections`, `transitions`, `frames`, `audio`, `final(slug)`, `record(slug)`.
- `resolve_pictures(book, slug, episodes_dir, assets_dir) -> dict[str, Path]` — logical id → file: `page_PP` from `illustration_file` under `assets/illustrations`, `cover_front`/`cover_back` from `assets/illustrations/cover_front.png|cover_back.png`, `intro_nook`/`outro_nook` per the precedence in Global Constraints (only present if found). Missing page/cover → `RenderError` naming the page and file. Also returns the section→picture-id mapping used by `build_sections`.
- `render_video(book_path, episodes_dir, build_dir, config_dir, assets_dir, force=False, run=run_ffmpeg, on_progress=None) -> RenderResult(path, duration, rendered, reused, up_to_date)`:
  1. load book, slug, script; gate with `approval_problems` (needs voice config + pronunciations to compute settings hash) → `RenderError` joining the problems.
  2. load timings for every track the script implies (`intro`, `page-PP`, `outro`); missing → RenderError naming it (AC-2.3).
  3. resolve pictures, sha256 each file, `build_sections`, keys.
  4. final record (`<slug>.json`) has `final_key` = sha of ordered section keys + transition keys + master-audio key (sha of the track files' bytes + page-turn bytes + cfg audio); if equal and the mp4 exists and not force → up to date (AC-7.1).
  5. for each section: if `sections/<name>.json` key matches and mp4 exists and not force → reuse; else compose canvas + caption PNGs into `frames/<name>/`, write filtergraph script, run, write mp4 to tmp then `os.replace`, then the json (AC-7.4).
  6. transitions likewise keyed by neighbour keys (grab last/first frames).
  7. master audio via `mix_master` → `audio/master.wav`; concat list → `tmp` joined video; mux to `<slug>.mp4.tmp.mp4`, `os.replace` to final; write record last.
- CLI `python -m video_pipeline.render_episode [--book --episodes-dir --build-dir --config-dir --assets-dir --force]` (defaults from `ROOT`), `allow_abbrev=False`; prints per-section progress and the FR-9 summary ("watch it before publishing"); catches `RenderError`, `PictureError`, `SoundError`, `BookError`, `ConfigError` → `error: ...` exit 1; ENOSPC → "not enough disk space to write video in <folder>" exit 1.

**Tests (`tests/test_render_cli.py`, failing first; FFmpeg replaced by a fake `run` that writes small placeholder files for each output path and records commands; `tests/render_helpers.py` builds a mini book world: synthesize approved audio for MINI_BOOK with spec 002's FakeEngine + identity shift, then `approve`)**:
- approved audio → exit 0, final path `build/<slug>/video/<slug>.mp4` exists, summary printed with "watch" (AC-1.1, FR-9); section commands issued in order intro, page-01..08, back-cover, outro (AC-1.3).
- not approved → exit 1, message names `approve`, no video folder (AC-2.1); script edited after approval → exit 1 "re-run synthesis" (AC-2.2); a track JSON deleted → exit 1 naming it (AC-2.3).
- missing illustration → exit 1 naming page and file; unreadable supplied nook → exit 1 naming the file (Error Scenarios).
- nook precedence: per-book file used over shared; shared over cover; none → cover ids (AC-6.1, AC-6.3).
- re-run unchanged → no section/transition/mux commands, "up to date" (AC-7.1); change one page's illustration bytes → exactly that section re-rendered plus its two transitions, then re-join (AC-7.2); `--force` → all sections re-rendered (AC-7.3); a failing section command leaves no final mp4 and no section json for it; next run re-renders it (AC-7.4).
- pilot book runs with the same command (fake run) (AC-1.5).

**Commit:** `feat(render): Phase 4: approval gate, pictures, caching and CLI`

---

## Phase 5: Real render of the pilot and docs

**Satisfies:** AC-1.2, AC-1.4 (real), NFR Performance/Memory/Quality
- `uv run pytest -m ffmpeg -v` passes.
- `/usr/bin/time -l uv run python -m video_pipeline.render_episode` on the pilot (background): exit 0, ≤ 15 min, peak RSS < 4 GB. ffprobe the result: 1920×1080, 30 fps, h264 yuv420p, BT.709, aac stereo 48 kHz, audio/video durations within 0.1 s, duration = predicted total.
- Re-run: "up to date" in < 10 s.
- Send the producer the video (or a compressed copy if > 30 MB) plus a contact sheet of a few frames; the producer watches it before publishing.
- Docs: `CLAUDE.md` (render command, outputs, nook/sfx locations, stage 3 implemented), `.claude/rules/ffmpeg.md` (no drawtext in Homebrew FFmpeg → Pillow caption PNGs; measured zoompan speed), `.claude/memory/steering/tech-stack.md`.
- **Commit:** `feat(render): Phase 5: real pilot render and docs` with measured time and memory.

**Results (2026-11-09):**
- `uv run pytest -m ffmpeg`: 10 passed.
- Pilot render: 15:15 video in 492.7 s (8.2 min) wall, 23 sections; max RSS 0.90 GB.
- Output: 1920×1080 30 fps h264 yuv420p BT.709, AAC 48 kHz stereo; video and audio both 915.300 s, 27459 frames, 220 MB.
- No-change re-run: "up to date" in 0.61 s.

**Deviations during execution:**
- Camera endpoints are clamped before interpolation (pan travel effectively 0.054).
- `load_page_turn` takes the `audio_sfx` folder.
- Captions use single-frame inputs + `eof_action=repeat`.
- BT.709 is tagged via `setparams`.
- Pictures are verified before any render.
- `resolve_pictures` falls back to `page_PP.png` when `illustration_file` is absent.
- The record is deleted at re-render start.

## Review fixes (round 1)

1. Approval covers the tracks (AC-2.2): `status.json` records `track_sha256` (`{track: {wav, json}}`) after the tracks are built; `_validity_problems` recomputes it, so render and `approve` refuse an edited track ("audio track page-03 changed since approval; …"), a status without it ("approval predates track checks; …") and name a missing track file; `next_status` keeps an approval only when the track hashes match.
2. Caption overlays are half-open: `enable='gte(t,a)*lt(t,b)'`, so contiguous cards never share a boundary frame.
3. A corrupt `status.json`/`preview.json` is "<path> is unreadable; re-run synthesis" (render and approve exit 1); synthesis treats it as absent and rewrites it.
4. `run_ffmpeg` names the step when FFmpeg is missing: "<step>: ffmpeg not found; brew install ffmpeg".
5. FFmpeg stderr "No space left on device" → "<step>: not enough disk space to write video in <output folder>".
6. `section_key` hashes the caption font files (path if absent); `RENDER_VERSION = 1` (timeline.py) is in the section, transition and final keys.
7. The outro fade starts no earlier than the end of the last outro line: `fade_out_frames = max(0, min(round(outro_fade*fps), frames - ceil((lead_in + last_end)*fps)))`; `config/render.json` `outro_fade` 1.0.

## Spec v1.1.0: readable captions

AC-4.5 and AC-4.6 replace the greedy two-lines-per-card packing in `caption_cards` (timeline.py):

- A line that wraps into ≤ `max_lines` stays one card. Otherwise a dynamic programme over word gaps picks the fewest cards whose text wraps into ≤ `max_lines` lines of `max_chars`, then the cheapest breaks: after a sentence end (`. ! ? …`, optionally followed by closing quotes/brackets; title abbreviations such as "Mrs." excluded) 0, after clause punctuation (`, ; : — – -`) 10, a bare word gap 100, plus 2 × ((card chars − even share) / max_chars)² for balance.
- Segments that are a single word or shorter than `captions.min_card_seconds` (new optional key in `config/render.json`, default 1.2, must be ≥ 0) are forbidden; if no split satisfies that, the duration rule is dropped, then the one-word rule, never the line limit.
- Timing is unchanged: proportional to words, contiguous, first start and last end exact.
- `RENDER_VERSION = 2` so cached sections re-render.
- Pilot script (synthetic 2.5 words/s): 0 single-word cards (greedy: 10), 61.8% of breaks at sentence/clause punctuation (greedy: 5.9%), shortest card in a multi-card line 1.6 s (greedy: 0.4 s). The rest of the breaks fall between words because many narration sentences exceed one 88-character card with no punctuation in reach at the fewest cards.

Follow-up (review r2, `RENDER_VERSION = 3`): summed break costs let two clause breaks (10 + 10) beat a sentence end plus a word gap (0 + 100), so pilot line page-14-03 ran "…fronds. Huddled on a crooked branch," across one card. The DP now compares splits of the fewest cards lexicographically by (breaks not after a sentence end, bare word breaks, imbalance), so a card ends mid-sentence only when no same-count split with more sentence-end breaks exists, and balance only ranks splits with identical break kinds. A dash joining two words with no spaces ("Falcon—the") is split after the dash into two break units; both a card break and a line wrap may fall there, and the halves rejoin with no space. For timing and the one-word rule the halves share one word (½ each), so timing of dash-free lines is unchanged. Pilot (synthetic 2.5 words/s): 102 breaks, 63.7% at punctuation (30 sentence, 35 clause, 37 word), 0 cards ending mid-sentence where a same-count sentence-end split existed (checked against a brute-force oracle in `tests/test_timeline.py`), 0 single-word cards, shortest card in a multi-card line 1.6 s.

## Spec coverage

| Spec item | Phase(s) |
|---|---|
| FR-1 Inputs/config | 1 (config), 4 (paths, pictures) |
| FR-2 Approval gate | 4 |
| FR-3 Sections | 1, 2 (canvas) |
| FR-4 Motion | 1 (camera math, expressions), 3 (zoompan, fade) |
| FR-5 Captions | 1 (cards), 2 (images), 3 (overlay) |
| FR-6 Transitions + sound | 2 (sound), 3 (xfade) |
| FR-7 Assembly | 2 (master audio), 3 (join, mux) |
| FR-8 Reuse | 1 (keys), 4 (cache, atomic) |
| FR-9 Report | 4 |
| NFRs | 5 (measured), 4 (one section at a time) |
| Error Scenarios | 2 (picture/sound errors), 3 (ffmpeg errors), 4 (gate, missing, disk full, interruption) |
