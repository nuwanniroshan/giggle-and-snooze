# Implement spec 005: book library and folder layout

## Context

Spec 005 (`docs/specs/005-book-library/spec.md`, approved) moves every book into one folder, `books/<slug>/`, holding its source and its generated output. Today the pilot's files are spread across the project root (`book_data.json`, `assets/illustrations/`, `assets/references/`), `episodes/<slug>/`, and `build/`. The build scripts hardcode `/Users/swiveltech/Disk/Swivel/kiddo` and the pilot's title. Adding a second book means copying files and editing code.

Baseline before any change: `uv run pytest` → 607 passed, 12 deselected; `ruff check` clean. Pilot outputs snapshotted at `/private/tmp/claude-501/-Users-swiveltech-Disk-Swivel-kiddo/581cd6b1-dcb9-4157-97fe-93838807d4f2/scratchpad/baseline/pilot.json` (page counts, page sizes, extracted-text hashes, book_data sha).

## Decisions (derived from the spec, not new scope)

- **Layout** (spec FR-1): `books/<slug>/{book.json, illustrations/, references/, nooks/, episode/, build/}`. `book.json` is the renamed `book_data.json`, content unchanged.
- **Generated output** goes under `books/<slug>/build/`: `audio/`, `video/`, `agent/` (Codex run logs, were `episodes/<slug>/agent/`), and the ebook files. Project-level output stays at root `build/`: `studio/`, `sfx/`, `voice-samples/` (FR-4).
- **Root cause of hardcoding removed:** `PROJECT_ROOT = Path(__file__).resolve().parent.parent` (no literal path). Every CLI takes `--book <slug>` (required, FR-6) and `--root` (default `PROJECT_ROOT`; tests pass a temp root). `--episodes-dir`, `--build-dir`, `--assets-dir`, `--sfx-dir` flags are removed: their values are derived from the book folder and root.
- **Output file stem** comes from the title: non-alphanumerics → `_` (`The Snail Mail Express` → `The_Snail_Mail_Express`), so pilot filenames are unchanged.
- **Approval survives the move.** `settings_sha256` hashes the script content and pronunciations, not paths (`synthesize_voices.current_settings_sha`). The pilot's voice approval stays valid after migration. Phase 7 verifies this with `render_check`.
- **Inert absolute paths** in `book.json` (`metadata.primary_reference`, `secondary_reference`) are not read by any code (grep confirmed). They stay unchanged so AC-3.1 holds.
- **Shared settings stay at root** (FR-3): `config/`, `assets/audio_sfx/`, `assets/nook/`.

## Global constraints

- TDD per phase: write the failing test, run it red, implement, run green.
- No pilot title, pilot path, or `book_data.json` literal in code outside migration and tests.
- Writes stay atomic (`write_atomic` / temp + `os.replace`).
- Mechanical call-site edits show the exact before/after signature and name the tests that pin them. Full files would not fit the plan (`build_book.py` is 1,303 lines).

## Phases

### Phase 1 — Library module (AC-1.1–1.4, FR-2, FR-5)

**Files:** new `video_pipeline/library.py`; new `tests/test_library.py`.

Core code:

```python
# video_pipeline/library.py
"""One folder per book: books/<slug>/ (spec 005). Pure path logic, no I/O beyond listing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOK_FILE = "book.json"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LibraryError(ValueError):
    """A slug or book folder that the library cannot use; the message names the problem."""


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.fullmatch(slug):
        raise LibraryError(
            f"invalid book slug {slug!r}: use lowercase letters, digits and single hyphens"
        )
    return slug


@dataclass(frozen=True)
class BookFolder:
    slug: str
    root: Path

    @property
    def folder(self) -> Path:
        return self.root / "books" / self.slug

    @property
    def book_json(self) -> Path:
        return self.folder / BOOK_FILE

    @property
    def illustrations(self) -> Path:
        return self.folder / "illustrations"

    @property
    def references(self) -> Path:
        return self.folder / "references"

    @property
    def nooks(self) -> Path:
        return self.folder / "nooks"

    @property
    def episode_dir(self) -> Path:
        return self.folder / "episode"

    @property
    def script_path(self) -> Path:
        return self.episode_dir / "episode_script.json"

    @property
    def reviewed_path(self) -> Path:
        return self.episode_dir / "reviewed.json"

    @property
    def build_dir(self) -> Path:
        return self.folder / "build"


def book_folder(slug: str, root: Path = PROJECT_ROOT) -> BookFolder:
    validate_slug(slug)
    folder = BookFolder(slug, Path(root))
    if not folder.book_json.is_file():
        raise LibraryError(f"book {slug!r} has no {BOOK_FILE} at {folder.book_json}")
    return folder


def list_book_slugs(root: Path = PROJECT_ROOT) -> tuple[list[str], list[str]]:
    """Return (valid slugs with a book.json, problems). Problems name the folder and the reason."""
    books_dir = Path(root) / "books"
    slugs: list[str] = []
    problems: list[str] = []
    if not books_dir.is_dir():
        return slugs, problems
    for entry in sorted(books_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            validate_slug(entry.name)
        except LibraryError as exc:
            problems.append(str(exc))
            continue
        if not (entry / BOOK_FILE).is_file():
            problems.append(f"book {entry.name!r} has no {BOOK_FILE}")
            continue
        slugs.append(entry.name)
    return slugs, problems


def file_stem(title: str) -> str:
    """`The Snail Mail Express` → `The_Snail_Mail_Express` (output filenames)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
```

Tests (`tests/test_library.py`), written first:
- `test_valid_slugs_pass` — `the-snail-mail-express`, `book2`, `a` pass.
- `test_invalid_slugs_refused` — parametrised over `Upper`, `has space`, `a.b`, `../x`, `a/b`, `-lead`, `trail-`, `a--b`, `""`; each raises `LibraryError` naming the slug.
- `test_book_folder_requires_book_json` — folder without `book.json` → `LibraryError` naming the file (AC-1.2).
- `test_list_book_slugs_reports_problems_and_keeps_valid` — tmp root with one valid book, one missing `book.json`, one `Bad_Name` folder → slugs `[valid]`, two problems (AC-5.2 data).
- `test_new_folder_is_listed_without_code_change` — create `books/new-book/book.json` → listed (AC-1.4).
- `test_file_stem_matches_pilot_filenames` — `file_stem("The Snail Mail Express") == "The_Snail_Mail_Express"`.
- `test_paths_are_under_the_book_folder` — every property path is relative to `books/<slug>/` (AC-2.1 precondition).

Expected: `uv run pytest tests/test_library.py -v` → all PASSED after implementation; red before.

### Phase 2 — Migration (AC-3.1, AC-3.3, AC-3.4, FR-7)

**Files:** new `video_pipeline/migrate_library.py`; new `tests/test_migrate_library.py`.

Move table for slug `the-snail-mail-express` (all paths relative to root):

| From | To (under `books/<slug>/`) |
|---|---|
| `book_data.json` | `book.json` (renamed, content unchanged) |
| `assets/illustrations/*` | `illustrations/` |
| `assets/references/*` | `references/` |
| `episodes/<slug>/episode_script.json` | `episode/episode_script.json` |
| `episodes/<slug>/reviewed.json` (if present) | `episode/reviewed.json` |
| `episodes/<slug>/{intro,outro}_nook.png` (if present) | `nooks/` |
| `episodes/<slug>/agent/*` | `build/agent/` |
| `build/<slug>/audio/` | `build/audio/` |
| `build/<slug>/video/` | `build/video/` |

Flat pilot outputs at root `build/` (`The_Snail_Mail_Express_*`, `index.html`, `.epub`) are regenerated by `build_book`, not moved. They are deleted only in Phase 7, after the comparison passes.

Algorithm (FR-7): (1) plan moves; `--dry-run` prints them and stops. (2) For each move: if the destination exists with the same sha256 → "already in place". Else copy to a `.tmp` then `os.replace`, and verify the sha256. (3) Only after every copy verifies, remove each original. (4) Remove emptied `episodes/<slug>/`, `assets/illustrations/`, `assets/references/`. Keep `assets/audio_sfx/` and `assets/nook/`.

Failure rule: any copy or hash mismatch before step 3 stops the run; no original is removed (AC-3.4).

Tests (tmp root, fixture book from `tests/fixtures/mini_book.json`):
- `test_migration_moves_every_file_with_equal_hashes` — AC-3.1.
- `test_rerun_changes_nothing` — AC-3.3: second run reports all "already in place", and no file mtime/sha changes.
- `test_copy_failure_leaves_originals` — monkeypatch the copy to raise on the 3rd file; assert all originals still exist with original content (AC-3.4).
- `test_dry_run_writes_nothing`.

CLI: `uv run python -m video_pipeline.migrate_library [--root PATH] [--dry-run]`.

### Phase 3 — Scripts: `build_book.py` and `compose_missing_illustrations.py` (AC-2.1–2.4, FR-4, FR-5)

**Files:** `scripts/build_book.py`, `scripts/compose_missing_illustrations.py`; new `tests/test_book_scripts_paths.py`.

Changes:
- Delete module globals `ROOT`, `ILLUSTRATIONS_DIR`, `BUILD_DIR`, `BOOK_DATA_PATH` (`build_book.py:29-32`) and `ROOT`, `ILL_DIR`, `REF_DIR` (`compose_missing_illustrations.py:12-14`).
- `main(argv=None)`: `--book` (required, slug) and `--root` (default `PROJECT_ROOT`). `paths = library.book_folder(args.book, args.root)` (raises → exit 2 with the message, no writes).
- Functions that read `ILLUSTRATIONS_DIR` or `BUILD_DIR` take `paths: BookFolder` as a parameter: `create_front_cover_image`, `create_back_cover_image`, `render_front_matter_pages`, `render_back_matter_page`, `build_pdfs`, the EPUB writer, `build_index_html`. Before: `ILLUSTRATIONS_DIR / "page_01.png"` → after: `paths.illustrations / "page_01.png"`.
- Output names: `BUILD_DIR / "The_Snail_Mail_Express_20Page_Digital_Edition.pdf"` → `paths.build_dir / f"{stem}_20Page_Digital_Edition.pdf"` where `stem = file_stem(book["metadata"]["title"])`. The same change applies to every `The_Snail_Mail_Express*` literal (`build_book.py` lines ~783–1287) and to the HTML `href`/`src` strings in `index.html`.
- `compose_missing_illustrations.py`: `ILL_DIR` → `paths.illustrations`; references from `paths.references`.

Tests (`test_book_scripts_paths.py`):
- `test_build_requires_book` — `main([])` → `SystemExit` code 2 (AC-2.3).
- `test_build_unknown_slug_exits_nonzero_and_writes_nothing` — `main(["--book", "nope", "--root", tmp])` → code ≠ 0, tmp tree unchanged (AC-2.2).
- `test_output_names_use_title_stem` — with a mini book fixture (title "Moon Kite Race") the planned output names start with `Moon_Kite_Race_` (AC-2.1). A pure helper `plan_outputs(paths, title) -> dict[str, Path]` exposes the names so the test does not render PDFs.
- `test_other_book_untouched` — two books in tmp root; plan/build for one does not touch the other (AC-2.4): hash the other book's folder before and after `plan_outputs` + the path helpers. The full render is exercised in Phase 7.

### Phase 4 — Video pipeline CLIs (AC-2.1, AC-2.4, AC-2.5, FR-6)

**Files:** `video_pipeline/episode_tools.py`, `generate_script.py`, `synthesize_voices.py`, `render_episode.py`, `mcp_server.py`, `video_pipeline/tts.py` if it resolves paths; tests listed below.

Changes:
- `episode_tools.output_path(book, episodes_dir)` → `output_path(folder: BookFolder) -> Path` returning `folder.script_path`. The slug now comes from the folder, not `slugify(title)`. `slugify` stays for tests and `file_stem` is separate.
- `EpisodeTools.from_files(book_path, config_dir, seed, episodes_dir, force)` → `from_files(folder: BookFolder, config_dir, seed, force)`. Agent logs go to `folder.build_dir / "agent"` (the `agent/` path in `agents.py`).
- `generate_script.py`: `--book` (required), `--root` (default `PROJECT_ROOT`); remove `--book PATH`, `--episodes-dir`. Its MCP child process gets `--book <slug> --root <root>`, not a path.
- `synthesize_voices.py`: `--book`, `--root`; `script = folder.script_path`, `AudioPaths(folder.build_dir / "audio")`; `config_dir = root/"config"`. Its `slug` comes from `folder.slug`.
- `render_episode.py`:
  - `_load_inputs(folder, root)`: `pictures = resolve_pictures(book, folder, root)` where illustrations come from `folder.illustrations`, nooks from `folder.nooks` then `root/assets/nook`, sfx from `root/assets/audio_sfx`.
  - `VideoPaths(folder.build_dir / "video")`; the sfx cache stays at `root/build/sfx/`.
  - `render_check(folder, root, ...)` and `render_video(folder, root, ...)` replace the path-list signatures.
- `mcp_server.py`: `--book <slug>` and `--root`.

Tests updated (call sites, same assertions, new call signatures): `tests/test_generate_script.py` (lines 85, 269–270), `tests/test_synthesize_cli.py` (63–65, 207–208), `tests/test_synthesize_voices.py`, `tests/test_render_cli.py`, `tests/test_render_ffmpeg.py`, `tests/test_mcp_server.py`, `tests/test_codex_integration.py` (line 22), `tests/test_timeline.py` (line 510 `PILOT_SCRIPT` → `books/the-snail-mail-express/episode/episode_script.json`), `tests/helpers.py` (`PILOT_BOOK`). New test: `test_video_commands_write_only_inside_the_book` — run `render_check` / `synthesize_voices` status on a tmp root with two books; assert the other book's files are unchanged (AC-2.5).

### Phase 5 — Studio (AC-5.1–5.3, FR-9)

**Files:** `video_pipeline/studio/books.py`, `jobs.py`, `app.py`; tests `test_studio_books.py`, `test_studio_jobs.py`, `test_studio_app.py`.

Changes:
- `StudioPaths(root)`: remove the pilot special case. `books_dir`, `build_dir` (project-level: `studio/`, `sfx/`, `voice-samples/`), `config_dir`, `assets_dir` (shared only). Per-book paths come from `library.BookFolder`.
- `list_books(paths)`: `library.list_book_slugs(root)`; each valid slug becomes a `Book` read from `book.json`; each problem becomes a listed entry with `error` (AC-5.2).
- `save_book(paths, book, files, replace)`: writes `folder/book.json` (an upload named `book_data.json` is accepted and saved as `book.json`); `illustrations/` → `folder/illustrations/`; nooks → `folder/nooks/` (was `episodes/<slug>/`); swap via temp dir as now.
- `jobs.py` command: `[..., "--book", slug, "--root", str(paths.root)]` replaces `--book <path> --episodes-dir ...`.
- `app.py`: media whitelist uses `folder.nooks` and `root/assets/nook`; `folder.illustrations` for pictures; the upload form text names `book.json`.

Tests: rewrite the pilot-as-root-file fixtures in the three studio test files to `books/<slug>/book.json`; add `test_invalid_folder_listed_with_problem_others_still_listed` (AC-5.2) and `test_stage_status_reads_only_its_folder` (AC-5.3).

### Phase 6 — Ignore rules and docs (FR-8)

- `.gitignore`: add `books/*/build/`; remove `episodes/*/agent/` (now inside `books/*/build/`). The existing `build/` pattern already matches `books/*/build/`, so the new line makes it explicit.
- `CLAUDE.md`: commands use `--book <slug>`; architecture section describes `books/<slug>/`. Remove the "hardcoded paths" bullet once fixed.
- `.claude/rules/data-pipeline.md`: the `book_data.json` and `episodes/` lines become `book.json` and `books/<slug>/episode/`.
- `.claude/memory/steering/conventions.md`: directory structure section.
- `PRD.md` is historical, left as is.

### Phase 7 — Cut-over and verification (AC-3.1, AC-3.2, cut-over)

1. `uv run python -m video_pipeline.migrate_library --dry-run` — review the list.
2. `uv run python -m video_pipeline.migrate_library` — move the pilot.
3. `uv run python scripts/build_book.py --book the-snail-mail-express` — regenerate outputs under `books/the-snail-mail-express/build/`.
4. Compare with `scratchpad/baseline/pilot.json`: 20-page PDF, reader PDF, KDP interior, and wrap cover match page count and page size; extracted-text hashes match; EPUB regenerated (sha may differ on timestamps, so check that it opens and has 20 pages in its spine). Any mismatch stops the cut-over.
5. `render_check` on the pilot → approval still valid (confirms `settings_sha256` independence from paths). If not valid, stop and report: a re-approval is needed.
6. Delete the old flat pilot outputs in root `build/` and the now-empty `episodes/` folder, only after step 4 passes.
7. `uv run pytest` (expected: 607 + new tests passing, 12 deselected) and `uv run ruff check video_pipeline tests scripts`.
8. `git status`: `books/*/build/` and `build/` are not listed; `books/*/book.json`, `illustrations/`, `episode/` are listed.

## Verification summary

- Unit: `uv run pytest -q` (all phases).
- Migration: dry run, then migrate, then rerun = no-op (AC-3.3).
- Build equivalence: pilot.json comparison (AC-3.2).
- Approval: `render_check` on migrated pilot (no re-approval).
- Isolation: other-book hash tests (AC-2.4, AC-2.5).
- Lint: `ruff check`.

## Risks

- Test churn: roughly 40 call sites across 12 test files. Mitigation: `tests/helpers.py` gets a `make_book_folder(root, slug)` helper.
- `build_book.py` is large. Mitigation: the output-name stem is the only change to rendering logic; a pilot baseline comparison catches regressions.
- Studio upload is replaced in spec 007. This phase only moves its target folders.
