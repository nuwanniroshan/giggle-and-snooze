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
    support_repos: tuple[tuple[str, str], ...] = ()


class VoiceUnavailable(Exception):
    """The local voice engine or model is not installed or not downloaded."""


class PitchShiftError(Exception):
    """FFmpeg could not apply the pitch lift."""


def pitch_ratio(semitones: float) -> float:
    return 2 ** (semitones / 12)


def pitch_filter(semitones: float, sample_rate: int) -> str:
    """Raise pitch by `semitones` and restore the original duration (AC-2.2)."""
    ratio = pitch_ratio(semitones)
    return f"asetrate={round(sample_rate * ratio)},aresample={sample_rate},atempo={1 / ratio:.6f}"


def pitch_shift(audio: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
    if semitones == 0:
        return audio
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-af",
        pitch_filter(semitones, sample_rate),
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "pipe:1",
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

    for repo, revision in ((settings.repo, settings.revision), *settings.support_repos):
        snapshot_download(repo_id=repo, revision=revision)


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
