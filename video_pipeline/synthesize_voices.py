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

VOICE_CONFIG_KEYS = (
    "model",
    "supported_voices",
    "pitch_semitones",
    "pauses",
    "checks",
    "sample_rate",
)


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
                (item["repo"], item["revision"]) for item in model.get("support_repos", [])
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
    cleaned = TAG_RE.sub(lambda m: m.group(0) if m.group(0)[1:-1] in ALLOWED_TAGS else " ", text)
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
            LineJob(
                line_id,
                track,
                speaker,
                line.get("role"),
                line["text"],
                spoken,
                word_count(line["text"]),
                voice,
                semitones,
                clip_key(spoken, voice, semitones, cfg),
            )
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
        raw = shift(
            engine.synthesize(job.spoken, job.voice, cfg.seed + attempt - 1),
            cfg.sample_rate,
            job.semitones,
        )
        trimmed = trim_silence(raw, cfg.sample_rate, cfg.checks.silence_db, cfg.checks.edge_silence)
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
                write_atomic(
                    meta_path,
                    {
                        "line_id": job.line_id,
                        "speaker": job.speaker,
                        "voice": job.voice,
                        "semitones": job.semitones,
                        "spoken": job.spoken,
                        "measure": measure.to_dict(),
                        "attempts": attempts,
                    },
                )
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
        if (
            paths.track(name).exists()
            and timing_path.exists()
            and json.loads(timing_path.read_text(encoding="utf-8")).get("track_key") == track_key
        ):
            continue
        clips = [sf.read(paths.clip(o.job.key), dtype="float32")[0] for o in items]
        audio, spans = stitch(clips, gaps, cfg.sample_rate)
        write_wav_atomic(paths.track(name), audio, cfg.sample_rate)
        write_atomic(
            timing_path,
            {
                "track": name,
                "track_key": track_key,
                "duration": round(len(audio) / cfg.sample_rate, 3),
                "lines": [
                    {
                        "line_id": o.job.line_id,
                        "speaker": o.job.speaker,
                        "role": o.job.role,
                        "text": o.job.text,
                        "start": start,
                        "end": end,
                        "clip": f"clips/{o.job.key}.wav",
                        "quality": o.measure.to_dict(),
                        "attempts": o.attempts,
                    }
                    for o, (start, end) in zip(items, spans, strict=True)
                ],
            },
        )
        rebuilt.append(name)
    return list(groups), rebuilt


def build_preview(track_names: list[str], paths: AudioPaths, cfg: VoiceConfig) -> None:
    tracks = [sf.read(paths.track(name), dtype="float32")[0] for name in track_names]
    gaps = [cfg.pauses["between_tracks"]] * max(0, len(tracks) - 1)
    audio, _ = stitch(tracks, gaps, cfg.sample_rate)
    write_wav_atomic(paths.preview, audio, cfg.sample_rate)
