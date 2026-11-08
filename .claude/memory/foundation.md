# Kiddo Foundation

> Loaded every session. To amend, follow the Amendment Process below.

## Mission
Turn illustrated children's books into low-cost, repeatable "simulated live" read-aloud YouTube episodes, released weekly or monthly, for young kids and their parents.

## Principles
1. **Local-first, near-zero cost:** heavy work (TTS, audio mixing, video rendering) runs locally on the M1 Pro 16 GB, one stage at a time. Cloud use is limited to text-only prompts or a few keyframe images, about $0.01 per episode.
2. **Kid-safe content:** every script line, Q&A and piece of metadata is age-appropriate for the book's audience and true to its story and illustrations.
3. **Natural, warm voices:** the Storyteller and both kids sound human and expressive, never robotic. Voice quality is a release blocker, not polish.
4. **Book-agnostic pipeline:** a new book is a new `book_data.json` plus illustrations, with no code edits, hardcoded titles/paths or per-book hand-tuning.

## Operational Context
Steering files in `.claude/memory/steering/` carry project-specific operational context
(tech stack, test strategy, conventions, team practices). Each file's `loaded-by`
frontmatter lists which skills silently incorporate it during that skill's session.
Edit steering files freely — they are not subject to the amendment process.

## Amendment Process
Document rationale → explicit team approval → backwards-compatibility check.
