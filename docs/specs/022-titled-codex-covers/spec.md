# Feature 022: Titled front covers painted by Codex

**Status:** Approved
**Version:** 1.0.1
**Created:** 2026-11-13
**Last Updated:** 2026-11-13
**Branch:** `feat/022-titled-codex-covers`
**Depends on:** spec 008 (landscape illustrations), spec 011 (blueprint art direction), spec 015
(cover lettering and brand), spec 019 (repaint one picture), spec 020 (editions per book)

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-13 | Initial draft |
| 1.0.1 | 2026-11-13 | PATCH: FR-4 — the reader gets only the cover; expected words are compared in code |

## Problem Statement

A book's front cover is painted without lettering, and the title is laid on top later: by the
video render (spec 015's title plate) and by the print build (a cream plaque). The lettering is
always the same few system fonts on a banner, so every book's cover looks alike and the words
never feel part of the picture. A sample cover painted by Codex with the title, subtitle and
Giggle & Snooze logo inside the art (approved 2026-11-13,
`books/mira-and-the-midnight-ticket/build/cover_samples/cover_front_sample_b.png`) looks like a
real published picture book, with lettering in the book's own art medium. New books should get
covers like that.

This feature reverses spec 015's non-goal "painting lettering with the image model" for new
books. Spelling is kept exact by checking every painted cover before it is accepted.

## Goals

- Each new book gets a finished front cover painted by Codex. The title, the subtitle and the
  Giggle & Snooze logo are part of the picture, and the lettering is drawn in the book's own art
  medium and palette (spec 011 blueprint).
- Two front covers per new book: a 4:3 landscape cover for the video, and a square cover for
  print and EPUB. Each is composed for its own shape.
- A cover is accepted only when its title and subtitle are spelled exactly and its logo is whole.
- The video and print builds show a titled cover as painted, with no second layer of lettering.

## Non-Goals

- Changing the 8 existing books. They keep today's covers and today's lettering until a later
  spec says otherwise.
- Changing the back cover.
- Placing the exact logo file on the cover. Codex paints the logo; small differences between
  books are accepted.
- Changing the story pages (they stay 16:9).

## Users and Context

**Primary user:** the producer (single user, one Mac), who adds books from ideas (spec 006) or
with autopilot (spec 021) and repaints pictures in the studio (spec 019).
**Usage context:** covers are painted in the illustrate stage after the story pages. The logo is
`assets/brand/giggle_and_snooze_logo.png` (1256×733, transparent).
**User mental model:** "the cover" is the book's front cover as a reader sees it in the shop:
picture, title, subtitle and publisher logo together.

## User Stories

### Story 1: A titled video cover

**As a** producer **I want** Codex to paint the video front cover with the title, subtitle and
logo inside the art **So that** each episode opens on a cover designed for that book.

- [x] **AC-1.1** Given a new book with a blueprint When the illustrate stage paints its covers
  Then a video front cover is saved whose size is exactly 4:3 landscape, with the shorter side at
  least the minimum picture size the illustrate stage already enforces
- [x] **AC-1.2** Given the request sent to Codex for the video cover When it is inspected Then it
  contains the book's exact title and subtitle, the blueprint's art direction (style prefix,
  medium, line, lighting, palette and avoid list), the cover layout rules (title as the main
  element, subtitle smaller near it, logo as a corner publisher mark of about 14% of the width,
  no other words) and the safe-area rule (the whole logo at least 12% of the width from the left
  and right edges and at least 5% from the top and bottom)
- [x] **AC-1.3** Given the video cover request When it is sent Then the logo file and one story
  page picture of the book are attached as reference images, and the request says which is which
- [x] **AC-1.4** Given a book without a blueprint When its covers are painted Then the request
  uses the book's `style_bible.style_prompt_prefix` as the art direction and the cover is still
  painted with title, subtitle and logo
- [x] **AC-1.5** Given two new books When their covers are painted Then each request is built
  from that book's own title, story blurb, characters and art direction (no fixed layout, font or
  colours shared between books)
- [x] **AC-1.6** Given the logo file is missing When the illustrate stage reaches the covers Then
  neither cover is painted, the stage fails naming the missing file, and the story pages already
  painted are kept

### Story 2: A titled square cover for print and EPUB

**As a** producer **I want** a second titled cover composed for the square print and EPUB shape
**So that** the printed book and the ebook match the video's cover without cropping its words.

- [x] **AC-2.1** Given a new book When the illustrate stage paints its covers Then a print front
  cover is saved whose size is exactly square, painted from its own request with the same title,
  subtitle, logo, art direction and safe-area rule (measured on the square)
- [x] **AC-2.2** Given a book with a titled print cover When the print build makes the front
  cover, the KDP wrap cover, the reader PDF and the EPUB cover Then each uses the titled print
  cover scaled to the needed size, and no title plaque, title text, subtitle text or logo badge
  is drawn over it
- [x] **AC-2.3** Given a book with a titled print cover When the flipbook is built Then its
  front cover shows the titled print cover with no lettering added

### Story 3: Covers are checked before they are accepted

**As a** producer **I want** every painted cover checked for spelling and a whole logo **So
that** a misspelled title or a cut-off logo never reaches a video or a book.

- [x] **AC-3.1** Given a newly painted cover When it is checked Then Codex is shown the finished,
  trimmed cover and reports the title text, the subtitle text and whether the whole Giggle &
  Snooze logo is visible
- [x] **AC-3.2** Given the reported title and subtitle When they are compared with the book's
  title and subtitle Then they pass only if they match after ignoring letter case, line breaks,
  repeated spaces and curly versus straight quotes and apostrophes; any other difference fails
- [x] **AC-3.3** Given a cover that fails the check (wrong words, logo not whole, or the check
  gave no readable answer) When painting continues Then that cover is painted again, up to 3
  paintings in all, and the cover is accepted after the first one that passes
- [x] **AC-3.4** Given all 3 paintings of a cover fail When the stage ends Then no failed cover
  is saved as the book's cover, the stage fails naming the cover and the reason from the last
  check, and the other pictures already painted are kept
- [x] **AC-3.5** Given a cover passes When it is saved Then the job log records the
  number of paintings it took and the text the check read

### Story 4: The video uses the painted cover as is

**As a** producer **I want** the render to show the titled cover without adding lettering **So
that** the episode opens on exactly the cover Codex painted.

- [x] **AC-4.1** Given a book with a titled video cover When the episode is rendered Then the
  intro shows that cover whole (4:3 centred in the 16:9 frame over the render's usual blurred
  background) and no title plate, title, subtitle or logo badge is drawn on it
- [x] **AC-4.2** Given an existing book (no titled covers) When the episode is rendered Then the
  cover is made exactly as before (spec 015), and its cached video sections are reused
- [x] **AC-4.3** Given the titled video cover is repainted When the episode is rendered again
  Then the intro section is rebuilt and other cached sections are reused

### Story 5: Repainting one titled cover

**As a** producer **I want** to repaint either titled cover on its own **So that** I can replace
a cover I don't like without repainting the book.

- [x] **AC-5.1** Given a book with titled covers When the producer repaints the video cover or
  the print cover (studio or CLI) Then only that cover is painted, with the same request rules,
  check and retries as above, and the old cover is kept as a backup and put back if every try
  fails (spec 019 behaviour)
- [x] **AC-5.2** Given a book with titled covers When the studio shows its pictures Then both
  front covers are listed by name ("video cover", "print cover") with their own repaint buttons,
  and the book's thumbnail uses the video cover
- [x] **AC-5.3** Given an existing book When the producer repaints its front cover Then it is
  painted the old way (no lettering) and the book does not switch to titled covers

## Functional Requirements

### FR-1: Which books get titled covers

**Must:**
- Give titled covers to books whose illustrations are painted after this feature ships (new
  books from ideas or autopilot).
- Record in the book's data that it uses titled covers, so the render, the print build and the
  studio can tell titled-cover books from older books without looking at pictures.

**Must not:**
- Change any file of the 8 existing books, or what their builds and renders produce.

### FR-2: Cover requests

**Must:**
- Build each cover request from book data and blueprint only: title, subtitle, blurb, character
  looks (spec 018), art direction, shape, layout rules and safe-area rule.
- Keep the layout rules, safe-area margins, logo size and check settings in shared config, not in
  code.
- Attach the logo file and one story page of the book as references.

**Must not:**
- Ask for an author name, barcode, price or any words other than the title, subtitle and logo.

### FR-3: Cover shapes

**Must:**
- Save the video cover at exactly 4:3 landscape and the print cover at exactly square, trimming
  evenly from both sides of the painted picture, and run the check on the trimmed result.
- Refuse a painted picture whose shape is too far from the target to trim (portrait for the video
  cover; for the print cover, any picture more than 1.3:1 in either direction) and paint it again
  within the 3 tries.

### FR-4: Cover check

**Must:**
- Check every accepted cover as in Story 3, with a separate Codex request that is given only the
  finished cover. The expected words are never sent to the reader (so it cannot echo them); they
  are compared with its reading in code (AC-3.2).
- Treat a check that times out or returns no readable answer as a failed try.

**Must not:**
- Save a cover that has not passed the check as the book's cover.

### FR-5: Consumers

**Must:**
- Video render: use the titled video cover as is (Story 4).
- Print build: use the titled print cover for the front cover, wrap cover, reader PDF, EPUB cover
  and flipbook cover (Story 2).
- Studio: show and repaint both covers (Story 5).

**Must not:**
- Draw lettering or the logo badge over a titled cover.

## Non-Functional Requirements

### Performance

- Painting both covers adds at most 2 picture requests and 2 check requests per book when every
  first try passes; each request uses the same time limit as story page painting.

### Security

- Cover and check requests run with the same Codex settings as page painting (no web search, a
  temporary work folder, no access to the repository).

### Reliability

- A failed cover never replaces a good one (AC-3.4, AC-5.1).
- Unit tests cover request building, shape trimming, the word comparison and the retry and fail
  paths without calling Codex.

## Error Scenarios

| Scenario | Expected behaviour |
|----------|-------------------|
| Codex misspells the title or subtitle | Paint again; after 3 failed tries the stage fails naming the cover and the words it read (AC-3.3, AC-3.4) |
| Logo cut off or missing | Same as a misspelling (AC-3.3) |
| Check times out or replies with something unreadable | Counted as a failed try (FR-4) |
| Painted picture is the wrong shape | Paint again within the 3 tries (FR-3) |
| Logo file missing | The stage stops before painting either cover and names the missing file (AC-1.6) |
| Book has no blueprint | Uses the style bible prefix (AC-1.4) |
| Repaint of a titled cover fails 3 times | Old cover is put back (AC-5.1) |

## Open Questions

None.

## Out of Scope (Future Considerations)

- Switching the 8 existing books to titled covers.
- A titled back cover.
- Placing the exact logo file over a painted cover.
- Choosing between several painted cover candidates in the studio.
