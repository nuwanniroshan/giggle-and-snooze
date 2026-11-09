---
scope: conventions
loaded-by: sdd-specify, sdd-plan, sdd-execute, sdd-review
---

# Conventions

## File Naming
snake_case Python modules and functions; illustrations named `page_NN.png`, `cover_front.png`, `cover_back.png` — edit to match reality

## Directory Structure
- `books/<slug>/book.json` — source of truth per book (metadata, style_bible, pages); one folder per book (spec 005)
- `assets/illustrations/`, `assets/references/` — art inputs; `assets/audio_sfx/` planned for music/SFX
- `scripts/` — book-production scripts
- `video_pipeline/` — planned video stages (generate_script, synthesize_voices, render_episode, mcp_server)
- `books/<slug>/build/` — generated deliverables per book; project-level tool output in `build/`; never hand-edit

## Code Style
Ruff format + lint (`py312`, line length 100); see `.claude/rules/python-project.md`. Type hints on public functions; `pathlib.Path` everywhere.

## Architectural Patterns
Linear batch pipeline of independent, idempotent stages that pass JSON/media files through `build/`. Each stage reads `book_data.json` (or the previous stage's output), never hardcoded book specifics. One CLI entry point per stage, later wrapped by a single command / MCP tool.
