# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A pipeline for turning an illustrated children's book (pilot: *The Snail Mail Express: A Whisperwood Hollow Story*) into publishable outputs. Two parts:

1. **Book production (exists):** Python scripts in `scripts/` that render print/digital editions from `books/<slug>/book.json` + `books/<slug>/illustrations/`.
2. **Storytime video factory (in progress, see `PRD.md`):** a `video_pipeline/` package that turns the same book into a 16:9 1080p YouTube read-aloud video. Stages 1–3 (episode script, voices, render) and a local web studio that runs them exist. Specs live in `docs/specs/`.

Git repository; feature work happens on `feat/NNN-*` branches.

## Commands

Python 3.12 venv managed by `uv` at `.venv` (has Pillow, reportlab, pymupdf, numpy, onnxruntime, PyYAML). `pyproject.toml` declares deps, pytest and Ruff for `video_pipeline/`.

```bash
# Regenerate the composited illustrations (pages 3, 6, 7, 10, 11, 14, 15, 19 + map_whisperwood.png)
.venv/bin/python scripts/compose_missing_illustrations.py --book the-snail-mail-express

# Build all book deliverables into books/<slug>/build/
.venv/bin/python scripts/build_book.py --book the-snail-mail-express

# Video pipeline (stage 1: episode script)
uv sync --inexact                      # install deps (keeps extra venv packages)
uv run pytest                          # unit tests (skips the real-provider test)
uv run pytest -m codex                 # real ChatGPT run via Codex (uses ChatGPT plan quota)
uv run python -m video_pipeline.generate_script --book <slug> [--provider codex|gemini-api] [--seed N] [--force]
uv run python -m video_pipeline.mcp_server --book <slug>   # the episode tools, to add to the ChatGPT/Codex app as an MCP server

# Video pipeline (stage 2: voices)
uv run python -m video_pipeline.synthesize_voices download        # one-time: fetch the pinned voice model
uv run python -m video_pipeline.synthesize_voices --book <slug> --reviewed [--force]   # stage 2: speak the reviewed script
uv run python -m video_pipeline.synthesize_voices --book <slug> approve   # after listening to preview.wav
uv run pytest -m "ffmpeg or tts"                                  # real FFmpeg pitch lift + real Orpheus

# Video pipeline (stage 3: render)
uv run python -m video_pipeline.render_episode --book <slug> [--sfx-dir DIR] [--force]    # render the approved episode to books/<slug>/build/video/<slug>.mp4
uv run python -m video_pipeline.render_episode --book <slug> --preview-page N        # one page section with its voice (no approval needed) to build/video/preview/page_NN.mp4

# Studio (web interface)
# Add a book from an idea (spec 006): the studio starts these; or run them by hand
uv run python -m video_pipeline.write_story ideas --run <run-id>        # drafts/ideas/<run-id>/
uv run python -m video_pipeline.write_story candidate --run <run-id>
uv run python -m video_pipeline.write_story book --draft <draft-id>     # drafts/<draft-id>/ (git-ignored)

# Book blueprint (spec 011): the per-book creative direction
uv run python -m video_pipeline.write_story blueprint --draft <draft-id> [--then-book]   # designs drafts/<draft-id>/blueprint.json, then optionally writes the story
uv run python -m video_pipeline.write_story blueprint --book <slug>                      # regenerate a book's blueprint (old one kept as blueprint.json.bak)

# Animation plan + layers (spec 009)
uv run python -m video_pipeline.motion_plan --book <slug> [--page N] [--force]   # Codex writes books/<slug>/animation/page_NN.json
uv run python -m video_pipeline.cut_layers download                              # one-time: SAM 2.1 Core ML + LaMa ONNX to ~/.cache/kiddo/models
uv run python -m video_pipeline.cut_layers --book <slug> [--page N] [--force]    # build/layers/page_NN/ (layers, background.png, layers.json)
uv run pytest -m models                                                          # real segmentation/fill models
uv run python -m video_pipeline.illustrate --book <slug> [--page N [--cut] | --cover front|back]   # repaint one picture (old kept as .bak), spec 019
uv run python -m video_pipeline.autopilot --book <slug>    # spec 021: paint → cut → script → auto review → voices → auto approve → render → editions (marks recorded "by": "autopilot")
uv run python -m video_pipeline.studio [--port 8765]   # local web UI at http://127.0.0.1:8765 (this Mac only)
```

`build_book.py` outputs (all in `books/<slug>/build/`; N = story pages, P = KDP interior pages): front/back cover PNGs (300 DPI), KDP wrap cover PDF/PNG (spine width from P), `<Title>_<N>Page_Digital_Edition.pdf` (8.5"×8.5"), complete reader PDF (covers + N pages), `<Title>_KDP_<P>Page_Print_Interior.pdf` (with bleed; P = max(24, front + N + back) rounded up to even, padded with "My drawing" pages; map page only when the book has `illustrations/map_*.png`), EPUB 3, and `index.html` flipbook (references `../illustrations/` by relative path, so open it from `build/`). Edition PDFs with other page counts are removed on rebuild. Book-specific front/back matter (bookplate label, map legend, activity page, back-cover headline, genre) comes from the book's optional `print_extras` object (the pilot's lives there), over the generic templates in `config/book_extras.json` (spec 020).

`ffmpeg` (Homebrew, no rubberband) is installed on the host for the video pipeline. The Codex CLI ships inside the ChatGPT app (`/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`, logged in with ChatGPT).

## Architecture

- **`books/<slug>/book.json` is the source of truth for each book.** Keys: `metadata` (title, author, trim/bleed sizes, page counts, blurb, dedication…), `style_bible` (art-style prompt prefix, character descriptions for Clara and Barnaby), and `pages` (20 entries: `page_number`, `title`, `text`, `layout`, `illustration_file`, and an illustration `prompt`). The per-page `prompt` descriptions are what lets the video pipeline script Q&A without vision models.
- **`layout`** per page drives text placement in `render_story_page` (`build_book.py`): `image_top_text_bottom` or `full_bleed_card_{top,bottom}_{left,right}`.
- **Illustrations:** `page_NN.png`, `cover_front.png`, `cover_back.png`, `map_whisperwood.png` in `books/<slug>/illustrations/`. Some pages are AI-generated originals; the ones listed above are produced by `compose_missing_illustrations.py`, which collages/color-grades crops from other illustrations and `assets/references/` — rerunning it overwrites those files.
- **Pipeline order:** `compose_missing_illustrations.py` → `build_book.py`. Inside `build_book.py`, covers are built first because the reader PDF and wrap cover consume `books/<slug>/build/*_Cover.png`.
- **Book folders (spec 005):** each book is `books/<slug>/` with `book.json`, `illustrations/`, `references/`, `nooks/`, `episode/` (script and review marks, committed) and `build/` (all generated output, git-ignored). Every book command takes `--book <slug>` (and `--root` for tests); shared settings stay at the project root (`config/`, `assets/audio_sfx/`, `assets/nook/`). Output filenames come from the book's title.
- **Fonts:** reportlab registers macOS Georgia from `/System/Library/Fonts/Supplemental`, falling back to Times.

## Video pipeline (from PRD.md)

Target: Apple Silicon M1 Pro, 16 GB — stages run sequentially to fit memory.

- Cast: Storyteller, Kid 1 (~8yo, `kid_older`), Kid 2 (~5yo, `kid_younger`). Kid Q&A every 2–3 pages (~7–8 per book).
- Stages / planned files:
  1. **Implemented:** `video_pipeline/` — deterministic core (`contract.py`, `episode.py`, `episode_tools.py`), model access (`llm.py` Gemini API + writing brief, `agents.py` Codex runner, `mcp_server.py` episode tools `get_episode_plan`/`validate_banter`/`save_episode_script`), CLI `generate_script.py`. Writes hand-editable `books/<slug>/episode/episode_script.json` (intro, per-page `lines` with `speaker`/`role`/`text`, `ffmpeg_camera` zoom targets, outro; regenerate with `--force`). Default provider: Codex from the ChatGPT app, run headless with only the `kiddo` MCP tools (`KIDDO_CODEX_BIN` overrides the binary); agent run logs (raw JSONL + `.stderr.log`, gitignored) go to `books/<slug>/build/agent/`. Opt-in `--provider gemini-api` reads `GEMINI_API_KEY`. No fallback between providers. The seed alone decides the episode plan. Cast in `config/cast.json`, kid-safety terms in `config/disallowed_terms.txt`. Every generated script needs human review before voice synthesis.
  2. **Implemented:** `video_pipeline/audio_checks.py` (numpy measurements, trim, stitch, pauses), `tts.py` (Orpheus via `mlx-audio` 0.6.4, pinned, offline with `HF_HUB_OFFLINE=1`; FFmpeg `asetrate`/`aresample`/`atempo` pitch lift because Homebrew FFmpeg lacks rubberband), CLI `synthesize_voices.py`. Voices per role in `config/cast.json` (tara / zac +2 st / zoe +3 st); model, pitch, pauses and checks in `config/voices.json`; optional `metadata.pronunciations` in book data (applied to spoken text only). Lines may carry inline tags `<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`. Outputs in `books/<slug>/build/audio/`: `clips/` (hash-keyed, reused), `tracks/` (intro, `page-PP`, outro WAV + timing JSON), `preview.wav`, `review.md`, `status.json` ("pending review" → "approved" via `approve`). Refuses to run without `--reviewed`.
  3. **Implemented:** `video_pipeline/timeline.py` (pure: sections, frame-exact durations, caption cards, camera math + matching zoompan expressions, cache keys), `frames.py` (Pillow canvas: whole illustration centred over a blurred darkened copy; caption PNGs; generated page-turn sound; numpy master audio mix), `render_episode.py` (FFmpeg section/transition clips, concat `-c copy`, AAC mux; approval gate via `synthesize_voices.approval_problems`; per-section caching; CLI). Config in `config/render.json`. Optional inputs: per-book `episodes/<slug>/intro_nook.png`/`outro_nook.png`, shared `assets/nook/…`, else covers; `assets/audio_sfx/page_turn.wav`, else generated (cached at `build/sfx/`). Outputs in `books/<slug>/build/video/`: `sections/`, `transitions/`, `frames/`, `audio/master.wav`, `<slug>.mp4` + `<slug>.json` record. On-screen captions, no subtitle file; background music not implemented yet (separate spec).
  4. **Implemented: Episode studio** (spec 004) — `video_pipeline/studio/`: `books.py` (book registry: every book is a folder `books/<slug>/` (spec 005); upload validation via `episode.book_problems`, which collects every book-data problem; `episodes/<slug>/reviewed.json` review mark; script line edits validated by `contract.validate_script`; stage status derived from pipeline files incl. `render_episode.render_check`), `jobs.py` (one stage at a time as a subprocess of the existing CLI; records/logs in `build/studio/jobs/`; stop = process-group SIGTERM→SIGKILL; stale running → interrupted, its process group killed only if it is a `video_pipeline` command with the recorded start time; one studio per project via an `flock` on `build/studio/studio.lock`, a second start exits 1), `app.py` (Starlette + Jinja, server-rendered, dark "editing suite" design (spec 014: covers via a `thumb` media kind, per-book sidebar, colours only in `static/tokens.css`), JS polling; binds 127.0.0.1 only; cross-origin POSTs refused; media served from a whitelist with Range). `.claude/launch.json` has a "studio" config. `mcp_server.py` still exposes only the stage-1 episode tools.
- **Motion plan + layers (spec 009):** `motion_plan.py` (pydantic plan schema, limits from `config/animation.json`, stale check by picture sha256, `plan` CLI; Codex sees the picture via `-i` and saves through `plan_mcp.py`'s validating `save_motion_plan`). `illustrate` plans each page right after painting it (old plan kept as `.bak`). Plans live in `books/<slug>/animation/page_NN.json` (committed, hand-editable; the only source of z, depth, pivot, motions, effects). `cut_layers.py` cuts each planned layer with SAM 2.1 Core ML (`CPU_AND_GPU` only: the Neural Engine overflows float16 and gives garbage masks; an embedding self-check fails loudly), gives overlapping pixels to the higher-`z` layer, fills the background with LaMa ONNX on the onnxruntime CPU provider, and caches by picture + cut fields + settings + model ids. Models are stored as real files (Core ML can't load `.mlpackage` through HF-cache symlinks), dir `$KIDDO_MODELS_DIR` or `~/.cache/kiddo/models`. The studio has an Animation view per book and page (boxes, masks, background, Plan/Cut jobs). Spec 010 (animate + render) is next.
- **Animated pages (spec 010):** `video_pipeline/animate.py` turns a page's plan + cut layers (spec 009) into moving frames with NumPy + OpenCV (dirty rects, cv2 arithmetic; motions, depth parallax, 8 effects, seeded RNG; ease-in from the original picture so frame 0 equals the still). A page animates only with a current plan and a matching cut; otherwise it renders still and the log/studio give the reason and fix command. Animated sections pipe rgb24 frames (zoompan-exact camera applied in Python) into FFmpeg for captions and encoding; still sections, transitions and audio are unchanged, and still sections keep their cache keys. Settings: `animation` block in `config/render.json`; per-page `"animation": false | 0.25–2.0` in `episode_script.json` (ignored by the script hash). The studio Animation view shows animated/still per page and previews a page.
- **Book blueprint (spec 011):** `video_pipeline/blueprint.py` (pydantic model, safety and distinctness checks, `assign_ambience`, `style_text`, `blueprint_used.json` helpers). A blueprint is one creative direction per book: `mood` (per-role pace/energy/quirk), `art` (medium, 5-colour palette, line, lighting, composition, `style_prompt_prefix`, avoid list), `qa` (Q&A format, question types, intro/outro hooks, moments range, kid notes), `sound` (ambience/sfx names from the sound library, music direction) and `animation` (intensity, effects). Codex designs it (`write_story blueprint`, its own MCP mode in `story_mcp.py`) in up to 3 attempts, each refused with every failure at once, including being too close to another book's or draft's blueprint (thresholds, synonyms and ranges in `config/blueprint.json`). It lives at `drafts/<id>/blueprint.json` and is copied to `books/<slug>/blueprint.json` (committed, hand-editable) on Create book, which also sets `style_bible.style_prompt_prefix`. Consumers: `illustrate` (art text in front of every picture request, covers included), `plan_episode`/`build_brief` (Q&A format, moment range clamped to spec 001 bounds, kid notes) and the episode script (`ambience` lists assigned from the palette; ambience outside it is refused). `books/<slug>/build/blueprint_used.json` records the blueprint sha256 each of `art` and `script` was made from; editing the blueprint only marks pictures and the script stale in the studio (with the refresh command), never re-runs a stage and never touches the script hash or voice approval. Books without a blueprint behave exactly as before (golden values in `tests/fixtures/legacy_golden.json`). The studio book page has a Blueprint panel (swatches, fields, stale marks, Regenerate with confirmation); drafts show `designing` then `writing`, and a failed design can be written without a blueprint.
- **Titled covers (spec 022):** new books carry `metadata.cover_style = "titled"` (from `config/book_defaults.json`; older books lack it and keep spec 015 lettering). For them `illustrate` paints `cover_front.png` (4:3 video cover) and `cover_front_print.png` (square print/EPUB cover) with Codex, title, subtitle and the Giggle & Snooze logo painted in the book's blueprint art (logo + page 1 attached as references). Each cover is read back by a second Codex call (title/subtitle/logo, compared in code ignoring case, spaces, line breaks and quote style) and repainted up to 3 times; a failing cover is never saved. Settings in `config/covers.json`, pure helpers in `video_pipeline/covers.py`. `--cover front|print|back` repaints one. Render shows the video cover as is (no title plate); `build_book.py` uses the print cover for front cover, wrap, reader PDF, EPUB and flipbook with no lettering added; the studio lists and repaints "video cover" and "print cover".
- **Sound library (spec 007):** `assets/sound_library/` holds CC0 sounds (ambience, sfx; music later) and an `index.json` (name, file, kind, length, author, license). `uv run python -m video_pipeline.sound_library list|check` shows and verifies it. An episode script's intro, pages and outro may carry an optional `ambience` list of library names; the render plays them quietly under the voices (looped, faded across page changes, offline) and keeps video section caches when only ambience changes. Background music is still a separate future spec.
- Act II (story pages) has no on-screen avatars or speech bubbles — visuals are the book pages only. Intro/outro use a single reading-nook keyframe each, animated by FFmpeg.
