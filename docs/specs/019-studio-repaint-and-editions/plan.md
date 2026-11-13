# Plan 019: Repaint one page and build the editions from the studio

**Spec:** spec.md (Approved)

## Context
Spec 016 added the studio Paint stage (`jobs.STAGE_MODULES[PAINT_STAGE]`, `books.paint_state`, the Pictures panel). This plan adds a one-picture repaint (CLI + studio job) and a Build book stage that runs `scripts/build_book.py` and lists/serves what it wrote. Spec 020 (planned in parallel) renames the edition files to `<Title>_<N>Page_Digital_Edition.pdf` / `<Title>_KDP_<P>Page_Print_Interior.pdf`, so the studio never hardcodes edition names: it lists the files that exist. Spec 018 adds character looks to picture requests inside `plan_images`, so a single repaint must go through `plan_images` too (it filters that list, never builds its own prompt).

Working tree: `feat/014-studio-redesign` with uncommitted work from specs 012–017. Touch only the files named here; commit nothing unless asked.

Gates: contracts (CLI flags, stage names, status shapes) before code; TDD, each phase starts with failing tests; tests use the `Commands` fake and `Studio` helper from `tests/test_studio_app.py` and never run real Codex, painting, SAM/LaMa or the book builder.

New stage names: `repaint` (one picture) and `build` (editions). Labels: `STAGE_LABELS["repaint"] = "picture repaint"`, `STAGE_LABELS["build"] = "book build"`.

---

### Phase 1: Repaint one picture from the CLI
**Implements:** FR-1 · **Satisfies:** AC-1.1 (CLI part), AC-1.4, error "Repaint fails" · **Files:** `video_pipeline/illustrate.py`, `tests/test_illustrate.py`

Goal: `python -m video_pipeline.illustrate --book <slug> --page N [--cut]` or `--cover front|back` repaints exactly one picture, keeps the old one as `<name>.png.bak`, and restores it if painting fails.

- Args: `--page N` and `--cover {front,back}` in a mutually exclusive group (both conflict with `--force`; the target is always repainted). `--cut` is allowed only with `--page`.
- `select_images(book, style, stem)` = the one `(stem, prompt)` from `plan_images(book, style)` (unknown page → `IllustrateError("page N is not in the book")`).
- Reference picture: the first story page's file when it exists and is not the target; for page 1, the next story page that exists; none if no other picture exists. Covers use page 1 as today.
- New `repaint_one(...)`:
```python
target = folder / f"{stem}.png"
backup = target.with_name(target.name + ".bak")
if target.is_file():
    shutil.copyfile(target, backup)            # old picture kept (AC-1.1)
try:
    paint(binary, prompt, reference, target)   # paint_one writes atomically
except IllustrateError:
    if backup.is_file():
        shutil.copyfile(backup, target)        # restored (error scenario)
    raise
```
- Story page: then `planner(page)` (already forced, keeps `.bak` of the plan). A failed plan → exit 1, no cut. With `--cut` and a saved plan: `cut_layers.main(["--book", slug, "--page", str(N), "--root", str(root)])` in the same process (models load only after Codex is done; the cut cache keys on the picture, so no `--force`). Its non-zero return is the CLI's return.
- Covers: no plan, no cut (AC-1.4). `record_use(..., "art", ...)` is never called for a single repaint (it is only for a full set).
- Tests (fake `paint`, fake planner, monkeypatched `cut_layers.main`): `test_page_repaint_paints_only_that_page_and_keeps_bak`; `test_page_repaint_uses_page_one_as_reference`; `test_page_one_repaint_uses_the_next_page_as_reference`; `test_failed_repaint_restores_the_old_picture`; `test_page_repaint_plans_then_cuts_that_page_only`; `test_failed_plan_skips_the_cut_and_exits_1`; `test_cover_repaint_has_no_plan_or_cut`; `test_page_and_cover_and_force_are_exclusive`; `test_cut_without_page_is_refused`; `test_unknown_page_is_refused`; `test_single_repaint_does_not_record_blueprint_use`; `test_repaint_prompt_is_the_plan_images_prompt` (so spec 018 looks apply).

### Phase 2: Repaint and prompt edit in the studio
**Implements:** FR-2 · **Satisfies:** AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-1.6 · **Files:** `video_pipeline/studio/jobs.py`, `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/animation_page.html`, `video_pipeline/studio/templates/book.html`, `tests/test_studio_app.py` (the `Commands` fake gains `cover=None`), `tests/test_studio_repaint.py` (new)

Goal: a page view button repaints, replans and recuts that one page as one job; a cover can be repainted from the Pictures panel; the page's picture prompt can be edited and saved.

- `jobs.STAGE_MODULES["repaint"] = "video_pipeline.illustrate"`; `stage_command(..., page=None, cover=None)`: `repaint` needs exactly one of them → `… --page N --cut` or `… --cover front|back` (`ValueError` otherwise). The `Commands` fake records `(stage, slug, page)` or `(stage, slug, f"cover:{cover}")`.
- `books.REPAINT_STAGE = "repaint"`, label as above. `stage_output("repaint")` is not needed: a repaint job shows only on the page view and the Pictures panel, never as a pipeline stage state.
- `books.save_page_prompt(paths, book, number, text) -> list[str]`: read `book.json`, set `pages[number].prompt` (by `page_number`, not list position), `episode.book_problems(data, slug)`; problems → nothing written; else `write_atomic`. Empty or whitespace prompt is a problem.
- `app.py`:
  - `POST /books/{slug}/animation/{number}/repaint` → `_start_stage(book, "repaint", form, page=number)`; `POST /books/{slug}/repaint-cover` with `cover=front|back`. Both: `confirm != "1"` → 409 "Confirm repainting this picture", nothing starts (AC-1.3). Busy → existing refusal naming the running job (AC-1.6).
  - `POST /books/{slug}/animation/{number}/prompt` → `save_page_prompt` under `write_lock`; refused (409) while a `paint`/`repaint` job runs for this book; 422 with problems; else redirect to the page view (AC-1.2).
  - `last_job` default stages include `repaint`; `animation_page_detail` adds `prompt`, `repaint_blocked` and `repaint_job = last_job(book, ("repaint",), number)`.
- `animation_page.html`: a "Picture" panel with the prompt `<textarea>` + Save, and a `<details>` "Repaint page" form (required confirm checkbox, one picture of image quota), the repaint job log. `book.html` Pictures panel: "Repaint front cover" / "Repaint back cover" in the same confirm pattern.
- AC-1.5 needs no new code: `render_check` keys the page section on the picture, so it reports out of date.
- Tests: `test_repaint_page_runs_one_job_with_page_and_cut` (`("repaint", slug, 3)`, argv has `--page 3 --cut`); `test_repaint_without_confirm_is_refused_and_nothing_starts`; `test_repaint_cover_runs_cover_only` (`("repaint", slug, "cover:front")`); `test_repaint_refused_while_any_job_runs_naming_it`; `test_repaint_command_needs_page_or_cover`; `test_save_prompt_writes_book_json_by_page_number`; `test_save_prompt_with_problems_writes_nothing`; `test_save_prompt_refused_while_painting`; `test_page_view_shows_prompt_and_repaint_log`; `test_changed_picture_makes_video_out_of_date` (render world from `tests/render_helpers.make_world`, rewrite one picture, `_video_state` → `out_of_date`).

### Phase 3: Build book stage
**Implements:** FR-3 · **Satisfies:** AC-2.1, AC-2.2, AC-2.3, error "Build fails" · **Files:** `video_pipeline/studio/jobs.py`, `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/book.html`, `tests/test_studio_editions.py` (new), `tests/test_studio_jobs.py`

Goal: a Build book stage whose state comes from files, which runs the existing builder as a job and lists what it produced.

- Command: `stage_command("build", ...)` → `[sys.executable, str(root / "scripts" / "build_book.py"), "--book", slug, "--root", str(root)]` (`build_book.py` is a script, not a module). Restart safety: `jobs.STAGE_MARKERS = ("video_pipeline", "build_book.py")` and `_is_recorded_stage` accepts any of them, so an orphaned build is still recognised and stopped.
- `books.BUILD_STAGE = "build"`, `STATUS_KEYS["build"] = "editions"`.
- `books.edition_files(paths, book) -> list[dict]`: every regular file directly in `build/` whose suffix is `.pdf`, `.epub`, `.png` or `.html` and whose name does not start with `_` or `.` (the builder's temp files), sorted by name: `{name, size, mtime}`. No names are hardcoded (spec 020 renames files).
- `books.editions_state(paths, book) -> {state, detail, files, missing}`:
  - `blocked` when `paint_state(...)["missing"]` (detail names them, AC-2.2);
  - `not_started` ("not built") when `edition_files` is empty;
  - `out_of_date` when `book.json` or any `illustrations/*.png` (or `blueprint.json`) is newer than the oldest edition file (AC-2.3);
  - else `done` ("built"). Job overrides as other stages, with `stage_output("build") = build/index.html` (written last by the builder).
- `stage_status` adds `status["editions"]`; `_next_action`: after "Watch video", "Build book" when editions are not done (never before the video, so the existing flow order is unchanged).
- `app.py`: `STAGE_BUTTONS["build"] = "Build book"`; `_run_stage` accepts `build`; `gate(book, "build")` → `"paint the missing pictures first: …"` when blocked. `flow_steps` gets an "Editions" step after Video.
- `book.html`: an Editions rail panel after Video: badge, detail, Build book button (blocked reason shown), list of files (name, human size, download link, from Phase 4).
- Tests: `test_build_command_runs_the_book_builder`; `test_orphaned_build_is_recognised_by_its_marker` (`test_studio_jobs.py`); `test_editions_blocked_names_missing_pictures`; `test_build_refused_while_blocked`; `test_no_edition_files_is_not_built`; `test_edition_files_are_listed_whatever_their_names` (writes `X_10Page_Digital_Edition.pdf`, `X_KDP_24Page_Print_Interior.pdf`, `index.html`, `_tmp_1.jpg`, `audio/`); `test_changed_book_json_or_picture_marks_editions_out_of_date`; `test_failed_build_job_shows_failed_and_keeps_earlier_files`; `test_build_refused_while_any_job_runs`; `test_book_page_lists_editions_with_sizes`.

### Phase 4: Downloads and the flipbook
**Implements:** FR-4 · **Satisfies:** AC-2.1 (download links), AC-2.4 · **Files:** `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/book.html`, `tests/test_studio_editions.py`

Goal: each edition file downloads from the studio, and the flipbook opens with its pictures, without serving anything else from the book folder.

- `GET /books/{slug}/editions/{name}`: `FileResponse` only when `name` is in `edition_files(...)` names, else 404 (FR-4). Book page links use `download` for PDF/EPUB/PNG and open `index.html` in a new tab.
- The flipbook (written by `build_book.py`) loads covers by sibling name (served by the route above) and story pictures by `../assets/illustrations/<file>`, which from `/books/{slug}/editions/index.html` resolves to `/books/{slug}/assets/illustrations/<file>`. Add that GET route, plus `/books/{slug}/illustrations/<file>` in case spec 020 corrects the relative path; both use `media_path(book, "illustration", name)` (the existing whitelist from `expected_files`).
- Tests: `test_edition_download_serves_a_listed_file`; `test_edition_route_refuses_unlisted_names` (`../book.json`, `audio`, `status.json`, `_tmp_1.jpg`); `test_flipbook_picture_paths_resolve_to_book_illustrations`; `test_flipbook_picture_route_refuses_unknown_files`.

---

### Verify 019
`uv run pytest tests/test_illustrate.py tests/test_studio_repaint.py tests/test_studio_editions.py tests/test_studio_jobs.py tests/test_studio_app.py tests/test_studio_paint.py`, then the full `uv run pytest` and `uv run ruff check video_pipeline tests`. Studio preview (`.claude/launch.json` "studio"): a book page shows the Editions panel (blocked for a book with missing pictures, the file list for `jack-and-the-topsy-turvy-dream`), a download link works and `index.html` shows its pictures; the page view shows the prompt editor and Repaint page. A real repaint (one ChatGPT image) or real build runs only if the user asks.

## Ruling (controller, 2026-11-13)
Spec 020 changes the flipbook picture path to `../illustrations/<name>` (relative to `books/<slug>/build/index.html`). The studio serves the flipbook at `/books/{slug}/editions/index.html`, so phase 4 must serve book pictures at `/books/{slug}/illustrations/{name}` (whitelisted to the book's `illustrations/` PNGs), not `../assets/illustrations/`. Cost if wrong: flipbook pictures fail to load in the studio; caught by the AC-2.4 test.
