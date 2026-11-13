# Plan 015: Storybook cover, channel brand and page-flip sound

**Spec:** [spec.md](spec.md) (Approved)

## Goal

A designed, palette-coloured front cover with the Giggle & Snooze logo badge; the channel
brand in place of Swivel Kids Press in book data and print; a recorded page-flip sound.

## Phase 1: Cover design

**Implements:** FR-1 to FR-4. **Satisfies:** AC-1.1 to AC-1.8.

**Files**
- `video_pipeline/cover_design.py` (new, pure Pillow + numpy):
  - `CoverPalette(fill, outline, plate, accent)` (RGB tuples)
  - `cover_palette(blueprint: dict | None) -> CoverPalette`: palette colours by relative
    luminance; fill = lightest (cream when its contrast against the outline is < 4.5:1),
    outline/plate = darkest, accent = the most saturated remaining colour; default palette when
    the blueprint or its `art.palette` is missing or malformed
  - `contrast_ratio(a, b) -> float` (WCAG)
  - `compose_cover(picture, title, subtitle, palette, logo, cfg, out) -> CoverLayout`: scalloped
    rounded title plate in the top part (plate colour ~80 % opacity, drop shadow, dotted accent
    stitch border, 4-point sparkles), title in Arial Rounded Bold (fallbacks) with light fill,
    dark outline and an accent extrude, at most 3 balanced lines; subtitle in Chalkboard SE;
    logo badge bottom-right ≤ 15 % frame width with a soft light glow; bottom caption band left
    untouched. Returns the plate, title-line and badge boxes for tests.
- `video_pipeline/render_episode.py`: `_use_titled_cover` reads `blueprint.json` palette and
  `cfg.channel_logo`; `COVER_RECIPE = 3`; key adds palette and logo sha256; missing logo logged
- `video_pipeline/timeline.py`: `channel_logo: str = ""` (excluded from section keys like
  `channel_intro`); `config/render.json`: `"channel_logo": "assets/brand/giggle_and_snooze_logo.png"`
- `video_pipeline/frames.py`: old `compose_cover` removed
- `tests/test_cover_design.py`

**Tests first:** palette by lightness and contrast ≥ 4.5:1; default palette for None/malformed;
titles of 2–10 words: ≤ 3 lines, every line box inside the plate and the frame; badge inside the
bottom-right corner and ≤ 18 % width; bottom caption band pixels equal the plain fitted picture;
no badge when logo is None; cover key changes with palette.

## Phase 2: Brand in book data and print

**Satisfies:** AC-2.1 to AC-2.3.

**Files**
- `config/book_defaults.json`, `books/*/book.json`: `author` and `metadata.publisher` =
  "Giggle & Snooze"
- `scripts/build_book.py`: print front cover's author ribbon replaced by the logo badge; back
  cover pill uses `metadata.publisher`; the "imprint of Swivel Kids Press" line removed
- `tests/test_brand.py`

**Tests first:** no "Swivel" in any book data or defaults; `build_book.py` source has no
"Swivel"; copyright lines name the publisher field.

## Phase 3: Page-flip sound

**Implements:** FR-5. **Satisfies:** AC-3.1 to AC-3.4.

**Files**
- `assets/audio_sfx/page_turn.wav`: a Pixabay page flip, trimmed to one flip of 0.4–1.5 s,
  10 ms fades, peak -6 dBFS
- `assets/sound_library/index.json`: entry with source URL, author, Pixabay Content License
- `tests/test_page_turn_asset.py`

**Tests first:** the file's length, end levels below -40 dBFS, peak -6 dBFS ± 0.5; index entry
has source and licence.

## Verification

`uv run pytest`; re-render Pip; inspect the cover frame and the join timings.
