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
        return (
            np.array([float(_db(np.sqrt(np.mean(audio.astype(np.float64) ** 2))))])
            if len(audio)
            else np.array([], dtype=np.float64)
        )
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


def squeeze_silences(audio: np.ndarray, sr: int, silence_db: float, max_gap: float) -> np.ndarray:
    """Shorten every silent stretch longer than max_gap to max_gap (the model sometimes stalls)."""
    size = max(1, int(sr * FRAME_S))
    quiet = frame_db(audio, sr) <= silence_db
    limit = max(1, int(max_gap / FRAME_S))
    keep = np.ones(len(quiet), dtype=bool)
    run = 0
    for i, is_quiet in enumerate(quiet):
        run = run + 1 if is_quiet else 0
        if run > limit:
            keep[i] = False
    if keep.all():
        return audio
    tail = audio[len(quiet) * size :]
    frames = audio[: len(quiet) * size].reshape(len(quiet), size)
    return np.concatenate([frames[keep].reshape(-1), tail])


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
