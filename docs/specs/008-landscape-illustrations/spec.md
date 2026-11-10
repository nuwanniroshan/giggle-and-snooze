# Spec 008: Landscape (16:9) illustrations

**Status:** Draft
**Created:** 2026-11-10
**Branch:** `feat/008-landscape-illustrations`
**Depends on:** specs 003 (render), 005 (book folders), 006 (idea to book, illustrations)

---

## Problem Statement

The video is 16:9, but every illustration is square. In the video the picture sits in the middle of the frame with blurred bars on both sides, and pan, zoom and Q&A camera moves can only use the square part of the frame. The producer wants new illustrations painted in 16:9 so the video uses the full frame. The print and digital book must still look right with landscape art.

## Goals

- New illustrations (pages, front cover, back cover) are painted in 16:9.
- In the video, a 16:9 picture fills the whole frame, and every camera effect works on the full frame with no blurred bars.
- The print, PDF, EPUB and flipbook editions place 16:9 art cleanly on their square pages, with the text beside or over the art.
- Existing square books keep working unchanged.

## Non-Goals

- Repainting the existing books (*The Snail Mail Express*, *A Pocket Full of Plops*). They stay square until the producer repaints them.
- Changing the print trim size, the KDP wrap cover size or the video resolution.
- Using any ratio other than 16:9 (4:3 was considered and rejected).
- Cropping or stretching a square picture to 16:9 in the video.

## Users and Context

**Primary user:** the producer, using the studio and the CLI on the M1 Pro.
**Context:** making the next book. Mixed libraries are normal: old square books next to new landscape books.

## User Stories

### Story 1: Paint in 16:9
As the producer, I want new pictures painted in 16:9 so that they fit the video.

- AC-1.1: Every picture request (page, front cover, back cover) asks for a 16:9 picture.
- AC-1.2: A painted picture is accepted only if its width-to-height ratio is within 2% of 16:9; otherwise it fails with the file name, the size and the expected ratio.
- AC-1.3: The minimum-size check applies to the shorter side as before.
- AC-1.4: Pictures already in the folder are kept unless the producer forces a repaint (no change).

### Story 2: Full-frame video
As the producer, I want a 16:9 picture to fill the video frame so that zoom and pan use the whole frame.

- AC-2.1: For a 16:9 picture the frame is the picture alone, scaled to 1920×1080, with no backdrop visible.
- AC-2.2: For any other ratio (square books) the frame is built exactly as before, so old books render identically.
- AC-2.3: The camera moves (push, pan, Q&A zoom targets) keep their existing meaning and limits on a full-frame picture; no camera position shows area outside the picture.
- AC-2.4: Captions stay inside the frame and readable on a full-frame picture.
- AC-2.5: Cached sections made from a square picture are not reused for a landscape picture of the same page (the cache key changes when the picture changes).

### Story 3: Landscape pictures in the book
As the producer, I want the book editions to show landscape art well on square pages.

- AC-3.1: On a story page, a 16:9 picture is shown whole (not cropped) at the full width of the page's safe area, and the page text sits below it or over a soft area of the picture, using the book's page layout value.
- AC-3.2: The page text never overlaps another page element and never leaves the safe area.
- AC-3.3: Covers: the front and back cover use a 16:9 picture filling the full cover width, with title and author text on the blended area, keeping the KDP wrap-cover size and bleed.
- AC-3.4: The EPUB, the digital PDF and the flipbook show the full 16:9 picture without cropping.
- AC-3.5: A square picture is still laid out as before in all editions.

### Story 4: Mixed library
- AC-4.1: A book may contain square pictures only, 16:9 only, or a mix; every stage handles each picture by its own ratio.
- AC-4.2: Nothing in the studio or the episode script claims a ratio for the pictures; the video stays 16:9, 1920×1080.

## Functional Requirements

- FR-1: A single place defines the target picture ratio (16:9) and the tolerance (2%).
- FR-2: The picture prompt wording says "16:9 landscape" in place of "square 1:1".
- FR-3: The page layouts in `book.json` keep their names; each layout has a defined behaviour for landscape pictures (AC-3.1).
- FR-4: Existing `book.json` files need no change.

## Non-Functional Requirements

- NFR-1: A square book renders to the same video frames as before the change (same cache keys for unchanged inputs).
- NFR-2: The full pilot render time does not grow by more than 10%.

## Error Scenarios

- A painted picture has the wrong ratio → the repaint fails for that file with a clear message; other files are kept.
- A picture file is unreadable → same error as today.

## Open Questions

- [NEEDS CLARIFICATION] Layout detail for AC-3.1: should each layout keep its own text position (text over the picture for `full_bleed_card_*`, text below for `image_top_text_bottom`), or should all landscape pages use one rule? Default if no answer: keep each layout's meaning.
- [NEEDS CLARIFICATION] Existing composited pages (`compose_missing_illustrations.py`) assume square art; default: leave that script square-only and out of scope.

## Out of Scope

Repainting existing books, other ratios, trim or video resolution changes, music, and the composition script.
