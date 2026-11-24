import subprocess

import numpy as np
import pytest

from video_pipeline import tts
from video_pipeline.tts import (
    ModelSettings,
    PitchShiftError,
    VoiceUnavailable,
    download_model,
    pitch_filter,
    pitch_ratio,
    pitch_shift,
    voice_filter,
)

SETTINGS = ModelSettings("org/model", "abc12345def")


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


def test_download_checks_digest_before_replacing_cached_file(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(tts, "model_folder", lambda settings: tmp_path)
    monkeypatch.setattr(tts, "MODEL_FILES", {"voice.bin": "invalid"})
    monkeypatch.setattr(tts.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad"))
    cached = tmp_path / "voice.bin"
    cached.write_bytes(b"previous")
    with pytest.raises(VoiceUnavailable, match="checksum"):
        download_model(SETTINGS)
    assert cached.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [cached]


def test_voice_filter_equals_pitch_filter_for_neutral_delivery():  # AC-3.2
    assert voice_filter(2, 24000) == pitch_filter(2, 24000)
    assert voice_filter(3, 24000, 1.0, 0.0) == pitch_filter(3, 24000)


def test_voice_filter_adds_speed_and_loudness_in_one_chain():  # AC-3.1
    assert voice_filter(12, 24000, 1.25, -3) == (
        "asetrate=48000,aresample=24000,atempo=0.625000,volume=-3dB"
    )
    assert voice_filter(0, 24000, 0.8, 0) == "asetrate=24000,aresample=24000,atempo=0.800000"
    assert voice_filter(0, 24000, 1.0, 1.5).endswith(",volume=1.5dB")


def test_pitch_shift_neutral_delivery_is_a_no_op():  # AC-3.2: no extra processing
    audio = np.ones(10, dtype=np.float32)
    assert pitch_shift(audio, 24000, 0, 1.0, 0.0) is audio


def test_pitch_shift_runs_ffmpeg_for_a_feeling_alone(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=np.ones(4, dtype="<f4").tobytes())

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    pitch_shift(np.ones(4, dtype=np.float32), 24000, 0, 0.9, -3)
    assert voice_filter(0, 24000, 0.9, -3) in seen["cmd"]
