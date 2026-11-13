# Spec 015: Storybook cover, channel brand and page-flip sound

**Status:** Approved
**Version:** 1.0.0
**Created:** 2026-11-12
**Last Updated:** 2026-11-12
**Branch:** `feat/015-storybook-cover-and-brand`
**Depends on:** spec 003 (render), spec 011 (blueprint palette), spec 012 (channel name, brand assets)

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-12 | Initial draft |

## Problem Statement

The episode's front cover is the painted picture with a dark band and plain outlined serif
lettering; it looks like text pasted on a picture, not a designed picture-book cover. It also
reads "by Swivel Kids Press", which is not the brand viewers know: the channel is
**Giggle & Snooze**. The printed books carry the same old name on their covers and copyright
page. And the page-turn sound is a generated noise burst that does not sound like paper.

## Goals

- A front cover that looks designed for each book: lettering and decoration coloured from that
  book's own art palette, sitting on a title plate that belongs to the picture.
- The Giggle & Snooze brand (its logo where pictures allow, its name in text) in place of
  "Swivel Kids Press" everywhere a viewer or reader sees it.
- A real recorded paper page-flip sound at every page turn.

## Non-Goals

- Painting lettering with the image model (spelling must always be exact).
- Repainting existing cover pictures.
- Changing the back cover design beyond the brand name.
- Fades and dissolves between the clips (spec 012 update).

## Users and Context

The producer (single user) renders on one Mac. Six books exist; five have a blueprint with a
5-colour palette, the pilot (`the-snail-mail-express`) has none. The logo is
`assets/brand/giggle_and_snooze_logo.png` (1256×733, transparent).

## User Stories

### Story 1: A designed front cover

**As a** producer **I want** a cover whose title looks designed for the story **So that** the
episode opens like a real picture book.

- [ ] **AC-1.1** Given a book with a blueprint When its video cover is made Then the title
  lettering, its outline and the title plate use colours taken from the blueprint palette (the
  lettering fill is the palette's lightest colour or cream when no palette colour is light
  enough to read, the outline and plate its darkest)
- [ ] **AC-1.2** Given a book without a blueprint When its cover is made Then a fixed default
  palette is used and the cover still renders
- [ ] **AC-1.3** Given any cover When it is made Then the title sits on a title plate (a soft,
  semi-opaque shaped banner with a decorative edge) in the top part of the frame, the picture
  stays visible around and below it, and every title line fits inside the plate
- [ ] **AC-1.4** Given titles of 2 to 10 words When covers are made Then no line leaves the
  plate or the frame and the title uses at most 3 lines
- [ ] **AC-1.5** Given the subtitle When the cover is made Then it is shown under the title in
  a smaller style in the same palette
- [ ] **AC-1.6** Given the cover When it is made Then the Giggle & Snooze logo is shown as a small
  badge (no more than 18% of the frame width) in a corner clear of the title, and no
  "by <author>" line is drawn
- [ ] **AC-1.7** Given the cover's inputs (picture, words, palette, logo, design version) are
  unchanged When the episode is rendered again Then the cover is not redrawn
- [ ] **AC-1.8** Given the logo file is missing When the cover is made Then the cover is made
  without the badge and the render log says the logo was not found

### Story 2: Channel brand instead of Swivel Kids Press

**As a** producer **I want** the channel name everywhere **So that** viewers and readers see one
brand.

- [ ] **AC-2.1** Given the six existing books and the new-book defaults When they are read
  Then the author is "Giggle & Snooze" and no book data contains "Swivel"
- [ ] **AC-2.2** Given a print build When the front cover, back cover, copyright page and EPUB
  are made Then none contains the text "Swivel", and the print front cover shows the
  Giggle & Snooze logo
- [ ] **AC-2.3** Given the copyright page When it is built Then it names Giggle & Snooze as the
  publisher

### Story 3: A real page-flip sound

**As a** producer **I want** a recorded paper page-flip **So that** page turns sound natural.

- [ ] **AC-3.1** Given the chosen recording is in the shared sound effects folder When an
  episode is rendered Then every page turn uses it instead of the generated sound
- [ ] **AC-3.2** Given the recording When it is checked Then it is a single page flip of 0.4 to
  1.5 s, mono or stereo, with no music or voice, and it starts and ends without a click
  (first and last 10 ms below -40 dBFS after its fades)
- [ ] **AC-3.3** Given the recording is in the sound library index When the library is
  checked Then its source page, author and licence (Pixabay Content License) are listed
- [ ] **AC-3.4** Given the page-turn sound changed When an episode is rendered Then the
  final audio is rebuilt (the existing page-turn checksum in the final cache key covers this)

## Functional Requirements

- **FR-1** The cover design reads the book's blueprint palette; colours are chosen by
  lightness so lettering always contrasts with its outline (contrast ratio at least 4.5:1).
- **FR-2** The cover keeps the caption area at the bottom of the frame free of lettering.
- **FR-3** The cover design version is part of the cover's cache key; changing the design
  redraws every cover once.
- **FR-4** The brand name and logo path come from the shared render settings
  (`channel_name`, a new logo setting); nothing hardcodes the brand in code.
- **FR-5** The page-flip recording lives in the shared sound-effects folder under the file name
  the renderer already looks for, levelled so its peak is -6 dBFS before the existing SFX gain.

## Non-Functional Requirements

- Making one cover takes under 3 s on the M1 Pro.
- Fonts are system fonts available on macOS (rounded/storybook style chosen from those present),
  with a fallback when one is missing.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Logo missing | Cover without badge, logged (AC-1.8) |
| Blueprint palette missing or malformed | Default palette (AC-1.2) |
| Page-flip file unreadable | Render stops naming the file (existing behaviour) |

## Open Questions

None.

## Out of Scope

- Per-book font choice; animated covers; a studio cover editor.
