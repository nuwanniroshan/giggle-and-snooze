# mlx-audio TTS and Ollama/Gemini text

- ✓ Load the Orpheus model once per run and reuse it for every line.
- ✓ Use the 4-bit model on 16 GB. Generate line by line and free MLX cache between lines (unverified for mlx-audio specifically: check its docs).
- ✓ Never run Ollama and MLX TTS at the same time. Run stages one after another and unload Ollama models (`keep_alive=0`).
- ✓ Cache audio by content hash (`sha256(text+voice+model)`) under `build/`, and skip synthesis when the file exists.
- ✓ Request JSON from LLMs (Ollama `format="json"`, Gemini `response_mime_type="application/json"` with a schema), then validate before writing `episode_script.json`.
- ✓ Pin model names/revisions, temperature and seed in config so scripts can be reproduced.
- ✓ Wrap Gemini calls in timeouts and retries with exponential backoff.
- ✗ Don't pass raw LLM output to TTS or FFmpeg. Validate it first: length, allowed Orpheus tags only (`<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`), and kid-safe content.
- ✗ Never load Hugging Face checkpoints with `trust_remote_code=True` or from unknown pickles.
