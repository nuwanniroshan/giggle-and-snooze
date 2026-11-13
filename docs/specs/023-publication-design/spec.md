# Feature 023: Giggle & Snooze publication design

**Status:** Approved
**Version:** 1.0.0
**Created:** 2026-11-13
**Last Updated:** 2026-11-13
**Branch:** `feat/023-publication-design`
**Depends on:** spec 005 (book folders), spec 009 (animation plans), spec 011 (blueprint),
spec 020 (editions per book), spec 022 (titled covers)
**Supersedes:** the page look of spec 020 (frames, cards, ornaments, interior page order)

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-13 | Initial draft, from the approved prototype in `prototypes/publication-design/`; display face Young Serif (licence) |

## Problem Statement

The video is production grade, but the book editions (digital PDF, complete reader, KDP print
interior and wrap cover, EPUB, web flipbook) still look dated: each 16:9 picture sits small
inside a beige box with a botanical frame, ornamental dividers, a drop cap and a parchment
card. Nothing marks them as Giggle & Snooze. A prototype for *Jack and the Topsy-Turvy Dream*
(approved 2026-11-13, "exactly how I want it") showed a look of our own: the picture fills
every page, the words are printed on it, and the book dims from day to night as it is read.
Every book's editions should be built that way.

## Goals

- Every edition of every book uses the approved look: full-bleed pictures, words on a scrim
  that hugs the text, a day to dusk to night colour ramp across the story pages.
- One shared design system (colours, two typefaces, sizes, page kinds) drives the PDFs, the
  EPUB and the web reader, so they read as one brand.
- The web reader replaces the flipbook: one full-screen page at a time, with an optional
  "Read to me" that plays the book's storytime audio.
- The EPUB becomes a fixed-layout picture book that matches the PDF pages.
- Typefaces are bundled with the project and embedded in every PDF and EPUB, so a build never
  depends on the network or on fonts installed on the Mac.

## Non-Goals

- Changing the pictures, the story text or the trim size (8.5 × 8.5 in stays).
- Changing the video, the studio or the titled cover painting (spec 022).
- Uploading to KDP or Apple Books; real ISBNs and barcodes.
- Per-book colour themes. The brand look is the same for every book.
- A landscape trim. (Possible later spec.)

## Users and Context

**Primary user:** the producer (single user, one Mac) who runs `scripts/build_book.py`, the
studio's Editions action (spec 019/020) or autopilot (spec 021).
**Secondary users:** parents and children reading the PDF, print book, EPUB or web reader,
often at bedtime.
**Usage context:** a build turns `books/<slug>/book.json`, `illustrations/`, optional
`animation/` plans, optional `blueprint.json` and optional `build/audio/tracks/` into
`books/<slug>/build/`. Same file names as spec 020.
**User mental model:** "the editions" are the files in `build/`. "Day to night" is the page
colour behind the words getting darker from the first story page to the last.

## User Stories

### Story 1: Story pages with the picture full bleed

**As** the producer **I want** each story page to show its picture across the whole page with
the words printed on it **so that** the book looks like a modern picture book.

**Acceptance criteria:**

- [ ] **AC-1.1** Given a book with N story pages When the digital edition is built Then it has
  N square pages, each covered edge to edge by that page's picture (no frame, no border, no
  empty band) with the page title, story text and page number drawn on it.
- [ ] **AC-1.2** Given a story page When it is built Then the words sit either at the top or
  at the bottom, on a scrim in the page's ramp colour that is solid behind every text line and
  fades to clear within a fixed distance past the last line; no other card, frame, divider,
  drop cap or badge is drawn.
- [ ] **AC-1.3** Given a page with an animation plan When it is built Then the picture is
  cropped around the plan's subject (the union of its two largest layer boxes) and the words
  go to the end (top or bottom) that overlaps that subject less.
- [ ] **AC-1.4** Given a page without an animation plan When it is built Then the crop is
  centred and the words follow the page's `layout` (`*_top_*` at the top, everything else at
  the bottom).
- [ ] **AC-1.5** Given a page with `print_layout` (`focus_x` 0–1 and/or `words` "top" or
  "bottom") in book.json When it is built Then those values win over AC-1.3 and AC-1.4.

### Story 2: Day to night

**As** a parent reading at bedtime **I want** the book to get darker as we read **so that**
the last pages are calm and ready for sleep.

**Acceptance criteria:**

- [ ] **AC-2.1** Given story pages 1…N When their ramp values are computed Then page 1 is 0,
  values never decrease, and the last page is at least the night threshold.
- [ ] **AC-2.2** Given a ramp value below the night threshold When the page colour is computed
  Then it lies between "day" and "dusk" (day to dusk over the first half of the ramp) and the
  words use dark ink.
- [ ] **AC-2.3** Given a ramp value at or above the night threshold When the page colour is
  computed Then the value is raised to at least the night floor, the colour is between "dusk"
  and "night", and the words use light ink.
- [ ] **AC-2.4** Given any story page When its ink and its scrim colour (solid part) are
  compared Then the contrast ratio is at least 4.5:1.

### Story 3: Front and back matter in the new look

**As** the producer **I want** every non-story page in the same quiet system **so that** the
whole book feels designed.

**Acceptance criteria:**

- [ ] **AC-3.1** Given any book When the KDP interior is built Then its pages are, in order:
  belongs-to page (logo, title, belongs-to line), title page (dedication, copyright, ISBN,
  age range), map page when the book has `illustrations/map_*.png`, a blank page when needed
  so story page 1 is a left-hand page, the story pages, the activity page, the Goodnight page,
  then "My drawing" pages up to P = max(24, front + N + 2) rounded up to even (the pilot
  grows from 24 to 26 pages).
- [ ] **AC-3.2** Given the activity page When it is built Then it uses the dusk colour and
  shows the activity title, its items and the talk-about questions (content as spec 020).
- [ ] **AC-3.3** Given the Goodnight page When it is built Then it uses the night colour, the
  word "Goodnight", the blueprint's `qa.outro_hook` when there is one (else the book's
  dedication) and the logo.
- [ ] **AC-3.4** Given a "My drawing" page When it is built Then it uses the day colour with
  a dashed drawing frame in dark ink (never a dark page).
- [ ] **AC-3.5** Given the complete reader PDF When it is built Then it holds, in order: front
  cover, belongs-to page, title page, story pages, activity page, Goodnight page, back cover,
  all 8.5 × 8.5 in.
- [ ] **AC-3.6** Given the KDP interior When it is built Then the page size and bleed rules of
  spec 020 hold (8.625 × 8.75 in, bleed on the outer edge) and every picture and colour field
  reaches the bleed edges.

### Story 4: Covers

**As** the producer **I want** covers in the same look **so that** the shelf view matches
the inside.

**Acceptance criteria:**

- [ ] **AC-4.1** Given a book without titled covers When the front cover is built Then the
  cover picture fills the square (cropped around `metadata.cover_focus_x`, 0–1, default 0.5), the title (display face, two lines at most for titles up to
  40 characters) and subtitle sit in the top area on a light scrim, and the logo sits bottom
  right.
- [ ] **AC-4.2** Given a book with titled covers (spec 022) When the front cover is built Then
  the painted print cover is used as is, with nothing drawn on it.
- [ ] **AC-4.3** Given any book When the back cover is built Then `cover_back.png` fills the
  square and a night scrim at the bottom carries the blurb, the logo and a light barcode slot
  in the bottom right.
- [ ] **AC-4.4** Given the wrap cover When it is built Then it is back + spine + front at the
  spec 020 sizes, made from the new front and back covers.

### Story 5: Web reader

**As** a parent **I want** to read the book on a phone, tablet or laptop **so that** we can
read it without a PDF viewer.

**Acceptance criteria:**

- [ ] **AC-5.1** Given a build When `build/index.html` is opened Then it shows the cover
  screen (picture, title, subtitle, "Start reading"), then one full-screen scene per story
  page in the print look, then a Goodnight scene with "Read again" and links to the digital
  PDF, the complete reader PDF and the EPUB (names with their real page counts).
- [ ] **AC-5.2** Given the reader When the reader presses the arrow keys, the on-screen
  arrows or swipes Then the previous or next scene shows, and a page count and progress line
  update.
- [ ] **AC-5.3** Given `build/audio/tracks/page-01.wav` exists at build time When the reader
  is built Then it has a "Read to me" toggle that plays each scene's track and turns to the
  next scene when a track ends; without tracks there is no toggle.
- [ ] **AC-5.4** Given scripting is off When `index.html` is opened Then every scene is
  visible, stacked, with its words.
- [ ] **AC-5.5** Given the reader When it is opened Then it loads nothing from the network
  (fonts, scripts and styles are local files next to it or inline).

### Story 6: EPUB

**As** the producer **I want** an EPUB that looks like the PDF **so that** e-readers show the
designed pages.

**Acceptance criteria:**

- [ ] **AC-6.1** Given a build When the EPUB is made Then it is EPUB 3, fixed layout
  (pre-paginated, square viewport), with the same pages as the complete reader.
- [ ] **AC-6.2** Given the EPUB When it is opened Then both typefaces are embedded files in
  the package and every picture has alt text from its page's `prompt`.
- [ ] **AC-6.3** Given the EPUB When its package is checked Then `mimetype` is the first,
  uncompressed entry, the spine lists every page in order, and there is a nav document.

### Story 7: Bundled typefaces

**As** the producer **I want** the fonts in the project **so that** every build is the same
on any Mac and offline.

**Acceptance criteria:**

- [ ] **AC-7.1** Given a build When the PDFs are written Then they embed the display face
  (Young Serif) and the body face (Andika) and no Georgia or Times.
- [ ] **AC-7.2** Given a font file is missing When a build starts Then it stops before
  writing any edition with a message naming the missing file.

## Functional Requirements

### FR-1: One design system

All colours, typefaces, type sizes, scrim fade, ramp curve and thresholds live in one
project config. PDFs, EPUB and the web reader take them from there.

**Must:** colours: giggle (logo orange), snooze (logo periwinkle), ink, ink soft, moon, moon
soft, day, dusk, night, paper. Display face Young Serif, body face Andika (both SIL OFL 1.1).
**Must not:** draw frames, ornaments, dividers, drop caps, badges, parchment cards or cream
backgrounds anywhere in the editions.

### FR-2: Ramp

Story page n of N gets `t = ((n−1)/(N−1))² × 0.9` (0 when N = 1). From the night threshold
(0.65) a page is night: `t` becomes at least the night floor (0.85) and the ink is light.
The page colour is day→dusk for t in [0, 0.5] and dusk→night for t in [0.5, 1], mixed in a
perceptual colour space.

### FR-3: Story page layout

Picture cropped to fill the page around `focus_x`; title (body bold, small) above the story
text (body regular); page number in the display face at the outer corner of the words' end;
words inside the 0.5 in safe area plus bleed; the words column leaves room for the page
number. The words block (title + text) may take at most 55% of the trim height. Fitting, in
order: the base size in a column of about 32 characters; then the full column width; then the
size steps down to a floor of 60% of the base. Long pages (the pilot has ~650 characters)
therefore fit at a smaller size.

### FR-4: Page kinds

Story, cover, belongs-to, title, map, blank, activity, Goodnight, drawing, back cover, as in
Stories 1–4. The map page shows the map picture whole on the day colour with the map title.

### FR-5: Web reader and EPUB

Generated from the same page content and design config. Fonts, styles and script are copied
next to `index.html` in `build/`; pictures are referenced by relative path as today; audio
tracks are referenced by relative path inside `build/`.

### FR-6: Same outputs, same names

File names, page counts in names, stale-edition clean-up and the studio's Editions view stay
as spec 020.

**Must not:** add a network dependency to any build.

## Non-Functional Requirements

### Performance

- A 10-page book's full build (all editions) finishes in under 2 minutes on the M1 Pro.
- PDFs embed pictures once per page at print resolution (≥ 300 DPI at trim for the print
  interior and covers); the digital edition stays under 60 MB for 20 pages.

### Reliability

- Builds are deterministic: the same inputs give the same page layout.
- Unit tests cover the ramp, colour conversion and contrast, focus/side choice, interior
  plan, scrim geometry and text fitting without rendering real PDFs.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Font file missing | Build stops before writing, naming the file (AC-7.2). |
| Animation plan unreadable or for another picture (sha256 differs) | Ignore it; use AC-1.4. |
| Story text too long for the words block | Column widens, then the size steps down to 60% of the base (FR-3); if it still does not fit, the build fails naming the page. |
| No `cover_back.png` | Back cover is the night colour with blurb, logo and barcode slot. |
| No blueprint | Goodnight uses the dedication; the page still builds. |
| No audio tracks | Reader has no "Read to me". |

## Open Questions

None. Resolved 2026-11-13: the prototype's display face Chubbo (ITF Free Font License 2.0)
forbids subsetting, extractable embedding (EPUB) and redistribution, so the producer chose an
OFL face instead: Young Serif (display) with Andika (body), both SIL OFL 1.1, committed with
their licence texts.

## Out of Scope

- A landscape trim, per-book colour themes, page-turn animations in the reader, offline
  installable web app, audio-synced word highlighting.
