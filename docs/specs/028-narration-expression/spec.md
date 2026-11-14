# Spec 028: Story-aware sound tags in the narration

**Status:** Approved
**Created:** 2026-11-14
**Branch:** `feat/028-narration-expression`
**Depends on:** specs 001 (episode script), 002 (voices), 011 (blueprint), 017 (script emotions)
**PRD:** [prd.md](prd.md)

---

## Problem Statement

The narrator reads every page without any sound of feeling: a surprise never gasps and a relief never sighs. Sound tags exist only in the kids' banter. The narration lines are taken word for word from the book, so the script writer (Codex) has no way to say where a narration tag should go.

## Goals

- For each newly generated episode script, Codex decides from the story where narration sound tags go, so two different books get different placements.
- Tags are used sparingly: at most 1 per page and, across the book, at most 1 per 2 pages on average.
- A tag can only sit at the start or end of a narration sentence; it never replaces a word or lands mid-sentence or inside a quotation.
- The printed book, captions and the comparison with the book text are unaffected.

## Non-Goals

- New tags beyond the six allowed (`<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`).
- Changing the kids' banter rules or the existing feeling counts.
- Rewriting existing scripts: they change only when regenerated with force.
- A fixed or rule-based tag pattern (for example a yawn at every book's end).

## Users and Context

**Primary user:** the producer generating an episode script (command line, studio or autopilot) before voices.
**Usage context:** once per book, at script generation; tags then sound in the voice stage and the video.
**Mental model:** "The storyteller should gasp where the story gasps, picked by the model for this book."

## User Stories

### Story 1: Codex places narration tags

**As a** producer **I want** Codex to choose narration tags from the story **So that** the narrator sounds expressive and each book differs.

- [ ] **AC-1.1** Given a book and the script writer When the script is generated Then the writer may return a list of narration tags, each naming a story page, a sentence of that page's narration (counted from 1), a tag from the allowed six and a position `start` or `end`.
- [ ] **AC-1.2** Given a returned list When the script is assembled Then each tag is inserted at the named position of the named narration sentence of that page, and no other narration text changes.
- [ ] **AC-1.3** Given a script with narration tags When its narration is compared with the book text Then the lines are equal once tags are removed.
- [ ] **AC-1.4** Given a script with narration tags When captions are made Then no caption contains tag text.
- [ ] **AC-1.5** Given no returned list, or an empty one When the script is assembled Then the narration is exactly as today.
- [ ] **AC-1.6** Given the writer's guidance When a script is written Then the writer is told that placements must follow the story (moments, feelings and the book's mood), that fixed patterns repeated in every book are not wanted, and the page and book limits.

### Story 2: Limits and refusals

- [ ] **AC-2.1** Given a returned list with more than the configured number of tags on one page (default 1) When it is checked Then it is refused naming the page.
- [ ] **AC-2.2** Given a returned list whose total exceeds the configured average (default 1 per 2 pages, rounded up) When it is checked Then it is refused with the total and the limit.
- [ ] **AC-2.3** Given a tag outside the allowed six, a page that is not a story page of the book, a sentence number outside that page's narration, or a position other than `start` or `end` When it is checked Then it is refused naming the entry and the reason.
- [ ] **AC-2.4** Given a list with several problems When it is refused Then every problem is listed together, and the writer is asked again within the existing attempt limit.
- [ ] **AC-2.5** Given the same sentence named twice When it is checked Then it is refused as a duplicate.
- [ ] **AC-2.6** Given a tag whose position would fall inside an open quotation (for example the start of the second sentence of a quote that continues across sentences), or a position that already carries a tag When it is checked Then it is refused with the reason.

### Story 3: Existing scripts and approvals

- [ ] **AC-3.1** Given an existing approved script When nothing is regenerated Then it is unchanged and still passes validation, whatever the limits.
- [ ] **AC-3.2** Given a hand-edited script with more narration tags than the generation limits When it is validated Then it passes (limits apply to newly generated output only).
- [ ] **AC-3.3** Given a script regenerated with force that gains narration tags When its hash changes Then the existing review and voice approval no longer match (as for any regeneration) and the studio shows the re-review and re-voice steps as needed.
- [ ] **AC-3.4** Given a regenerated script When voices are synthesized Then only narration lines whose spoken text changed are spoken again; other clips are reused.

### Story 4: Providers

- [ ] **AC-4.1** Given the Codex provider When it saves the script Then its save tool accepts the narration-tag list and returns refusals as in Story 2.
- [ ] **AC-4.2** Given the opt-in Gemini provider When it returns banter Then the same list is accepted and checked the same way.

## Functional Requirements

- **FR-1** Banter output gains an optional `narration_tags` list: `{page_number, sentence, position, tag}` (`sentence` from 1, `position` is `start` or `end`). Absent or empty means none.
- **FR-2** Sentences are those the script builder already splits each page's narration into; the tag text is added to the sentence's spoken line and the result is checked against the book text with tags removed.
- **FR-3** Limits live in configuration: tags per page (default 1) and average per page (default 0.5, applied as the rounded-up total over the page count). They are enforced only on newly generated banter, never in script validation of saved or hand-edited scripts.
- **FR-4** The writing guidance and the output schema of both providers describe the list, the limits and the "follow the story, no repeated fixed pattern" rule.
- **FR-5** Refusals list every problem at once and reuse the existing attempt limit.
- **FR-6** Existing pronunciation handling, captions and the voice stage need no change: tags in narration are already allowed and stripped where needed.

## Non-Functional Requirements

- **NFR-1 Testability:** parsing, insertion and limits are pure functions unit-tested without Codex or Gemini; an opt-in real test generates a script with Codex.
- **NFR-2 Safety:** tags come only from the allowed six; free text from the writer never enters a shell, file name or the book data.
- **NFR-3 Compatibility:** scripts and tools that do not know the field behave as before; the book data is never changed.

## Error Scenarios

| Scenario | Behaviour |
|---|---|
| Writer returns unknown tag, page or sentence | Refused with the entry and reason, retried within the attempt limit |
| Too many tags on a page or in the book | Refused with the counts and limits |
| Attempts run out | Script generation fails as it does today; nothing is saved |
| Existing script has many tags | Still valid (limits apply to generation only) |

## Open Questions

- None.

## Out of Scope (Future Considerations)

Per-feeling tag effects, a studio editor for tags, deterministic tag rules, changing the printed book, per-role voice sampling (spec 027, deferred).
