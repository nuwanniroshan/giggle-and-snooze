---
scope: test-strategy
loaded-by: sdd-plan, sdd-execute, sdd-review
---

# Test Strategy

## Test Framework
None yet. Planned: pytest, run with `uv run pytest` — edit to match reality

## Test Levels
- Unit tests: pure logic — filtergraph/command builders, timing and offset math, episode-script and book JSON validation, path/slug naming
- Integration tests: short end-to-end renders on a tiny fixture book (1–2 pages, low resolution) checked with `ffprobe` (duration, streams, resolution)
- E2E tests: full-book render reviewed by a human — voice quality and kid-safety are release blockers (see foundation)

## Coverage Expectations
[Edit to match reality — e.g. all command/filtergraph builders and validators covered; heavy model stages covered by fixture integration runs]

## Mocking Policy
Don't call real TTS, Ollama or Gemini in unit tests — stub at the stage boundary. Real FFmpeg is allowed in integration tests on small fixtures.
