"""Local voice engine (spec 002 FR-2): Orpheus via mlx-audio, plus an FFmpeg pitch lift.

The model is loaded once per run, from a pinned revision, offline (HF_HUB_OFFLINE=1). mlx-audio
loads safetensors weights and never runs code shipped with the model.
"""

from __future__ import annotations

import gc
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _support_kwargs(repo: str, revision: str, patterns: tuple[str, ...] | None) -> dict:
    kwargs: dict = {"repo_id": repo, "revision": revision}
    if patterns is not None:
        kwargs["allow_patterns"] = list(patterns)
    return kwargs


def _main_ref(repo: str) -> Path:
    """The HF cache's refs/main file, which mlx-audio resolves when it loads a repo by name."""
    from huggingface_hub import constants

    try:
        from huggingface_hub.file_download import repo_folder_name

        folder = repo_folder_name(repo_id=repo, repo_type="model")
    except ImportError:
        folder = "models--" + repo.replace("/", "--")
    return Path(constants.HF_HUB_CACHE) / folder / "refs" / "main"


def _pin_main_ref(repo: str, revision: str) -> None:
    ref = _main_ref(repo)
    ref.parent.mkdir(parents=True, exist_ok=True)
    tmp = ref.with_name(ref.name + ".tmp")
    tmp.write_text(revision, encoding="utf-8")
    os.replace(tmp, ref)  # noqa: PTH105


def download_model(settings: ModelSettings) -> None:
    """The only network use (spec 002 Cost): fetch the pinned model and its support files.

    mlx-audio loads the support repos by name (refs/main), so refs/main is pointed at the pin.
    """
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=settings.repo, revision=settings.revision)
    for repo, revision, patterns in settings.support_repos:
        snapshot_download(**_support_kwargs(repo, revision, patterns))
        _pin_main_ref(repo, revision)


def _check_support_repos(settings: ModelSettings, snapshot_download) -> None:
    for repo, revision, patterns in settings.support_repos:
        ref = _main_ref(repo)
        current = ref.read_text(encoding="utf-8").strip() if ref.exists() else None
        if current != revision:
            raise VoiceUnavailable(
                f"voice support files {repo} are not pinned to {revision[:8]} "
                f"(cache refs/main is {current[:8] if current else 'missing'}); {DOWNLOAD_HINT}"
            )
        try:
            snapshot_download(**_support_kwargs(repo, revision, patterns), local_files_only=True)
        except Exception as exc:  # noqa: BLE001 - any lookup failure means "not downloaded"
            raise VoiceUnavailable(
                f"voice support files {repo}@{revision[:8]} are not downloaded "
                f"({type(exc).__name__}); {DOWNLOAD_HINT}"
            ) from None


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
        try:  # huggingface_hub may have read HF_HUB_OFFLINE at import, before it was set above
            from huggingface_hub import constants

            if hasattr(constants, "HF_HUB_OFFLINE"):
                constants.HF_HUB_OFFLINE = True
        except ImportError:
            pass
        _check_support_repos(settings, snapshot_download)
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

    def recover(self) -> None:
        """Free what a failed GPU run left behind, before a retry."""
        gc.collect()
        self._mx.clear_cache()

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
