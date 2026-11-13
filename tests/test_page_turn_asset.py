"""Spec 015 Story 3: the recorded page-flip sound (AC-3.1 to AC-3.3, FR-5)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from video_pipeline import frames
from video_pipeline.sound_library import check_library, load_library

ROOT = Path(__file__).resolve().parents[1]
SFX_DIR = ROOT / "assets" / "audio_sfx"
LIBRARY = ROOT / "assets" / "sound_library"
SR = 48000


def _decode() -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(SFX_DIR / frames.PAGE_TURN_FILE),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype="<f4").astype(np.float64)


def _dbfs(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt(np.mean(np.square(x)))), 1e-12))


def test_page_turn_file_exists_under_the_name_the_renderer_loads():
    assert (SFX_DIR / "page_turn.wav").is_file()
    assert frames.PAGE_TURN_FILE == "page_turn.wav"


def test_page_turn_is_one_flip_long():
    seconds = len(_decode()) / SR
    assert 0.4 <= seconds <= 1.5


def test_page_turn_peak_is_minus_6_dbfs():
    peak = 20 * np.log10(float(np.abs(_decode()).max()))
    assert abs(peak - (-6.0)) <= 0.5


def test_page_turn_starts_and_ends_without_a_click():
    x = _decode()
    n = round(0.010 * SR)
    assert _dbfs(x[:n]) < -40
    assert _dbfs(x[-n:]) < -40


def test_load_page_turn_uses_the_recording(tmp_path):
    audio = frames.load_page_turn(SFX_DIR, SR, tmp_path / "unused.wav")
    assert not (tmp_path / "unused.wav").exists()  # no generated sound was written
    assert 0.4 <= len(audio) / SR <= 1.5


def test_library_lists_the_page_flip_with_source_author_and_licence():
    raw = json.loads((LIBRARY / "index.json").read_text(encoding="utf-8"))["sounds"]["page_flip"]
    assert raw["url"].startswith("https://pixabay.com/")
    assert raw["author"]
    assert raw["license"].startswith("Pixabay Content License")
    sound = load_library(LIBRARY).get("page_flip")
    assert sound.kind == "sfx"
    assert sound.file.resolve() == (SFX_DIR / "page_turn.wav").resolve()
    assert check_library(LIBRARY) == []
