# PRD: Story-aware sound tags in the narration

**Date:** 2026-11-14
**Feature:** 028-narration-expression

## Problem

Orpheus sound tags (`<gasp> <sigh> <yawn>` and the others) appear only in the kids' banter. The narrator reads every page flat, so a surprise never gasps and a relief never sighs. Narration lines are not written by Codex at all: the script builder takes them from `book.json`, so Codex has no way to place a narration tag.

## Users & Context

The book maker generating an episode script with Codex (specs 001, 017) and then voices (spec 002). Touches `contract.py` (`Banter`, `parse_banter`, narration checks), `episode.py` (`assemble_script`, `split_narration`), `episode_tools.py` and the MCP tools, `llm.py` (the Gemini provider's banter schema), the writing brief, `config/emotions.json` generation limits, and the script hash and approval flow. Narration lines may already carry the six allowed tags: they are stripped before comparing with the book text and for captions.

## Goals

- Codex decides, per book and from the story, where narration tags go; no fixed pattern (for example no automatic yawn at the end). Allowed tags are the existing six.
- Codex states placement as data in its banter output: an optional `narration_tags` list of `{page_number, sentence_index, tag}`. `assemble_script` inserts each tag at the start or end of the named narration sentence, so a tag can never land mid-sentence, inside a quotation or replace words.
- Limits live in config: at most 1 narration tag per page and, across the book, at most 1 per 2 pages on average. They apply only to newly generated banter (like the spec 017 feeling counts), never inside script validation of saved or hand-edited scripts.
- Both providers (Codex and the opt-in Gemini API) accept the same field in their output schema.
- Tags exist only in the spoken script; the printed book, captions and the plain-text comparison with the book are unchanged.
- A banter output that breaks a limit or names a missing page or sentence is refused with every problem listed (existing refusal flow).

## Non-Goals

- New tags beyond the six.
- Changing the kids' banter rules.
- Rewriting existing scripts: a script changes only when regenerated with `--force` (the narration change alters the script hash, which already voids its review and audio approval as any regeneration does; only tagged narration clips get new keys).
- A deterministic or fixed tag rule.

## Success Criteria

- A newly generated script carries narration tags within the limits, and two different books get different placements.
- Narration lines with tags equal the book text once tags are stripped; captions contain no tag text.
- Violations (too many on a page or on average, disallowed tag, unknown page or sentence) are refused with the field and reason.
- An existing approved script is unchanged and still validates.
- Unit tests cover parsing, insertion and limits; one opt-in real test generates a script with Codex.

## Architecture Decisions

None need an ADR: an optional output field inserted by deterministic code, easy to reverse.

## Out of Scope

Voice sampling (spec 027); per-feeling tag effects; a studio editor for tags; changes to the printed book.
