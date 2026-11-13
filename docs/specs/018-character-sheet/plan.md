# Plan 018: Characters look the same on every page

**Spec:** spec.md (Approved)

## Goal
A book written from an idea carries a character sheet (`style_bible.characters`) and per-page
`characters_on_page`. Every picture request quotes the look of the characters it shows; covers
quote the whole sheet. The studio lists the sheet on the draft and book pages. Books without a
sheet (the pilot with `clara_description`/`barnaby_description`, older idea books) behave exactly
as today.

## Context and constraints
- Working tree: branch `feat/014-studio-redesign` carries uncommitted work from specs 012–017. Work
  on top of it, touch only the files named here, commit nothing unless asked.
- **Data shape (kept simple on purpose):** `style_bible.characters = [{"name": str, "look": str}]`,
  page `characters_on_page: [str]` (names from the sheet; may be empty for a scenery-only page).
  Spec 020 (planned in parallel) reads `style_bible.characters` for back matter; spec 019 will
  edit `illustrate.py` too. All sheet logic lives in one small module, `video_pipeline/characters.py`,
  so 019/020 import it instead of re-parsing, and the `illustrate.py` change stays a few lines in
  `plan_images`.
- The writer submits the sheet as a top-level `characters` key next to `metadata` and `pages`
  (AC-1.1); `create_book` moves it into `style_bible.characters` (AC-1.4).
- Two levels of checking:
  - **Writer check** (`story_tools.book_errors`, strict, AC-1.1–1.3): sheet required, 1–6 entries,
    name 1–40 chars, look 1–2 sentences and 20–300 chars, unique names, kid-safe terms, every page
    has a `characters_on_page` list of known names.
  - **Load check** (`episode.book_problems`, lenient, FR-3 hand-editable): only when the fields are
    present, their shape must be right (list of `{name, look}` non-empty strings; names unique;
    `characters_on_page` a list of strings naming sheet entries). Absent fields add no problem, so
    the pilot and every existing book still load.
- FR-3: no new staleness rule. Editing a look in `book.json` does not repaint anything; existing
  rules (blueprint `art` stale, missing pictures) are unchanged. `blueprint.rebuild_style_bible`
  already uses `setdefault(...)[...] =`, so it keeps `characters` (pinned by a test).
- No extra model calls (NFR): the sheet comes from the same `submit_book` run.
- TDD: each phase starts with failing tests. Three phases.

## Phase 1: Sheet contract, writer check and Create book
**Implements:** FR-1, FR-3 · **Satisfies:** AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-2.3 (load side)
**Files:** `video_pipeline/characters.py` (new), `video_pipeline/episode.py`,
`video_pipeline/story_tools.py`, `video_pipeline/write_story.py`, `video_pipeline/story_mcp.py`,
`video_pipeline/drafts.py`, tests below.

Goal: the writer must return a valid sheet, and Create book stores it.

- `characters.py` (pure, no imports from `episode`/`story_tools` to avoid cycles):
```python
MAX_CHARACTERS = 6
NAME_LENGTH = (1, 40)
LOOK_LENGTH = (20, 300)
LOOK_SENTENCES = (1, 2)

def sheet(book: dict) -> list[dict]:
    """style_bible.characters, or [] when the book has none (pilot, older books)."""

def sheet_problems(book: dict, where: str) -> list[str]:
    """Shape problems of the optional fields (load check); [] when they are absent."""
```
- `episode.book_problems` appends `characters.sheet_problems(book, where)` (after the page loop).
- `story_tools.book_errors(book, max_pages, terms, gentle, require_characters=True)` adds
  `character_errors(book, terms, gentle)` on the top-level `characters`: messages name the field,
  e.g. `characters: must hold 1 to 6 characters`, `characters[2].look: must be 20 to 300
  characters`, `page 7: characters_on_page names unknown character 'Zib'`. With
  `require_characters=False` a missing `characters` key is not a problem (only shape is checked).
- `DraftStore.update_page_text` passes `require_characters="characters" in book`, so a draft written
  before this spec can still have its page text edited.
- `write_story.BOOK_PROMPT`: step 2 adds `"characters_on_page"` (names of the characters shown,
  from the sheet); a new step asks for `"characters"`: 1–6 recurring characters, each `name` and a
  fixed `look` (1–2 sentences: body, colours, clothing, distinguishing features, e.g. number of
  eyes), and says each page `prompt` must use the same names. Step 3's `submit_book` shape gains
  `"characters": [...]`. `story_mcp.submit_book` docstring gets the same shape.
- `drafts.create_book`: `if book.get("characters"): style_bible["characters"] = book["characters"]`
  (works with or without a blueprint); pages are copied as they are, so `characters_on_page` stays.
- Tests (write first):
  - `tests/test_characters.py` (new): `sheet()` returns [] for the pilot `book.json` and for a book
    with no `style_bible`; `sheet_problems` is [] when fields are absent; refuses a non-list sheet,
    an entry without `look`, duplicate names, a non-list `characters_on_page`, an unknown name
    (message names the page and the name).
  - `tests/test_story_tools.py`: update the shared `book()` fixture to carry a 2-character sheet
    and `characters_on_page` on every page (it feeds `tests/helpers.add_book_from_draft` and other
    studio tests). New: missing `characters` refused naming the field (AC-1.2); 0 and 7 characters
    refused; name of 41 chars refused; look of 19 / 301 chars and of 3 sentences refused; a look
    with a disallowed term refused; page missing `characters_on_page` refused naming the page; an
    unknown name refused naming page and name (AC-1.3); `StoryTools.submit_book` saves a valid
    book and refuses an invalid one without saving.
  - `tests/test_episode.py`: the pilot and `MINI_BOOK` still load (AC-2.3); a book with a valid
    sheet loads; `test_load_book_rejects_invalid_books` gains a malformed-sheet case.
  - `tests/test_drafts.py`: Create book writes `style_bible.characters` and keeps each page's
    `characters_on_page` (AC-1.4), with and without a blueprint; a pre-018 ready draft (no
    `characters`) still creates a book with no sheet; `update_page_text` on such a draft still
    saves.
  - `tests/test_write_story.py`: `BOOK_PROMPT` mentions `characters` and `characters_on_page`;
    `validate_payload("book", ...)` refuses a book without a sheet.
  - `tests/test_blueprint.py`: `rebuild_style_bible` keeps `style_bible.characters`.

## Phase 2: Picture requests quote the sheet
**Implements:** FR-2 · **Satisfies:** AC-2.1, AC-2.2, AC-2.3
**Files:** `video_pipeline/characters.py`, `video_pipeline/illustrate.py`, `tests/test_illustrate.py`

Goal: each request carries "<name>: <look>" for the characters it shows; books without a sheet get
byte-identical requests.

- `characters.py`:
```python
def page_characters_text(book: dict, page: dict) -> str:
    """'Characters in this picture (keep exactly this look):\n- Name: look' for the page's
    characters_on_page in sheet order; '' when the book has no sheet or the page lists none."""

def cover_characters_text(book: dict) -> str:
    """The same block for every character in the sheet; '' without a sheet."""
```
  The cover block says "if they appear" so the back cover's "without people" direction still holds
  (AC-2.2 wants the looks in the request either way).
- `illustrate.plan_images`: append the block to each page prompt and to both cover prompts only
  when it is non-empty (`f"{prompt}\n\n{block}"`), before the existing `style` prefix step. Page 1
  stays the reference picture; `build_image_command`, `STYLE_LINE` and `illustrate()` are untouched
  (keeps the diff small for spec 019).
- Tests (`tests/test_illustrate.py`):
  - page with `characters_on_page: ["Jack", "Zib"]` → its prompt contains `Jack: <look>` and
    `Zib: <look>` and not a third character's look, plus the page prompt and the style text
    (AC-2.1);
  - `cover_front` and `cover_back` prompts contain every sheet look (AC-2.2);
  - a page with an empty `characters_on_page` keeps exactly its old prompt;
  - golden: `plan_images` for the pilot `book.json` and for a sheetless book equals the output
    computed from the unchanged formula (page prompt / cover text, with and without `style`)
    (AC-2.3);
  - `illustrate()` with a fake `paint` receives the character block in the prompt for a painted
    page.

## Phase 3: Show the sheet in the studio
**Implements:** spec story 3 · **Satisfies:** AC-3.1, AC-3.2
**Files:** `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`,
`video_pipeline/studio/templates/book.html`, `video_pipeline/studio/templates/draft.html`,
`video_pipeline/studio/static/studio.css` (only if a layout class is missing; colours stay in
`tokens.css`), tests below.

Goal: the producer can read the sheet; editing is by hand in `book.json` (FR-3), which the panel
says with the file path.

- `books.py`: `character_sheet(paths, book) -> list[dict]` reads `book.book_path` and returns
  `characters.sheet(...)`, [] on a read/JSON error (the book page already reports a broken book).
- `app.book_page` context: `"characters": sb.character_sheet(self.paths, book)`.
- `book.html`: a "Characters" panel after the Blueprint panel, shown only when the list is
  non-empty: a `<dl>` of name/look and a muted line "Edit in books/<slug>/book.json
  (style_bible.characters); pictures are not repainted automatically."
- `draft.html`: when `draft.book.characters` exists, a "Characters" panel above Pages (name/look);
  each page card shows a small "Characters: Jack, Zib" line from `page.characters_on_page`. No
  context change needed (the draft record already holds the book).
- Tests:
  - `tests/test_studio_books.py`: `character_sheet` returns the sheet for a book made with
    `add_book_from_draft`, [] for the pilot-style `MINI_BOOK` and for broken JSON.
  - `tests/test_studio_app.py`: book page lists each name and look (AC-3.1); a book without a
    sheet shows no Characters panel.
  - `tests/test_studio_story.py`: a ready draft's page lists the sheet and each page's characters
    (AC-3.2); a pre-018 draft without `characters` renders without the panel.

## Verify
- `uv run pytest tests/test_characters.py tests/test_story_tools.py tests/test_episode.py
  tests/test_drafts.py tests/test_write_story.py tests/test_illustrate.py tests/test_studio_books.py
  tests/test_studio_app.py tests/test_studio_story.py tests/test_blueprint.py`, then the full
  `uv run pytest` and `uv run ruff check video_pipeline tests`.
- `tests/fixtures/legacy_golden.json` values stay unchanged (pilot behaviour).
- Studio preview: open a book created from a draft with a sheet (Characters panel) and the pilot
  (no panel); open a ready draft (sheet plus per-page names).
- Optional real run, only if the user wants (uses ChatGPT quota): write one book from an idea and
  check the draft has a sheet; paint one page and read its request in the log.

## Risks
- Stricter writer check: existing tests that submit a book through `book_errors` fail until the
  shared `book()` fixture carries a sheet (done first in Phase 1).
- The writer may name characters inconsistently between `characters_on_page` and the sheet; the
  unknown-name refusal uses the existing retry limit (`MAX_SAVES` = 3).
- Back cover "without people" plus character looks may tempt the painter to add characters; the
  cover block's "if they appear" wording mitigates it, no vision check (non-goal).
- `illustrate.py` is also edited by spec 019: keep this change inside `plan_images` only.
