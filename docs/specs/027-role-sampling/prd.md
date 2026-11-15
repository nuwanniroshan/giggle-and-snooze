# PRD: Per-role voice sampling from the blueprint mood

**Date:** 2026-11-14
**Feature:** 027-role-sampling

## Problem

Orpheus `temperature` and `top_p` are one global setting for every voice. A book's mood (spec 011) already says how each role should sound (energy and pace per role), but nothing connects that mood to the speech model: a hushed narrator and bouncy kids are spoken with the same sampling.

## Users & Context

The book maker generating voices for a book (CLI or studio). Touches `config/voices.json`, `video_pipeline/synthesize_voices.py` (`VoiceConfig`, `clip_key`, `settings_hash`, review output, and the uncommitted `role_delivery` per-role gain and speed work this builds on and commits first), `tts.py` (`OrpheusVoice.synthesize`, today the only place sampling reaches the model), the blueprint `mood` (`Role.energy`: hushed, calm, lively, bouncy; `Role.pace`: slow, steady, brisk, for storyteller, kid_older and kid_younger) and the studio audio view.

## Goals

- Each role has its own `temperature` and `top_p`: a per-role default in `voices.json` (a separate `role_sampling` block, validated like `role_delivery`), shifted by the book's blueprint mood through a table in `voices.json` (energy shifts temperature and top_p; pace shifts temperature only; offsets add) and clamped to configured bounds.
- Shipped defaults equal today's global values (0.6 and 0.8) for every role, so the sampling change is opt-in through the blueprint mood: a book whose mood maps to zero offsets, and every book without a blueprint, sounds and caches exactly as before.
- Effective values are the existing `temperature` and `top_p` fields of each clip's cache key (no new key field), so keys are byte-identical whenever the values are equal; a role whose values change gets new clip keys and only its clips are re-spoken at the next synthesis.
- Voices are marked stale in the studio when the effective per-role values no longer match those the audio was made with (recorded in `blueprint_used.json` as a `voices` entry keyed on the effective values, not the blueprint sha), with the re-synthesize command. Editing the blueprint never voids audio approval or the script hash (spec 011 rule).
- `OrpheusVoice.synthesize` takes per-call temperature and top_p (test fakes follow).
- The effective values per role are listed in `review.md` and the studio audio view.
- A blueprint that exists but cannot be loaded makes synthesis use the per-role defaults and log why.

## Non-Goals

- Changing pitch, speed, gain, seeds or voices.
- Raw temperature or top_p numbers in the blueprint.
- Per-line sampling.
- Changing sampling defaults for books without a blueprint.

## Success Criteria

- For a blueprint with narrator energy hushed and a kid's energy bouncy, the narrator's effective temperature is lower than that kid's and every value lies inside the bounds.
- With no blueprint, each role's effective values equal its config default, and clip keys equal today's (golden test).
- Changing one role's mood changes only that role's clip keys.
- After a mood edit the studio shows voices stale while the approval stays; after re-synthesis the stale mark clears.
- `review.md` lists each role's temperature and top_p.

## Architecture Decisions

None need an ADR: additive config, existing cache-key fields, easy to reverse.

## Out of Scope

Tuning table values by listening beyond shipping reasonable defaults; mood-driven pitch or speed; a studio editor for the table; making the new defaults apply to every existing book.
