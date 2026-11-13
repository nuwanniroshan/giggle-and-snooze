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
    download_model,
    pitch_filter,
    pitch_ratio,
    pitch_shift,
    voice_filter,
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


def install_fakes(monkeypatch, snapshot=None, model=None, cache=None):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # restored after each test
    calls = {"seed": [], "clear": 0, "snapshot": []}
    constants = types.ModuleType("huggingface_hub.constants")
    constants.HF_HUB_CACHE = str(cache or "/nonexistent-hf-cache")
    constants.HF_HUB_OFFLINE = False
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
    hub.constants = constants
    utils = types.ModuleType("mlx_audio.tts.utils")
    utils.load_model = lambda path: model or FakeModel()
    for name, module in {
        "mlx": mlx,
        "mlx.core": core,
        "huggingface_hub": hub,
        "huggingface_hub.constants": constants,
        "mlx_audio": types.ModuleType("mlx_audio"),
        "mlx_audio.tts": types.ModuleType("mlx_audio.tts"),
        "mlx_audio.tts.utils": utils,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_download_fetches_only_listed_files_from_support_repos(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, cache=tmp_path)
    settings = ModelSettings(
        "org/model",
        "abc12345def",
        0.6,
        0.8,
        3000,
        support_repos=(
            ("org/codec", "rev1", None),
            ("org/full", "rev2", ("config.json", "tokenizer.json")),
        ),
    )
    download_model(settings)
    assert calls["snapshot"] == [
        {"repo_id": "org/model", "revision": "abc12345def"},
        {"repo_id": "org/codec", "revision": "rev1"},
        {
            "repo_id": "org/full",
            "revision": "rev2",
            "allow_patterns": ["config.json", "tokenizer.json"],
        },
    ]


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
        {
            "text": "<giggle> Hi!",
            "voice": "zoe",
            "temperature": 0.6,
            "top_p": 0.8,
            "max_tokens": 3000,
            "verbose": False,
        }
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


SUPPORT = ModelSettings(
    "org/model",
    "abc12345def",
    0.6,
    0.8,
    3000,
    support_repos=(("org/codec", "rev1", None), ("org/full", "rev2", ("config.json",))),
)


def ref_file(cache, repo):
    return cache / f"models--{repo.replace('/', '--')}" / "refs" / "main"


def test_download_pins_support_repo_refs(monkeypatch, tmp_path):  # review fix 5
    install_fakes(monkeypatch, cache=tmp_path)
    download_model(SUPPORT)
    assert ref_file(tmp_path, "org/codec").read_text() == "rev1"
    assert ref_file(tmp_path, "org/full").read_text() == "rev2"
    assert not ref_file(tmp_path, "org/model").exists()  # loaded by path, not by name


def pin_refs(cache, refs):
    for repo, revision in refs.items():
        ref_file(cache, repo).parent.mkdir(parents=True, exist_ok=True)
        ref_file(cache, repo).write_text(revision)


def test_engine_checks_pinned_support_repos(monkeypatch, tmp_path):  # review fix 5
    calls = install_fakes(monkeypatch, cache=tmp_path)
    pin_refs(tmp_path, {"org/codec": "rev1", "org/full": "rev2"})
    OrpheusVoice(SUPPORT)
    assert {
        "repo_id": "org/codec",
        "revision": "rev1",
        "local_files_only": True,
    } in calls["snapshot"]
    assert {
        "repo_id": "org/full",
        "revision": "rev2",
        "local_files_only": True,
        "allow_patterns": ["config.json"],
    } in calls["snapshot"]
    assert sys.modules["huggingface_hub.constants"].HF_HUB_OFFLINE is True


@pytest.mark.parametrize("refs", [{"org/full": "rev2"}, {"org/codec": "other", "org/full": "rev2"}])
def test_engine_refuses_unpinned_support_repo(monkeypatch, tmp_path, refs):  # review fix 5
    install_fakes(monkeypatch, cache=tmp_path)
    pin_refs(tmp_path, refs)
    with pytest.raises(VoiceUnavailable, match="org/codec") as excinfo:
        OrpheusVoice(SUPPORT)
    assert DOWNLOAD_HINT in str(excinfo.value)


def test_engine_refuses_missing_support_snapshot(monkeypatch, tmp_path):  # review fix 5
    def snapshot(**kwargs):
        if kwargs["repo_id"] == "org/full":
            raise FileNotFoundError("not cached")
        return "/models/x"

    install_fakes(monkeypatch, snapshot=snapshot, cache=tmp_path)
    pin_refs(tmp_path, {"org/codec": "rev1", "org/full": "rev2"})
    with pytest.raises(VoiceUnavailable, match="org/full") as excinfo:
        OrpheusVoice(SUPPORT)
    assert DOWNLOAD_HINT in str(excinfo.value)


# --- spec 017: feelings (AC-3.1, AC-3.2) ----------------------------------------------------


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
