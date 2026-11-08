# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A pipeline for turning an illustrated children's book (pilot: *The Snail Mail Express: A Whisperwood Hollow Story*) into publishable outputs. Two parts:

1. **Book production (exists):** Python scripts in `scripts/` that render print/digital editions from `book_data.json` + `assets/illustrations/`.
2. **Storytime video factory (planned, see `PRD.md`):** a `video_pipeline/` package that turns the same book into a 16:9 1080p YouTube read-aloud video. None of this code exists yet — `PRD.md` is the spec.

Not a git repository.

## Commands

Python 3.12 venv managed by `uv` at `.venv` (has Pillow, reportlab, pymupdf, numpy, onnxruntime, PyYAML). `pyproject.toml` declares deps, pytest and Ruff for `video_pipeline/`.

```bash
# Regenerate the composited illustrations (pages 3, 6, 7, 10, 11, 14, 15, 19 + map_whisperwood.png)
.venv/bin/python scripts/compose_missing_illustrations.py

# Build all book deliverables into build/
.venv/bin/python scripts/build_book.py

# Video pipeline (stage 1: episode script)
uv sync --inexact                      # install deps (keeps extra venv packages)
uv run pytest                          # unit tests (skips the real-provider test)
uv run pytest -m codex                 # real ChatGPT run via Codex (uses ChatGPT plan quota)
uv run python -m video_pipeline.generate_script [--book PATH] [--provider codex|gemini-api] [--seed N] [--force]
uv run python -m video_pipeline.mcp_server --book PATH   # the episode tools, to add to the ChatGPT/Codex app as an MCP server
```

`build_book.py` outputs (all in `build/`): front/back cover PNGs (300 DPI), KDP wrap cover PDF/PNG, 20-page digital PDF (8.5"×8.5"), complete reader PDF (covers + 20 pages), 24-page KDP print interior PDF (with bleed), EPUB 3, and `index.html` flipbook (references `../assets/illustrations/` by relative path, so open it from `build/`).

`ffmpeg` (Homebrew) is installed on the host for the planned video pipeline. The Codex CLI ships inside the ChatGPT app (`/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`, logged in with ChatGPT).

## Architecture

- **`book_data.json` is the single source of truth.** Keys: `metadata` (title, author, trim/bleed sizes, page counts, blurb, dedication…), `style_bible` (art-style prompt prefix, character descriptions for Clara and Barnaby), and `pages` (20 entries: `page_number`, `title`, `text`, `layout`, `illustration_file`, and an illustration `prompt`). The per-page `prompt` descriptions are what lets the video pipeline script Q&A without vision models.
- **`layout`** per page drives text placement in `render_story_page` (`build_book.py`): `image_top_text_bottom` or `full_bleed_card_{top,bottom}_{left,right}`.
- **Illustrations:** `page_NN.png`, `cover_front.png`, `cover_back.png`, `map_whisperwood.png` in `assets/illustrations/`. Some pages are AI-generated originals; the ones listed above are produced by `compose_missing_illustrations.py`, which collages/color-grades crops from other illustrations and `assets/references/` — rerunning it overwrites those files.
- **Pipeline order:** `compose_missing_illustrations.py` → `build_book.py`. Inside `build_book.py`, covers are built first because the reader PDF and wrap cover consume `build/*_Cover.png`.
- **Hardcoded paths/book name:** both scripts set `ROOT = Path("/Users/swiveltech/Disk/Swivel/kiddo")`, and output filenames embed `The_Snail_Mail_Express`. Supporting another book means parameterizing these.
- **Fonts:** reportlab registers macOS Georgia from `/System/Library/Fonts/Supplemental`, falling back to Times.

## Planned video pipeline (from PRD.md)

Target: Apple Silicon M1 Pro, 16 GB — stages run sequentially to fit memory.

- Cast: Storyteller, Kid 1 (~8yo, `kid_older`), Kid 2 (~5yo, `kid_younger`). Kid Q&A every 2–3 pages (~7–8 per book).
- Stages / planned files:
  1. **Implemented:** `video_pipeline/` — deterministic core (`contract.py`, `episode.py`, `episode_tools.py`), model access (`llm.py` Gemini API + writing brief, `agents.py` Codex runner, `mcp_server.py` episode tools `get_episode_plan`/`validate_banter`/`save_episode_script`), CLI `generate_script.py`. Writes hand-editable `episodes/<slug>/episode_script.json` (intro, per-page `lines` with `speaker`/`role`/`text`, `ffmpeg_camera` zoom targets, outro; regenerate with `--force`). Default provider: Codex from the ChatGPT app, run headless with only the `kiddo` MCP tools (`KIDDO_CODEX_BIN` overrides the binary); agent run logs (raw JSONL + `.stderr.log`, gitignored) go to `episodes/<slug>/agent/`. Opt-in `--provider gemini-api` reads `GEMINI_API_KEY`. No fallback between providers. The seed alone decides the episode plan. Cast in `config/cast.json`, kid-safety terms in `config/disallowed_terms.txt`. Every generated script needs human review before voice synthesis.
  2. `video_pipeline/synthesize_voices.py` — local TTS via `mlx-audio` + Orpheus `orpheus-3b-0.1-ft-4bit`; lines may carry inline tags `<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`.
  3. `video_pipeline/render_episode.py` — Pillow + FFmpeg: `zoompan` Ken Burns, `xfade` page flips, `sidechaincompress` BGM ducking, page-turn SFX from `assets/audio_sfx/`, H.264/AAC 1920×1080.
  4. One-command CLI/MCP wrapper for the whole pipeline for future books (`mcp_server.py` currently exposes only the stage-1 episode tools).
- Act II (story pages) has no on-screen avatars or speech bubbles — visuals are the book pages only. Intro/outro use a single reading-nook keyframe each, animated by FFmpeg.
