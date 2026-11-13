# Plan 020: Editions fit each book

**Spec:** spec.md (Approved)

## Context
`scripts/build_book.py` was written for the pilot. Pilot-only content is hardcoded in five places: the half-title ("EX LIBRIS • OFFICIAL POSTMASTER LOG", "Whisperwood Hollow Postal Service…" line), the map page (title, subtitle, `map_whisperwood.png`, legend), the back-matter page ("The Junior Postmaster's Field Guide", treasures, Clara/Barnaby questions), the back-cover headline ("No Rush, No Fuss—Always Delivered!") and the copyright genre line ("Juvenile Fiction / Animals / Woodland"). The KDP interior is always front 3 + story + back 1 (14 pages for a 10-page book), the wrap cover hardcodes `17.304"` (24-page spine), and the file names and flipbook say "20Page"/"24Page"/"of 20"/`idx === 21`.

Working tree: branch `feat/014-studio-redesign` carries uncommitted work; I touch only the files named here and commit nothing unless asked. Spec 018 (character sheet, `style_bible.characters = [{"name","look"}]`) is planned in parallel; this plan reads that key if present and never writes it.

Design decisions:
- **Pilot extras move to the pilot's own `book.json`** under a new top-level `print_extras` object (not `metadata`, so the drafts/blueprint code that builds `metadata` is untouched). Keys: `bookplate_label`, `front_footer`, `back_cover_headline`, `copyright_genre`, `map` (`file`, `title`, `subtitle`, `legend` list), `activity` (`title`, `subtitle`, `items` [{`title`,`text`}], `questions_title`, `questions`). Every key is optional; anything missing falls back to the generic templates. The pilot's values are the exact strings now in code, so its output is unchanged (AC-1.4).
- **Generic templates in `config/book_extras.json`** (FR-2): bookplate label ("This storybook belongs to:" block heading), back-cover headline (empty = none), copyright genre line, activity title ("Can you find…?"), subtitle, `questions_title`, `question_templates` (≥3 strings with `{title}`, `{character}`, `{character2}` placeholders), `drawing_page` caption ("My drawing"), max characters listed (6).
- **Characters:** `style_bible.characters[*].name` (spec 018), else page titles (Error Scenarios row 1). Questions: the first 2–3 templates whose placeholders can be filled (a template needing `{character2}` is skipped with only one name).
- **Map page** appears only when the book has `illustrations/map_*.png` (AC-1.2); its title/legend come from `print_extras.map`, else the title is "Map of <title>" with no legend.
- **Interior page plan** (pure): front matter (half-title, title/copyright, map if any) → one blank filler if needed so story page 1 lands on an even (left) interior page, as today (pilot: page 4) → story pages → "My drawing" pages → activity page last ("THE END"). P = max(24, total) rounded up to even (AC-2.1); padding goes before the activity page. Bleed side by parity, exactly as today (even = left bleed, odd = right) (AC-2.3). Front matter keeps today's `bleed/2` both sides.
- **Spine width** from P: `spine_in = P * 0.002252`; wrap width `2 * (8.5 + 0.125) + spine_in`. For P=24 this is 17.30405" → 5191 px, the same as today's hardcoded 17.304".
- **Names** (pure): `<stem>_<N>Page_Digital_Edition.pdf`, `<stem>_KDP_<P>Page_Print_Interior.pdf` (AC-3.1). Before writing, delete `<stem>_*Page_Digital_Edition.pdf` and `<stem>_KDP_*Page_Print_Interior.pdf` in `build/` whose names differ from the current ones (AC-3.2). The pilot's names stay `…_20Page_…`/`…_KDP_24Page_…`.
- Note: editing the pilot's `book.json` changes its sha256, so the studio's script review mark (`book_sha256`, spec 004) will show the pilot as needing re-review once. Flagged in Risks.

Gates: ≤3 phases, no new module (helpers stay in `build_book.py`, the script under test), contract (config + book data) before code. TDD: each phase starts with failing tests.

## Phase 0 (before any code): capture the pilot golden
Run the current builder against the pilot in a temp root and save page-by-page text (pymupdf `page.get_text()`) of the digital, reader and KDP interior PDFs plus the wrap-cover PNG size to `tests/fixtures/pilot_editions_golden.json`. This is the AC-1.4 regression baseline; it is generated once from today's code and committed with the feature.

## Phase 1: Contract + pure helpers (FR-1, FR-2, FR-3; AC-1.3, AC-2.1, AC-2.2, AC-3.1)
**Implements:** `print_extras` data, `config/book_extras.json`, page plan, names, spine math, activity content.
**Satisfies:** AC-1.3, AC-1.4 (data half), AC-2.1, AC-2.2, AC-3.1, Error Scenarios.
**Files:** `config/book_extras.json` (new), `books/the-snail-mail-express/book.json`, `scripts/build_book.py`, `tests/test_book_editions.py` (new)

Goal: everything count- and text-related is decided by small pure functions before any rendering changes.

```python
@dataclass(frozen=True)
class InteriorPage:
    kind: str            # "half_title" | "title" | "map" | "blank" | "story" | "drawing" | "activity"
    number: int          # 1-based interior page number
    story: dict | None = None

def plan_interior(book: dict, has_map: bool) -> list[InteriorPage]: ...
def edition_names(stem: str, story_pages: int, interior_pages: int) -> dict[str, str]: ...
def spine_inches(interior_pages: int) -> float: ...           # P * KDP_COLOR_PAGE_INCHES
def wrap_size_inches(interior_pages: int) -> tuple[float, float]: ...
def character_names(book: dict) -> list[str]: ...            # sheet names, else page titles
def activity_content(book: dict, extras: dict) -> dict: ...  # print_extras.activity or filled templates
def load_extras(root: Path, book: dict) -> dict: ...         # config defaults overlaid with book print_extras
def stale_editions(build_dir: Path, stem: str, keep: set[str]) -> list[Path]: ...
```

- Constants in one place (`KDP_MIN_PAGES = 24`, `KDP_COLOR_PAGE_INCHES = 0.002252`, trim/bleed).
- Pilot `book.json` gains `print_extras` with today's strings verbatim (map legend, field-guide items, Clara/Barnaby questions, postal footer, back-cover headline, genre line).

Tests (`tests/test_book_editions.py`, no rendering):
- `plan_interior`: pilot (map, 20 pages) → 24 pages, story 1 on page 4, activity on 24, no drawing/blank; 10-page book without map → 24, blank at 3, story 1 on page 4, 10 drawing pages, activity on 24; 30-page book → 36 (even, no padding beyond rounding); odd total rounds up by one drawing page; story pages keep `page_number` order; parity → bleed side table.
- `edition_names(“Jack…”, 10, 24)` → `…_10Page_Digital_Edition.pdf`, `…_KDP_24Page_Print_Interior.pdf`; pilot → today's names.
- `spine_inches(24)` and wrap px = 5191×2625 (today's); P=36 gives a wider wrap.
- `character_names`: sheet present → names (max 6); absent → page titles.
- `activity_content`: pilot → its `print_extras.activity` verbatim; Jack → title "Can you find…?", list of names, 2–3 questions containing the book's title/characters, no `{` left; one character → `{character2}` templates skipped.
- `load_extras`: missing `config/book_extras.json` key → clear error naming file and key.
- `stale_editions`: `X_20Page_Digital_Edition.pdf` and `X_KDP_14Page_Print_Interior.pdf` returned, current names and other books' stems kept.

## Phase 2: Render from the plan (FR-1, FR-3; AC-1.1, AC-1.2, AC-2.1–2.3, AC-3.2)
**Implements:** front/back matter and covers read `extras`; `build_pdfs` walks `plan_interior`; drawing and blank pages; wrap cover sized from P; stale file removal.
**Satisfies:** AC-1.1, AC-1.2, AC-1.4, AC-2.1, AC-2.2, AC-2.3, AC-3.1, AC-3.2.
**Files:** `scripts/build_book.py`, `tests/test_book_editions.py`, `tests/test_book_scripts_paths.py`

- `render_front_matter_pages(…, extras, has_map)`: bookplate picture is the book's own page 1 (`pages[0].illustration_file`), labels/footer from `extras`; map page only when `map_*.png` exists.
- `render_back_matter_page(…, activity)`: "Can you find…?" checkbox list (names, no descriptions for generic books; pilot items keep their descriptions) + questions + "THE END".
- `render_drawing_page`: framed blank area, caption "My drawing", the book's title small at the foot. `render_blank_page`: cream fill only.
- `create_back_cover_image`: headline from `extras` (skipped when empty); `copyright_lines` genre line and reading-age/grade line from `metadata.age_range`/`grade_level` for non-pilot books (pilot keeps its exact strings via `print_extras`).
- `create_kdp_wrap_cover(front, back, png, pdf, interior_pages)`: size from `wrap_size_inches`; `main` computes P via `plan_interior` and passes it. Cover fallbacks `page_04`/`page_20` become first/last story page.
- `build_pdfs` returns the names it wrote; `main` deletes `stale_editions` after a successful build.

Tests:
- Render the mini fixture book (`tests/helpers.MINI_BOOK`, tiny generated PNGs) into `tmp_path`, extract text with pymupdf: no "Whisperwood", "Postmaster", "Clara", "Barnaby", "Snail Mail" in any PDF unless in that book.json (AC-1.1); title/author/dedication present; KDP interior page count == planned P and even and ≥24; "My drawing" appears P − front − N − back times; no map page without `map_*.png`, one with it (AC-1.2).
- Interior page sizes: story pages’ image x-offset differs by parity as today (check `page.get_images` bbox on two consecutive story pages) (AC-2.3).
- Wrap PDF width == `wrap_size_inches(P)` (AC-2.2).
- Rebuild with leftover `<stem>_20Page_Digital_Edition.pdf` / `<stem>_KDP_14Page_Print_Interior.pdf` → removed; current files and other stems kept (AC-3.2).
- Pilot golden: build the pilot into a temp root (book.json copy + symlinked illustrations), compare per-page text and page counts with `tests/fixtures/pilot_editions_golden.json` (AC-1.4).
- Update `test_build_writes_only_into_the_book_build_folder` for the new `create_kdp_wrap_cover` argument; add a source check that `build_book.py` contains none of the five banned words.

## Phase 3: Flipbook and docs (AC-3.1; FR-3)
**Implements:** flipbook links and page counter from the real counts.
**Satisfies:** AC-3.1.
**Files:** `scripts/build_book.py`, `tests/test_book_editions.py`, `CLAUDE.md`

- `build_interactive_flipbook(book, names, *, paths)`: PDF links use `edition_names`; button labels "<N>-Page PDF" / "KDP <P>-Page Print PDF"; JS uses `pages.length` (`of ${pages.length}`, back cover index `pages.length + 1`); image path `../illustrations/` (the book folder layout since spec 005).
- Module docstring and `CLAUDE.md` `build_book.py` outputs line: "N-page digital PDF", "KDP interior (≥24 pages, even)", `print_extras` and `config/book_extras.json`.
- Tests: flipbook HTML for a 10-page book has hrefs equal to the files written, "of ${pages.length}" logic, no "20Page"/"24Page" unless those are the real counts; pilot flipbook links unchanged.

## Verify
`uv run pytest tests/test_book_editions.py tests/test_book_scripts_paths.py tests/test_brand.py`, then full `uv run pytest` and `uv run ruff check`. Then real builds: `.venv/bin/python scripts/build_book.py --book the-snail-mail-express` (file names unchanged, golden passes, build time noted against today's) and `--book jack-and-the-topsy-turvy-dream` (`Jack_and_the_Topsy_Turvy_Dream_10Page_Digital_Edition.pdf`, `…_KDP_24Page_Print_Interior.pdf` with 24 pages, "Can you find…?" from page titles, no pilot words; open `build/index.html` and check links and pictures). Build time for a 20-page book must stay within 2× today's (NFR).

## Risks
- Pilot `book.json` edit changes its sha256: the studio shows the pilot's script review as stale once (re-mark reviewed; no audio/script change).
- Golden is text-only: layout shifts that keep text identical would pass; mitigated by page-count, page-size and wrap-size checks.
- Blank filler page before the story (to keep story spreads as today) is a design choice not named in the spec; confirm with the user.
- Spec 018 may land with a different key; `character_names` is the single place to adapt.
