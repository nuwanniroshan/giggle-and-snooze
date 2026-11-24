"""Offline Kokoro voice synthesis and FFmpeg delivery adjustments."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DOWNLOAD_HINT = "run: uv run python -m video_pipeline.synthesize_voices download"


@dataclass(frozen=True)
class ModelSettings:
    repo: str
    revision: str


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


def voice_filter(
    semitones: float, sample_rate: int, speed: float = 1.0, gain_db: float = 0.0
) -> str:
    """The role's pitch lift plus a feeling's speed and loudness in one chain (spec 017 FR-3).

    With speed 1 and no gain it is exactly `pitch_filter`, so neutral lines are unchanged."""
    if speed == 1 and gain_db == 0:
        return pitch_filter(semitones, sample_rate)
    ratio = pitch_ratio(semitones)
    chain = (
        f"asetrate={round(sample_rate * ratio)},aresample={sample_rate},atempo={speed / ratio:.6f}"
    )
    return chain + (f",volume={gain_db:g}dB" if gain_db else "")


def pitch_shift(
    audio: np.ndarray,
    sample_rate: int,
    semitones: float,
    speed: float = 1.0,
    gain_db: float = 0.0,
) -> np.ndarray:
    if semitones == 0 and speed == 1 and gain_db == 0:
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
        voice_filter(semitones, sample_rate, speed, gain_db),
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
        raise PitchShiftError("ffmpeg not found; install FFmpeg and add it to PATH") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        raise PitchShiftError(f"ffmpeg pitch shift failed: {detail}") from None
    return np.frombuffer(result.stdout, dtype="<f4").astype(np.float32)


MODEL_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def model_folder(settings: ModelSettings) -> Path:
    base = Path(os.environ.get("KIDDO_MODELS_DIR", Path.home() / ".cache/kiddo/models"))
    return base / settings.revision


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_model(settings: ModelSettings) -> None:
    folder = model_folder(settings)
    folder.mkdir(parents=True, exist_ok=True)
    for name, digest in MODEL_FILES.items():
        target = folder / name
        if target.is_file() and checksum(target) == digest:
            continue
        url = f"https://github.com/{settings.repo}/releases/download/{settings.revision}/{name}"
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as tmp:
            partial = Path(tmp.name)
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out)
            if checksum(partial) != digest:
                raise VoiceUnavailable(f"incorrect checksum for {name}; {DOWNLOAD_HINT}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


def _resolve_voice(model, voice: str, blends: dict[str, dict[str, float]]):
    recipe = blends.get(voice)
    if not recipe:
        return voice
    mix = None
    for name, weight in recipe.items():
        style = model.get_voice_style(name)
        mix = style * float(weight) if mix is None else mix + style * float(weight)
    if mix is None:
        raise VoiceUnavailable(f"voice blend {voice!r} has no components")
    return mix.astype(np.float32)


class KokoroVoice:
    sample_rate = 24000

    def __init__(self, settings: ModelSettings, voice_blends: dict[str, dict[str, float]] | None = None) -> None:
        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise VoiceUnavailable(f"Kokoro runtime unavailable ({exc}); run uv sync") from exc
        folder = model_folder(settings)
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            session = ort.InferenceSession(
                str(folder / "kokoro-v1.0.onnx"), options, providers=["CPUExecutionProvider"]
            )
            self._model = Kokoro.from_session(session, str(folder / "voices-v1.0.bin"))
        except Exception as exc:
            raise VoiceUnavailable(
                f"cannot load Kokoro from {folder}: {exc}; {DOWNLOAD_HINT}"
            ) from exc
        self._voice_blends = voice_blends or {}

    def synthesize(
        self,
        text: str,
        voice: str,
        seed: int,
        speed: float = 1.0,
        sentence_pause: float = 0.25,
        clause_pause: float = 0.1,
    ) -> np.ndarray:
        resolved = _resolve_voice(self._model, voice, self._voice_blends)
        audio, _ = self._model.create(
            text,
            voice=resolved,
            lang="en-us",
            speed=speed,
            trim=False,
            sentence_pause=sentence_pause,
            clause_pause=clause_pause,
        )
        return audio
