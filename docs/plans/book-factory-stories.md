# Book factory stories (draft plan)

**Status:** Draft, not approved
**Created:** 2026-11-09
**Scope:** three producer stories. Each becomes a spec under `docs/specs/` once the open decisions below are answered.

## Build order

```
005 Book library & folder layout  →  006 Publish: ebook + video  →  007 Idea to book (text)  →  008 Per-book art style & illustrations
```

- 005 is the foundation: 006–008 all assume books live in one predictable folder per book.
- 006 can ship before 007/008, using the pilot book.
- 007 and 008 are the new AI work. They are split because text generation (existing providers) and image generation (new) carry different risks and costs.

---

## Story 1 — Organised books, outputs in the source folder (spec 005)

**Problem:** Book files are spread across four places: `book_data.json` at the root, `assets/illustrations/`, `episodes/<slug>/`, and `build/` (with flat `The_Snail_Mail_Express_*` files). `scripts/` hardcodes the root path and the book title. Adding a second book means copying files by hand.

**Goal:** one folder per book, containing its source and its outputs, inside this project. Any number of books, no code edits.

**Proposed layout** (interpretation of "outputs in source folder": outputs live in the project tree, next to their book, not in a separate build folder):

```
books/
  the-snail-mail-express/
    book.json              # was book_data.json (metadata, style_bible, pages)
    illustrations/         # was assets/illustrations/
    nooks/                 # intro/outro keyframes (was episodes/<slug>/)
    episode/               # episode_script.json, review and audio approval marks (committed)
    build/                 # all generated output: ebook, video, audio, publish/ (git-ignored)
```

**Acceptance criteria**
- A book is identified only by its folder name (slug). No title or path is hardcoded in code.
- `scripts/` and `video_pipeline/` read book paths from the slug. Running them with `--book <slug>` works for any book in `books/`.
- The pilot book moves into `books/the-snail-mail-express/`. Its outputs are byte-for-byte equivalent in content (page count, page sizes, text) to today's build; a before/after check compares page counts, sizes and text extraction.
- The studio's book registry lists every folder in `books/` and reports a clear error for a folder missing `book.json`.
- Shared assets (SFX, nook fallbacks, cast/voice config) stay at the root. Per-book content does not.
- Generated outputs are git-ignored; source (`book.json`, illustrations, nooks, approved script) is committed.

**Open decisions**
1. **Decided:** generated outputs are kept in the project tree and git-ignored. Only source is committed.
2. Keep the old paths working during migration, or cut over in one step? *(Recommended: one step, with a migration script and a test.)*

---

## Story 3 — Each publish produces a printable ebook and a video (spec 006)

**Problem:** Building the printable book and rendering the video are separate commands, and only the book side is complete. There is no single "publish" moment.

**Goal:** one Publish action per book that produces both deliverables into `books/<slug>/publish/`, with a record of exactly what was published and when.

**Outputs per publish**
- Printable ebook: 20-page digital PDF, full reader PDF, EPUB 3, KDP 24-page print interior and wrap cover (existing `build_book.py` outputs).
- Video: 16:9 1080p MP4 (existing `render_episode`).
- `publish.json`: version, timestamp, input hashes, output paths, page count, video duration.
- Re-publishing with unchanged inputs skips work (idempotent, per the data-pipeline rule).

**Acceptance criteria**
- Publish is blocked until the script is approved and the audio is approved (existing gates reused, not bypassed).
- Publish runs book build and video render in sequence, one stage at a time (16 GB rule).
- The studio shows the publish status and links to each output.
- Nothing in `publish/` is hand-edited; a rerun regenerates it.

**Open decisions**
3. Should Publish require the video, or may a book be published as print-only? *(Recommended: both by default, with a "print only" option.)*
4. Version scheme for a publish: date-stamped folder, or incrementing number? *(Recommended: incrementing number plus the date in `publish.json`.)*

Decisions 2, 3 and 4 are still open. Decision 2 (cut-over vs. migration) is also still open.

---

## Story 2 — Add a book from an AI idea (specs 007 and 008)

**Problem:** Adding a book needs a hand-made `book_data.json`, which the producer uploads. The producer wants to start from an idea instead.

**Goal:** the producer either clicks "Suggest ideas" and picks one of several AI story ideas, or types their own idea. The AI turns the chosen idea into a full book (text, pages, prompts). Once the producer picks, the pipeline creates the book and its video.

### 007 — Idea to book (text)

- **Suggest ideas:** the AI proposes 3–5 short ideas. Each has a title, a one-line premise, the age range and the core lesson. The producer can regenerate the list.
- **Type your own:** the producer types an idea. The AI improves it into a candidate premise, then asks for the producer's pick.
- **Build the book:** after the pick, the AI writes the 20-page story (same shape as the pilot: short lines, repetition, escalating beats, a warm ending), the per-page `prompt`, the characters, and the cast mapping for the video.
- **Gate:** the producer reviews the full text (editable in the studio) before any image or audio is generated.

**Acceptance criteria**
- Text generation uses Codex only (decision 5), via the same runner and validation as spec 001. Output is validated against `book.json` rules before it is saved.
- Kid-safety check on every page (`config/disallowed_terms.txt` plus the foundation's kid-safe principle).
- Unfinished ideas are kept as drafts and can be resumed.
- No upload of JSON files. The old upload route is removed.

**Open decisions**
5. **Decided:** Codex for everything (ideas and full books). Runs on the ChatGPT plan quota, no API spend.
6. How many pages per AI book? *(Recommended: 20, to match the existing layout and the video pacing.)*

### 008 — Per-book art style and illustrations

**Requirement:** every book has its own cartoon style, matched to that book's story. No two books may look alike, and no book may copy an existing artist's or book's style.

- **Style brief, not style copy:** each book gets an "art direction" generated from its story: medium (e.g. cut paper, gouache, ink and watercolour, digital flat vector), palette, line quality, texture, character construction, background approach. Research references (for example, bold textured collage picture books) inform the *categories* of choice only.
- **Originality guard:** the style prompt never names a living artist or an existing book, and never copies a published book's visual signature. The style brief is checked against the style briefs of every other book in `books/` and must differ on at least a set number of fields (medium, palette family, line, texture).
- **Style lock:** the approved art direction is written to `book.json` as `style_bible`, and reused for every page. A reference image is generated for the main character first, then used as the consistency reference for all pages.
- **Gate:** the producer approves the style sample (cover + 2 pages) before the other pages are generated.
- **Illustrations:** one image per page (20), plus cover front, cover back, and any map. Stored under `illustrations/`.

**Acceptance criteria**
- Two books generated from different ideas get visibly different art directions, verified by the originality check.
- The approved style is stored, and regenerating a page reuses it.
- Image generation has a per-book cost estimate shown before it starts.

**Open decisions**
7. **Decided:** image generation goes through ChatGPT, exposed as an MCP tool the same way the episode tools are exposed to the ChatGPT/Codex app (`video_pipeline/mcp_server.py`). No image API key.
   - **Spike first (before 008 is specified in full):** confirm that ChatGPT image generation can be called from the pipeline and returns image files to disk; check whether one approved reference image keeps the main character consistent across 20 pages; measure how many images one book uses against the plan quota.
   - The PRD's Gemini image plan for the intro/outro nooks is unchanged; this decision covers AI-written books only.
8. Keep the pilot's existing illustrations, or regenerate them under the new style system? *(Recommended: keep the pilot as-is. It is the reference for the Snail Mail Express look. Only new books use 008.)*

---

## Risks

- **Image consistency across 20 pages** is the hardest part of 008. A single reference image helps but may not hold. Plan a spike before committing to the full story.
- **Quota cost of AI books:** each new book means one Codex text pass and about 22 ChatGPT image generations. Both draw on the ChatGPT plan quota, so show the estimate and get approval before the image step.
- **Kid safety** applies to AI text and images. Reviewers must approve both before publish.
- **16 GB memory:** ChatGPT image generation runs remotely, so it does not compete with TTS for memory. Keep stages sequential anyway.
- **Image consistency and ChatGPT's terms for generated images** need checking in the spike before 008 is approved.

## Not in scope

- Publishing to YouTube or KDP (the outputs are ready for manual upload).
- Background music (separate spec, already noted in spec 004).
- Accounts, multi-user access.
