# PRD: Per-book blueprint

**Date:** 2026-11-11
**Feature:** 011-book-blueprint

## Problem

Every book turns out feeling the same. Q&A follows one template (fixed intro/outro, "older asks plot, younger reacts", 2–5 lines), books made from an idea default to the same watercolor look (their `style_bible` is empty; only the pilot has a real one), and sound is the same for every story. The people making the books (the studio user) cannot get distinct books without hand-editing each stage.

## Users & Context

The user adds books from an idea in the studio (spec 006), then runs illustrate, script, voices and render. This feature adds a step before the story is written: a background job that designs the book's creative direction. It touches `write_story` (spec 006), `illustrate` (spec 008), `episode`/`llm` brief and `plan_episode` (spec 001), the sound library and ambience (spec 007), and the studio job runner (spec 004). Existing books have no blueprint and must keep working unchanged.

## Goals

- A **blueprint job** that runs after an idea candidate is chosen and before the story is written, started from the studio and from `write_story blueprint --draft <id>`.
- A committed, hand-editable, schema-validated `blueprint.json` beside `book.json`, containing:
  - **mood**: a per-book mood chosen by the LLM and required to differ from existing books (not drawn by code), with a fixed set of per-role energy fields (pace, energy level, one quirk sentence) for Storyteller, kid_older and kid_younger;
  - **art**: medium, 5-colour palette, line quality, lighting, composition, and a `style_prompt_prefix`;
  - **qa**: format, question-type mix, intro/outro hook, moment-count range, kid personality notes (cast and voices stay fixed);
  - **sound**: ambience and sfx names chosen only from the sound library, plus music direction (recorded only);
  - **animation**: intensity and preferred effects (recorded only).
- The blueprint is written by the LLM, which sees a summary of every existing blueprint (its own book excluded on Regenerate), and code rejects a blueprint too close to an existing one, retrying up to 3 times and then failing with the reason. "Too close" is defined as: equal normalised key for `art.medium` together with `mood.key`, or equal `qa.format` key, or at least 3 of 5 palette colours within a CIELAB distance threshold of a colour in another palette (threshold set in the spec, in `config/`). Keys are lowercase slugs from a closed starter list that code maps synonyms onto (for example gouache and watercolor both map to `painted-water-media`); an unknown label is accepted only with a distinct key not already in the list.
- While the book is a draft the blueprint lives in `drafts/<id>/` and is copied into `books/<slug>/` on promotion. `blueprint.json` is the source of truth; `style_bible` is a derived copy. If the blueprint is edited later, art is marked stale and the studio's rebuild action rewrites `style_bible` from it.
- Consumers use it:
  - story writing is conditioned on the mood;
  - `illustrate` uses the blueprint's style prefix, palette and lighting for pages and covers, and the prefix is copied into `style_bible`;
  - the episode script uses the blueprint's Q&A format, mood notes and hooks. Spec 001's structure is unchanged: the blueprint's moment-count range must sit inside spec 001 AC-3.1 (7–8 for 20 pages), and placement, gaps (AC-3.2), kid mix (AC-3.3), and 2–5 lines ending with a Storyteller answer (AC-3.4) stay as they are. The format changes what is asked and how it sounds, not that structure; the seed still draws the exact count and pages inside the range;
  - the script's `ambience` names come from the blueprint's sound palette and are validated against it and the library.
- Editing a blueprint after later stages ran marks the script, art and audio stale, and the studio says what to rerun. Nothing regenerates silently.
- The studio shows the blueprint on the book page with a Regenerate action.

## Non-Goals

- Building the music layer, or playing sfx tied to story events.
- Applying animation preferences to the animation plan.
- Per-book casts or voices.
- Re-styling existing books or backfilling blueprints for them.
- Relaxing any safety rule: kid-line length, allowed tags, kid-safe terms and grounding checks stay in code.

## Success Criteria

- Three books made from different ideas have blueprints that pass the similarity check against each other (different medium+mood keys, Q&A format keys, and palettes), and their ambience palettes are not identical.
- A blueprint too close to an existing one is rejected and retried, and after 3 attempts the job fails with a message naming the matching book and field. The draft stays usable: the user can retry the job or continue without a blueprint, as for books today.
- Hashes: only the art, script and audio stages that read the blueprint include it in their keys, and an absent blueprint contributes nothing.
- Every sound named in a blueprint exists in the library; a blueprint with an unknown sound, bad hex colour, or a disallowed term is refused.
- Existing books without a blueprint render exactly as before (cache keys unchanged).
- Editing `blueprint.json` marks downstream outputs stale in the studio and names the commands to rerun.
- The blueprint job runs as one studio job with log and stop, like the other stages.

## Architecture Decisions

- [LLM-invented blueprint with enforced diversity](../../adr/011-llm-invented-blueprint.md) — Codex designs the whole blueprint from the idea while seeing past blueprints; code validates schema, sounds, palette and safety terms, and rejects near-duplicates.
- Inline (no ADR): the blueprint is a separate `blueprint.json` file next to `book.json`, because it is hand-editable, has its own staleness hash, and `book.json` stays the story's source of truth. Style prefix is also copied into `style_bible` so existing consumers keep working.

## Out of Scope

- Music generation or selection logic beyond recording the direction.
- Sfx cues tied to story moments.
- Animation tuning from the blueprint.
- Any change to voices, pitch or TTS.
- Catalog-based or seeded drawing of blueprint axes (considered and rejected in the ADR).
