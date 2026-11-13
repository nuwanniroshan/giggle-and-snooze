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
        model["repo"],
        model["revision"],
        model["temperature"],
        model["top_p"],
        model["max_tokens"],
    )
    raw = OrpheusVoice(settings).synthesize("<giggle> Look at the tiny snail!", "zoe", 7)
    trimmed = trim_silence(raw, SR, -40, 0.1)
    measure = measure_clip(raw, trimmed, SR, 5, CheckSettings())
    assert clip_problems(measure, CheckSettings()) == []


def _rms(audio):
    return float(np.sqrt(np.mean(np.square(audio))))


@pytest.mark.ffmpeg
def test_feelings_change_duration_pitch_and_loudness():  # spec 017 AC-3.1
    from video_pipeline.emotions import load_emotions

    feelings = load_emotions(CONFIG.parent)
    t = np.arange(SR * 2) / SR
    sine = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)

    def voiced(name, lift=2):
        f = feelings.feeling(name)
        return pitch_shift(sine, SR, lift + f.semitones, f.speed, f.gain_db)

    neutral, sleepy, excited = voiced("neutral"), voiced("sleepy"), voiced("excited")
    assert len(sleepy) > len(neutral) * 1.05  # slower
    assert _rms(sleepy) < _rms(neutral) * 0.85  # quieter
    assert dominant_hz(excited) > dominant_hz(neutral) * 1.03  # higher
    assert _rms(excited) > _rms(neutral) * 1.1  # louder
    assert len(excited) < len(neutral)  # faster
