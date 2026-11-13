# Plan 013: Final grade

**Spec:** [spec.md](spec.md) (Approved 1.0.0)

## Phase 1: Grade the finished video

**Implements:** FR-1 to FR-6. **Satisfies:** AC-1.1 to AC-4.5.

**Files**
- `config/render.json`: `grade` block (defaults from `manual-edits/pip/NOTES.md`)
- `video_pipeline/timeline.py`: `GradeSettings` loading and validation; per-book override merge from `book.json` `grade`
- `video_pipeline/grade.py` (new, pure): picture filter text (`eq`, `colorbalance`, `unsharp`, `vignette`, `fade`) and sound filter text (`highpass`, `acompressor`, `loudnorm`, `afade`); fade shortening
- `video_pipeline/render_episode.py`: final grading pass after the blended joins, cached by input sha256 + settings; measured loudness/peak in the record; `--no-grade`
- `video_pipeline/studio/` video page: show the grade settings used (read-only)
- tests: `tests/test_grade.py` (new), render and studio tests

**Tests first:** filter text for defaults, overrides and zero fades; validation errors; short-video fade shortening; cache reuse and invalidation; settings-only change re-renders no sections; off switch gives identical output; real-FFmpeg test (loudness, peak, fade frames, length within one frame).
