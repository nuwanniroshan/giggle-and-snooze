# Spec 020: Editions fit each book

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/020-editions-per-book`
**Depends on:** spec 005 (book folders), 018 (character sheet)

---

## Problem Statement

The book builder was written for the pilot. Every book's print interior and editions carry the pilot's own pages: the Whisperwood Hollow map, "The Junior Postmaster's Field Guide", questions about Clara and Barnaby, and a postal-service line. File names say "20Page" and "24Page" whatever the real count. A 10-page book gets a 14-page KDP interior, below KDP's 24-page paperback minimum, so KDP would refuse it.

## Goals

- No book's editions contain another book's text or pictures.
- The KDP print interior always meets KDP's paperback minimum and an even page count.
- File names state the real page count.

## Non-Goals

- Changing trim size, bleed, cover layout or the pilot's own look.
- Hardcover or other formats.

## Users and Context

**Primary user:** the producer publishing a book to KDP and e-book stores.

## User Stories

### Story 1: Only the book's own content
- [ ] **AC-1.1** Given any book other than the pilot When the editions are built Then no edition contains the words "Whisperwood", "Postmaster", "Clara", "Barnaby" or "Snail Mail" unless that book's own `book.json` contains them.
- [ ] **AC-1.2** Given a book When the front matter is built Then it uses only that book's metadata (title, subtitle, author, publisher, dedication, year) and its own pictures; the map page appears only when the book has `map_*.png` in its illustrations.
- [ ] **AC-1.3** Given a book When the back-matter activity page is built Then it is made from that book's data: a "Can you find…?" list of the book's characters (spec 018 sheet, else page titles) and 2–3 talk-about questions from config templates filled with the book's title and characters.
- [ ] **AC-1.4** Given the pilot When its editions are built Then they keep their current content (the pilot's extras move to its own book data, not code).

### Story 2: KDP-ready page count
- [ ] **AC-2.1** Given a book with N story pages When the KDP interior is built Then its page count is max(24, front matter + N + back matter) rounded up to an even number, padded with "My drawing" pages (a framed blank page with the book's title).
- [ ] **AC-2.2** Given the interior page count P When the wrap cover is built Then the spine width uses P.
- [ ] **AC-2.3** Given the story pages When the interior is built Then story pages keep their facing-page order and bleed side as today.

### Story 3: Honest file names
- [ ] **AC-3.1** Given N story pages and an interior of P pages When files are written Then they are named `<Title>_<N>Page_Digital_Edition.pdf` and `<Title>_KDP_<P>Page_Print_Interior.pdf`, and the flipbook links match.
- [ ] **AC-3.2** Given a rebuild When older files with other page counts exist in `build/` Then they are removed so only current files remain.

## Functional Requirements

- **FR-1** Pilot-specific text and pictures must come from the pilot's book data, not code.
- **FR-2** Back-matter templates live in config.
- **FR-3** Page counts and names derive from the book data.

## Non-Functional Requirements

- Build time stays within 2× today's for a 20-page book.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Book has no character sheet | Activity list uses page titles. |
| Book has more than 24 interior pages | No padding beyond the even rounding. |

## Open Questions

None.

## Out of Scope

- Uploading to KDP, ISBN handling.
