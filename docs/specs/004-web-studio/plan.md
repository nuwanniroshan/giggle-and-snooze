# Implementation Plan: Episode studio (local web interface)

**Spec:** docs/specs/004-web-studio/spec.md (v1.0.0, Approved)
**Branch:** feat/004-web-studio
**Created:** 2026-11-09

## Context

The three stages already exist as CLIs: `video_pipeline.generate_script`, `video_pipeline.synthesize_voices` (`--reviewed`, `approve`), `video_pipeline.render_episode`. Each takes `--book`, `--episodes-dir`, `--build-dir` (where relevant), `--config-dir`, and render takes `--assets-dir` (pictures under `<assets>/illustrations/`, shared nooks under `<assets>/nook/`, page-turn sound under `<assets>/audio_sfx/`). The pilot lives at `book_data.json` + `assets/`.

**Tech (traced to NFR Simplicity, no build step):** Starlette 1.10 + uvicorn 0.54 + Jinja2 3.1 + python-multipart 0.0.33 are already installed in `.venv` (pulled in by `mcp`); declare them explicitly in `pyproject.toml`. Pages are server-rendered Jinja templates with a little vanilla JS (polling a JSON endpoint every second for job progress). Tests use Starlette's `TestClient`, which needs `httpx` (dev dependency). Media playback uses Starlette `FileResponse` (supports HTTP Range).

**Design: four modules in a new `video_pipeline/studio/` package.**
- `books.py` — book registry, upload validation/storage, reviewed mark, script editing, stage status. No web code.
- `jobs.py` — one-at-a-time background job runner: each stage runs as a **subprocess of the existing CLI** (same code path as the command line; stoppable by terminating its process group; isolates memory), output streamed to a log file, job record persisted as JSON.
- `app.py` — Starlette routes + templates + media serving; `main()` starts uvicorn on `127.0.0.1`.
- `templates/` + `static/studio.js` + `static/studio.css`.

## Global Constraints

- **Book layout.** Pilot: `book_data.json`, assets root `assets/`. Added books: `books/<slug>/book_data.json`, `books/<slug>/illustrations/<files>`; uploaded nooks go to `episodes/<slug>/intro_nook.png` / `outro_nook.png` (render already prefers per-book nooks). Slug = `contract.slugify(metadata.title)`. Render for an added book: `--assets-dir books/<slug>`; add a `--sfx-dir` option to `render_episode` (default `<ROOT>/assets/audio_sfx`) so the shared page-turn sound applies to every book.
- **Studio state** lives under `build/studio/`: `jobs/<job_id>.json` (record) and `jobs/<job_id>.log` (output). Everything else is derived from existing pipeline files (FR-2).
- **Reviewed mark:** `episodes/<slug>/reviewed.json` = `{script_sha256, reviewed_at}`; valid only while the script's sha matches.
- **Stage states:** `not_started | running | done | needs_review | approved | failed | interrupted | out_of_date`.
- **Server binds only `127.0.0.1`**, default port 8765 (`--port` to change); port in use → clear error, exit 1.
- **All writes atomic** (`*.tmp` then `os.replace`). Upload file names are reduced to their basename and must match a name the book data expects (page `illustration_file`s, `cover_front.png`, `cover_back.png`, `intro_nook.png`, `outro_nook.png`); anything else is rejected. Upload size limit 25 MB per file.
- No change to existing CLI behaviour except the additive `--sfx-dir` option and an additive `check` helper in render (below).

---

## Phase 1: Books, uploads, reviewed mark, script editing, status

**Implements:** FR-1, FR-2, FR-4 (review mark), FR-5, FR-6 | **Satisfies:** AC-1.2, AC-1.3, AC-2.1–2.5, AC-4.2–4.4 (data side), AC-5.3, AC-6.2
**Files:** `video_pipeline/studio/__init__.py`, `video_pipeline/studio/books.py`, `video_pipeline/render_episode.py` (additive), `tests/test_studio_books.py`

**Interfaces:**
- `Book(slug, title, book_path: Path, assets_dir: Path, is_pilot: bool)`; `StudioPaths(root)` with `books_dir`, `episodes_dir`, `build_dir`, `config_dir`, `studio_dir`.
- `list_books(paths) -> list[Book]` — pilot (if `book_data.json` loads) + each `books/*/book_data.json` that loads; sorted by title; a broken added book is listed with an `error` string instead of being hidden.
- `validate_upload(book_json: bytes, files: dict[str, bytes]) -> tuple[dict | None, list[str]]` — parse JSON, validate with `episode.load_book`-equivalent rules (reuse `episode.load_book` by writing to a temp file, or factor its validation), check every expected illustration and both covers are present and open with Pillow, reject unexpected names and oversize files. Returns the parsed book and every problem.
- `save_book(paths, book, files, replace=False) -> Book` — refuses an existing slug unless `replace`; writes into a temp dir then swaps into `books/<slug>/` atomically (rename old dir aside, rename new in, delete old); nooks to `episodes/<slug>/`. Refuses a slug equal to the pilot's.
- `script_path(paths, book)`, `load_script(...)`, `mark_reviewed(paths, book) -> dict`, `reviewed_ok(paths, book) -> bool`.
- `save_line_edits(paths, book, edits: dict[str, str]) -> list[str]` — `edits` maps a line id (`intro-NN` / `page-PP-NN` / `outro-NN`, same numbering as spec 002) to new text; applies to a copy, runs `contract.validate_script(script, book, terms)`; problems → return them (nothing written); else atomic write. Any successful save clears the reviewed mark (deletes `reviewed.json`).
- In `render_episode.py`: add `--sfx-dir` (default `ROOT/"assets"/"audio_sfx"`) threaded through `render_video(..., sfx_dir=None)`; add `render_check(book_path, episodes_dir, build_dir, config_dir, assets_dir, sfx_dir) -> str` returning `"up_to_date" | "out_of_date" | "not_rendered" | "blocked"` by computing the final key exactly like `render_video` does but without running FFmpeg (factor the key computation into a shared helper; no behaviour change).
- `stage_status(paths, book, jobs_view) -> dict` with keys `script`, `voices`, `video`, each `{state, detail}`, and `next_action` string from AC-1.3. Uses: script file + reviewed mark; `synthesize_voices.approval_problems` + status.json; `render_check`; and the job runner's latest record per (book, stage) for `running/failed/interrupted`.

**Tests (failing first):** list_books with pilot + one added + one broken; upload validation (valid; bad JSON; missing illustration; missing cover; unreadable image; unexpected filename incl. `../evil.png`; oversize); save_book (new; duplicate refused; replace swaps; pilot slug refused; nook placement); reviewed mark set/cleared by edit/stale when script changed externally; save_line_edits valid/invalid (nothing written, problems returned, unknown line id rejected); render_check states with a fake world (reuse `tests/render_helpers.py`); `--sfx-dir` used (fake runner world with a supplied page_turn.wav in a custom dir); stage_status/next_action for each combination.

**Commit:** `feat(studio): Phase 1: books, uploads, review mark, script edits and status`

---

## Phase 2: One-at-a-time job runner

**Implements:** FR-3 | **Satisfies:** AC-3.1 (command gating data), AC-3.2–3.6, NFR Reliability
**Files:** `video_pipeline/studio/jobs.py`, `tests/test_studio_jobs.py`

**Interfaces:**
- `stage_command(stage, book, paths, force=False) -> list[str]` — `[sys.executable, "-m", "video_pipeline.<module>", ...]`: script → `generate_script --book B --episodes-dir E --config-dir C [--force]`; voices → `synthesize_voices --book B --episodes-dir E --build-dir D --config-dir C --reviewed [--force]`; render → `render_episode --book B --episodes-dir E --build-dir D --config-dir C --assets-dir A --sfx-dir S [--force]`.
- `JobRunner(studio_dir, env=None)`: `start(book_slug, stage, cmd) -> Job` (raises `Busy(current)` if a job is running); background thread runs `subprocess.Popen(cmd, stdout=PIPE, stderr=STDOUT, text=True, start_new_session=True, env={..., "PYTHONUNBUFFERED": "1"})`, appends each line to the log file and an in-memory tail; record JSON `{id, book, stage, cmd, started_at, ended_at, state: running|done|failed|stopped|interrupted, exit_code}` written atomically on start and end. `stop()` sends SIGTERM to the process group, SIGKILL after 5 s → state `stopped`. `current()`, `latest(book, stage)`, `log(job_id, offset) -> (text, new_offset)`. On construction, any persisted record still `running` becomes `interrupted`.
- Gating (AC-3.1/4.4) lives in the app layer using Phase 1 status (voices requires `reviewed_ok`; render requires no `approval_problems`).

**Tests (failing first; jobs use tiny `python -c` commands, no pipeline):** start → done with log captured and exit 0; failing command → failed with exit code and output; second start while running → Busy; stop → stopped and process gone; restart with a stale running record → interrupted; log offsets stream incrementally; stage_command shapes for pilot and added book (force flags, sfx/assets dirs).

**Commit:** `feat(studio): Phase 2: one-at-a-time job runner`

---

## Phase 3: Web app, pages and media

**Implements:** FR-3 (display), FR-4, FR-7, Story 1 | **Satisfies:** AC-1.1–1.3, AC-2.x (UI), AC-3.x (UI), AC-4.1–4.4, AC-5.1–5.3, AC-6.1–6.2
**Files:** `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/{base,home,book,upload,script,audio,video}.html`, `video_pipeline/studio/static/{studio.js,studio.css}`, `pyproject.toml` (declare starlette, uvicorn, jinja2, python-multipart; dev httpx), `uv.lock`, `tests/test_studio_app.py`

**Visual style (producer request, 2026-11-09): ultra-minimalist, clean, black and white, generous spacing.**
- Colours: black `#000` text on white `#fff` only; a single mid-grey (`#6b6b6b`) for secondary text and `#e5e5e5` for 1px rules. No other colours, no gradients, no icons or emoji. Depth (spec v1.1.0): soft black shadows at most 12% opacity (`rgb(0 0 0 / ≤0.12)`, declared as tokens) and 2–4px radii on raised elements; no coloured or glowing shadows. State badges are plain uppercase words (e.g. `APPROVED`, `RUNNING`, `FAILED`); failed is bold, not red.
- Type: system font stack (`-apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif`), 16px base, 1.6 line height; headings by size and weight only; monospace (`ui-monospace, Menlo, monospace`) for logs and paths.
- Spacing: an 8px scale (8/16/24/48/64); content column max 880px centred with 48px side padding (24px under 600px wide); 48px between sections, 24px between items, buttons with 12px×24px padding.
- Controls: buttons are black filled with white text (primary) or white with a 1px black border (secondary); disabled buttons are grey text on white with the reason in small grey text beside them. Links underlined. Focus outline 2px black. Inputs and textareas 1px border with a 2px radius and a faint inset shadow (spec v1.1.0).
- Tables: no vertical lines, 1px `#e5e5e5` row rules, 16px cell padding.
- Media: `<audio>`/`<video>` native controls, full column width.
- One stylesheet `static/studio.css` (< 200 lines), no CSS framework.

**Routes (HTML unless noted):**
- `GET /` home: books table with per-stage state badges and next action; banner when a job is running.
- `GET /books/new`, `POST /books` (multipart: `book_data` + `files[]` + optional `replace=1`) → errors listed, or redirect to the book.
- `GET /books/{slug}`: stage cards with buttons (disabled with a reason when gated or busy), last job status + live log panel, force/regenerate options with confirmation (JS `confirm()` + a hidden `confirm=1` field the server requires for regenerate).
- `POST /books/{slug}/run/{stage}` (`force`, `confirm`) → 409 with message if busy/gated; else start job, redirect back.
- `POST /jobs/stop`; `GET /api/jobs/current` and `GET /api/jobs/{id}/log?offset=N` (JSON) for polling.
- `GET /books/{slug}/script`: intro, pages (text, illustration thumbnail, lines with speaker/role, editable textareas named by line id), outro; `POST /books/{slug}/script` saves edits (problems shown per line); `POST /books/{slug}/script/reviewed`.
- `GET /books/{slug}/audio`: `<audio>` for preview and each track, review-sheet table (from track timing JSONs), status + approved_at; `POST /books/{slug}/audio/approve` calls `synthesize_voices.approve` in-process (fast) and shows refusal reasons.
- `GET /books/{slug}/video`: `<video controls>`, duration, path, out-of-date notice from `render_check`.
- `GET /media/{slug}/{kind}/{name}`: serve only whitelisted files (illustrations, audio tracks/preview, the final video) resolved from the book — never a client-supplied path.
- `main(argv)`: `--port` (8765), `--host` fixed to 127.0.0.1 (not configurable), prints `Studio running at http://127.0.0.1:PORT`; port in use → `error: port PORT is already in use` exit 1.

**Tests (TestClient, failing first; JobRunner and stage commands faked; worlds from `tests/render_helpers.py`):** home lists books with states/next action; upload good/bad/duplicate/replace; run gating (voices blocked until reviewed, render blocked until approved) and busy 409; regenerate requires confirm; script page renders lines, save valid/invalid, reviewed button; audio page lists tracks and approve works/refuses; video page plays and flags out-of-date; media route refuses unknown names and traversal; `Range` request returns 206; server refuses non-loopback host (unit-test the bind address used by `main`).

**Commit:** `feat(studio): Phase 3: web pages, stage controls and media`

---

## Phase 4: Real run and docs

- Start the studio (`uv run python -m video_pipeline.studio`), check with `curl` that `/` lists the pilot with voices approved and video up to date; open it in the browser pane and screenshot home, script, audio and video pages.
- Upload a copy of the mini test book through the browser form (or `curl -F`) and confirm it appears with next step "Generate script"; delete it afterwards (it is test data created by this phase).
- Run a short real stage from the UI against the pilot: approve is already done; run render (should report "up to date" in < 1 s) and confirm live log + done state.
- Docs: `CLAUDE.md` (studio command, book layout, `--sfx-dir`), steering tech-stack, `.claude/rules` note if useful.
- **Commit:** `feat(studio): Phase 4: real run and docs`

### Results (2026-11-09)

- 555 unit tests + 10 ffmpeg tests pass.
- Studio started on the real project: home lists the pilot as script DONE / voices APPROVED / video DONE / next "Watch video".
- Script, audio (preview 14:40, per-track players, review table) and video (15:15) pages render.
- Media 200, Range 206, traversal 404, cross-origin POST 403.
- A real render stage run from the UI finished "up to date" in 0:01 with exit 0 and its log shown.
- Real upload through the running server was not exercised against the repo (covered by TestClient tests).

### Deviations during execution

- Jobs read the subprocess with `Popen` in binary mode and collapse `\r` progress lines.
- `log()` returns complete lines only while the job is running.
- Overwriting an existing book needs a required overwrite checkbox instead of a JS confirm.
- The app lifespan stops a running job on shutdown.
- Script edits and approve are refused while that book's job runs.
- No Host-header (DNS-rebinding) check; POSTs are still protected by the Origin check.
- The script stage counts as done when the audio status matches the script (CLI `--reviewed` path).

## Review fixes (round 1)

1. Reserved slugs (`books.RESERVED_SLUGS`, at least `new`, which is the `/books/new` route) are refused by `validate_upload` and so by `save_book`.
2. A page `illustration_file` may not be a cover or nook name. Upload names (pages, covers, nooks) may not differ only by case, because APFS would merge them. Two pages may still share one file.
3. `reviewed.json` stores `book_sha256`. The mark only counts while both the script and the book file match, so an old mark without that field no longer counts. Replacing a book deletes the mark.
4. Handlers that read files or wait on a job are plain `def` (Starlette runs them in its thread pool). Handlers that await the form hand the rest to `run_in_threadpool`. `video_pipeline/file_hash.sha256_file` caches hashes by (path, inode, size, mtime_ns); voices, render and timeline all use it.
5. A failed or interrupted job overrides the files only when its `ended_at` is later than the stage's output file (`episode_script.json`, audio `status.json`, video record JSON). A running job always overrides. An interrupted record ends when its log last changed.
6. When `JobRunner` starts and finds a stale `running` record whose process group is still alive, it stops that group (SIGTERM, then SIGKILL after the grace period). It notes this in the job's log, then marks the record interrupted.
7. `POST /books` is refused before the form is parsed: 413 when Content-Length is over (`MAX_UPLOAD_FILES` + 1) × 25 MB (`MAX_UPLOAD_FILES` = 100), 411 when Content-Length is missing. Book data over 25 MB is reported as "book data is larger than 25 MB".
8. `TrustedHostMiddleware(allowed_hosts=["127.0.0.1", "localhost"])`. The tests already use `base_url=http://127.0.0.1:8765`.
9. `render_episode` without `--sfx-dir` uses `<assets-dir>/audio_sfx` again, as before spec 004. The studio passes `paths.sfx_dir` explicitly in `stage_command` and in `books.video_check`, and `tests/render_helpers.cli_args` no longer forces `--sfx-dir`.
10. `render_check_detail` returns (state, reason). The approval gate raises `AudioNotApproved` (a `RenderError`), so a missing picture is reported as itself, not as an audio problem, in the status detail and on the video page. A replace upload gets 409 while a stage runs for that book.

## Review fixes (round 2)

1. Single instance: `JobRunner` takes an exclusive non-blocking `flock` on `<studio_dir>/studio.lock` when it is created and holds it until `close()` (or garbage collection). A second runner, in another process or the same one (each opens the file itself, so the locks conflict), raises `StudioLocked` before it looks at any record. `app.main` prints "error: another studio is already running on this project" and exits 1 before uvicorn binds the port. App shutdown (lifespan) and `main` stop the running job and release the lock.
2. Safer orphan kill: a job record stores `proc_started` (`ps -o lstart`) when it starts. A stale record's process group is stopped only when its leader's command line contains `video_pipeline` and its start time matches the record. Otherwise the record is only marked interrupted (this includes records written before round 2, which have no start time).
3. Serialized check-and-write: `Studio.write_lock` (one `threading.Lock`) covers busy/gate check → `runner.start`, running-job check → `save_book`, → `save_line_edits`, → `mark_reviewed`, and voices-running check → `approve`. Only the check and that one write run inside it; stop and page views don't take it.
4. `file_hash.sha256_file` re-stats after hashing and caches only when the stat is unchanged. A test now covers the 411 (missing Content-Length) branch.

## Spec coverage

| Spec item | Phase |
|---|---|
| FR-1 Books | 1 |
| FR-2 Status | 1 (+ render_check) |
| FR-3 Jobs | 2, 3 |
| FR-4 Gates | 1 (review mark), 3 (enforced in routes) |
| FR-5 Script editing | 1, 3 |
| FR-6 Uploads | 1, 3 |
| FR-7 Media | 3 |
| NFR Security / Responsiveness / Simplicity / Reliability / Compatibility | 3 (bind, media whitelist), 3 (polling), 3 (no build), 2 (interrupted), 1 (`--sfx-dir` additive; added books runnable by CLI) |
