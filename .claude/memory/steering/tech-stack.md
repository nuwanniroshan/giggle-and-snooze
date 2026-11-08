---
scope: tech-stack
loaded-by: sdd-brainstorm, sdd-specify, sdd-plan, sdd-execute, sdd-review
---

# Tech Stack

## Languages
Python 3.12 (CPython via uv) — edit to match reality

## Frameworks
- Book production: Pillow, reportlab (PDF), hand-built EPUB 3 (zipfile), static HTML flipbook
- Planned video pipeline (PRD.md): FFmpeg (zoompan, xfade, sidechaincompress, H.264/AAC), `mlx-audio` + Orpheus TTS `orpheus-3b-0.1-ft-4bit`, Ollama (`qwen2.5:7b` / `llama3.2:3b`) or Gemini text for script generation
- Venv also contains pymupdf/pymupdf4llm, numpy, onnxruntime, PyYAML

## Infrastructure
Single local machine: Apple Silicon M1 Pro, 16 GB unified memory, macOS. Stages run sequentially to fit memory. Cloud limited to Gemini text / a few keyframe images. Output target: 1920×1080 YouTube MP4.

## Package Manager
uv (`.venv` at repo root). No `pyproject.toml` or lockfile yet — dependencies were installed ad hoc. FFmpeg and Ollama come from the host (Homebrew / installer).
