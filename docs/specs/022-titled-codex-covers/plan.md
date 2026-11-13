# Plan 022: Titled front covers painted by Codex

**Spec:** docs/specs/022-titled-codex-covers/spec.md (Approved)
**Created:** 2026-11-13

## Context

New books should open on a cover Codex paints whole: title, subtitle and the Giggle & Snooze logo
inside the art, in the book's own medium (approved sample:
`books/mira-and-the-midnight-ticket/build/cover_samples/cover_front_sample_b.png`, made by
a one-off scratch script). Each new book gets two front covers: a 4:3 video
cover and a square print/EPUB cover. Every cover is read back by Codex and accepted only when the
words match and the logo is whole. The 8 existing books must not change.

Working tree is `feat/014-studio-redesign` with uncommitted work from specs 012–021. Branch
`feat/022-titled-codex-covers` from it at the start of execution; touch only the files named here.

## Global Constraints

- Existing books (no `metadata.cover_style`) keep exactly today's pictures, prompts, renders and
  print builds (FR-1); `tests/fixtures/legacy_golden.json` must not change.
- Up to 3 paintings per cover; a cover that never passes is never saved (AC-3.3, AC-3.4).
- Word match ignores only letter case, line breaks, repeated spaces and curly vs straight quotes
  (AC-3.2).
- Logo about 14% of the cover width; whole logo ≥12% of the width from left/right and ≥5% from
  top/bottom of the finished cover (AC-1.2). These values live in `config/covers.json`, not code.
- No real Codex calls in unit tests: inject the subprocess runner / `paint` callable.
- Ruff (py312, line 100); `pathlib.Path`; atomic JSON writes; tests first in every phase.

## Goal

`illustrate` paints, checks and saves `cover_front.png` (4:3) and `cover_front_print.png` (square)
for titled-cover books; render, print build and studio use them without adding lettering.

## Design summary

- **Marker:** `metadata.cover_style = "titled"`, added to `config/book_defaults.json` metadata so
  `Drafts.create_book` (video_pipeline/drafts.py:168, merges `_defaults()`) gives it to every new
  book. Older books lack it. `episode.book_problems` accepts the key only with value `"titled"`.
- **New module `video_pipeline/covers.py`** — pure pieces only (no subprocess, no import of
  illustrate, so render/build/studio can import it): settings, marker, request text, trim, check
  command, reading parse, word match.
- **`video_pipeline/illustrate.py`** gets the painting loop `paint_titled_cover` (it already owns
  the Codex subprocess, `IllustrateError`, `check_image`, `IMAGE_TIMEOUT_S`). `plan_images`
  yields the titled request for the two front stems of a titled book; `illustrate()` and
  `repaint_one` use a `paint_cover` callable for those stems. The existing `paint_one`,
  `STYLE_LINE` ("No text, letters or logos") and 16:9 crop stay for pages and old covers.
- File names: video cover keeps `cover_front.png` (render, thumbnails and `resolve_pictures`
  need no change of name); print cover `cover_front_print.png`; back cover unchanged.

---

## Phase 0: Settings, marker and contracts

**Implements:** FR-1, FR-2 (config) | **Satisfies:** AC-1.6 (config part)
**Files:** `config/covers.json` (new), `config/book_defaults.json`, `video_pipeline/covers.py`
(new), `video_pipeline/episode.py`, `tests/test_covers.py` (new), `tests/test_episode.py`,
`tests/helpers.py` (copy `covers.json` into test roots like the other config files)
**Interfaces:** Consumes: nothing. Produces: `CoverShape`, `CoverSettings`,
`load_cover_settings(config_dir: Path) -> CoverSettings`, `titled_covers(book: dict) -> bool`,
`TITLED_STEMS = ("cover_front", "cover_front_print")`, `CoverError(Exception)`.

`config/covers.json`:
```json
{
  "logo": "assets/brand/giggle_and_snooze_logo.png",
  "logo_name": "Giggle & Snooze",
  "logo_width_pct": 14,
  "safe_side_pct": 12,
  "safe_top_bottom_pct": 5,
  "tries": 3,
  "shapes": {
    "cover_front": {
      "label": "video cover", "ratio": [4, 3], "accept": [1.0, null],
      "frame": "landscape 4:3 (generate at 1536x1024; the left and right edges are trimmed to 4:3)"
    },
    "cover_front_print": {
      "label": "print cover", "ratio": [1, 1], "accept": [0.77, 1.3],
      "frame": "square 1:1 (generate at 1024x1024)"
    }
  },
  "layout_rules": [
    "The title is the hero: large, bold, perfectly spelled, hand-lettered in this book's own medium and integrated into the scene, not a flat font pasted on top. Place it where it reads best for this composition.",
    "The subtitle is smaller, near the title, perfectly spelled.",
    "The main character is large and appealing in an inviting moment from the story, in the book's setting, with one clear focal point and depth.",
    "No other text: no author name, no barcode, no price, no extra words."
  ]
}
```
`accept` = allowed width/height of the painted picture before trimming (`null` = no upper
bound): portrait refused for the video cover, beyond 1.3:1 either way refused for print (FR-3).

`covers.py` contracts:
```python
TITLED_STEMS = ("cover_front", "cover_front_print")

class CoverError(Exception):
    """A cover setting, shape or reading problem; the message names the file or field."""

@dataclass(frozen=True)
class CoverShape:
    label: str
    ratio: tuple[int, int]
    accept: tuple[float, float | None]
    frame: str

@dataclass(frozen=True)
class CoverSettings:
    logo: Path            # relative to the project root
    logo_name: str
    logo_width_pct: int
    safe_side_pct: int
    safe_top_bottom_pct: int
    tries: int
    shapes: dict[str, CoverShape]   # keyed by stem
    layout_rules: list[str]

def load_cover_settings(config_dir: Path) -> CoverSettings: ...  # CoverError naming file/field

def titled_covers(book: dict) -> bool:
    return (book.get("metadata") or {}).get("cover_style") == "titled"
```
- Tests: `test_settings_load_from_config`, `test_missing_or_bad_settings_name_the_field`,
  `test_titled_covers_only_with_marker`, `test_book_defaults_mark_new_books_titled`,
  `test_book_problems_refuse_unknown_cover_style` (in test_episode.py),
  `test_create_book_marks_titled_covers` (test_drafts.py).
- Run `uv run pytest tests/test_covers.py tests/test_episode.py tests/test_drafts.py` — FAIL,
  implement, PASS. Commit `feat(covers): settings and titled-cover marker (spec 022)`.

---

## Phase 1: Cover requests, trim and reading (pure)

**Implements:** FR-2, FR-3, FR-4 (pure parts) | **Satisfies:** AC-1.2, AC-1.4, AC-1.5, AC-2.1
(request), AC-3.2
**Files:** `video_pipeline/covers.py`, `tests/test_covers.py`
**Interfaces:** Consumes: Phase 0. Produces:
`cover_request(book: dict, art: dict | None, stem: str, settings: CoverSettings) -> str`,
`build_cover_command(binary: Path, request: str, logo: Path, reference: Path | None) -> list[str]`,
`trim_to_ratio(path: Path, shape: CoverShape) -> None` (in place; `CoverError` when outside
`accept`), `build_check_command(binary: Path, cover: Path, schema: Path, reply: Path,
logo_name: str) -> list[str]`, `CHECK_SCHEMA: dict`, `CoverReading` (dataclass: `title`,
`subtitle`, `logo_whole`), `parse_reading(text: str) -> CoverReading` (`CoverError` when not the
schema), `words_match(expected: str, read: str) -> bool`, `check_problem(reading, title,
subtitle) -> str | None` (None = pass, else the reason).

- `cover_request` assembles, from book data only (no fixed font/colours, AC-1.5): frame line
  (shape `frame`), `Book: "<title>" — subtitle "<subtitle>"`, `Story: <blurb>`, art direction
  (blueprint `art`: `style_prompt_prefix`, `medium_label`, `line`, `lighting`, palette
  `name hex` list, `avoid`; without a blueprint `style_bible.style_prompt_prefix`, AC-1.4), the
  reference note ("Reference image 1 is the channel logo "Giggle & Snooze" … Reference image 2 is
  a page from this book: keep the characters' looks"), the logo rule built from the settings
  (faithful glossy logo, about `logo_width_pct`% of width, complete; whole logo ≥ `safe_side_pct`%
  from left/right and ≥ `safe_top_bottom_pct`% from top/bottom of the finished cover; not
  restyled), `layout_rules`, and `characters.cover_characters_text(book)` (spec 018) when non-empty.
  Text proven by the Mira sample (the one-off scratch script) is the template.
- `build_cover_command` = same flags as `illustrate.build_image_command` (exec, ephemeral,
  skip-git-repo-check, ignore-user-config, ignore-rules, workspace-write, web search disabled),
  `-i logo` then `-i reference` (AC-1.3), task "Use the image generation tool to create this
  picture, then copy the generated image file to ./out.png …" + request. No `STYLE_LINE`.
- `trim_to_ratio`: refuse outside `accept`; else centre-crop evenly to `ratio` exactly (same
  method as `illustrate.crop_to_landscape`), save PNG.
- Check request (FR-4): attaches only the trimmed cover; asks for the main title and the subtitle
  exactly as lettered (line breaks as spaces) and whether the whole `logo_name` logo is visible and
  not cut by an edge. The expected title is deliberately not sent, so the reader cannot echo it;
  the comparison is in code. Flags as above plus `--output-schema schema -o reply` with
  ```python
  CHECK_SCHEMA = {
      "type": "object",
      "properties": {"title": {"type": "string"}, "subtitle": {"type": "string"},
                     "logo_whole": {"type": "boolean"}},
      "required": ["title", "subtitle", "logo_whole"],
      "additionalProperties": False,
  }
  ```
- `words_match`:
  ```python
  QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

  def _norm(text: str) -> str:
      return " ".join(text.translate(QUOTES).split()).casefold()

  def words_match(expected: str, read: str) -> bool:
      return _norm(expected) == _norm(read)
  ```
- `check_problem` → `"title read as '…'"`, `"subtitle read as '…'"` or `"logo not whole"`.
- Tests: `test_request_has_exact_words_art_layout_and_safe_area`,
  `test_request_without_blueprint_uses_style_bible`, `test_requests_differ_per_book`,
  `test_print_request_is_square`, `test_request_adds_character_looks`,
  `test_cover_command_attaches_logo_then_page_and_no_style_line`,
  `test_trim_to_4_3_and_square_exactly`, `test_trim_refuses_portrait_video_and_wide_print`,
  `test_check_command_sends_only_the_cover_and_schema`, `test_parse_reading_ok_and_bad`,
  `test_words_match_ignores_case_breaks_spaces_quotes` (and fails on a missing letter, extra word,
  changed punctuation), `test_check_problem_reasons`.
- FAIL → implement → PASS. Commit `feat(covers): cover requests, trim and read-back (spec 022)`.

---

## Phase 2: Paint, check and retry in illustrate

**Implements:** FR-1, FR-3, FR-4 | **Satisfies:** AC-1.1, AC-1.3, AC-1.6, AC-2.1, AC-3.1,
AC-3.3, AC-3.4, AC-3.5, AC-5.1, AC-5.3
**Files:** `video_pipeline/illustrate.py`, `tests/test_illustrate.py`
**Interfaces:** Consumes: Phases 0–1. Produces:
`paint_titled_cover(binary, prompt, reference, target, *, words: tuple[str, str],
settings: CoverSettings, root: Path, run=subprocess.run, log=print) -> None` (same first four
parameters as `paint_one`, so it slots into `illustrate()` / `repaint_one`); CLI
`--cover front|print|back`.

- `plan_images(book, style)`: for a titled book the cover items are
  `("cover_front", cover_request(..., "cover_front", ...))`,
  `("cover_front_print", cover_request(..., "cover_front_print", ...))`, then the unchanged back
  cover. The titled requests carry their own art text, so `style` is not prefixed to them. Needs
  `art` → new keyword `art: dict | None = None` (main passes `bp.art.model_dump()`), and
  `settings` loaded in `main`. Old books: identical list (golden).
- `illustrate(..., paint_cover=None)`: when `stem in TITLED_STEMS` and `paint_cover` is set, call
  it instead of `paint`. `repaint_one(..., paint=...)` is called with `paint_cover` for those
  stems by `_repaint_main`.
- `paint_titled_cover` loop:
  ```python
  logo = root / settings.logo
  if not logo.is_file():
      raise IllustrateError(f"{target.name}: logo not found: {logo}")   # AC-1.6
  shape = settings.shapes[target.stem]
  reason = ""
  for attempt in range(1, settings.tries + 1):
      with tempfile.TemporaryDirectory(prefix="kiddo-cover-") as tmp:
          work = Path(tmp)
          try:
              out = _paint_cover_once(binary, prompt, logo, reference, work, run)  # out.png
              trim_to_ratio(out, shape)
              check_image(out)
              reading = _read_cover(binary, out, settings, work, run)
              reason = check_problem(reading, *words) or ""
          except (IllustrateError, CoverError) as exc:
              reason = str(exc)
          if not reason:
              partial = target.with_name(target.name + ".tmp")
              partial.write_bytes(out.read_bytes()); partial.replace(target)
              log(f"  {target.stem}: accepted after {attempt} painting(s); read title "
                  f"{reading.title!r}, subtitle {reading.subtitle!r}")        # AC-3.5
              return
          log(f"  {target.stem}: try {attempt} failed: {reason}")
  raise IllustrateError(f"{target.name}: failed the cover check {settings.tries} times; "
                        f"last: {reason}")                                    # AC-3.4
  ```
  A timeout of either Codex call (`subprocess.TimeoutExpired`, `IMAGE_TIMEOUT_S`) or an
  unreadable reply becomes an `IllustrateError` inside the try, so it counts as a failed try (FR-4).
  The logo check happens before the first cover, so neither cover is painted (AC-1.6); pages
  already saved stay (each is written on its own).
- `main`: load settings; for a titled book bind
  `paint_cover = partial(paint_titled_cover, words=(title, subtitle), settings=settings,
  root=args.root)`. `--cover print` on an old book → `IllustrateError("this book has no print
  cover (it was made before titled covers)")`. Old book `--cover front` → old request (AC-5.3).
  `record_use` count already uses `len(plan_images(...))`, which now includes the print cover.
- Tests (fake `run` writes `out.png` sized as asked and the reply JSON at `-o`):
  `test_titled_book_paints_both_front_covers_with_logo_and_page_references`,
  `test_accepts_first_passing_try_and_logs_reading`, `test_retries_wrong_words_then_passes`,
  `test_logo_cut_counts_as_failed_try`, `test_check_timeout_or_bad_reply_counts_as_failed_try`,
  `test_wrong_shape_is_painted_again`, `test_three_failures_save_nothing_and_name_reason`,
  `test_missing_logo_paints_no_cover_and_keeps_pages`,
  `test_repaint_print_cover_keeps_bak_and_restores_on_failure`,
  `test_old_book_cover_prompts_unchanged` (golden), `test_print_cover_refused_for_old_book`,
  `test_titled_requests_not_prefixed_with_style`.
- FAIL → implement → PASS. Commit `feat(illustrate): paint and check titled covers (spec 022)`.

---

## Phase 3: Render and print use titled covers as painted

**Implements:** FR-5 (render, print) | **Satisfies:** AC-2.2, AC-2.3, AC-4.1, AC-4.2, AC-4.3
**Files:** `video_pipeline/render_episode.py`, `scripts/build_book.py`,
`tests/test_render_episode*.py` (existing render tests file), `tests/test_book_scripts_paths.py`
(or the existing build_book test file)
**Interfaces:** Consumes: `covers.titled_covers`. Produces: nothing new.

- render_episode.py:893:
  ```python
  if folder.blueprint_path.is_file() and not titled_covers(book):
      cover_notes = _use_titled_cover(book, folder, cfg, pictures, Path(root))
  ```
  The intro then uses `illustrations/cover_front.png` as is; the existing canvas centres the whole
  picture over the blurred copy (4:3 in 16:9, AC-4.1). Section caches key on the picture sha, so a
  repainted cover rebuilds only the intro (AC-4.3); old books unchanged (AC-4.2).
- build_book.py `create_front_cover_image`: at the top
  ```python
  if titled_covers(book_data):
      raw = paths.illustrations / "cover_front_print.png"
      if not raw.is_file():
          raise FileNotFoundError(f"print cover not found: {raw} (run illustrate --cover print)")
      with Image.open(raw) as im:
          final_img = ImageOps.fit(im.convert("RGB"), (size_px, size_px),
                                   Image.Resampling.LANCZOS)
      final_img.save(out_png, quality=98, dpi=(300, 300))
      return final_img
  ```
  Wrap cover, reader PDF, EPUB and flipbook all read `_Front_Cover.png` / the returned image
  (build_book.py:1210, 1529, 1591), so they follow with no lettering, plaque or badge (AC-2.2,
  AC-2.3).
- Tests: `test_titled_book_render_skips_lettered_cover` (render world from
  `tests/render_helpers.make_world` with the marker; `compose_cover` never called, intro picture
  is `illustrations/cover_front.png`), `test_old_book_render_still_letters_cover`,
  `test_repainted_titled_cover_rebuilds_intro_only`, `test_titled_print_cover_used_without_lettering`
  (front PNG pixels equal the fitted print cover), `test_titled_book_missing_print_cover_fails`.
- FAIL → implement → PASS. Commit `feat(render,build): use titled covers as painted (spec 022)`.

---

## Phase 4: Studio shows and repaints both covers

**Implements:** FR-5 (studio) | **Satisfies:** AC-5.1, AC-5.2
**Files:** `video_pipeline/studio/books.py`, `video_pipeline/studio/jobs.py`,
`video_pipeline/studio/app.py`, `video_pipeline/studio/templates/book.html`,
`tests/test_studio_books.py`, `tests/test_studio_app.py`, `tests/test_studio_jobs.py`
**Interfaces:** Consumes: `covers.titled_covers`, `load_cover_settings`. Produces:
`cover_files(book: dict) -> dict[str, str]`, `cover_sides(book: dict) -> dict[str, str]`
(side → label).

- books.py: replace the fixed uses of `COVER_FILES` with `cover_files(book)`:
  titled → `{"cover_front.png": "video cover", "cover_front_print.png": "print cover",
  "cover_back.png": "back cover"}`, else today's dict. `expected_files` and `paint_state` use it
  (a titled book is `not_started` until the print cover exists). `cover_front_print.png` joins
  the reserved page names for every book. `cover_sides(book)` → `{"front": "video cover",
  "print": "print cover", "back": "back cover"}` or `{"front": "front cover", "back": "back cover"}`;
  `COVER_SIDES` becomes `("front", "print", "back")` (all values the job accepts).
- jobs.py: validation message "expected front, print or back"; command unchanged
  (`--cover <side>`).
- app.py: book page context `cover_sides=sb.cover_sides(book_data)`; `_repaint_cover` refuses a
  side not in that book's sides (422, nothing starts). Thumbnail stays `cover_front.png` (video
  cover, AC-5.2).
- book.html: loop `{% for side, label in cover_sides.items() %}` → "Repaint {{ label }}".
- Tests: `test_titled_book_lists_video_and_print_covers`, `test_old_book_cover_list_unchanged`,
  `test_titled_paint_state_waits_for_print_cover`, `test_repaint_print_cover_runs_cover_print_job`,
  `test_print_side_refused_for_old_book`, `test_book_page_shows_repaint_buttons_per_cover`.
- FAIL → implement → PASS. Commit `feat(studio): video and print covers (spec 022)`.

---

## Phase 5: Integration verification

**Implements:** All FRs | **Satisfies:** All ACs

- `uv run pytest` — full suite passes; `uv run ruff check video_pipeline scripts tests` and
  `uv run ruff format --check` clean; `legacy_golden.json` unchanged.
- Update CLAUDE.md (illustrate line, covers config, `cover_front_print.png`) and mark spec ACs.
- Commit `feat: titled Codex covers (spec 022)`.

## Quickstart Validation (real Codex, uses image quota)

1. Add a new book from an idea in the studio (or `write_story`) and Create book; confirm
   `metadata.cover_style == "titled"` in its book.json.
2. `uv run python -m video_pipeline.illustrate --book <slug>` — log shows each front cover
   "accepted after N painting(s); read title …". Check `cover_front.png` is 4:3 and
   `cover_front_print.png` is square, both lettered with a whole logo.
3. After the script and voices are approved, `uv run python -m video_pipeline.render_episode
   --book <slug>`; the intro shows the titled 4:3 cover with no title plate.
4. `uv run python scripts/build_book.py --book <slug>`; open `build/index.html` and the EPUB: the
   front cover is the square titled cover, no extra lettering.
5. Studio book page: "Repaint video cover" / "Repaint print cover" buttons; repaint the print
   cover, see the `.bak` kept.
6. Render and build `mira-and-the-midnight-ticket` (old book): output identical to before.
