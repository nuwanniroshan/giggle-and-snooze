# Implementation Plan: Voice Synthesis

**Spec:** docs/specs/002-voice-synthesis/spec.md (v1.0.0, Approved)
**Decisions:** none (no ADRs; tech choices traced inline)
**Created:** 2026-11-09

> Data model: [data-model.md](data-model.md). Branch `feat/002-voice-synthesis` already exists, with the spec committed (e042e7c).

## Context

Spec 002 turns a reviewed `episodes/<slug>/episode_script.json` into spoken audio, locally:
- a clip per line;
- stitched intro, page and outro tracks with timing data;
- automatic quality checks with retries;
- a listening-review approval gate;
- per-book pronunciations;
- hash-based reuse.

**Timing test (2026-11-09, scratch venv, mlx-audio 0.6.4):**
- Speed and memory: Orpheus `mlx-community/orpheus-3b-0.1-ft-4bit` at revision `b30aed4e…` generates about 0.65 s per spoken word, about 24 minutes for the pilot's 2,151 words. Peak memory is about 6 GB.
- Cut-off: the default `max_tokens=1200` cuts lines at about 14.6 s, and `3000` fixes it.
- Loading: mlx-audio also loads the SNAC decoder (`mlx-community/snac_24khz` at `556af1cd…`) and the tokenizer files from `mlx-community/orpheus-3b-0.1-ft-bf16` at `3786aac5…`. All three are cached on this Mac now.
- Pitch: FFmpeg 8.1.2 has no `rubberband`, so the pitch lift uses `asetrate,aresample,atempo`.

**Key design choice:** keep three components.
- `audio_checks.py` holds the pure numpy measurements, trimming, stitching and pause rules, all fully unit-testable.
- `tts.py` is the local engine: the Orpheus wrapper, loaded once and offline, plus the FFmpeg pitch lift.
- `synthesize_voices.py` holds the line planning, cache keys, retries, tracks, review sheet, status, the approve and download commands, and the CLI.

Unit tests use a fake engine and an identity pitch shift. Real Orpheus and FFmpeg run only in tests marked `tts` and `ffmpeg`, per `.claude/rules/python-project.md`.

## Global Constraints

- Lines are identified as `intro-NN`, `page-PP-NN` and `outro-NN`. Tracks are named `intro`, `page-PP` and `outro`. Everything is written under `build/<slug>/audio/`.
- Clip identity is `sha256(spoken text, voice, semitones, model repo/revision, temperature, top_p, max_tokens, sample rate)`. It doesn't include the quality-check settings (FR-7).
- Pauses within a track, with narration-to-Q&A checked first:
  - after the last narration line, before the first Q&A line: 1.0 s;
  - same speaker: 0.4 s;
  - speaker change: 0.7 s.

  The gap between tracks in the preview is 1.0 s (AC-1.4, FR-4).
- Clip checks run on the final clip, after the pitch lift. The cut-off check uses the raw clip; every other check uses the trimmed clip:
  - duration at least 0.3 s;
  - peak below 0.999;
  - longest silence at most 1.5 s, at −40 dBFS over 20 ms frames;
  - 1.2–4.0 words per second (the timing test measured 2.0–2.7);
  - the last 0.2 s of the raw clip below −40 dBFS (not cut off).

  Edges are trimmed to at most 0.1 s. A failing line gets up to 3 attempts, with seed = `seed + attempt − 1` (AC-3.1 to AC-3.3).
- Audio format is mono, 24 kHz, 16-bit PCM WAV. Every WAV and JSON file is written atomically, to a temporary file first and then `os.replace`.
- The default voices are storyteller `tara`, kid_older `zac` and kid_younger `zoe`, with pitch lifts of 0, +2 and +3 semitones. Supported Orpheus voices are tara, leah, jess, leo, dan, mia, zac and zoe.
- The engine is created only when a clip actually needs synthesizing, so a run with no changes never loads the model (performance target under 30 s). Synthesis sets `HF_HUB_OFFLINE=1`. The only network use is the explicit `download` command.
- New dependencies are `mlx-audio==0.6.4` and `soundfile>=0.12`. Add them with `uv add --no-sync`, then `uv sync --inexact`, so the extra packages already in `.venv` (pymupdf, onnxruntime and others) stay.
- New pytest markers are `tts` (the real voice model) and `ffmpeg` (the real ffmpeg binary). Both are excluded by default, as `codex` already is.

## Goal

`uv run python -m video_pipeline.synthesize_voices --reviewed` speaks the pilot episode into `build/the-snail-mail-express/audio/` within 30 minutes and marks it "pending review". `… approve` then marks it approved once you have listened.

**Gates**
- **Simplicity:** three components, as above. ✓
- **Anti-abstraction:** the engine is a plain class with `sample_rate` and `synthesize(text, voice, seed)`. Tests pass a fake with the same two members, with no protocol hierarchy. ✓
- **Integration-first:** the CLI contract tests (Phase 4) drive real files end to end with only the engine and the pitch shift faked. The real-engine tests run in Phase 5. ✓

---

## Phase 1: Audio measurements, trimming, stitching and pauses

**Implements:** FR-3 (checks), FR-4 (stitching, pauses) | **Satisfies:** AC-1.4, AC-3.1, AC-3.2, AC-3.5 (measures)
**Files:** `video_pipeline/audio_checks.py`, `tests/test_audio_checks.py`
**Interfaces:** Produces:
- the constants `FRAME_S = 0.02` and `FULL_SCALE = 0.999`;
- the frozen dataclasses `CheckSettings(min_duration=0.3, max_silence=1.5, min_wps=1.2, max_wps=4.0, silence_db=-40.0, edge_silence=0.1, cutoff_window=0.2)` and `ClipMeasure(duration, peak, longest_silence, wps, cut_off)` with `to_dict()`;
- `frame_db(audio, sr)`, `trim_silence(audio, sr, silence_db, keep)`, `longest_silence(audio, sr, silence_db)`, `is_cut_off(audio, sr, silence_db, window)`, `measure_clip(raw, trimmed, sr, words, settings)`, `clip_problems(measure, settings) -> list[str]`, `stitch(clips, gaps, sr) -> (audio, spans)` and `pause_between(prev_speaker, prev_role, next_speaker, next_role, pauses) -> float`.

- [ ] Write `tests/test_audio_checks.py`:
```python
import numpy as np
import pytest

from video_pipeline.audio_checks import (
    CheckSettings,
    clip_problems,
    is_cut_off,
    longest_silence,
    measure_clip,
    pause_between,
    stitch,
    trim_silence,
)

SR = 24000
S = CheckSettings()
PAUSES = {"same_speaker": 0.4, "speaker_change": 0.7, "narration_to_qa": 1.0}


def tone(seconds, amp=0.3, freq=220.0):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.float32)


def test_trim_keeps_at_most_the_edge_silence():  # AC-3.2
    trimmed = trim_silence(np.concatenate([silence(1), tone(2), silence(1)]), SR, -40, 0.1)
    assert 2.0 <= len(trimmed) / SR <= 2.0 + 0.2 + 0.04


def test_trim_of_pure_silence_is_empty():
    assert len(trim_silence(silence(1), SR, -40, 0.1)) == 0


def test_longest_silence():
    audio = np.concatenate([tone(1), silence(1.6), tone(1)])
    assert longest_silence(audio, SR, -40) == pytest.approx(1.6, abs=0.04)


def test_cut_off_detects_speech_at_the_end():
    assert is_cut_off(tone(2), SR, -40, 0.2)
    assert not is_cut_off(np.concatenate([tone(2), silence(0.3)]), SR, -40, 0.2)


def clean_measure(words=10, body=tone(4)):
    raw = np.concatenate([silence(0.2), body, silence(0.5)])
    return measure_clip(raw, trim_silence(raw, SR, -40, 0.1), SR, words, S)


def test_clean_clip_has_no_problems():  # AC-3.1
    measure = clean_measure()
    assert clip_problems(measure, S) == []
    assert set(measure.to_dict()) == {"duration", "peak", "longest_silence", "wps", "cut_off"}


@pytest.mark.parametrize(
    ("raw", "words", "expected"),
    [
        (silence(1), 3, "too short or empty"),
        (np.concatenate([tone(4, amp=1.0), silence(0.3)]), 10, "clipping"),
        (np.concatenate([tone(2), silence(2), tone(2), silence(0.3)]), 10, "silence of"),
        (np.concatenate([tone(4), silence(0.3)]), 40, "speaking rate"),
        (np.concatenate([tone(4), silence(0.3)]), 1, "speaking rate"),
        (tone(4), 10, "cut off"),
    ],
)
def test_clip_problems(raw, words, expected):  # AC-3.1
    measure = measure_clip(raw, trim_silence(raw, SR, -40, 0.1), SR, words, S)
    assert any(expected in problem for problem in clip_problems(measure, S))


def test_stitch_places_clips_with_gaps():  # AC-1.3, AC-1.4
    audio, spans = stitch([tone(1), tone(2), tone(1)], [0.4, 1.0], SR)
    assert spans == [(0.0, 1.0), (1.4, 3.4), (4.4, 5.4)]
    assert len(audio) / SR == pytest.approx(5.4, abs=0.001)


def test_stitch_needs_one_gap_fewer_than_clips():
    with pytest.raises(ValueError):
        stitch([tone(1), tone(1)], [], SR)


@pytest.mark.parametrize(
    ("prev", "nxt", "expected"),
    [
        (("storyteller", "narration"), ("storyteller", "narration"), 0.4),
        (("storyteller", "narration"), ("kid_older", "question"), 1.0),
        (("storyteller", "narration"), ("storyteller", "answer"), 1.0),
        (("kid_older", "question"), ("kid_younger", "reaction"), 0.7),
        (("kid_older", "question"), ("kid_older", "question"), 0.4),
        (("kid_younger", None), ("storyteller", None), 0.7),
    ],
)
def test_pause_between(prev, nxt, expected):  # AC-1.4
    assert pause_between(*prev, *nxt, PAUSES) == expected
```
- [ ] Run `uv run pytest tests/test_audio_checks.py -q`. Expect FAIL (`ModuleNotFoundError: video_pipeline.audio_checks`).
- [ ] Create `video_pipeline/audio_checks.py`:
```python
"""Pure audio measurements, trimming, stitching and pause rules (spec 002 FR-3, FR-4). numpy only."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

FRAME_S = 0.02
FULL_SCALE = 0.999


@dataclass(frozen=True)
class CheckSettings:
    min_duration: float = 0.3
    max_silence: float = 1.5
    min_wps: float = 1.2
    max_wps: float = 4.0
    silence_db: float = -40.0
    edge_silence: float = 0.1
    cutoff_window: float = 0.2


@dataclass(frozen=True)
class ClipMeasure:
    duration: float
    peak: float
    longest_silence: float
    wps: float
    cut_off: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _db(rms: np.ndarray | float) -> np.ndarray | float:
    return 20 * np.log10(np.maximum(rms, 1e-6))


def frame_db(audio: np.ndarray, sr: int) -> np.ndarray:
    size = max(1, int(sr * FRAME_S))
    count = len(audio) // size
    if count == 0:
        return np.array([float(_db(np.sqrt(np.mean(audio.astype(np.float64) ** 2))))]) if len(
            audio
        ) else np.array([], dtype=np.float64)
    frames = audio[: count * size].astype(np.float64).reshape(count, size)
    return _db(np.sqrt(np.mean(frames**2, axis=1)))


def trim_silence(audio: np.ndarray, sr: int, silence_db: float, keep: float) -> np.ndarray:
    voiced = frame_db(audio, sr) > silence_db
    if not voiced.any():
        return audio[:0]
    size = max(1, int(sr * FRAME_S))
    first = int(np.argmax(voiced))
    last = len(voiced) - 1 - int(np.argmax(voiced[::-1]))
    pad = int(sr * keep)
    return audio[max(0, first * size - pad) : min(len(audio), (last + 1) * size + pad)]


def longest_silence(audio: np.ndarray, sr: int, silence_db: float) -> float:
    run = best = 0
    for voiced in frame_db(audio, sr) > silence_db:
        run = 0 if voiced else run + 1
        best = max(best, run)
    return round(best * FRAME_S, 3)


def is_cut_off(audio: np.ndarray, sr: int, silence_db: float, window: float) -> bool:
    tail = audio[-int(sr * window) :] if len(audio) else audio
    if len(tail) == 0:
        return False
    return bool(_db(np.sqrt(np.mean(tail.astype(np.float64) ** 2))) > silence_db)


def measure_clip(
    raw: np.ndarray, trimmed: np.ndarray, sr: int, words: int, settings: CheckSettings
) -> ClipMeasure:
    duration = len(trimmed) / sr
    return ClipMeasure(
        duration=round(duration, 3),
        peak=round(float(np.max(np.abs(trimmed))), 4) if len(trimmed) else 0.0,
        longest_silence=longest_silence(trimmed, sr, settings.silence_db),
        wps=round(words / duration, 3) if duration > 0 else 0.0,
        cut_off=is_cut_off(raw, sr, settings.silence_db, settings.cutoff_window),
    )


def clip_problems(measure: ClipMeasure, settings: CheckSettings) -> list[str]:
    problems: list[str] = []
    if measure.duration < settings.min_duration:
        problems.append(f"too short or empty ({measure.duration:.2f} s)")
    if measure.peak >= FULL_SCALE:
        problems.append(f"clipping (peak {measure.peak:.3f})")
    if measure.longest_silence > settings.max_silence:
        problems.append(
            f"silence of {measure.longest_silence:.2f} s (max {settings.max_silence} s)"
        )
    if measure.duration >= settings.min_duration and not (
        settings.min_wps <= measure.wps <= settings.max_wps
    ):
        problems.append(
            f"speaking rate {measure.wps:.2f} words/s outside {settings.min_wps}-{settings.max_wps}"
        )
    if measure.cut_off:
        problems.append("cut off: speech still under way at the end")
    return problems


def stitch(
    clips: list[np.ndarray], gaps: list[float], sr: int
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    if len(gaps) != max(0, len(clips) - 1):
        raise ValueError("stitch needs exactly one gap between each pair of clips")
    parts: list[np.ndarray] = []
    spans: list[tuple[float, float]] = []
    position = 0
    for index, clip in enumerate(clips):
        if index:
            gap = np.zeros(int(round(gaps[index - 1] * sr)), dtype=np.float32)
            parts.append(gap)
            position += len(gap)
        spans.append((round(position / sr, 3), round((position + len(clip)) / sr, 3)))
        parts.append(clip.astype(np.float32))
        position += len(clip)
    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    return audio, spans


def pause_between(
    prev_speaker: str,
    prev_role: str | None,
    next_speaker: str,
    next_role: str | None,
    pauses: dict[str, float],
) -> float:
    if prev_role == "narration" and next_role not in (None, "narration"):
        return pauses["narration_to_qa"]
    if prev_speaker == next_speaker:
        return pauses["same_speaker"]
    return pauses["speaker_change"]
```
- [ ] Run `uv run pytest -q` (all pass) and `uv run ruff check . && uv run ruff format --check .` (clean).
- [ ] Commit: `feat(voice): Phase 1: audio measurements, trimming, stitching and pauses`

---

## Phase 2: Local voice engine and pitch lift

**Implements:** FR-2 (model loaded once, voices, pitch lift, tags), NFR Security (pinned, offline, no remote code), Error Scenario (model missing) | **Satisfies:** AC-2.2 (via the Phase 5 `ffmpeg` test), AC-2.3 (tags passed through)
**Files:** `pyproject.toml`, `uv.lock`, `video_pipeline/tts.py`, `tests/test_tts.py`, `tests/test_tts_real.py`

> **Corrected during Phase 5 verification (commit 6def1e8):** `support_repos` entries became 3-tuples `(repo, revision, allow_patterns | None)`, read from an optional `"files"` list per support repo in `config/voices.json`. The bf16 repo is needed only for its tokenizer files, and the whole repo is ~6.5 GB. The snippets below show the corrected shape.

**Interfaces:** Produces:
- the frozen dataclass `ModelSettings(repo, revision, temperature, top_p, max_tokens, support_repos: tuple[tuple[str, str, tuple[str, ...] | None], ...] = ())`, each support repo being `(repo, revision, allow_patterns | None)`;
- `VoiceUnavailable`, `PitchShiftError` and `DOWNLOAD_HINT`;
- `pitch_ratio(semitones)`, `pitch_filter(semitones, sample_rate) -> str` and `pitch_shift(audio, sample_rate, semitones) -> np.ndarray`;
- `download_model(settings) -> None`;
- the class `OrpheusVoice(settings)`, with `.sample_rate` and `.synthesize(text, voice, seed) -> np.ndarray`.

- [ ] `uv add --no-sync "mlx-audio==0.6.4" "soundfile>=0.12"`, then `uv sync --inexact`. Check that the existing extra packages survived with `.venv/bin/python -c "import fitz, numpy, onnxruntime, yaml, PIL, reportlab, mlx_audio, soundfile"`.
- [ ] In `pyproject.toml` `[tool.pytest.ini_options]`:
  - set `markers` to `["codex: runs the real Codex (ChatGPT) provider; needs the ChatGPT app logged in", "tts: runs the real local voice model (slow; model must be downloaded)", "ffmpeg: runs the real ffmpeg binary"]`;
  - set `addopts = "-m 'not codex and not tts and not ffmpeg'"`.
- [ ] Write `tests/test_tts.py`:
```python
import os
import subprocess
import sys
import types

import numpy as np
import pytest

from video_pipeline import tts
from video_pipeline.tts import (
    DOWNLOAD_HINT,
    ModelSettings,
    OrpheusVoice,
    PitchShiftError,
    VoiceUnavailable,
    pitch_filter,
    pitch_ratio,
    pitch_shift,
)

SETTINGS = ModelSettings("org/model", "abc12345def", 0.6, 0.8, 3000)


def test_pitch_ratio():
    assert pitch_ratio(12) == pytest.approx(2.0)
    assert pitch_ratio(0) == 1.0


def test_pitch_filter_raises_pitch_and_keeps_tempo():
    assert pitch_filter(12, 24000) == "asetrate=48000,aresample=24000,atempo=0.500000"


def test_pitch_shift_zero_is_a_no_op():
    audio = np.ones(10, dtype=np.float32)
    assert pitch_shift(audio, 24000, 0) is audio


def test_pitch_shift_without_ffmpeg(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(tts.subprocess, "run", missing)
    with pytest.raises(PitchShiftError, match="ffmpeg not found"):
        pitch_shift(np.ones(10, dtype=np.float32), 24000, 2)


def test_pitch_shift_reports_ffmpeg_errors(monkeypatch):
    def failing(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"bad filter")

    monkeypatch.setattr(tts.subprocess, "run", failing)
    with pytest.raises(PitchShiftError, match="bad filter"):
        pitch_shift(np.ones(10, dtype=np.float32), 24000, 2)


def test_pitch_shift_builds_an_argument_list(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=np.ones(4, dtype="<f4").tobytes())

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    out = pitch_shift(np.ones(4, dtype=np.float32), 24000, 2)
    assert isinstance(seen["cmd"], list) and seen["cmd"][0] == "ffmpeg"
    assert pitch_filter(2, 24000) in seen["cmd"]
    assert out.dtype == np.float32 and len(out) == 4


class FakeResult:
    def __init__(self, audio):
        self.audio = audio


class FakeModel:
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield FakeResult(np.ones(3, dtype=np.float32))
        yield FakeResult(np.zeros(2, dtype=np.float32))


def install_fakes(monkeypatch, snapshot=None, model=None):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # restored after each test
    calls = {"seed": [], "clear": 0, "snapshot": []}
    core = types.SimpleNamespace(
        random=types.SimpleNamespace(seed=lambda s: calls["seed"].append(s)),
        clear_cache=lambda: calls.__setitem__("clear", calls["clear"] + 1),
    )
    mlx = types.ModuleType("mlx")
    mlx.core = core
    hub = types.ModuleType("huggingface_hub")

    def default_snapshot(**kwargs):
        calls["snapshot"].append(kwargs)
        return "/models/orpheus"

    hub.snapshot_download = snapshot or default_snapshot
    utils = types.ModuleType("mlx_audio.tts.utils")
    utils.load_model = lambda path: model or FakeModel()
    for name, module in {
        "mlx": mlx,
        "mlx.core": core,
        "huggingface_hub": hub,
        "mlx_audio": types.ModuleType("mlx_audio"),
        "mlx_audio.tts": types.ModuleType("mlx_audio.tts"),
        "mlx_audio.tts.utils": utils,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_engine_loads_pinned_model_offline(monkeypatch):
    calls = install_fakes(monkeypatch)
    engine = OrpheusVoice(SETTINGS)
    assert engine.sample_rate == 24000
    assert calls["snapshot"] == [
        {"repo_id": "org/model", "revision": "abc12345def", "local_files_only": True}
    ]
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_engine_synthesizes_with_seed_and_settings(monkeypatch):
    model = FakeModel()
    calls = install_fakes(monkeypatch, model=model)
    audio = OrpheusVoice(SETTINGS).synthesize("<giggle> Hi!", "zoe", 9)
    assert audio.dtype == np.float32 and len(audio) == 5
    assert calls["seed"] == [9] and calls["clear"] == 1
    assert model.calls == [
        {"text": "<giggle> Hi!", "voice": "zoe", "temperature": 0.6, "top_p": 0.8,
         "max_tokens": 3000, "verbose": False}
    ]


def test_engine_reports_missing_model(monkeypatch):  # Error scenario: model not downloaded
    def missing(**kwargs):
        raise FileNotFoundError("not cached")

    install_fakes(monkeypatch, snapshot=missing)
    with pytest.raises(VoiceUnavailable, match="not downloaded") as excinfo:
        OrpheusVoice(SETTINGS)
    assert DOWNLOAD_HINT in str(excinfo.value)


def test_engine_reports_missing_package(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", None)
    monkeypatch.setitem(sys.modules, "mlx_audio", None)
    with pytest.raises(VoiceUnavailable, match="uv sync"):
        OrpheusVoice(SETTINGS)
```
- [ ] Write `tests/test_tts_real.py`:
```python
"""Real FFmpeg and real Orpheus. Run with: uv run pytest -m "ffmpeg or tts" -v"""

import json
from pathlib import Path

import numpy as np
import pytest

from video_pipeline.audio_checks import CheckSettings, clip_problems, measure_clip, trim_silence
from video_pipeline.tts import ModelSettings, OrpheusVoice, pitch_shift

SR = 24000
CONFIG = Path(__file__).resolve().parent.parent / "config" / "voices.json"


def dominant_hz(audio):
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return np.fft.rfftfreq(len(audio), 1 / SR)[int(np.argmax(spectrum))]


@pytest.mark.ffmpeg
@pytest.mark.parametrize("semitones", [2, 3])
def test_pitch_lift_raises_pitch_and_keeps_duration(semitones):  # AC-2.2
    t = np.arange(SR * 2) / SR
    sine = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    lifted = pitch_shift(sine, SR, semitones)
    measured = 12 * np.log2(dominant_hz(lifted) / 220.0)
    assert measured == pytest.approx(semitones, abs=1.0)
    assert len(lifted) / len(sine) == pytest.approx(1.0, abs=0.05)


@pytest.mark.tts
def test_orpheus_speaks_a_line_that_passes_checks():
    model = json.loads(CONFIG.read_text())["model"]
    settings = ModelSettings(
        model["repo"], model["revision"], model["temperature"], model["top_p"],
        model["max_tokens"],
    )
    raw = OrpheusVoice(settings).synthesize("<giggle> Look at the tiny snail!", "zoe", 7)
    trimmed = trim_silence(raw, SR, -40, 0.1)
    measure = measure_clip(raw, trimmed, SR, 5, CheckSettings())
    assert clip_problems(measure, CheckSettings()) == []
```
- [ ] Run `uv run pytest tests/test_tts.py -q`. Expect FAIL (`ModuleNotFoundError: video_pipeline.tts`).
- [ ] Create `video_pipeline/tts.py`:
```python
"""Local voice engine (spec 002 FR-2): Orpheus via mlx-audio, plus an FFmpeg pitch lift.

The model is loaded once per run, from a pinned revision, offline (HF_HUB_OFFLINE=1). mlx-audio
loads safetensors weights and never runs code shipped with the model.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import numpy as np

DOWNLOAD_HINT = "run: uv run python -m video_pipeline.synthesize_voices download"


@dataclass(frozen=True)
class ModelSettings:
    repo: str
    revision: str
    temperature: float
    top_p: float
    max_tokens: int
    # (repo, revision, allow_patterns): None fetches the whole repo, a tuple only those files.
    support_repos: tuple[tuple[str, str, tuple[str, ...] | None], ...] = ()


class VoiceUnavailable(Exception):
    """The local voice engine or model is not installed or not downloaded."""


class PitchShiftError(Exception):
    """FFmpeg could not apply the pitch lift."""


def pitch_ratio(semitones: float) -> float:
    return 2 ** (semitones / 12)


def pitch_filter(semitones: float, sample_rate: int) -> str:
    """Raise pitch by `semitones` and restore the original duration (AC-2.2)."""
    ratio = pitch_ratio(semitones)
    return (
        f"asetrate={round(sample_rate * ratio)},aresample={sample_rate},atempo={1 / ratio:.6f}"
    )


def pitch_shift(audio: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
    if semitones == 0:
        return audio
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
        "-af", pitch_filter(semitones, sample_rate),
        "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "pipe:1",
    ]
    try:
        result = subprocess.run(
            command, input=audio.astype("<f4").tobytes(), capture_output=True, check=True
        )
    except FileNotFoundError:
        raise PitchShiftError("ffmpeg not found; install it with: brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        raise PitchShiftError(f"ffmpeg pitch shift failed: {detail}") from None
    return np.frombuffer(result.stdout, dtype="<f4").astype(np.float32)


def download_model(settings: ModelSettings) -> None:
    """The only network use (spec 002 Cost): fetch the pinned model and its support files."""
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=settings.repo, revision=settings.revision)
    for repo, revision, patterns in settings.support_repos:
        if patterns is None:
            snapshot_download(repo_id=repo, revision=revision)
        else:
            snapshot_download(repo_id=repo, revision=revision, allow_patterns=list(patterns))


class OrpheusVoice:
    def __init__(self, settings: ModelSettings) -> None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            import mlx.core as mx
            from huggingface_hub import snapshot_download
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise VoiceUnavailable(
                f"voice engine not installed ({exc.name}); run: uv sync --inexact"
            ) from None
        try:
            path = snapshot_download(
                repo_id=settings.repo, revision=settings.revision, local_files_only=True
            )
            self._model = load_model(path)
        except Exception as exc:  # noqa: BLE001 - any load failure means "not usable"
            raise VoiceUnavailable(
                f"voice model {settings.repo}@{settings.revision[:8]} is not downloaded "
                f"or cannot load ({type(exc).__name__}); {DOWNLOAD_HINT}"
            ) from None
        self._mx = mx
        self.settings = settings
        self.sample_rate = int(self._model.sample_rate)

    def synthesize(self, text: str, voice: str, seed: int) -> np.ndarray:
        self._mx.random.seed(seed)
        chunks = [
            np.asarray(result.audio, dtype=np.float32)
            for result in self._model.generate(
                text=text,
                voice=voice,
                temperature=self.settings.temperature,
                top_p=self.settings.top_p,
                max_tokens=self.settings.max_tokens,
                verbose=False,
            )
        ]
        self._mx.clear_cache()
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
```
  (Ruff's `BLE` rules aren't selected, so if Ruff reports an unused `noqa`, drop it.)
- [ ] Run `uv run pytest -q` (all pass; the real tests are deselected) and Ruff (clean).
- [ ] Commit: `feat(voice): Phase 2: local Orpheus engine and FFmpeg pitch lift`

---

## Phase 3: Line planning, cached clips with retries, and tracks

**Implements:** FR-1 (config, cast voices), FR-2 (voices per role, tags, pronunciation on spoken text), FR-3 (retries, recorded results), FR-4 (tracks, timing, preview), FR-5, FR-7 (identity, reuse, only changed tracks, force, atomic) | **Satisfies:** AC-1.1–1.4, AC-2.1, AC-2.4, AC-3.3–3.5, AC-5.1–5.4, AC-6.1–6.5
**Files:** `config/voices.json` (new), `config/cast.json`, `video_pipeline/synthesize_voices.py` (new, library part), `tests/test_synthesize_voices.py` (new), `tests/voice_helpers.py` (new)
**Interfaces:** Consumes Phase 1, `ModelSettings` and `pitch_shift` (Phase 2), and from spec 001: `ALLOWED_TAGS`, `TAG_RE`, `ConfigError` and `word_count` (`contract.py`), plus `write_atomic` (`episode_tools.py`). Produces:
- the frozen dataclasses `VoiceConfig`, `LineJob` and `AudioPaths`, and the dataclass `ClipOutcome`;
- `LineFailed(failures)`;
- `load_voice_config(path)`, `load_pronunciations(book, book_path)`, `spoken_text(text, prons)` and `clip_key(spoken, voice, semitones, cfg)`;
- `plan_jobs(script, cfg, prons) -> list[LineJob]`;
- `write_wav_atomic(path, audio, sr)`;
- `make_clip(job, engine, cfg, shift)`;
- `synthesize_clips(jobs, paths, engine_factory, cfg, force=False, shift=None, on_progress=None) -> list[ClipOutcome]`;
- `build_tracks(outcomes, paths, cfg) -> tuple[list[str], list[str]]` (all track names and the rebuilt ones);
- `build_preview(track_names, paths, cfg)`.

- [ ] Create `config/voices.json`:
```json
{
  "model": {
    "repo": "mlx-community/orpheus-3b-0.1-ft-4bit",
    "revision": "b30aed4ecfa03c0177a5419b51919d14a105f183",
    "support_repos": [
      {"repo": "mlx-community/snac_24khz", "revision": "556af1cd3b1c5f2d294f6aa9bb886245d7b716ac"},
      {
        "repo": "mlx-community/orpheus-3b-0.1-ft-bf16",
        "revision": "3786aac51797181edddeb0d89a8253deaee99aab",
        "files": ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
      }
    ],
    "temperature": 0.6,
    "top_p": 0.8,
    "max_tokens": 3000,
    "seed": 7
  },
  "supported_voices": ["tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"],
  "pitch_semitones": {"storyteller": 0, "kid_older": 2, "kid_younger": 3},
  "pauses": {"same_speaker": 0.4, "speaker_change": 0.7, "narration_to_qa": 1.0, "between_tracks": 1.0},
  "checks": {
    "min_duration": 0.3, "max_silence": 1.5, "min_wps": 1.2, "max_wps": 4.0,
    "silence_db": -40, "edge_silence": 0.1, "cutoff_window": 0.2, "max_attempts": 3
  },
  "sample_rate": 24000
}
```
- [ ] In `config/cast.json`, set `kid_older.voice` to `"zac"` and `kid_younger.voice` to `"zoe"`. The Storyteller stays `"tara"`.
- [ ] Create `tests/voice_helpers.py`:
```python
"""Fake engine and script builders for the voice-synthesis tests (no model, no ffmpeg)."""

import json
from pathlib import Path

import numpy as np

from tests.helpers import CAST_PATH, load_json, make_banter
from video_pipeline.contract import word_count
from video_pipeline.episode import assemble_script, plan_episode

SR = 24000
VOICES_PATH = Path(__file__).resolve().parent.parent / "config" / "voices.json"


def tone(seconds, amp=0.3, freq=220.0):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.float32)


class FakeEngine:
    """Speaks 2.5 words per second. `bad` maps a text fragment to how many attempts clip."""

    sample_rate = SR

    def __init__(self, bad=None):
        self.calls = []
        self.bad = dict(bad or {})

    def synthesize(self, text, voice, seed):
        self.calls.append((text, voice, seed))
        amp = 0.3
        for fragment, remaining in self.bad.items():
            if fragment in text and remaining > 0:
                self.bad[fragment] = remaining - 1
                amp = 1.0
        speech = tone(max(word_count(text), 1) / 2.5, amp=amp)
        return np.concatenate([silence(0.1), speech, silence(0.3)])


def identity_shift(audio, sample_rate, semitones):
    return audio


def make_script(book_path, seed=7):
    book = load_json(book_path)
    plan = plan_episode(book, seed)
    return assemble_script(book, load_json(CAST_PATH), plan, make_banter(book, plan))


def write_script(script, episodes_dir, slug):
    path = Path(episodes_dir) / slug / "episode_script.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
```
- [ ] Write `tests/test_synthesize_voices.py`:
```python
import dataclasses
import json

import numpy as np
import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, load_json
from tests.voice_helpers import VOICES_PATH, FakeEngine, identity_shift, make_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import ConfigError
from video_pipeline.synthesize_voices import (
    AudioPaths,
    LineFailed,
    build_preview,
    build_tracks,
    clip_key,
    load_pronunciations,
    load_voice_config,
    plan_jobs,
    spoken_text,
    synthesize_clips,
)

CFG = load_voice_config(VOICES_PATH)


@pytest.fixture
def script():
    return make_script(MINI_BOOK)


def run(script, tmp_path, engine=None, cfg=CFG, prons=None, force=False):
    engine = engine or FakeEngine()
    jobs = plan_jobs(script, cfg, prons or {})
    paths = AudioPaths(tmp_path / "audio")
    outcomes = synthesize_clips(jobs, paths, lambda: engine, cfg, force=force,
                                shift=identity_shift)
    names, rebuilt = build_tracks(outcomes, paths, cfg)
    return jobs, paths, outcomes, names, rebuilt, engine


def test_spoken_text_keeps_allowed_tags_and_drops_others():  # AC-2.3
    assert spoken_text("<giggle> Hi <whisper>there</whisper>!", {}) == "<giggle> Hi there !"


def test_spoken_text_applies_pronunciations_whole_word():  # AC-5.1
    prons = {"Tilly": "Til-ee"}
    assert spoken_text("TILLY waved at Tilly's friend, not Tillyard.", prons) == (
        "Til-ee waved at Til-ee's friend, not Tillyard."
    )


@pytest.mark.parametrize("bad", [["Tilly"], {"Tilly": ""}, {"": "x"}, {"Tilly": 3}])
def test_load_pronunciations_rejects_malformed(tmp_path, bad):
    with pytest.raises(ConfigError, match="pronunciations"):
        load_pronunciations({"metadata": {"pronunciations": bad}}, tmp_path / "book.json")


def test_load_pronunciations_is_optional(tmp_path):  # AC-5.3
    assert load_pronunciations({"metadata": {}}, tmp_path / "book.json") == {}


def test_plan_jobs_covers_every_line_with_role_voices(script):  # AC-1.1, AC-2.1, AC-2.4
    jobs = plan_jobs(script, CFG, {})
    expected = (
        len(script["intro"]["lines"])
        + sum(len(p["lines"]) for p in script["pages"])
        + len(script["outro"]["lines"])
    )
    assert len(jobs) == expected
    assert jobs[0].line_id == "intro-01" and jobs[-1].track == "outro"
    assert "page-01-01" in {j.line_id for j in jobs}
    voices = {j.speaker: j.voice for j in jobs}
    assert voices == {"storyteller": "tara", "kid_older": "zac", "kid_younger": "zoe"}
    assert {j.speaker: j.semitones for j in jobs} == {
        "storyteller": 0.0, "kid_older": 2.0, "kid_younger": 3.0
    }


def test_plan_jobs_rejects_unsupported_voice(script):
    script["cast"]["kid_younger"]["voice"] = "robot"
    with pytest.raises(ConfigError, match="kid_younger.*robot"):
        plan_jobs(script, CFG, {})


def test_clip_key_identity():  # FR-7
    base = clip_key("Hello there.", "tara", 0, CFG)
    assert base == clip_key("Hello there.", "tara", 0, CFG)
    assert base != clip_key("Hello there!", "tara", 0, CFG)
    assert base != clip_key("Hello there.", "zoe", 0, CFG)
    assert base != clip_key("Hello there.", "tara", 2, CFG)
    other_model = dataclasses.replace(
        CFG, model=dataclasses.replace(CFG.model, revision="different")
    )
    assert base != clip_key("Hello there.", "tara", 0, other_model)


def test_pronunciation_changes_only_affected_lines(script):  # AC-5.4
    before = plan_jobs(script, CFG, {})
    after = plan_jobs(script, CFG, {"Tilly": "Til-ee"})
    changed = {a.line_id for a, b in zip(before, after, strict=True) if a.key != b.key}
    assert changed == {j.line_id for j in before if "tilly" in j.text.lower()}
    assert changed


def test_writes_a_clip_per_line_and_every_track(script, tmp_path):  # AC-1.1, AC-1.2
    jobs, paths, _, names, rebuilt, _ = run(script, tmp_path)
    assert all(paths.clip(j.key).exists() for j in jobs)
    expected = ["intro", *[f"page-{n:02d}" for n in range(1, 9)], "outro"]
    assert names == expected and rebuilt == expected
    assert all(paths.track(n).exists() and paths.track_timing(n).exists() for n in names)


def test_track_timing_and_pauses(script, tmp_path):  # AC-1.3, AC-1.4
    jobs, paths, *_ = run(script, tmp_path)
    page = next(p for p in script["pages"] if p["has_qa"])
    timing = load_json(paths.track_timing(f"page-{page['page_number']:02d}"))
    lines = timing["lines"]
    assert [x["text"] for x in lines] == [x["text"] for x in page["lines"]]
    assert {"line_id", "speaker", "role", "start", "end"} <= set(lines[0])
    for prev, nxt in zip(lines, lines[1:]):
        gap = nxt["start"] - prev["end"]
        if prev["role"] == "narration" and nxt["role"] != "narration":
            assert gap == pytest.approx(1.0, abs=0.05)
        elif prev["speaker"] == nxt["speaker"]:
            assert gap == pytest.approx(0.4, abs=0.05)
        else:
            assert gap == pytest.approx(0.7, abs=0.05)
    audio, sr = sf.read(paths.track(f"page-{page['page_number']:02d}"))
    assert sr == 24000 and timing["duration"] == pytest.approx(len(audio) / sr, abs=0.01)


def test_failing_attempt_is_retried_with_a_new_seed(script, tmp_path):  # AC-3.3
    target = script["outro"]["lines"][-1]["text"]
    engine = FakeEngine(bad={target: 1})
    _, _, outcomes, *_ = run(script, tmp_path, engine=engine)
    seeds = [seed for text, _, seed in engine.calls if text == target]
    assert seeds == [7, 8]
    outcome = next(o for o in outcomes if o.job.text == target)
    assert outcome.attempts == 2 and outcome.problems == []


def test_line_failing_every_attempt_is_reported(script, tmp_path):  # AC-3.4
    target = script["intro"]["lines"][0]["text"]
    with pytest.raises(LineFailed) as excinfo:
        run(script, tmp_path, engine=FakeEngine(bad={target: 99}))
    [(job, problems)] = excinfo.value.failures
    assert job.line_id == "intro-01" and any("clipping" in p for p in problems)
    paths = AudioPaths(tmp_path / "audio")
    assert not paths.tracks.exists()
    assert paths.clips.exists() and any(paths.clips.glob("*.wav"))  # passing clips kept


def test_quality_results_are_recorded(script, tmp_path):  # AC-3.5
    jobs, paths, *_ = run(script, tmp_path)
    meta = load_json(paths.clip_meta(jobs[0].key))
    assert set(meta["measure"]) == {"duration", "peak", "longest_silence", "wps", "cut_off"}
    assert meta["attempts"] == 1


def test_rerun_reuses_every_clip_and_track(script, tmp_path):  # AC-6.1
    jobs, paths, _, names, _, _ = run(script, tmp_path)
    before = {n: paths.track(n).read_bytes() for n in names}

    def no_engine():
        raise AssertionError("no clip should need synthesis")

    outcomes = synthesize_clips(jobs, paths, no_engine, CFG, shift=identity_shift)
    assert not any(o.synthesized for o in outcomes)
    names, rebuilt = build_tracks(outcomes, paths, CFG)
    assert rebuilt == []
    assert {n: paths.track(n).read_bytes() for n in names} == before


def test_one_changed_line_rebuilds_one_track(script, tmp_path):  # AC-6.2
    run(script, tmp_path)
    page = next(p for p in script["pages"] if p["has_qa"])
    page["lines"][-1]["text"] = "What a lovely page!"
    engine = FakeEngine()
    *_, rebuilt, _ = run(script, tmp_path, engine=engine)
    assert [text for text, *_ in engine.calls] == ["What a lovely page!"]
    assert rebuilt == [f"page-{page['page_number']:02d}"]


def test_changed_pitch_resynthesizes_that_speaker(script, tmp_path):  # AC-6.3
    run(script, tmp_path)
    cfg = dataclasses.replace(CFG, pitch_semitones={**CFG.pitch_semitones, "kid_younger": 4.0})
    engine = FakeEngine()
    jobs, *_ = run(script, tmp_path, engine=engine, cfg=cfg)
    expected = {j.key for j in jobs if j.speaker == "kid_younger"}
    assert len(engine.calls) == len(expected) and expected


def test_force_resynthesizes_everything(script, tmp_path):  # AC-6.4
    jobs, *_ = run(script, tmp_path)
    engine = FakeEngine()
    run(script, tmp_path, engine=engine, force=True)
    assert len(engine.calls) == len({j.key for j in jobs})


def test_interrupted_clip_write_leaves_no_partial_file(script, tmp_path, monkeypatch):  # AC-6.5
    def boom(src, dst):
        raise OSError("interrupted")

    monkeypatch.setattr(sv.os, "replace", boom)
    with pytest.raises(OSError):
        run(script, tmp_path)
    clips = AudioPaths(tmp_path / "audio").clips
    assert list(clips.glob("*.wav")) == []


def test_preview_joins_tracks_with_gaps(script, tmp_path):  # FR-4
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    total = sum(load_json(paths.track_timing(n))["duration"] for n in names)
    audio, sr = sf.read(paths.preview)
    assert len(audio) / sr == pytest.approx(total + 1.0 * (len(names) - 1), abs=0.05)


def test_clips_are_mono_24k_pcm(script, tmp_path):  # FR-4 format
    jobs, paths, *_ = run(script, tmp_path)
    info = sf.info(paths.clip(jobs[0].key))
    assert (info.channels, info.samplerate, info.subtype) == (1, 24000, "PCM_16")


def test_pitch_shift_receives_the_role_lift(script, tmp_path):  # AC-2.2 wiring
    seen = []

    def recording_shift(audio, sample_rate, semitones):
        seen.append(semitones)
        return audio

    jobs = plan_jobs(script, CFG, {})
    synthesize_clips(jobs, AudioPaths(tmp_path / "a"), FakeEngine, CFG, shift=recording_shift)
    assert set(seen) == {0.0, 2.0, 3.0}


def test_engine_with_another_sample_rate_is_rejected(script, tmp_path):
    class Wrong(FakeEngine):
        sample_rate = 16000

    jobs = plan_jobs(script, CFG, {})
    with pytest.raises(ConfigError, match="sample rate"):
        synthesize_clips(jobs, AudioPaths(tmp_path / "a"), Wrong, CFG, shift=identity_shift)


def test_stitched_audio_matches_clips(script, tmp_path):
    jobs, paths, *_ = run(script, tmp_path)
    timing = load_json(paths.track_timing("intro"))
    track, sr = sf.read(paths.track("intro"), dtype="float32")
    first = timing["lines"][0]
    clip, _ = sf.read(paths.clip(jobs[0].key), dtype="float32")
    start = int(round(first["start"] * sr))
    assert np.allclose(track[start : start + len(clip)], clip, atol=1e-4)
```
- [ ] Run `uv run pytest tests/test_synthesize_voices.py -q`. Expect FAIL (`ImportError: cannot import name 'synthesize_voices'`).
- [ ] Create `video_pipeline/synthesize_voices.py` (library part; Phase 4 adds the review, status and CLI):
```python
"""Stage 2: speak a reviewed episode script (spec 002).

Run: uv run python -m video_pipeline.synthesize_voices --reviewed      # synthesize
     uv run python -m video_pipeline.synthesize_voices approve         # after listening
     uv run python -m video_pipeline.synthesize_voices download        # one-time model fetch
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from video_pipeline.audio_checks import (
    CheckSettings,
    ClipMeasure,
    clip_problems,
    measure_clip,
    pause_between,
    stitch,
    trim_silence,
)
from video_pipeline.contract import ALLOWED_TAGS, SPEAKERS, TAG_RE, ConfigError, word_count
from video_pipeline.episode_tools import write_atomic
from video_pipeline.tts import ModelSettings, pitch_shift

VOICE_CONFIG_KEYS = ("model", "supported_voices", "pitch_semitones", "pauses", "checks",
                     "sample_rate")


@dataclass(frozen=True)
class VoiceConfig:
    model: ModelSettings
    seed: int
    supported_voices: tuple[str, ...]
    pitch_semitones: dict[str, float]
    pauses: dict[str, float]
    checks: CheckSettings
    max_attempts: int
    sample_rate: int
    raw: dict = field(compare=False, repr=False)


def load_voice_config(path: Path) -> VoiceConfig:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"voice configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"voice configuration is not valid JSON: {path} ({exc})") from None
    missing = [key for key in VOICE_CONFIG_KEYS if key not in data]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}")
    model = data["model"]
    checks = dict(data["checks"])
    max_attempts = int(checks.pop("max_attempts", 3))
    return VoiceConfig(
        model=ModelSettings(
            repo=model["repo"],
            revision=model["revision"],
            temperature=float(model["temperature"]),
            top_p=float(model["top_p"]),
            max_tokens=int(model["max_tokens"]),
            support_repos=tuple(
                (
                    item["repo"],
                    item["revision"],
                    tuple(item["files"]) if item.get("files") is not None else None,
                )
                for item in model.get("support_repos", [])
            ),
        ),
        seed=int(model["seed"]),
        supported_voices=tuple(data["supported_voices"]),
        pitch_semitones={k: float(v) for k, v in data["pitch_semitones"].items()},
        pauses={k: float(v) for k, v in data["pauses"].items()},
        checks=CheckSettings(**{k: float(v) for k, v in checks.items()}),
        max_attempts=max_attempts,
        sample_rate=int(data["sample_rate"]),
        raw=data,
    )


def load_pronunciations(book: dict, book_path: Path) -> dict[str, str]:
    prons = book.get("metadata", {}).get("pronunciations", {})
    if not isinstance(prons, dict):
        raise ConfigError(
            f"{book_path}: metadata.pronunciations must map each word to a spoken respelling"
        )
    for word, spoken in prons.items():
        if not word.strip() or not isinstance(spoken, str) or not spoken.strip():
            raise ConfigError(
                f"{book_path}: metadata.pronunciations entry {word!r} must map a word to a "
                "non-empty spoken respelling"
            )
    return prons


def spoken_text(text: str, prons: dict[str, str]) -> str:
    """What the voice model hears: allowed tags kept, other markup dropped, respellings applied."""
    cleaned = TAG_RE.sub(
        lambda m: m.group(0) if m.group(0)[1:-1] in ALLOWED_TAGS else " ", text
    )
    for word, spoken in prons.items():
        cleaned = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", spoken, cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def clip_key(spoken: str, voice: str, semitones: float, cfg: VoiceConfig) -> str:
    identity = {
        "text": spoken,
        "voice": voice,
        "semitones": semitones,
        "repo": cfg.model.repo,
        "revision": cfg.model.revision,
        "temperature": cfg.model.temperature,
        "top_p": cfg.model.top_p,
        "max_tokens": cfg.model.max_tokens,
        "sample_rate": cfg.sample_rate,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class LineJob:
    line_id: str
    track: str
    speaker: str
    role: str | None
    text: str
    spoken: str
    words: int
    voice: str
    semitones: float
    key: str


def plan_jobs(script: dict, cfg: VoiceConfig, prons: dict[str, str]) -> list[LineJob]:
    cast = script["cast"]
    for role in SPEAKERS:
        voice = cast.get(role, {}).get("voice")
        if voice not in cfg.supported_voices:
            raise ConfigError(
                f"cast role {role!r}: voice {voice!r} is not supported by the voice model "
                f"(supported: {', '.join(cfg.supported_voices)})"
            )
    jobs: list[LineJob] = []

    def add(line_id: str, track: str, line: dict) -> None:
        speaker = line["speaker"]
        voice = cast[speaker]["voice"]
        semitones = cfg.pitch_semitones.get(speaker, 0.0)
        spoken = spoken_text(line["text"], prons)
        jobs.append(
            LineJob(line_id, track, speaker, line.get("role"), line["text"], spoken,
                    word_count(line["text"]), voice, semitones,
                    clip_key(spoken, voice, semitones, cfg))
        )

    for index, line in enumerate(script["intro"]["lines"], start=1):
        add(f"intro-{index:02d}", "intro", line)
    for page in script["pages"]:
        number = page["page_number"]
        for index, line in enumerate(page["lines"], start=1):
            add(f"page-{number:02d}-{index:02d}", f"page-{number:02d}", line)
    for index, line in enumerate(script["outro"]["lines"], start=1):
        add(f"outro-{index:02d}", "outro", line)
    return jobs


@dataclass(frozen=True)
class AudioPaths:
    root: Path

    @property
    def clips(self) -> Path:
        return self.root / "clips"

    @property
    def tracks(self) -> Path:
        return self.root / "tracks"

    @property
    def preview(self) -> Path:
        return self.root / "preview.wav"

    @property
    def review(self) -> Path:
        return self.root / "review.md"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    def clip(self, key: str) -> Path:
        return self.clips / f"{key}.wav"

    def clip_meta(self, key: str) -> Path:
        return self.clips / f"{key}.json"

    def track(self, name: str) -> Path:
        return self.tracks / f"{name}.wav"

    def track_timing(self, name: str) -> Path:
        return self.tracks / f"{name}.json"


def write_wav_atomic(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.wav")
    try:
        sf.write(tmp, audio, sample_rate, subtype="PCM_16")
        os.replace(tmp, path)  # noqa: PTH105 - tests patch synthesize_voices.os.replace
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class ClipOutcome:
    job: LineJob
    measure: ClipMeasure | None
    attempts: int
    synthesized: bool
    problems: list[str]


class LineFailed(Exception):
    def __init__(self, failures: list[tuple[LineJob, list[str]]]) -> None:
        super().__init__(f"{len(failures)} line(s) failed the quality checks")
        self.failures = failures


Shift = Callable[[np.ndarray, int, float], np.ndarray]


def make_clip(
    job: LineJob, engine, cfg: VoiceConfig, shift: Shift
) -> tuple[np.ndarray | None, ClipMeasure, int, list[str]]:
    """Synthesize, lift, trim and check; up to cfg.max_attempts with a new seed each (AC-3.3)."""
    measure: ClipMeasure | None = None
    problems: list[str] = []
    for attempt in range(1, cfg.max_attempts + 1):
        raw = shift(engine.synthesize(job.spoken, job.voice, cfg.seed + attempt - 1),
                    cfg.sample_rate, job.semitones)
        trimmed = trim_silence(raw, cfg.sample_rate, cfg.checks.silence_db,
                               cfg.checks.edge_silence)
        measure = measure_clip(raw, trimmed, cfg.sample_rate, job.words, cfg.checks)
        problems = clip_problems(measure, cfg.checks)
        if not problems:
            return trimmed, measure, attempt, []
    return None, measure, cfg.max_attempts, problems


def synthesize_clips(
    jobs: list[LineJob],
    paths: AudioPaths,
    engine_factory: Callable[[], object],
    cfg: VoiceConfig,
    force: bool = False,
    shift: Shift | None = None,
    on_progress: Callable[[ClipOutcome], None] | None = None,
) -> list[ClipOutcome]:
    shift = shift or pitch_shift
    engine = None
    done: dict[str, ClipOutcome] = {}
    outcomes: list[ClipOutcome] = []
    failures: list[tuple[LineJob, list[str]]] = []
    for job in jobs:
        if job.key in done:
            first = done[job.key]
            outcomes.append(ClipOutcome(job, first.measure, first.attempts, False, first.problems))
            continue
        meta_path = paths.clip_meta(job.key)
        if not force and paths.clip(job.key).exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            outcome = ClipOutcome(job, ClipMeasure(**meta["measure"]), meta["attempts"], False, [])
        else:
            if engine is None:
                engine = engine_factory()
                if engine.sample_rate != cfg.sample_rate:
                    raise ConfigError(
                        f"voice model sample rate {engine.sample_rate} does not match the "
                        f"configured sample rate {cfg.sample_rate}"
                    )
            audio, measure, attempts, problems = make_clip(job, engine, cfg, shift)
            if audio is None:
                failures.append((job, problems))
            else:
                write_wav_atomic(paths.clip(job.key), audio, cfg.sample_rate)
                write_atomic(meta_path, {
                    "line_id": job.line_id, "speaker": job.speaker, "voice": job.voice,
                    "semitones": job.semitones, "spoken": job.spoken,
                    "measure": measure.to_dict(), "attempts": attempts,
                })
            outcome = ClipOutcome(job, measure, attempts, True, problems)
        done[job.key] = outcome
        outcomes.append(outcome)
        if on_progress:
            on_progress(outcome)
    if failures:
        raise LineFailed(failures)
    return outcomes


def _track_groups(outcomes: list[ClipOutcome]) -> dict[str, list[ClipOutcome]]:
    groups: dict[str, list[ClipOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(outcome.job.track, []).append(outcome)
    return groups


def build_tracks(
    outcomes: list[ClipOutcome], paths: AudioPaths, cfg: VoiceConfig
) -> tuple[list[str], list[str]]:
    """Stitch each track; rebuild only tracks whose clips or pauses changed (AC-6.2)."""
    groups = _track_groups(outcomes)
    rebuilt: list[str] = []
    for name, items in groups.items():
        gaps = [
            pause_between(a.job.speaker, a.job.role, b.job.speaker, b.job.role, cfg.pauses)
            for a, b in zip(items, items[1:], strict=False)
        ]
        track_key = hashlib.sha256(
            json.dumps([[o.job.key for o in items], gaps]).encode()
        ).hexdigest()[:32]
        timing_path = paths.track_timing(name)
        if paths.track(name).exists() and timing_path.exists():
            if json.loads(timing_path.read_text(encoding="utf-8")).get("track_key") == track_key:
                continue
        clips = [sf.read(paths.clip(o.job.key), dtype="float32")[0] for o in items]
        audio, spans = stitch(clips, gaps, cfg.sample_rate)
        write_wav_atomic(paths.track(name), audio, cfg.sample_rate)
        write_atomic(timing_path, {
            "track": name,
            "track_key": track_key,
            "duration": round(len(audio) / cfg.sample_rate, 3),
            "lines": [
                {
                    "line_id": o.job.line_id, "speaker": o.job.speaker, "role": o.job.role,
                    "text": o.job.text, "start": start, "end": end,
                    "clip": f"clips/{o.job.key}.wav", "quality": o.measure.to_dict(),
                    "attempts": o.attempts,
                }
                for o, (start, end) in zip(items, spans, strict=True)
            ],
        })
        rebuilt.append(name)
    return list(groups), rebuilt


def build_preview(track_names: list[str], paths: AudioPaths, cfg: VoiceConfig) -> None:
    tracks = [sf.read(paths.track(name), dtype="float32")[0] for name in track_names]
    gaps = [cfg.pauses["between_tracks"]] * max(0, len(tracks) - 1)
    audio, _ = stitch(tracks, gaps, cfg.sample_rate)
    write_wav_atomic(paths.preview, audio, cfg.sample_rate)
```
- [ ] Run `uv run pytest -q` (all pass, including spec 001's tests with the new cast voices) and Ruff (clean).
- [ ] Commit: `feat(voice): Phase 3: line planning, cached clips with retries, and tracks`

---

## Phase 4: CLI, script gate, review sheet, approval and download

**Implements:** FR-1 (inputs, validation), FR-6 (status, approve), FR-4 (preview), Error Scenarios, NFR Reliability | **Satisfies:** AC-1.1, AC-1.5, AC-3.4 (CLI), AC-4.1–4.5, AC-5.1–5.3 (end to end), AC-6.1 (CLI), AC-7.1–7.3
**Files:** `video_pipeline/synthesize_voices.py` (append), `tests/test_synthesize_cli.py` (new)
**Interfaces:** Consumes Phase 3, `OrpheusVoice`, `download_model`, `VoiceUnavailable` and `PitchShiftError` (Phase 2), and from spec 001: `load_book`, `BookError`, `validate_script`, `load_disallowed_terms`, `slugify`, `ROOT` and `CONFIG_DIR`. Produces:
- `ApproveError`;
- `sha256_file(path)`, `settings_hash(cfg, script, prons)`, `describe(job)`, `write_review(paths, outcomes, track_names, title, status)`, `update_status(paths, script_sha, settings_sha, any_synthesized) -> dict` and `approve(paths, script_path) -> dict`;
- `create_engine(cfg)`;
- `main(argv=None) -> int`, with the commands `synthesize` (the default), `approve` and `download`, and the flags `--book --episodes-dir --build-dir --config-dir --reviewed --force`.

- [ ] Write `tests/test_synthesize_cli.py`:
```python
import errno
import json
import shutil

import pytest

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from tests.voice_helpers import FakeEngine, identity_shift, make_script, write_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.tts import VoiceUnavailable

SLUG = "the-lantern-bug-parade"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(sv, "create_engine", lambda cfg: engine)
    monkeypatch.setattr(sv, "pitch_shift", identity_shift)
    script = make_script(MINI_BOOK)
    script_path = write_script(script, tmp_path / "episodes", SLUG)
    return {"tmp": tmp_path, "engine": engine, "script": script, "script_path": script_path,
            "audio": tmp_path / "build" / SLUG / "audio"}


def run(env, *extra, book=MINI_BOOK, command=None):
    args = [*( [command] if command else [] ), "--book", str(book),
            "--episodes-dir", str(env["tmp"] / "episodes"),
            "--build-dir", str(env["tmp"] / "build"), *extra]
    return sv.main(args)


def test_synthesizes_the_book(env, capsys):  # AC-1.1, AC-1.2, AC-4.1, AC-4.2
    assert run(env, "--reviewed") == 0
    audio = env["audio"]
    tracks = sorted(p.stem for p in (audio / "tracks").glob("*.wav"))
    assert tracks == sorted(["intro", "outro", *[f"page-{n:02d}" for n in range(1, 9)]])
    assert (audio / "preview.wav").exists()
    review = (audio / "review.md").read_text()
    for page in env["script"]["pages"]:
        assert f"page-{page['page_number']:02d}-01" in review
    assert "intro-01" in review and "outro-01" in review
    status = load_json(audio / "status.json")
    assert status["status"] == "pending review" and status["approved_at"] is None
    out = capsys.readouterr().out
    assert "listen" in out.lower() and "approve" in out


def test_other_book_needs_no_changes(env, tmp_path):  # AC-1.5
    write_script(make_script(PILOT_BOOK), tmp_path / "episodes", "the-snail-mail-express")
    assert run(env, "--reviewed", book=PILOT_BOOK) == 0
    assert (tmp_path / "build" / "the-snail-mail-express" / "audio" / "preview.wav").exists()


def test_missing_script(env, capsys):  # AC-7.3
    env["script_path"].unlink()
    assert run(env, "--reviewed") == 1
    assert str(env["script_path"]) in capsys.readouterr().err


def test_invalid_script_is_refused(env, capsys):  # AC-7.1
    env["script_path"].write_text("{}", encoding="utf-8")
    assert run(env, "--reviewed") == 1
    assert "missing top-level key" in capsys.readouterr().err
    assert env["engine"].calls == []


def test_unreviewed_script_is_refused(env, capsys):  # AC-7.2
    assert run(env) == 1
    err = capsys.readouterr().err
    assert "reviewed by a person" in err and "--reviewed" in err
    assert env["engine"].calls == [] and not env["audio"].exists()


def test_unsupported_voice_is_named(env, capsys):
    env["script"]["cast"]["kid_older"]["voice"] = "robot"
    write_script(env["script"], env["tmp"] / "episodes", SLUG)
    assert run(env, "--reviewed") == 1
    assert "kid_older" in capsys.readouterr().err


def test_missing_model_is_reported(env, monkeypatch, capsys):  # Error scenario
    def unavailable(cfg):
        raise VoiceUnavailable("voice model x is not downloaded; run: ... download")

    monkeypatch.setattr(sv, "create_engine", unavailable)
    assert run(env, "--reviewed") == 1
    assert "not downloaded" in capsys.readouterr().err
    assert not (env["audio"] / "status.json").exists()


def test_failing_line_names_line_and_check(env, monkeypatch, capsys):  # AC-3.4
    page = next(p for p in env["script"]["pages"] if p["has_qa"])
    target = page["lines"][-1]["text"]
    engine = FakeEngine(bad={target: 99})
    monkeypatch.setattr(sv, "create_engine", lambda cfg: engine)
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert f"page {page['page_number']} line {len(page['lines'])} (storyteller)" in err
    assert "clipping" in err
    audio = env["audio"]
    assert not (audio / "tracks").exists() and not (audio / "review.md").exists()
    assert not (audio / "status.json").exists()


def test_rerun_reuses_everything(env, monkeypatch, capsys):  # AC-6.1
    assert run(env, "--reviewed") == 0
    capsys.readouterr()

    def no_engine(cfg):
        raise AssertionError("nothing should be synthesized")

    monkeypatch.setattr(sv, "create_engine", no_engine)
    assert run(env, "--reviewed") == 0
    assert "synthesized 0" in capsys.readouterr().out


def test_force_resynthesizes(env):  # AC-6.4
    assert run(env, "--reviewed") == 0
    first = len(env["engine"].calls)
    assert run(env, "--reviewed", "--force") == 0
    assert len(env["engine"].calls) == 2 * first


def test_approval_flow(env, capsys):  # AC-4.3, AC-4.4, AC-4.5
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0
    status = load_json(env["audio"] / "status.json")
    assert status["status"] == "approved" and status["approved_at"]

    assert run(env, "--reviewed") == 0  # nothing changed: approval kept
    assert load_json(env["audio"] / "status.json")["status"] == "approved"

    page = next(p for p in env["script"]["pages"] if p["has_qa"])
    page["lines"][-1]["text"] = "What a lovely page!"
    write_script(env["script"], env["tmp"] / "episodes", SLUG)
    capsys.readouterr()
    assert run(env, command="approve") == 1  # AC-4.5
    assert "re-run synthesis" in capsys.readouterr().err

    assert run(env, "--reviewed") == 0  # AC-4.4
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


def test_approve_without_audio(env, capsys):
    assert run(env, command="approve") == 1
    assert "run synthesis first" in capsys.readouterr().err


def test_pronunciations_reach_the_voice_only(env, tmp_path):  # AC-5.1, AC-5.2
    book = load_json(MINI_BOOK)
    book["metadata"]["pronunciations"] = {"Tilly": "Til-ee"}
    book_path = tmp_path / "book.json"
    book_path.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed", book=book_path) == 0
    spoken = " ".join(text for text, _, _ in env["engine"].calls)
    assert "Til-ee" in spoken and "Tilly" not in spoken
    review = (env["audio"] / "review.md").read_text()
    timing = (env["audio"] / "tracks" / "page-01.json").read_text()
    assert "Tilly" in review and "Til-ee" not in review
    assert "Tilly" in timing and "Til-ee" not in timing


def test_malformed_pronunciations_name_the_book(env, tmp_path, capsys):
    book = load_json(MINI_BOOK)
    book["metadata"]["pronunciations"] = {"Tilly": ""}
    book_path = tmp_path / "book.json"
    book_path.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed", book=book_path) == 1
    err = capsys.readouterr().err
    assert str(book_path) in err and "Tilly" in err


def test_disk_full_names_the_folder(env, monkeypatch, capsys):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(sv, "write_wav_atomic", full)
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert "disk space" in err and str(env["audio"]) in err


def test_download_command(env, monkeypatch, capsys):
    fetched = []
    monkeypatch.setattr(sv, "download_model", lambda settings: fetched.append(settings.repo))
    assert run(env, command="download") == 0
    assert fetched == ["mlx-community/orpheus-3b-0.1-ft-4bit"]
    assert "ready" in capsys.readouterr().out


def test_defaults_resolve_from_the_project_root():
    args = sv.parse_args([])
    assert args.book == sv.ROOT / "book_data.json"
    assert args.build_dir == sv.ROOT / "build"
    assert args.command == "synthesize"
```
- [ ] Run `uv run pytest tests/test_synthesize_cli.py -q`. Expect FAIL (`AttributeError: module … has no attribute 'main'`, or `create_engine`).
- [ ] Append to `video_pipeline/synthesize_voices.py`. Add `argparse`, `errno`, `sys` and `from datetime import UTC, datetime` to the imports, plus:
```python
from video_pipeline.contract import load_disallowed_terms, slugify, validate_script
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import CONFIG_DIR, ROOT
from video_pipeline.tts import OrpheusVoice, PitchShiftError, VoiceUnavailable, download_model
```
  and append:
```python
REVIEW_NOTICE = (
    "Listening review required: play {preview} (each track is in {tracks}). Listen for "
    "robotic or garbled speech, mispronounced names and emotion tags read aloud, then approve "
    "with: uv run python -m video_pipeline.synthesize_voices approve --book {book}"
)


class ApproveError(Exception):
    """The audio cannot be approved yet."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def settings_hash(cfg: VoiceConfig, script: dict, prons: dict[str, str]) -> str:
    payload = {"voices": cfg.raw, "cast": script["cast"], "pronunciations": prons}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def describe(job: LineJob) -> str:
    """Human name of a line (AC-3.4): 'page 3 line 2 (kid_older)'."""
    parts = job.line_id.split("-")
    if parts[0] == "page":
        return f"page {int(parts[1])} line {int(parts[2])} ({job.speaker})"
    return f"{parts[0]} line {int(parts[1])} ({job.speaker})"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_review(
    paths: AudioPaths, outcomes: list[ClipOutcome], track_names: list[str], title: str,
    status: str,
) -> None:
    groups = _track_groups(outcomes)
    lines = [
        f"# Listening review: {title}",
        "",
        f"Status: **{status}**. Full episode: `preview.wav`. Tracks: `tracks/`.",
        "",
    ]
    for name in track_names:
        duration = json.loads(paths.track_timing(name).read_text(encoding="utf-8"))["duration"]
        lines += [
            f"## {name} ({duration:.1f} s)",
            "",
            "| Line | Speaker | Duration (s) | Peak | Longest silence (s) | Words/s | Attempts | Text |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for o in groups[name]:
            m = o.measure
            lines.append(
                f"| {o.job.line_id} | {o.job.speaker} | {m.duration:.2f} | {m.peak:.2f} | "
                f"{m.longest_silence:.2f} | {m.wps:.2f} | {o.attempts} | {_cell(o.job.text)} |"
            )
        lines.append("")
    paths.review.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.review.with_name(paths.review.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, paths.review)  # noqa: PTH105


def update_status(
    paths: AudioPaths, script_sha: str, settings_sha: str, any_synthesized: bool
) -> dict:
    previous = (
        json.loads(paths.status.read_text(encoding="utf-8")) if paths.status.exists() else None
    )
    keep = (
        previous is not None
        and previous.get("status") == "approved"
        and not any_synthesized
        and previous.get("script_sha256") == script_sha
        and previous.get("settings_sha256") == settings_sha
    )
    status = previous if keep else {
        "status": "pending review",
        "script_sha256": script_sha,
        "settings_sha256": settings_sha,
        "synthesized_at": _now(),
        "approved_at": None,
    }
    write_atomic(paths.status, status)
    return status


def approve(paths: AudioPaths, script_path: Path) -> dict:
    if not paths.status.exists():
        raise ApproveError(f"no synthesized audio in {paths.root}; run synthesis first")
    status = json.loads(paths.status.read_text(encoding="utf-8"))
    if not script_path.exists() or sha256_file(script_path) != status["script_sha256"]:
        raise ApproveError(
            f"{script_path} changed since the audio was synthesized; re-run synthesis first"
        )
    status.update(status="approved", approved_at=_now())
    write_atomic(paths.status, status)
    return status


def create_engine(cfg: VoiceConfig):
    return OrpheusVoice(cfg.model)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.synthesize_voices",
        description="Speak a reviewed episode script with the local voice model (spec 002).",
        allow_abbrev=False,
    )
    parser.add_argument("command", nargs="?", default="synthesize",
                        choices=("synthesize", "approve", "download"))
    parser.add_argument("--book", type=Path, default=ROOT / "book_data.json")
    parser.add_argument("--episodes-dir", type=Path, default=ROOT / "episodes")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--reviewed", action="store_true",
                        help="confirm a person has reviewed the episode script (spec 001 AC-7.4)")
    parser.add_argument("--force", action="store_true", help="re-synthesize every line")
    return parser.parse_args(argv)


def _synthesize(args: argparse.Namespace, cfg: VoiceConfig, book: dict, slug: str,
                script_path: Path, paths: AudioPaths) -> int:
    if not script_path.exists():
        print(f"error: episode script not found: {script_path}", file=sys.stderr)
        return 1
    script = json.loads(script_path.read_text(encoding="utf-8"))
    terms = load_disallowed_terms(args.config_dir / "disallowed_terms.txt")
    errors = validate_script(script, book, terms)
    if errors:
        print(f"error: {script_path} fails validation:", file=sys.stderr)
        for problem in errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if not args.reviewed:
        print(
            "error: the episode script must be reviewed by a person before voice synthesis "
            f"(spec 001 AC-7.4). After reviewing {script_path}, re-run with --reviewed.",
            file=sys.stderr,
        )
        return 1
    prons = load_pronunciations(book, args.book)
    jobs = plan_jobs(script, cfg, prons)
    print(f"synthesizing {len(jobs)} lines into {paths.root}")
    outcomes = synthesize_clips(
        jobs, paths, lambda: create_engine(cfg), cfg, force=args.force,
        shift=lambda audio, sr, semitones: pitch_shift(audio, sr, semitones),
        on_progress=lambda o: o.synthesized and print(
            f"  {o.job.line_id}: {o.measure.duration:.1f} s, attempt {o.attempts}"),
    )
    names, rebuilt = build_tracks(outcomes, paths, cfg)
    if rebuilt or not paths.preview.exists():
        build_preview(names, paths, cfg)
    any_synthesized = any(o.synthesized for o in outcomes)
    status = update_status(paths, sha256_file(script_path), settings_hash(cfg, script, prons),
                           any_synthesized)
    write_review(paths, outcomes, names, script["title"], status["status"])
    synthesized = sum(o.synthesized for o in outcomes)
    print(f"synthesized {synthesized}, reused {len(outcomes) - synthesized} clips; "
          f"rebuilt {len(rebuilt)} of {len(names)} tracks; status: {status['status']}")
    if status["status"] != "approved":
        print(REVIEW_NOTICE.format(preview=paths.preview, tracks=paths.tracks, book=args.book))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths: AudioPaths | None = None
    try:
        cfg = load_voice_config(args.config_dir / "voices.json")
        if args.command == "download":
            download_model(cfg.model)
            create_engine(cfg)
            print(f"voice model ready: {cfg.model.repo}@{cfg.model.revision[:8]}")
            return 0
        book = load_book(args.book)
        slug = slugify(book["metadata"]["title"])
        script_path = args.episodes_dir / slug / "episode_script.json"
        paths = AudioPaths(args.build_dir / slug / "audio")
        if args.command == "approve":
            approve(paths, script_path)
            print(f"approved the audio in {paths.root}")
            return 0
        return _synthesize(args, cfg, book, slug, script_path, paths)
    except (BookError, ConfigError, VoiceUnavailable, PitchShiftError, ApproveError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LineFailed as exc:
        print(f"error: {exc}; nothing stitched:", file=sys.stderr)
        for job, problems in exc.failures:
            print(f"  - {describe(job)}: {'; '.join(problems)}", file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            where = paths.root if paths else args.build_dir
            print(f"error: not enough disk space to write audio in {where}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
```
  Note: `shift` and `create_engine` are looked up at call time through lambdas, so tests can monkeypatch `sv.pitch_shift` and `sv.create_engine`.
- [ ] Run `uv run pytest -q` (all pass) and Ruff (clean).
- [ ] Commit: `feat(voice): Phase 4: CLI, script gate, review sheet, approval and download`

---

## Phase 5: Real voice verification and docs

**Implements:** All FRs | **Satisfies:** AC-2.2 (real FFmpeg), AC-2.3 (listening), NFR Performance (≤ 30 min first run, under 30 s re-run, under 8 GB), NFR Quality (listening gate)
**Files:** `CLAUDE.md`, `.claude/rules/tts-and-llm.md`, `.claude/memory/steering/tech-stack.md`
**Interfaces:** Consumes everything above. Produces nothing new.

- [ ] Run `uv run pytest -m ffmpeg -v`. Expect PASS: the pitch is lifted by 2 and 3 semitones within ±1, and the duration stays within 5% (AC-2.2).
- [ ] Run `uv run python -m video_pipeline.synthesize_voices download`. The model is already cached, so this mostly verifies the pinned revisions. Expect "voice model ready". Then run `uv run pytest -m tts -v` and expect PASS.
- [ ] In the untracked, hand-editable pilot script `episodes/the-snail-mail-express/episode_script.json`, update `cast.kid_older.voice` to `"zac"` and `cast.kid_younger.voice` to `"zoe"` so it matches `config/cast.json`. Use a one-off `uv run python -c` that loads, edits and rewrites the JSON with `indent=2, ensure_ascii=False`. Don't regenerate the banter.
- [ ] Run the full pilot synthesis in the background, timed, with peak memory recorded: `/usr/bin/time -l uv run python -m video_pipeline.synthesize_voices --reviewed`. Expect exit 0 in at most 30 minutes, with "maximum resident set size" under 8 GB.
  - **If lines fail the checks:** read `describe(job)` and the measurements first. If the speaking-rate bounds or the silence threshold are wrong for real speech, adjust `config/voices.json` checks, not the code, and record why in the commit message.
  - Stop after 2 full attempts and report.
- [ ] Re-run the same command. Expect "synthesized 0" in under 30 s (AC-6.1, NFR).
- [ ] Send the user `build/the-snail-mail-express/audio/preview.wav` plus one Q&A page track for the listening review (AC-2.3, Story 4). Don't run `approve`: approving is the producer's decision.
- [ ] Update `CLAUDE.md`:
  - **Commands:** add `uv run python -m video_pipeline.synthesize_voices download`, `uv run python -m video_pipeline.synthesize_voices --reviewed [--force]`, `uv run python -m video_pipeline.synthesize_voices approve` and `uv run pytest -m "ffmpeg or tts"`.
  - **Stage-2 note:** voices in `config/cast.json`, model, pitch, pauses and checks in `config/voices.json`, optional `metadata.pronunciations` in the book data, outputs in `build/<slug>/audio/` (clips, tracks with timing JSON, preview.wav, review.md, status.json), and the render stage must require the "approved" status.
- [ ] `.claude/rules/tts-and-llm.md`: replace "Generate line by line and free MLX cache between lines (unverified…)" with "✓ Use the 4-bit model with `max_tokens=3000` (the default 1200 cuts lines off at ~15 s). Generate line by line and call `mx.clear_cache()` between lines (verified 2026-11-09: ~6 GB peak)."
- [ ] `.claude/memory/steering/tech-stack.md`: add mlx-audio 0.6.4 with Orpheus 4-bit (pinned revisions in `config/voices.json`), soundfile, and the FFmpeg `asetrate/aresample/atempo` pitch lift.
- [ ] Commit (docs and config only; never `build/` or `episodes/`): `feat(voice): Phase 5: real voice verification and docs` with the measured run time and memory.

## Review fixes (after Phase 5)

An implementation review found eleven defects; each was fixed test-first.

1. `build_tracks`: `track_key` now hashes, per line, the clip key, the sha256 of the clip WAV bytes, `line_id`, `speaker`, `role`, `text`, the `measure` dict and `attempts`, plus the gaps and sample rate. `--force` or a re-synthesized clip now rebuilds its tracks.
2. `build_preview(track_names, paths, cfg) -> bool` keeps a `preview.json` sidecar (`preview_key` over the ordered `[track, track_key]` list, `between_tracks` and the sample rate), written after `preview.wav`; it rebuilds whenever the key differs or a file is missing. `_synthesize` always calls it.
3. The timing JSON follows markup-only or role-only edits (covered by 1).
4. `write_wav_atomic` turns a `soundfile.LibsndfileError` into `OSError(ENOSPC)` when the error mentions space or less than 50 MB is free, so the disk-full message names the audio folder.
5. `tts.download_model` writes each support repo's pinned revision into the HF cache's `refs/main`; `OrpheusVoice` checks `refs/main` and the cached pinned snapshot before loading and sets `huggingface_hub.constants.HF_HUB_OFFLINE = True`.
6. `LineError`: any engine or pitch-shift error in `make_clip` is re-raised as `"<page N line M (speaker)>: <Type>: <message>"`; `main` prints it and exits 1.
7. A line sharing a clip key with an earlier failed line is also listed in `LineFailed`.
8. Reused clips are re-checked with `clip_problems(stored measure, cfg.checks)`; failing ones are re-synthesized. Ruling: `model.seed` stays out of clip identity.
9. `plan_jobs` rejects two roles sharing a voice (AC-2.4).
10. `approve(paths, script_path, settings_sha)` also refuses when the voice settings changed since synthesis. `_synthesize` computes the status with `next_status`, writes `review.md`, then `status.json` last.
11. Approval follows the preview (AC-4.4): clips re-synthesized by an interrupted or failed run (e.g. an interrupted `--force`) were reused next run and the approval survived on audio nobody heard. `status.json` now records `preview_key` (read with `current_preview_key(paths)` from `preview.json` after `build_preview`); `next_status(paths, script_sha, settings_sha, preview_key, any_synthesized)` keeps "approved" only if the previous status is approved, its `preview_key` equals the current one, nothing was synthesized this run and both hashes match. `approve` refuses with "the preview changed since synthesis finished; re-run synthesis first" when the keys differ, and with "episode script not found: <path>" when the script is missing.

---

## Quickstart Validation

```bash
uv sync --inexact
uv run python -m video_pipeline.synthesize_voices download          # one-time; model already cached here
uv run python -m video_pipeline.synthesize_voices                    # → error: must be reviewed … --reviewed
uv run python -m video_pipeline.synthesize_voices --reviewed         # → ~24 min; build/the-snail-mail-express/audio/ + listening notice
uv run python -m video_pipeline.synthesize_voices --reviewed         # → "synthesized 0 …" in < 30 s
uv run python -m video_pipeline.synthesize_voices approve            # → status approved (after you listen)
uv run pytest -v && uv run pytest -m "ffmpeg or tts" -v
```

## Spec coverage

| Spec item | Phase(s) |
|---|---|
| FR-1 Input | 3 (`load_voice_config`, cast voices), 4 (book, script path, validation) |
| FR-2 Voices | 2 (engine, pitch), 3 (voices per role, tags, respellings) |
| FR-3 Checks and retries | 1 (measures), 3 (`make_clip`, recorded results) |
| FR-4 Tracks, timing, preview | 1 (`stitch`, pauses), 3 (`build_tracks`, `build_preview`), 4 (preview on rebuild) |
| FR-5 Pronunciation | 3 (`load_pronunciations`, `spoken_text`), 4 (end to end) |
| FR-6 Review status | 4 (`update_status`, renamed `next_status` in the review fixes; `approve`, review sheet) |
| FR-7 Reuse and re-runs | 3 (`clip_key`, cache, track keys, force, atomic) |
| NFR Performance | 3 (lazy engine), 5 (timed runs) |
| NFR Quality / Security / Reliability / Cost | 5 (listening gate), 2 (pinned, offline), 3+4 (atomic, named errors), 2+4 (`download` only) |
| Error Scenarios | 4 (missing or invalid script, not reviewed, model missing, unsupported voice, failing lines, bad pronunciations, approve errors, disk full), 3 (interruption) |
