# Python project conventions

- ✓ Use `uv` for everything: `uv add`, `uv run python ...`. Add a `pyproject.toml` (`requires-python = ">=3.12"`) and commit `uv.lock`.
- [observed] Book-production scripts live in `scripts/`; the planned video pipeline goes in `video_pipeline/` (per PRD.md). Note: web research recommends a `src/kiddo/` package layout — confirm this matches your intent.
- ✓ Naming: modules and functions in `snake_case`, classes in `PascalCase`, constants in `UPPER_SNAKE`. Name functions verb-first (`render_page`, `build_filtergraph`).
- ✓ Lint and format with Ruff only, configured in `[tool.ruff]` (`target-version = "py312"`, `line-length = 100`, lint `select = ["E","F","I","UP","B","SIM","PTH"]`).
- ✓ Use `pathlib.Path` for every path, and derive the project root from `Path(__file__)` or a CLI arg, never a hardcoded absolute path (today both scripts hardcode `ROOT`).
- ✓ Add type hints on public functions. Use `@dataclass` or `TypedDict` for structured records instead of bare dicts.
- ✓ Write pytest tests for the pure logic: filtergraph strings, timing math, JSON validation. Don't run real FFmpeg or TTS in unit tests.
- ✗ No secrets in code. Read API keys such as `GEMINI_API_KEY` from the environment.
