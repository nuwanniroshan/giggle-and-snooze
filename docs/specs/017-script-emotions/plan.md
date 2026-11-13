# Plan 017: Feelings in the episode script

**Spec:** spec.md (Approved)

## Context
A book made in the studio arrives with no pictures (painting is CLI-only), and generated scripts have almost no `<giggle>`-style tags and no sense of delivery. Specs 016 and 017 (both approved) fix this. On approval these two sections are saved as `docs/specs/016-studio-illustrate/plan.md` and `docs/specs/017-script-emotions/plan.md`.

Working tree: branch `feat/014-studio-redesign` has much uncommitted work from specs 012–015. I work on top of it, touch only the files named here, and commit nothing unless asked.

Gates: each plan has ≤3 components, no new wrappers beyond one small config module (017), and contracts (config/schema) come before code. TDD: each phase starts with failing tests.

## Spec 017 — Feelings in the episode script

### Phase 1: Feelings config + contract (FR-1, FR-4; AC-2.1–2.2, AC-3.4, AC-1.5)
**Files:** `config/emotions.json` (new), `video_pipeline/emotions.py` (new), `video_pipeline/contract.py`, `video_pipeline/episode.py`, `tests/test_emotions.py` (new)

```json
{
  "feelings": {
    "neutral":   {"speed": 1.0,  "semitones": 0,    "gain_db": 0},
    "excited":   {"speed": 1.08, "semitones": 1,    "gain_db": 1.5},
    "happy":     {"speed": 1.04, "semitones": 0.5,  "gain_db": 1},
    "curious":   {"speed": 1.0,  "semitones": 0.5,  "gain_db": 0},
    "surprised": {"speed": 1.06, "semitones": 1.5,  "gain_db": 2},
    "calm":      {"speed": 0.94, "semitones": -0.5, "gain_db": -1.5},
    "sleepy":    {"speed": 0.88, "semitones": -1,   "gain_db": -3},
    "whisper":   {"speed": 0.92, "semitones": 0,    "gain_db": -7}
  },
  "generation": {"tags_per_moment_min": 1, "tags_per_moment_max": 2, "feelings_per_moment_min": 1}
}
```
- `emotions.py`: pydantic `Feeling(speed 0.8–1.25, semitones −2…2, gain_db −9…3)`, `EmotionSettings(feelings, generation)`, `load_emotions(config_dir) -> EmotionSettings` raising `ConfigError` naming feeling + field; requires `neutral` = (1, 0, 0).
- `contract.BanterLine.emotion: str | None = None`. `validate_script(..., emotions: Collection[str] | None = None)`: when given, any line `emotion` not in it → `"<where>: emotion 'x' is not allowed (allowed: …)"`. Narration lines may not carry `emotion`.
- `episode._qa_line/_bookend_line` copy `emotion` only when set (scripts without feelings stay byte-identical).
- Tests: limits, unknown feeling refused, missing → neutral, assembled script carries emotion.

### Phase 2: Generation rules + brief (FR-2; AC-1.1–1.4, AC-2.1, AC-2.4)
**Files:** `video_pipeline/episode.py`, `video_pipeline/llm.py`, `video_pipeline/episode_tools.py`, `video_pipeline/generate_script.py`, `tests/test_emotions.py`

```python
def banter_feeling_errors(banter: Banter, plan: EpisodePlan, gen: Generation) -> list[str]:
    lines = [*banter.intro, *(l for m in banter.moments for l in m.lines), *banter.outro]
    m = len(plan.qa_speakers); count = sum(len(TAG_RE.findall(l.text)) for l in lines)
    errors = []
    if count < m * gen.tags_per_moment_min: errors.append(f"only {count} sound tags (need at least {m * gen.tags_per_moment_min}) …")
    if count > m * gen.tags_per_moment_max: errors.append(f"{count} sound tags (at most {m * gen.tags_per_moment_max})")
    errors += [f"{where}: more than one sound tag in a line" for where, l in … if len(TAG_RE.findall(l.text)) > 1]
    errors += [f"page {mo.page_number}: give at least {gen.feelings_per_moment_min} line a feeling other than neutral"
               for mo in banter.moments if sum((l.emotion or "neutral") != "neutral" for l in mo.lines) < gen.feelings_per_moment_min]
    return errors
```
- `EpisodeTools` loads `emotions` in `load()`; `check()` adds `banter_feeling_errors` and passes `emotions` to `validate_script` (generation only; plain `validate_script` callers never get the count rules → AC-1.4).
- `build_brief(..., feelings: EmotionSettings | None = None)`: when given, adds a FEELINGS block (allowed feelings, tag min/max for M moments, one tag per line, "at least one non-neutral feeling per moment") and the RULES tag line says "use them where they fit". `None` keeps today's brief, so `tests/fixtures/legacy_golden.json` stays valid; `EpisodeTools.get_episode_plan` and `llm.build_prompt` pass the loaded settings.
- `generate_script.py` passes `emotions` to its `validate_script` call.
- Tests: too few / too many / two-in-a-line / all-neutral moment refused with the counts; a good banter passes; brief lists feelings and counts; hand-edited script with 0 tags validates.

### Phase 3: Voice delivery (FR-3, FR-6; AC-2.3, AC-3.1–3.5)
**Files:** `video_pipeline/tts.py`, `video_pipeline/synthesize_voices.py`, `tests/test_synthesize_voices.py`, `tests/test_tts.py`

- `tts.voice_filter(semitones, sr, speed=1.0, gain_db=0.0)`: equals `pitch_filter` when speed 1 and gain 0; else `asetrate,aresample,atempo={speed/ratio},volume={gain_db}dB`. `pitch_shift(audio, sr, semitones, speed=1.0, gain_db=0.0)` returns audio unchanged when all neutral.
- `LineJob` gains `emotion`, `speed`, `gain_db`; `plan_jobs(script, cfg, prons, emotions)` sets total semitones = role + feeling. `clip_key` adds `"delivery": {...}` only for a non-neutral feeling (neutral key unchanged → AC-2.3).
- `make_clip` calls `shift(raw, sr, job.semitones, job.speed, job.gain_db)` before trim/measure (checks see shaped audio, AC-3.5). Clip meta records the feeling. `_synthesize` loads emotions (bad config → fails at start) and passes them to `validate_script`.
- Test fakes `identity_shift`/`recording_shift` take `*_`. Tests: filter strings; neutral key equals today's key for a fixed line; changing a feeling changes only that clip's key; `@pytest.mark.ffmpeg` test that `sleepy` is longer/quieter and `excited` higher than neutral.

### Phase 4: Studio feelings (FR-5; AC-4.1–4.2)
**Files:** `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/script.html`, `tests/test_studio_app.py`

- `save_line_edits`: form keys `emotion:<line_id>` set/clear `emotion` (neutral → key removed); validated with `emotions`; a change clears the reviewed mark like text.
- `script_context` entries add `emotion` and `feelings` (non-narration lines only); `script.html` shows a `<select name="emotion:{{ line.id }}">` beside the textarea.
- Tests: page shows feeling (neutral when missing); saving `excited` writes it and clears review; unknown value → 422 naming line and allowed list.

### Verify 017
`uv run ruff check video_pipeline tests`, `uv run pytest`, `uv run pytest -m ffmpeg`. Then regenerate Jack's script (`generate_script --book jack-and-the-topsy-turvy-dream --force`, uses ChatGPT quota) and check its tag count and feelings in the studio script view.
