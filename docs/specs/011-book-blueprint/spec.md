# Spec 011: Per-book blueprint

**Status:** Approved
**Version:** 1.1.0
**Created:** 2026-11-11
**Last Updated:** 2026-11-11
**Branch:** `feat/011-book-blueprint`
**PRD:** [prd.md](prd.md)
**ADRs:** [011-llm-invented-blueprint](../../adr/011-llm-invented-blueprint.md) (Accepted)
**Depends on:** specs 001 (episode script and its Q&A rules), 004 (studio), 006 (idea to book, drafts), 007 (sound library and ambience), 008 (illustrations), 010 (animation settings)

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-11 | Initial approved spec |
| 1.1.0 | 2026-11-11 | Added AC-5.5 to AC-5.7 and the matching FR-7 line: a generated script gets `ambience` lists from the blueprint (found while planning: the script writer's output has no `ambience`, so the sound palette would otherwise have no audible effect) |

## Problem Statement

Every book feels the same. The Q&A follows one template, books made from an idea all default to the same watercolor look (their art style is empty; only the pilot has a real one), and the sound is handled the same way for every story. The producer cannot get distinct books without hand-editing every stage. Before a story is written, each new book needs its own creative direction (mood, art, Q&A shape, sound palette) that the later stages follow.

## Goals

- A background **design** step runs after the producer picks an idea and before the story is written, and produces a hand-editable `blueprint.json` for the book.
- Each blueprint differs measurably from the blueprints of other books.
- The story, the illustrations, the episode script and the ambience follow the blueprint.
- Editing a blueprint marks the outputs made from the old one as stale, and nothing regenerates silently.
- Books without a blueprint behave exactly as they do today.

## Non-Goals

- Building the music layer, or playing sfx tied to story events (the blueprint records music and sfx picks only).
- Applying the blueprint's animation preferences to the animation plan (recorded only).
- Per-book casts or voices.
- Re-styling existing books, or creating blueprints for them automatically.
- Relaxing any safety rule: kid-line length, allowed tags, kid-safe terms and grounding checks.

## Users and Context

**Primary user:** the producer, using the studio (or the CLI) on the M1 Pro (16 GB).

**Usage context:**
- Today the producer picks an idea and chooses "Write this book" (spec 006); a draft in `drafts/<draft-id>/` is written, reviewed, then turned into `books/<slug>/` by "Create book".
- After the book exists, the producer runs illustrate, script, voices and render, one stage at a time (spec 004).
- Spec 001 fixes the Q&A structure: the number of Q&A pages is between ⌈N/3⌉ and ⌊N/2.5⌋ (AC-3.1), first and last positions and gaps of 2–3 pages (AC-3.2), a kid mix (AC-3.3), and 2–5 lines per moment ending with a Storyteller answer (AC-3.4). This spec does not change them.
- The sound library (spec 007) lists CC0 sounds by name and kind (ambience, sfx, music).

**Vocabulary:** *blueprint* is the per-book creative direction. *Key* is a lowercase slug (letters, digits, hyphens, 2 to 40 characters) that names a medium, a mood or a Q&A format so that two blueprints can be compared. *kid_older* and *kid_younger* are the roles of Leo and Mia in `config/cast.json`.

## User Stories

### Story 1: A blueprint is made before the story

**As a** producer
**I want** each new book to get its own creative direction before the story is written
**So that** the story and everything after it start from a distinct look and feel.

**Acceptance criteria:**

- [ ] **AC-1.1** Given an idea is shown When the producer chooses "Write this book" Then a draft is created in state `designing`, the blueprint is made in the background, and the story is not started until the blueprint is saved
- [ ] **AC-1.2** Given the blueprint was saved When the draft moves on Then the draft's state becomes `writing` automatically and the draft folder contains `blueprint.json`
- [ ] **AC-1.3** Given a draft is `designing` When the studio is restarted Then the draft is listed as `interrupted` with its idea, and the producer can start the design again
- [ ] **AC-1.4** Given the blueprint could not be made within the attempt limit When the job ends Then the draft is in state `design_failed`, the page lists the reasons (each naming the field or book that caused a rejection), and offers "Try the design again" and "Write without a blueprint"
- [ ] **AC-1.5** Given a draft in state `design_failed` When the producer chooses "Write without a blueprint" Then the story is written as in spec 006 and the resulting book has no blueprint
- [ ] **AC-1.6** Given a draft with a blueprint When the producer chooses "Create book" Then `books/<slug>/blueprint.json` exists with the same content, `books/<slug>/book.json` has its `style_bible.style_prompt_prefix` set from the blueprint, and the draft is removed
- [ ] **AC-1.7** Given a draft is `designing` When the producer chooses "Discard" and confirms Then the job stops and the draft and its log are deleted

### Story 2: Each blueprint is distinct

**As a** producer
**I want** a new book's blueprint to differ from my other books
**So that** two stories do not feel the same.

**Acceptance criteria:**

- [ ] **AC-2.1** Given a candidate blueprint with the same `art.medium_key` and the same `mood.key` as another book's blueprint When it is checked Then it is rejected, and the reason names that book and both fields
- [ ] **AC-2.2** Given a candidate blueprint with the same `qa.format_key` as another book's blueprint When it is checked Then it is rejected, and the reason names that book and the field
- [ ] **AC-2.3** Given a candidate blueprint where at least 3 of its 5 palette colours are each within the configured colour distance of some colour in another book's palette When it is checked Then it is rejected, and the reason names that book and the three colours
- [ ] **AC-2.4** Given a candidate blueprint that passes all three checks When it is checked Then it is saved
- [ ] **AC-2.5** Given a candidate was rejected When the job continues Then the writer is told the rejection reasons and tries again, up to the attempt limit in `config/story.json`, and each attempt gets its own time limit from the same file
- [ ] **AC-2.6** Given a key that is a known synonym in `config/blueprint.json` (for example a medium listed as a synonym of another medium) When a blueprint uses it Then it is compared as its canonical key
- [ ] **AC-2.7** Given a key that is not in the configured lists When a blueprint uses it Then it is accepted only if it is a valid slug, and it is then compared as itself
- [ ] **AC-2.8** Given a blueprint is regenerated for a book or draft When the checks run Then that book's or draft's own current blueprint is not compared
- [ ] **AC-2.9** Given other drafts have blueprints When a new blueprint is checked Then those drafts' blueprints are compared as well as the books' blueprints
- [ ] **AC-2.10** Given the writer is working When it is briefed Then the brief contains, for every blueprint it will be compared with, the book title, the three keys and the five palette colours

### Story 3: The pictures follow the blueprint

**As a** producer
**I want** the illustrations to use the book's own art direction
**So that** each book looks different.

**Acceptance criteria:**

- [ ] **AC-3.1** Given a book with a blueprint When a page or cover picture is painted Then the painting request contains the blueprint's style prompt prefix, palette (names and colours), lighting and avoid list, and does not contain the default watercolor style text
- [ ] **AC-3.2** Given a book without a blueprint When a page or cover picture is painted Then the painting request is identical to the one made before this spec
- [ ] **AC-3.3** Given a draft with a blueprint When the story is written Then the writer's brief contains the blueprint's mood label and summary and its art style prefix, and tells the writer to describe scenes without naming a painting medium
- [ ] **AC-3.4** Given a book whose `blueprint.json` was edited When the producer chooses "Rebuild style bible" Then `style_bible.style_prompt_prefix` in `book.json` equals the blueprint's value and no other field of `book.json` changes

### Story 4: The Q&A follows the blueprint

**As a** producer
**I want** the kids' questions and the intro and outro to fit this book
**So that** the episode does not follow one fixed template.

**Acceptance criteria:**

- [ ] **AC-4.1** Given a book with a blueprint When the episode script is requested Then the writer's brief contains the blueprint's Q&A format, question types, kid notes, intro and outro hooks and the mood's per-role energy, and does not contain the fixed "kid_older asks about the plot, kid_younger reacts to illustration details" instruction
- [ ] **AC-4.2** Given a book with a blueprint When the number of Q&A moments is chosen Then it lies in the blueprint's moment range clamped to the spec 001 limits for the book's actual page count (AC-3.1 of spec 001), the same seed gives the same number, and the script's log says when the range was clamped
- [ ] **AC-4.3** Given a generated script for a book with a blueprint When it is checked Then every rule of spec 001 passes unchanged: placement, gaps, kid mix, 2 to 5 lines per moment ending with a Storyteller answer, kid lines of at most 25 words, allowed tags only, kid-safe terms and grounding
- [ ] **AC-4.4** Given a book without a blueprint When the episode script is requested Then the writer's brief and the plan are identical to those made before this spec

### Story 5: The ambience follows the blueprint

**As a** producer
**I want** the background sounds to come from the book's own sound palette
**So that** the soundscape matches the story's setting.

**Acceptance criteria:**

- [ ] **AC-5.1** Given a book with a blueprint When the episode script is requested Then the writer's brief lists the blueprint's ambience names as the only names allowed in `ambience` lists
- [ ] **AC-5.2** Given a script for a book with a blueprint whose intro, page or outro `ambience` names a sound that is not in the blueprint's ambience list When the script is checked Then it is refused and the message names the sound and where it is used
- [ ] **AC-5.3** Given a script for a book without a blueprint When `ambience` is checked Then it is checked only against the sound library, as before this spec
- [ ] **AC-5.4** Given a blueprint that names a sound not in the sound library, or an ambience entry whose sound is not of kind `ambience`, or an sfx entry whose sound is not of kind `sfx` When it is saved Then it is refused and the message names the sound
- [ ] **AC-5.5** Given a script is generated for a book with a blueprint When it is assembled Then the intro, every page and the outro each carry an `ambience` list drawn from the blueprint's ambience names: the names are split into consecutive runs across the pages in palette order, the intro gets the first name and the outro the last, every name is used at least once when the palette has no more names than the book has pages, and the same inputs always give the same lists
- [ ] **AC-5.6** Given a generated script with `ambience` lists from the blueprint When the producer edits a list by hand within the blueprint's palette Then the edit is kept, the script's voice hash and review mark are unchanged, and a regeneration with `--force` is the only thing that rewrites the lists
- [ ] **AC-5.7** Given a script generated for a book without a blueprint When it is assembled Then it has no `ambience` key in any block, as before this spec

### Story 6: I can see, edit and regenerate the blueprint, and stale outputs are flagged

**As a** producer
**I want** to inspect and change a blueprint and see what it affects
**So that** I stay in control and nothing changes without me.

**Acceptance criteria:**

- [ ] **AC-6.1** Given a book with a blueprint When the producer opens the book page Then it shows the mood, art medium, five colour swatches with names, Q&A format, sound palette, and the recorded music and animation notes
- [ ] **AC-6.2** Given a book or draft with a blueprint When the producer chooses "Regenerate blueprint" and confirms Then a design job runs, the old file is kept as `blueprint.json.bak`, and the new blueprint replaces it only if it passes validation and the distinctness checks
- [ ] **AC-6.3** Given a book without a blueprint When the producer opens the book page Then no "Regenerate blueprint" action is offered
- [ ] **AC-6.4** Given a book whose blueprint changed after its illustrations were painted When the producer opens the book page Then the pictures are marked stale and the page names the illustrate command to rerun
- [ ] **AC-6.5** Given a book whose blueprint changed after its episode script was generated When the producer opens the book page Then the script is marked stale and the page names the script command to rerun
- [ ] **AC-6.6** Given a stale output When the producer does not rerun anything Then the output is not changed, and a render still uses the existing files
- [ ] **AC-6.7** Given a hand-edited `blueprint.json` that breaks the schema or a validation rule of FR-3 When the book is opened or a stage reads it Then the studio and the stage refuse it, naming the file, the field and the reason
- [ ] **AC-6.8** Given a blueprint job is running When the producer starts another stage Then the studio refuses, as for any other running stage
- [ ] **AC-6.9** Given a blueprint job is running When the producer chooses Stop Then the job ends, the existing `blueprint.json` is unchanged and the job is listed as stopped

### Story 7: Existing books are unchanged

**As a** producer
**I want** my existing books to keep working without a blueprint
**So that** nothing I made breaks.

**Acceptance criteria:**

- [ ] **AC-7.1** Given a book with no `blueprint.json` When any stage runs Then its inputs, requests and cache keys are the same as before this spec, so existing renders stay up to date
- [ ] **AC-7.2** Given a book with no `blueprint.json` When the studio shows it Then it shows no blueprint panel errors and no stale marks

## Functional Requirements

### FR-1: Blueprint file

A blueprint is `blueprint.json` in the book folder (`books/<slug>/`) or the draft folder (`drafts/<draft-id>/`). It uses `snake_case` keys and has these fields:

- `version`: the number 1.
- `mood`: `key`, `label` (up to 40 characters), `summary` (up to 300), and `roles` with `storyteller`, `kid_older` and `kid_younger`, each with `pace` (`slow`, `steady` or `brisk`), `energy` (`hushed`, `calm`, `lively` or `bouncy`) and `quirk` (one sentence, up to 200 characters).
- `art`: `medium_key`, `medium_label` (up to 40), `palette` (exactly 5 entries of `name` up to 30 characters and `hex` as `#rrggbb`), `line` (up to 200), `lighting` (up to 200), `composition` (up to 200), `style_prompt_prefix` (up to 600), `avoid` (0 to 6 entries of up to 80 characters).
- `qa`: `format_key`, `format_label` (up to 40), `question_types` (2 to 4 entries of 1 to 40 characters), `intro_hook` and `outro_hook` (up to 200 each), `moments_min` and `moments_max` (whole numbers 1 to 12, min at most max), `kid_notes` with `kid_older` and `kid_younger` (up to 200 each).
- `sound`: `ambience` (1 to 6 library names), `sfx` (0 to 6 library names), `music_direction` (up to 200).
- `animation`: `intensity` (0.25 to 2.0) and `effects` (0 to 4 names of the effects defined in the animation settings of `config/render.json`).
- `rationale`: up to 400 characters, saying how this blueprint differs from the others.

**Must:** be written atomically with `indent=2` and `ensure_ascii=False`.
**Must:** be valid JSON with only these fields; an unknown field is a validation error naming the field.
**Must not:** be committed from a draft folder (drafts stay git-ignored); `books/<slug>/blueprint.json` is committed.

### FR-2: Design job

The design job takes a draft (its idea) or an existing book with a blueprint (its title, blurb and age range) and writes a blueprint. It runs as one studio job of the existing runner, with a log, stop, and a one-stage-at-a-time rule, and from the CLI as `write_story blueprint --draft <id>` or `--book <slug>`. It uses the same text provider and the same attempt count and time limit per attempt as the story stage (`config/story.json`). The writer may save only through a validating save step.

**Must:** run before the story is written for every new draft, and start the story automatically when it succeeds.
**Must:** leave an existing blueprint unchanged until a new one passes all checks.
**Must not:** retry after the attempt limit; it ends with the reasons.

### FR-3: Validation on save and on load

A blueprint is refused when:
- it breaks the field rules of FR-1 (types, lengths, enums, ranges, slug rule, `#rrggbb`);
- any text field contains a term in `config/disallowed_terms.txt` (matched as the story stage matches them);
- a sound name is not in the sound library or has the wrong kind (AC-5.4);
- an effect name is not defined in the animation settings;
- `moments_min` is greater than `moments_max`.

The same checks run when a stage reads a hand-edited file (AC-6.7). Every message names the file, the field and the reason.

### FR-4: Distinctness checks

On save by the design job (not on hand edits) the candidate is compared with every other book's blueprint and every other draft's blueprint, excluding its own. It is rejected when any of these holds (AC-2.1 to AC-2.3):
- same `art.medium_key` and same `mood.key`;
- same `qa.format_key`;
- at least 3 of its 5 palette colours are each within the configured perceptual colour distance of some colour in the other palette.

Keys are canonicalised through the synonym map in `config/blueprint.json` before comparing. `config/blueprint.json` holds the starter key lists, the synonym map, and the colour distance (default 20 on the CIE76 scale). The writer's brief carries the data needed to avoid a rejection (AC-2.10).

**Must:** report every failed check in one rejection, not just the first.

### FR-5: Story brief

For a draft with a blueprint, the story writer's brief adds the mood label and summary and the art style prefix, and tells the writer not to name a painting medium in page prompts (AC-3.3). Without a blueprint the brief is unchanged.

### FR-6: Illustration requests

For a book with a blueprint, the picture request uses the blueprint's art fields in place of the default style text (AC-3.1). `style_bible.style_prompt_prefix` in `book.json` is a derived copy of the blueprint's prefix, set at book creation and rewritten only by "Rebuild style bible" (AC-3.4). `blueprint.json` is the source of truth; if the two differ, the blueprint wins.

### FR-7: Episode script

For a book with a blueprint, the writer's brief and the Q&A plan follow Story 4, and `ambience` follows Story 5. The validators of spec 001 and the sound library stay in force (AC-4.3).

**Must:** clamp the moment range to the spec 001 limits for the actual page count, and log it.
**Must:** write the `ambience` lists of AC-5.5 when it assembles the script of a book with a blueprint, and write none for a book without one (AC-5.7).
**Must not:** change spec 001's Q&A placement, gaps, kid mix, line count or line-length rules.

### FR-8: Staleness

Each of these stages records which blueprint content it was made from: illustration painting, episode script generation. A stage whose recorded blueprint differs from the current one is stale. A book without a blueprint has no recorded blueprint and is never stale for this reason. The recorded blueprint content is ignored by the script hash used for voice approval (as `ambience` is), so editing the blueprint alone does not withdraw the voice approval.

**Must:** show stale outputs with the command that fixes them.
**Must not:** regenerate or delete any output because a blueprint changed.

### FR-9: Studio

The book page shows the blueprint panel (AC-6.1) with "Regenerate blueprint" (AC-6.2, not offered without a blueprint) and "Rebuild style bible" (AC-3.4). The draft page shows the `designing` and `design_failed` states with the actions of AC-1.4. Job records and logs follow the existing job conventions.

### FR-10: Legacy compatibility

A book without `blueprint.json` is handled exactly as before this spec in every stage, in requests, plans and cache keys (AC-3.2, AC-4.4, AC-5.3, AC-7.1).

## Non-Functional Requirements

### Performance

- The distinctness check completes in under 1 second for 100 other blueprints.
- The design job takes at most the attempt limit times the per-attempt time limit from `config/story.json`.

### Security

- The writer can only save a blueprint through the validating save step; its output is never passed to the image or audio stages unvalidated.
- Blueprint text is subject to the kid-safe terms list before it can reach a request.
- No secrets are stored in the blueprint, the config or the logs.

### Reliability

- `blueprint.json` is never left partly written: it is written to a temporary file and moved into place.
- A failed, stopped or interrupted job leaves the previous blueprint (or none) in place.
- Running the design twice with the same inputs may give different blueprints; the committed file is the record.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Writer returns invalid data or breaks a FR-3 rule | The save is refused with the file, field and reason; the writer is told and tries again within the attempt limit |
| Writer's blueprint is too close to another book | Rejected with the book, field(s) and colours named; the writer tries again |
| All attempts fail | Draft becomes `design_failed` with the reasons; producer may retry the design or write without a blueprint |
| Provider unavailable or the time limit is hit | The attempt counts as failed with the provider's reason; same handling as other attempts |
| Studio restarts during design | Draft is listed as `interrupted`; the producer can start the design again |
| Hand-edited `blueprint.json` is invalid | Studio and stages refuse it, naming file, field and reason; no output is changed |
| Script's `ambience` names a sound outside the blueprint's palette | The script is refused naming the sound and where it is used |
| Sound named in a blueprint is removed from the library later | The stage that reads the blueprint refuses it, naming the sound; the producer edits or regenerates the blueprint |
| Another job is running | The studio refuses to start the design job, as for other stages |
| Moment range outside the spec 001 limits for the page count | The range is clamped and the log says so; it is not an error |

## Decisions

- **Comparison scope:** a new blueprint is compared with every other book and draft, not only recent ones.
- **Colour distance default:** 20 on the CIE76 scale, set in `config/blueprint.json` so it can be tuned without code changes.
- **Moment range is clamped, not rejected,** at script time, because the final page count is not known when the blueprint is made (the story may be shorter than the maximum).
- **Failure keeps the draft usable:** "Write without a blueprint" produces a book exactly like today's.
- **Regenerate is offered only where a blueprint exists;** this spec does not create blueprints for existing books.
- **Rejections in the design job only:** hand-edited blueprints are validated (FR-3) but not compared for distinctness (FR-4), so the producer can make deliberate choices.
- **Stale outputs are marked, never rebuilt,** and editing a blueprint does not withdraw voice approval.

## Open Questions

- None. (Resolved: a new blueprint is compared with every existing book and draft, with no limit to recent ones; see Decisions.)

## Out of Scope (Future Considerations)

- Music generation or selection beyond recording `music_direction`.
- Sfx cues tied to story moments.
- Applying `animation` preferences to the animation plan.
- Any change to voices, pitch or TTS.
- Catalog-based or seeded drawing of blueprint axes.
- Blueprints for existing books and bulk re-styling.
