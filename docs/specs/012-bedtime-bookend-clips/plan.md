# Plan 012: Bedtime bookend clips

**Spec:** [spec.md](spec.md) (Approved)

## Goal

Final video order: logo reveal, opening clip, body (cover, story, outro), ending clip. Joins are 1.5 s cross-dissolves (spec v3.0.0); the start/end fades come from the final grade (spec 013).

## Phase 1: Join the clips

**Implements:** FR-1 to FR-5. **Satisfies:** AC-1.1 to AC-3.6.

**Files**
- `assets/brand/bedtime_open.mp4` (the 10 s clip), `assets/brand/bedtime_close.mp4` (the 20 s clip)
- `config/render.json`: `channel_open_clip`, `channel_close_clip`
- `video_pipeline/timeline.py`: the two settings fields, excluded from section cache keys like `channel_intro`
- `video_pipeline/render_episode.py`: generalise `prepend_channel_intro` to join `[logo, open, body, close]`; each clip conformed with `channel_intro_command` and cached by checksum; silence for a clip with no audio; missing file stops early naming the path; record JSON lists clip checksums and the total duration includes them; preview render skips clips
- `tests/test_channel_look.py`, `tests/render_helpers.py`, `tests/test_render_real.py`

**Tests first:** join order; no clips configured gives unchanged output; missing file error; silent clip; cache reuse and invalidation; preview skips clips; one real-FFmpeg run with the real clips.

## Phase 2: Dinosaur book (separate step)

Check that the pipeline accepts 5 pages, then create the book through the idea, blueprint and story flow.

## Phase 3: Longer dissolves and start/end fades (spec v3.0.0)

**Implements:** FR-5 (v3). **Satisfies:** AC-5.1 to AC-5.6.

**Files**
- `config/render.json`: `channel_blend_seconds` 1.5; `grade.picture_fade_in`/`sound_fade_in` 0.8, `picture_fade_out`/`sound_fade_out` 2.0
- `video_pipeline/timeline.py`: default `channel_blend_seconds` 1.5
- `tests/test_channel_blend.py`, `tests/test_grade.py`

**Tests first:** a 45-frame blend over a 279-frame opening clip, logo and body plans and total length (sum − 45 per join); the shipped grade config fades in over 0.8 s and out over 2.0 s with the last frame black.
