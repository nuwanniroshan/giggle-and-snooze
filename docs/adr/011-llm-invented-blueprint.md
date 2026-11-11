# ADR-011-llm-invented-blueprint: LLM-invented book blueprint with enforced diversity

**Status:** Accepted
**Date:** 2026-11-11
**Feature:** 011-book-blueprint

## Context

Every book today gets the same Q&A template (fixed intro/outro, "older asks plot, younger reacts", 2–5 lines), the same default "warm watercolor" art style for books made from an idea (only the pilot has a real `style_bible`), and the same ambience handling. Stories feel alike.

The PRD needs a per-book blueprint (mood, art, Q&A shape, sound palette) made before the story is written, so that books made from different ideas differ visibly and audibly. Constraints:
- Text generation runs through Codex (ChatGPT login), with one validated MCP save tool as for the story and motion-plan stages.
- Output is untrusted LLM text: it must be schema-validated, kid-safe, and may only name sounds that exist in `assets/sound_library/`.
- The blueprint becomes a durable input for illustrate, episode script and render, so its schema is costly to change later.
- The user chose maximum creativity over a fixed catalog.

## Options Considered

### Option A: Hybrid draw + write

Code draws the axes by seed (mood, Q&A format, medium family, sound palette) from catalogs and rejects close combinations; Codex writes the concrete style prompt, palette and Q&A notes inside the picks.

**Pros:** distinct and reproducible; diversity guaranteed by construction.
**Cons:** variety capped by catalog size; catalogs must be curated and maintained; styles feel templated.

### Option B: Catalog draw only

Everything is picked from curated catalogs by seed.

**Pros:** deterministic, cheap, no model call.
**Cons:** least variety; prompts feel templated; new looks need code changes.

### Option C: LLM invents all, sees past books

Codex designs the whole blueprint from the idea. The brief carries a summary of every existing blueprint, and code enforces difference after the fact.

**Pros:** most creative and idea-specific; no catalogs to maintain; new looks need no code change.
**Cons:** similarity is only discouraged by the prompt, so it needs a code-side check; runs are not reproducible from the seed alone; the model can name sounds that do not exist.

## Decision

**We chose Option C** because the PRD's goal is books that do not feel the same, and the user asked for the model to invent each blueprint rather than draw from a list. Its weaknesses are covered in code, not by trust:
- The save tool validates the schema, every sound name against the library, palette hex values, and `config/disallowed_terms.txt`.
- A similarity check rejects a blueprint matching an existing book on art medium plus mood, on Q&A format, or on palette (too-close colours), then retries up to 3 attempts and fails with the reason.
- The accepted blueprint is stored as `blueprint.json`, so reproducibility comes from the committed file rather than the seed.

## Consequences

- Easier: new looks and Q&A formats need no code change; each book is shaped by its idea.
- Harder: the similarity check needs measurable fields. The schema carries normalised slug keys for `art.medium`, `qa.format` and `mood`, from a closed starter list with a synonym map (an unknown label needs a new distinct key), plus palette colours compared by CIELAB distance, so synonyms such as gouache and watercolor do not slip past the check.
- Quality varies with the model; a human can edit `blueprint.json` and the studio can regenerate it.
- If superseded (for example by a catalog draw), the `blueprint.json` schema and its consumers stay; only the generation stage changes.
