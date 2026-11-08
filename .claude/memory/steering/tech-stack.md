---
scope: tech-stack
loaded-by: sdd-brainstorm, sdd-specify, sdd-plan, sdd-execute, sdd-review
---

# Tech Stack

## Languages
Python 3.12 (CPython via uv) — edit to match reality

## Frameworks
- Book production: Pillow, reportlab (PDF), hand-built EPUB 3 (zipfile), static HTML flipbook
- Episode script generator (`video_pipeline/`, spec 001 v2.0.0): pydantic (banter/script models and validation), `mcp` (stdio MCP server exposing the episode tools). Default text provider: the Codex CLI bundled with the ChatGPT desktop app, run headless on the producer's ChatGPT login and limited to the episode tools. Opt-in: Gemini API (`--provider gemini-api`, `GEMINI_API_KEY`, `gemini-2.5-flash`). No local text model.
- Planned later stages (PRD.md): FFmpeg (zoompan, xfade, sidechaincompress, H.264/AAC), `mlx-audio` + Orpheus TTS `orpheus-3b-0.1-ft-4bit`
- Venv also contains pymupdf/pymupdf4llm, numpy, onnxruntime, PyYAML

## Infrastructure
Single local machine: Apple Silicon M1 Pro, 16 GB unified memory, macOS. Stages run sequentially to fit memory. Cloud limited to script text (ChatGPT via Codex by default, Gemini API opt-in) and a few keyframe images. Output target: 1920×1080 YouTube MP4.

## Package Manager
uv (`.venv` at repo root). `pyproject.toml` declares the dependencies (Pillow, reportlab, pydantic, mcp; dev: pytest, Ruff) with `uv.lock`; install with `uv sync --inexact` (keeps the extra venv packages). FFmpeg comes from Homebrew; Codex comes with the ChatGPT desktop app.
