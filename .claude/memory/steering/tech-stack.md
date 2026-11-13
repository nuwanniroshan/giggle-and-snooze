---
scope: tech-stack
loaded-by: sdd-brainstorm, sdd-specify, sdd-plan, sdd-execute, sdd-review
---

# Tech Stack

## Languages
Python 3.12 (CPython via uv) — edit to match reality

## Frameworks
- Book production (spec 023): Pillow + numpy background rasters, reportlab vector text (PDF), hand-built fixed-layout EPUB 3 (zipfile), static HTML web reader; bundled OFL fonts in `assets/fonts/`
- Episode script generator (`video_pipeline/`, spec 001 v2.0.0): pydantic (banter/script models and validation), `mcp` (stdio MCP server exposing the episode tools). Default text provider: the Codex CLI bundled with the ChatGPT desktop app, run headless on the producer's ChatGPT login and limited to the episode tools. Opt-in: Gemini API (`--provider gemini-api`, `GEMINI_API_KEY`, `gemini-2.5-flash`). No local text model.
- Voice synthesis (`video_pipeline/`, spec 002): `mlx-audio` 0.6.4 with Orpheus 3B 4-bit (`mlx-community/orpheus-3b-0.1-ft-4bit`; pinned revisions of it, SNAC and the bf16 tokenizer files in `config/voices.json`), run offline; `soundfile` for WAV I/O; numpy audio checks. Kid voices are pitch-lifted with FFmpeg `asetrate`/`aresample`/`atempo` (Homebrew FFmpeg has no rubberband).
- Render (`video_pipeline/`, spec 003): Pillow canvas (illustration over a blurred darkened copy) and caption PNGs; numpy master audio mix; FFmpeg `zoompan`, `xfade`, concat `-c copy`, H.264/AAC mux. No background music yet (separate spec).
- Studio (`video_pipeline/studio/`, spec 004): Starlette 1.10, uvicorn, Jinja2, python-multipart (declared; they came with `mcp`); server-rendered pages, no JS build. Dev: httpx for Starlette's `TestClient`.
- YouTube upload (spec 024): `google-auth-oauthlib` (desktop consent with PKCE on a loopback redirect) and `google-api-python-client` (YouTube Data API v3: resumable `videos.insert`, `thumbnails.set`, `channels.list`, `videos.list`). Default quota 10,000 units/day ≈ 6 uploads/day; unaudited API projects keep uploads Private.
- Planned later stages (PRD.md): background music with `sidechaincompress` ducking
- Page animation (spec 010): `opencv-python-headless` 4.x (warpAffine, remap, resize, cv2 arithmetic) with NumPy; on macOS its parallel backend is GCD, so `cv2.setNumThreads` has no useful effect.
- Layer cutting (spec 009): `coremltools` 8.x running `apple/coreml-sam2.1-small` (Core ML, `CPU_AND_GPU`) and `onnxruntime` running `Carve/LaMa-ONNX` `lama_fp32.onnx` (CPU provider); revisions pinned in `config/animation.json`, both Apache-2.0.
- Venv also contains pymupdf/pymupdf4llm, numpy, onnxruntime, PyYAML

## Infrastructure
Single local machine: Apple Silicon M1 Pro, 16 GB unified memory, macOS. Stages run sequentially to fit memory. Cloud limited to script text (ChatGPT via Codex by default, Gemini API opt-in) and a few keyframe images. Output target: 1920×1080 YouTube MP4.

## Package Manager
uv (`.venv` at repo root). `pyproject.toml` declares the dependencies (Pillow, reportlab, pydantic, mcp, mlx-audio, soundfile; dev: pytest, Ruff) with `uv.lock`; install with `uv sync --inexact` (keeps the extra venv packages). FFmpeg comes from Homebrew; Codex comes with the ChatGPT desktop app.
