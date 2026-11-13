# Plan 016: Paint illustrations from the studio

**Spec:** spec.md (Approved)

## Context
A book made in the studio arrives with no pictures (painting is CLI-only), and generated scripts have almost no `<giggle>`-style tags and no sense of delivery. Specs 016 and 017 (both approved) fix this. On approval these two sections are saved as `docs/specs/016-studio-illustrate/plan.md` and `docs/specs/017-script-emotions/plan.md`.

Working tree: branch `feat/014-studio-redesign` has much uncommitted work from specs 012–015. I work on top of it, touch only the files named here, and commit nothing unless asked.

Gates: each plan has ≤3 components, no new wrappers beyond one small config module (017), and contracts (config/schema) come before code. TDD: each phase starts with failing tests.

## Spec 016 — Paint from the studio

### Phase 1: Paint stage status + command (FR-1, FR-4; AC-3.1–3.4)
**Files:** `video_pipeline/studio/books.py`, `video_pipeline/studio/jobs.py`, `tests/test_studio_paint.py` (new)

- `jobs.STAGE_MODULES["paint"] = "video_pipeline.illustrate"`; `stage_command("paint", book, paths, force)` already yields `… -m video_pipeline.illustrate --book <slug> --root <root> [--force]`.
- `books.py`: `PAINT_STAGE = "paint"`, `STAGE_LABELS["paint"] = "picture painting"`, and:
```python
def paint_state(paths: StudioPaths, book: Book) -> dict:
    """{state, detail, missing}: done / not_started / out_of_date (blueprint art stale, spec 011)."""
    required, _ = expected_files(load_book(book.book_path))
    have = folder_of(paths, book).illustrations
    missing = [n for n in required if not (have / n).is_file()]
    pages = [n for n in required if n not in COVER_FILES]
    detail = (f"{len(pages) - sum(n in missing for n in pages)} of {len(pages)} pages, "
              f"{2 - sum(n in missing for n in COVER_FILES)} of 2 covers")
    if missing: return {**_state("not_started", detail), "missing": missing}
    if any(s["output"] == "pictures" for s in blueprint_status(paths, book)["stale"]):
        return {**_state("out_of_date", "made from an older blueprint; repaint all"), "missing": []}
    return {**_state("done", detail), "missing": []}
```
- `stage_status` adds `status["pictures"]` (job override via `_job_overrides`, `stage_output("paint")` = `illustrations/cover_back.png`, the last file painted). `_next_action`: running paint → "Wait for picture painting"; pictures missing → "Paint pictures" before "Generate script".
- Tests: counts/missing names for a partial book; done; stale with a changed blueprint; command argv with and without `--force`; legacy book with all pictures shows done and nothing else changes.

### Phase 2: Studio routes + book page (FR-1, FR-3; AC-2.1–2.5, AC-3.x display)
**Files:** `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/book.html`, `tests/test_studio_paint.py`

- `STAGE_BUTTONS["paint"] = "Paint pictures"`; `_run_stage` accepts `"paint"`; `last_job` default stages include it.
- `_start_stage`: for paint with `force == "1"` and no `confirm == "1"` → refuse `"Confirm repainting every picture"` (409, nothing starts).
- `book_page` context: `paint = sb.paint_state(...)`, `paint_blocked = busy or self.gate(book, "paint")`.
- `book.html`: a "Pictures" rail panel above Script: badge, counts, missing file names, "Paint missing pictures" button, and a `<details>` "Repaint all" form with a required confirm checkbox + `force=1`. `flow_steps` gains a "Pictures" step after Book.
- Tests (TestClient + `Commands` fake from `tests/test_studio_app.py`): POST `/run/paint` records `("paint", slug, False)`; force without confirm → 409 and no call; with confirm → `("paint", slug, True)`; busy → refused naming the running job; failing command → book page shows failed + log.

### Phase 3: Create book starts painting (FR-2, FR-5; AC-1.1–1.3)
**Files:** `video_pipeline/studio/app.py`, `tests/test_studio_story.py`

```python
slug = self.drafts.create_book(draft["id"])
book = sb.get_book(self.paths, slug)
if book is not None and not book.error and self.busy_reason() is None:
    try:
        self.runner.start(slug, "paint", self.command_for("paint", book, self.paths))
    except (Busy, ValueError):
        pass  # the book stays; its page offers Paint (AC-1.2)
```
- Tests: create → command fake called with `("paint", slug, False)`, redirect to book page, job running; runner raising `Busy` → book still exists, page shows "Paint pictures"; busy before create → 409, draft kept (existing test); no script/voices/render call is ever made.

### Verify 016
`uv run pytest tests/test_studio_paint.py tests/test_studio_app.py tests/test_studio_story.py`, then full `uv run pytest`. Studio preview: open a book with missing pictures, check the Pictures panel, start Paint with a stub-free real run only if the user wants (it uses ChatGPT quota).

