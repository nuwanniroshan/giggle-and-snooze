# Plan: spec 023 — Giggle & Snooze publication design

## Context

The approved prototype (`prototypes/publication-design/`, Jack) replaces the dated look of
`scripts/build_book.py` (beige frames, ornaments, drop caps, boxed 16:9 art). Spec
`docs/specs/023-publication-design/spec.md` (Approved) defines it: full-bleed pictures, words on a
scrim that hugs the text, a day → dusk → night ramp, Young Serif + Andika (both OFL, bundled),
Goodnight page, light drawing pads, a full-screen web reader with "Read to me", and a fixed-layout
EPUB. File names and the studio Editions flow stay as spec 020. This plan is saved as
`docs/specs/023-publication-design/plan.md` once approved. Branch `feat/023-publication-design`
from the current branch (022 is not merged yet; it builds on titled covers).

## Approach: one layout engine, three renderers

Each page is laid out once in Python as a `PageLayout` (page size, background picture + crop,
scrim box and colour, positioned text lines, images such as the logo). Three renderers draw the
same layout: reportlab (PDF, vector text over a JPEG background raster), Pillow (cover PNGs), and
XHTML with absolutely positioned lines (EPUB, over the same background raster). The web reader is
responsive, so it uses CSS (the prototype's `reader.css`/`reader.js`) fed with the same tokens and
per-page ramp/focus/side values.

Simplicity gate: 2 new modules + the existing script. No new dependencies (Pillow, reportlab,
numpy already in the venv).

## Files

| File | Responsibility |
|---|---|
| `config/publication.json` (new) | Design tokens: OKLCH colours, fonts, sizes in `u` (1% of trim), ramp (0.9, threshold 0.65, floor 0.85), fade 12u, safe 7.5u, words max 55%, size floor 0.6, logo widths, DPI (print 300, digital 200, EPUB 1200 px). |
| `assets/fonts/` (new) | `YoungSerif-Regular.ttf`, `Andika-Regular.ttf`, `Andika-Bold.ttf`, `OFL-YoungSerif.txt`, `OFL-Andika.txt` (already downloaded to the scratchpad). |
| `assets/publication/` (new) | `reader.css`, `reader.js` (from the prototype; tokens come from config), `epub.css`. |
| `video_pipeline/publication.py` (new, pure) | Load and validate the config (`PublicationError` naming file and key); `oklch_to_srgb`, `mix_oklch`, `contrast_ratio`; `ramp(n, total)`, `page_tone(t) -> Tone(scrim_rgb, ink_rgb, ink_soft_rgb, night)`; `subject_box(plan, picture_sha)`, `choose_focus_side(page, plan, sha) -> (focus_x, side)`; `fit_words(...)` (column width, then size down to the floor, else error naming the page); `layout_*` builders for every page kind returning `PageLayout`. |
| `video_pipeline/publication_render.py` (new) | `register_fonts()` (bundled TTFs, missing file → `PublicationError`); `render_background(layout, px) -> Image` (cover-fit crop at focus, numpy scrim gradient); `draw_pdf_page(canvas, layout, bg_jpeg)`; `render_png(layout, px)`; `epub_page_xhtml(layout, bg_name)`; `reader_html(...)` and `tokens_css(config)`. |
| `video_pipeline/publication_web.py` (new, added in Phase 3) | The EPUB and the web reader live in this third module (`write_epub`, `page_xhtml`, `reader_html`, `write_reader`, `tokens_css`); the EPUB uses its own 1200 px background cache, separate from the PDFs' 200/300 DPI rasters. |
| `scripts/build_book.py` (modify) | Keep: `plan_interior` (new order), `edition_names`, `stale_editions`, spine/wrap, extras, `activity_content`, `map_content`, `copyright_lines`. Remove: Georgia fonts, frames, dividers, badges, old story/front/back/drawing renderers, old EPUB and flipbook. `build_pdfs`, `create_front_cover_image`, `create_back_cover_image`, `create_kdp_wrap_cover` (spine = night colour), `build_epub`, `build_reader` call the new modules. |
| `tests/test_publication.py` (new) | Pure tests (below). |
| `tests/test_book_editions.py`, `tests/test_build_titled_cover.py`, `tests/test_brand.py` (update) | New page order, text and bleed assertions; regenerate `tests/fixtures/pilot_editions_golden.json`. |
| `CLAUDE.md`, `.claude/rules/images-pdf-epub.md`, steering `tech-stack.md` | Describe spec 023 (fonts bundled, new look). |

## Phases

### Phase 1 — Design system and page math (FR-1, FR-2, FR-3; AC-1.3–1.5, AC-2.1–2.4, AC-7.2)
Tests first in `tests/test_publication.py`:
- `oklch_to_srgb` matches known values (white, the CSS reference for `oklch(0.24 0.06 278)`).
- `ramp`: page 1 = 0, non-decreasing, last ≥ 0.85; `ramp(1, 1) == 0`; values ≥ 0.65 raised to ≥ 0.85.
- `page_tone`: t < 0.65 → dark ink, between day and dusk; night → light ink; contrast(ink, scrim) ≥ 4.5 for every t in a 0..1 sweep (AC-2.4).
- `choose_focus_side`: plan union of the two largest boxes → focus_x and the side overlapping it less; stale sha → falls back to `layout` (`*_top_*` → top, else bottom), centred; `print_layout` override wins.
- `fit_words`: short text keeps the base size and a ~32-char column; ~650 chars fits at ≥ 60% of base within 55% of trim; an impossible text raises an error naming the page.
- Config missing key → `PublicationError` naming `config/publication.json` and the key; missing font → names the file.
Then implement `publication.py` and `register_fonts` in `publication_render.py`; add config and fonts.

### Phase 2 — PDFs and covers (FR-4, FR-6; Stories 1, 3, 4; AC-3.6, AC-7.1)
- `plan_interior`: front (half_title, title, map?, blank to make story 1 left) + stories + activity + goodnight + drawings up to max(24, front + N + 2), rounded even. Update the plan tests (pilot → 26 pages; ten-page book → 3 + 10 + 2 + 9 drawings).
- Layout builders for story, half_title (logo, title, subtitle, belongs-to line), title (dedication, author line, copyright lines), map (contained map + title/subtitle/legend), blank, activity (dusk; fit like FR-3), goodnight (night; blueprint `qa.outro_hook`, else the dedication; logo), drawing (day; dashed frame; "My drawing" and the title), front cover (non-titled: art at `metadata.cover_focus_x`, title ≤ 2 lines in Young Serif + subtitle on a light scrim, logo bottom right; titled: print cover as is), back cover (cover_back full bleed, night scrim, blurb, logo, barcode slot; no picture → night colour).
- Rendering: digital and reader at 8.5 in, 200 DPI backgrounds; KDP at 8.625 × 8.75 in, 300 DPI, bleed on the outer edge (the safe area shifts with the bleed side); covers 2550 px; wrap = back + night spine + front. Backgrounds go to reportlab as JPEG (q 90) through `ImageReader(BytesIO)`, cached per (page, size) so digital, reader and EPUB share them.
- Tests: the editions tests are rewritten for the new order and texts (activity no longer last; "THE END" goes; Goodnight present; drawing pages light — sample a pixel); the bleed test asserts the picture covers the whole page and the text x shifts by the bleed between facing pages; PDF fonts are Young Serif/Andika only, no Georgia/Times (AC-7.1); titled-cover tests keep passing; the pilot golden is regenerated from a real build and reviewed.

### Phase 3 — EPUB and web reader (FR-5; Stories 5, 6)
- EPUB 3 fixed layout (`rendition:layout pre-paginated`, 1200 × 1200 viewport): same pages as the complete reader; backgrounds as JPEG 1200 px; text lines absolutely positioned from the layout; fonts embedded from `assets/fonts/`; alt text from `prompt`; `mimetype` first and stored; nav document. Tests: package structure, embedded fonts, spine order, alt text (AC-6.1–6.3).
- Web reader `build/index.html` + `build/reader/` (tokens.css generated from config, reader.css, reader.js, fonts, logo): cover, story scenes (`--t`, `--fx`, words top/bottom, night class), Goodnight with "Read again" and links to the digital PDF, reader PDF and EPUB; "Read to me" only when `build/audio/tracks/page-01.wav` exists (tracks for intro, pages, outro). No network URLs. Tests: no `http` in index/CSS; links resolve to files written; with and without tracks; every story text is in the HTML (AC-5.4); studio editions tests keep passing (`../illustrations/` paths).

### Phase 4 — Integration and docs
- Build Jack and the pilot for real; check contact sheets against the prototype; time the Jack build (< 2 min) and check the digital PDF size (< 60 MB for 20 pages).
- Full `uv run pytest` and `uv run ruff check`; update CLAUDE.md (build_book outputs, fonts, spec 023), rules and steering files; mark ACs in the spec; then `sdd-review`.

## Verification

- `uv run pytest tests/test_publication.py tests/test_book_editions.py tests/test_build_titled_cover.py tests/test_brand.py tests/test_studio_editions.py`, then the full suite.
- `.venv/bin/python scripts/build_book.py --book jack-and-the-topsy-turvy-dream` and `--book the-snail-mail-express`; render contact sheets of the digital, reader and KDP PDFs with pymupdf and compare them with `prototypes/publication-design/out/preview/`.
- Open `books/jack-and-the-topsy-turvy-dream/build/index.html` in the browser pane (static server); step through pages, toggle Read to me, check the mobile size, and confirm no network requests.
- Check the EPUB: unzip it, confirm the fonts and the `pre-paginated` property, and open a page XHTML in the browser.
