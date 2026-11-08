# mlx-audio TTS and provider (Codex/Gemini) text

- ✓ Load the Orpheus model once per run and reuse it for every line.
- ✓ Use the 4-bit model on 16 GB. Generate line by line and free MLX cache between lines (unverified for mlx-audio specifically: check its docs).
- ✓ Text generation runs through a provider CLI or API, not a local model, so voice synthesis has the full memory.
- ✓ Cache audio by content hash (`sha256(text+voice+model)`) under `build/`, and skip synthesis when the file exists.
- ✓ Request JSON from the Gemini API (`responseMimeType`) and validate every banter through `EpisodeTools.check` before writing.
- ✓ Pin model names/revisions, temperature and seed in config so scripts can be reproduced.
- ✓ Wrap Gemini calls in timeouts and retries with exponential backoff.
- ✗ Don't pass raw LLM output to TTS or FFmpeg. Validate it first: length, allowed Orpheus tags only (`<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`), and kid-safe content.
- ✗ Never load Hugging Face checkpoints with `trust_remote_code=True` or from unknown pickles.
