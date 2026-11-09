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
