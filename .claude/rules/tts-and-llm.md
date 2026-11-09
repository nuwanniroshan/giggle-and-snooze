# mlx-audio TTS and provider (Codex/Gemini) text

- ✓ Load the Orpheus model once per run and reuse it for every line.
- ✓ Use the 4-bit model with `max_tokens=3000` (the default 1200 cuts lines off at ~15 s). Generate line by line and call `mx.clear_cache()` between lines (verified 2026-11-09: ~6 GB peak).
- ✓ The bf16 Orpheus repo is needed only for its tokenizer files; download it with `allow_patterns` (whole repo is ~6.5 GB).
- ✓ Text generation runs through a provider CLI or API, not a local model, so voice synthesis has the full memory.
- ✓ Cache audio by content hash (`sha256(text+voice+model)`) under the book's `build/audio/clips/`, and skip synthesis when the file exists.
- ✓ Request JSON from the Gemini API (`responseMimeType`) and validate every banter through `EpisodeTools.check` before writing.
- ✓ Pin model names/revisions, temperature and seed in config so scripts can be reproduced.
- ✓ Wrap Gemini calls in timeouts and retries with exponential backoff.
- ✗ Don't pass raw LLM output to TTS or FFmpeg. Validate it first: length, allowed Orpheus tags only (`<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`), and kid-safe content.
- ✗ Never load Hugging Face checkpoints with `trust_remote_code=True` or from unknown pickles.
