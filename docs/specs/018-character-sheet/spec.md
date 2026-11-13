# Spec 018: Characters look the same on every page

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/018-character-sheet`
**Depends on:** specs 006 (idea to book), 008 (illustrations), 011 (blueprint)

---

## Problem Statement

Books written from an idea store no description of their characters. Each picture request gets only that page's prompt, the art style and page 1 as a reference picture. A character who is not on page 1, or whose page prompt leaves out their look, is painted differently. In *Jack and the Topsy-Turvy Dream*, page 7's three-eyed friend in a lilac scarf came out with two eyes and bunny ears.

## Goals

- Every book written from an idea carries a character sheet: each recurring character's name and fixed look.
- Every picture request (pages and covers) includes the look of each character on that page.
- The producer can read and edit the character sheet.

## Non-Goals

- Painting a separate reference picture per character.
- Checking pictures with a vision model.
- Changing books that already have their own character descriptions (the pilot's `clara_description`/`barnaby_description`).

## Users and Context

**Primary user:** the producer, adding books from ideas in the studio.

## User Stories

### Story 1: The writer records the characters
- [ ] **AC-1.1** Given a draft is written When the book is submitted Then it includes `characters`: 1–6 entries, each with `name` (1–40 characters) and `look` (one or two sentences, 20–300 characters: body, colours, clothing, distinguishing features).
- [ ] **AC-1.2** Given a submitted book with no `characters` or a character whose `look` is outside the limits When it is checked Then it is refused naming the field, and the writer fixes it (same retry path as other book problems).
- [ ] **AC-1.3** Given a submitted book When it is checked Then each page lists in `characters_on_page` the names (from `characters`) of the characters it shows; an unknown name is refused naming the page.
- [ ] **AC-1.4** Given Create book When the book folder is written Then `style_bible.characters` holds the sheet and each page keeps its `characters_on_page`.

### Story 2: Pictures use the sheet
- [ ] **AC-2.1** Given a page with `characters_on_page` When its picture is requested Then the request contains, for each listed character, "<name>: <look>", in addition to the page prompt, art style and reference picture.
- [ ] **AC-2.2** Given the front or back cover When it is requested Then the request contains the look of every character in the sheet.
- [ ] **AC-2.3** Given a book without `style_bible.characters` (older books, the pilot) When pictures are requested Then the request is exactly as today.

### Story 3: See and edit the sheet
- [ ] **AC-3.1** Given a book with a character sheet When the book page is shown Then the sheet is listed (name and look).
- [ ] **AC-3.2** Given the draft review page When a draft is shown Then the sheet is listed with each page's characters.

## Functional Requirements

- **FR-1** The story writer brief must ask for the sheet and per-page character names; the book check must enforce AC-1.1–1.3.
- **FR-2** Picture requests must include character looks as in AC-2.1–2.2.
- **FR-3** `book.json` stays hand-editable; editing a look marks pictures stale only through the existing rules (pictures are not repainted automatically).

## Non-Functional Requirements

- No extra model calls: the sheet is written in the same story run.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Writer omits characters | Refused with the field named; writer retries up to the attempt limit. |
| Page names an unknown character | Refused naming the page and name. |

## Open Questions

None.

## Out of Scope

- Per-character reference images, vision checks, retrofitting older books.
