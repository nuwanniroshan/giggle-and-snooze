"""Stage 2: speak a reviewed episode script (spec 002).

Run: uv run python -m video_pipeline.synthesize_voices --reviewed      # synthesize
     uv run python -m video_pipeline.synthesize_voices approve         # after listening
     uv run python -m video_pipeline.synthesize_voices download        # one-time model fetch
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from video_pipeline.audio_checks import (
    FULL_SCALE,
    CheckSettings,
    ClipMeasure,
    clip_problems,
    measure_clip,
    pause_between,
    squeeze_silences,
    stitch,
    trim_silence,
)
from video_pipeline.contract import (
    SPEAKERS,
    TAG_RE,
    ConfigError,
    load_disallowed_terms,
    validate_script,
    word_count,
)
from video_pipeline.emotions import NEUTRAL, EmotionSettings, Feeling, load_emotions
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.file_hash import script_sha256, sha256_file
from video_pipeline.library import PROJECT_ROOT, LibraryError, book_folder
from video_pipeline.tts import (
    KokoroVoice,
    ModelSettings,
    PitchShiftError,
    VoiceUnavailable,
    download_model,
    pitch_shift,
)

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


MAX_PAUSE_INSIDE_LINE = 0.6  # seconds; longer stalls inside a line are cut down to this


def spoken_text(text: str, prons: dict[str, str]) -> str:
    """What the voice model hears: markup dropped, respellings applied."""
    cleaned = TAG_RE.sub(" ", text)
    for word, spoken in prons.items():
        cleaned = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", spoken, cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def clip_key(
    spoken: str,
    voice: str,
    semitones: float,
    cfg: VoiceConfig,
    emotion: str | None = None,
    delivery: Feeling | None = None,
    tts_speed: float = 1.0,
    sentence_pause: float = 0.25,
    clause_pause: float = 0.1,
) -> str:
    """Clip identity includes voice, role delivery and optional emotion delivery."""
    # model.seed is deliberately left out: a seed change must not discard passing clips.
    identity: dict = {
        "text": spoken,
        "voice": voice,
        "semitones": semitones,
        "tts_speed": tts_speed,
        "sentence_pause": sentence_pause,
        "clause_pause": clause_pause,
        "repo": cfg.model.repo,
        "revision": cfg.model.revision,
        "sample_rate": cfg.sample_rate,
    }
    if delivery is not None and not delivery.is_neutral:
        identity["delivery"] = {
            "emotion": emotion,
            "speed": delivery.speed,
            "semitones": delivery.semitones,
            "gain_db": delivery.gain_db,
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
    semitones: float  # the role's lift plus the feeling's offset
    key: str
    emotion: str = NEUTRAL
    speed: float = 1.0
    gain_db: float = 0.0
    tts_speed: float = 1.0
    sentence_pause: float = 0.25
    clause_pause: float = 0.1


def plan_jobs(
    script: dict,
    cfg: VoiceConfig,
    prons: dict[str, str],
    emotions: EmotionSettings | None = None,
) -> list[LineJob]:
    """One job per line. With `emotions` each line's feeling shapes its delivery (spec 017);
    without them every line is spoken neutral."""
    aliases = cfg.raw.get("legacy_voices", {})
    cast = {
        role: {**member, "voice": aliases.get(member.get("voice"), member.get("voice"))}
        for role, member in script["cast"].items()
    }
    blends = cfg.raw.get("voice_blends", {})
    for role in SPEAKERS:
        voice = cast.get(role, {}).get("voice")
        if voice not in cfg.supported_voices:
            raise ConfigError(
                f"cast role {role!r}: voice {voice!r} is not supported by the voice model "
                f"(supported: {', '.join(cfg.supported_voices)})"
            )
        for part in blends.get(voice, {}):
            if part not in cfg.supported_voices:
                raise ConfigError(
                    f"voice blend {voice!r}: component {part!r} is not a supported voice"
                )
    seen: dict[str, str] = {}
    for role in SPEAKERS:
        voice = cast[role]["voice"]
        if voice in seen:
            raise ConfigError(
                f"cast roles {seen[voice]!r} and {role!r} share the voice {voice!r}; "
                "each role needs its own voice (AC-2.4)"
            )
        seen[voice] = role
    jobs: list[LineJob] = []

    def add(line_id: str, track: str, line: dict) -> None:
        speaker = line["speaker"]
        voice = cast[speaker]["voice"]
        semitones = cfg.pitch_semitones.get(speaker, 0.0)
        role_delivery = cfg.raw.get("role_delivery", {}).get(speaker, {})
        tts_speed = float(role_delivery.get("speed", 1.0))
        sentence_pause = float(role_delivery.get("sentence_pause", 0.25))
        clause_pause = float(role_delivery.get("clause_pause", 0.1))
        spoken = spoken_text(line["text"], prons)
        emotion, delivery = NEUTRAL, None
        if emotions is not None:
            emotion = line.get("emotion") or NEUTRAL
            if emotion not in emotions.feelings:
                raise ConfigError(
                    f"{line_id}: emotion {emotion!r} is not allowed "
                    f"(allowed: {', '.join(emotions.names)})"
                )
            delivery = emotions.feeling(emotion)
        extra = delivery.semitones if delivery else 0.0
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
                semitones + extra,
                clip_key(spoken, voice, semitones, cfg, emotion, delivery, tts_speed, sentence_pause, clause_pause),
                emotion,
                delivery.speed if delivery else 1.0,
                delivery.gain_db if delivery else 0.0,
                tts_speed,
                sentence_pause,
                clause_pause,
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
    def preview_meta(self) -> Path:
        return self.root / "preview.json"

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


LOW_DISK_BYTES = 50 * 1024 * 1024


def write_wav_atomic(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.wav")
    try:
        try:
            sf.write(tmp, audio, sample_rate, subtype="PCM_16")
        except sf.LibsndfileError as exc:
            # libsndfile reports a full disk as a RuntimeError, not OSError(ENOSPC).
            if "space" in str(exc).lower() or shutil.disk_usage(path.parent).free < LOW_DISK_BYTES:
                raise OSError(errno.ENOSPC, f"No space left on device: {path}") from exc
            raise
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


class LineError(Exception):
    """The voice engine or the pitch lift failed on one line."""


def describe(job: LineJob) -> str:
    """Human name of a line (AC-3.4): 'page 3 line 2 (kid_older)'."""
    parts = job.line_id.split("-")
    if parts[0] == "page":
        return f"page {int(parts[1])} line {int(parts[2])} ({job.speaker})"
    return f"{parts[0]} line {int(parts[1])} ({job.speaker})"


class LineFailed(Exception):
    def __init__(self, failures: list[tuple[LineJob, list[str]]]) -> None:
        super().__init__(f"{len(failures)} line(s) failed the quality checks")
        self.failures = failures


# (audio, sample_rate, semitones, speed, gain_db) -> audio
Shift = Callable[[np.ndarray, int, float, float, float], np.ndarray]


GPU_RETRIES = 3
GPU_BACKOFF_S = (5.0, 15.0, 30.0)
GPU_HINT = (
    "the GPU timed out while speaking; close other GPU and memory-heavy apps, then run it "
    "again: finished lines are kept"
)


def is_gpu_timeout(exc: BaseException) -> bool:
    """A Metal GPU watchdog timeout from the voice engine (it is transient under load)."""
    text = str(exc)
    return isinstance(exc, RuntimeError) and (
        "kIOGPUCommandBufferCallbackErrorTimeout" in text
        or "GPU Timeout" in text
        or "command buffer" in text.lower()
    )


def synthesize_retrying(
    engine,
    text: str,
    voice: str,
    seed: int,
    speed: float = 1.0,
    sentence_pause: float = 0.25,
    clause_pause: float = 0.1,
    sleep=time.sleep,
) -> np.ndarray:
    """One engine call; retries GPU timeout if engine supports recovery."""
    for retry in range(GPU_RETRIES + 1):
        try:
            return engine.synthesize(
                text,
                voice,
                seed,
                speed=speed,
                sentence_pause=sentence_pause,
                clause_pause=clause_pause,
            )
        except TypeError:
            return engine.synthesize(text, voice, seed)
        except RuntimeError as exc:
            if not is_gpu_timeout(exc) or retry == GPU_RETRIES:
                raise
            if hasattr(engine, "recover"):
                engine.recover()
            sleep(GPU_BACKOFF_S[retry])
    raise AssertionError("unreachable")


def make_clip(
    job: LineJob, engine, cfg: VoiceConfig, shift: Shift
) -> tuple[np.ndarray | None, ClipMeasure, int, list[str]]:
    """Synthesize, lift (with the feeling's delivery), trim and check; up to cfg.max_attempts
    with a new seed each (AC-3.3). The checks measure the shaped audio (spec 017 AC-3.5)."""
    measure: ClipMeasure | None = None
    problems: list[str] = []
    speed, gain_db = job.speed, job.gain_db
    for attempt in range(1, cfg.max_attempts + 1):
        raw = shift(
            synthesize_retrying(
                engine,
                job.spoken,
                job.voice,
                cfg.seed + attempt - 1,
                speed=job.tts_speed,
                sentence_pause=job.sentence_pause,
                clause_pause=job.clause_pause,
            ),
            cfg.sample_rate,
            job.semitones,
            speed,
            gain_db,
        )
        trimmed = trim_silence(raw, cfg.sample_rate, cfg.checks.silence_db, cfg.checks.edge_silence)
        trimmed = squeeze_silences(
            trimmed, cfg.sample_rate, cfg.checks.silence_db, MAX_PAUSE_INSIDE_LINE
        )
        measure = measure_clip(raw, trimmed, cfg.sample_rate, job.words, cfg.checks)
        problems = clip_problems(measure, cfg.checks)
        if not problems:
            return trimmed, measure, attempt, []
        # Leave some headroom for variation in the next synthesis.
        if measure.peak >= FULL_SCALE:
            gain_db += 20 * math.log10(0.90 / measure.peak)
        if measure.wps > cfg.checks.max_wps:
            speed *= max(0.6, 0.92 * cfg.checks.max_wps / measure.wps)
        elif 0 < measure.wps < cfg.checks.min_wps:
            speed *= min(1.6, 1.08 * cfg.checks.min_wps / measure.wps)
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
            if first.problems:
                failures.append((job, first.problems))
            continue
        meta_path = paths.clip_meta(job.key)
        outcome = None
        if not force and paths.clip(job.key).exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            stored = ClipMeasure(**meta["measure"])
            # Re-check against today's thresholds; a clip that no longer passes is redone.
            if not clip_problems(stored, cfg.checks):
                outcome = ClipOutcome(job, stored, meta["attempts"], False, [])
        if outcome is None:
            if engine is None:
                engine = engine_factory()
                if engine.sample_rate != cfg.sample_rate:
                    raise ConfigError(
                        f"voice model sample rate {engine.sample_rate} does not match the "
                        f"configured sample rate {cfg.sample_rate}"
                    )
            try:
                audio, measure, attempts, problems = make_clip(job, engine, cfg, shift)
            except (LineFailed, ConfigError):
                raise
            except Exception as exc:  # noqa: BLE001 - name the line for any engine/pitch error
                hint = f"; {GPU_HINT}" if is_gpu_timeout(exc) else ""
                raise LineError(f"{describe(job)}: {type(exc).__name__}: {exc}{hint}") from exc
            if audio is None:
                failures.append((job, problems))
            else:
                write_wav_atomic(paths.clip(job.key), audio, cfg.sample_rate)
                meta = {
                    "line_id": job.line_id,
                    "speaker": job.speaker,
                    "voice": job.voice,
                    "semitones": job.semitones,
                    "spoken": job.spoken,
                    "measure": measure.to_dict(),
                    "attempts": attempts,
                }
                if job.emotion != NEUTRAL:
                    meta["emotion"] = job.emotion
                write_atomic(meta_path, meta)
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
        # Audio content, timing and the text/role written to the timing JSON all feed the key,
        # so a re-synthesized clip or a markup/role-only edit rebuilds the track.
        payload = {
            "lines": [
                {
                    "key": o.job.key,
                    "audio_sha256": sha256_file(paths.clip(o.job.key)),
                    "line_id": o.job.line_id,
                    "speaker": o.job.speaker,
                    "role": o.job.role,
                    "text": o.job.text,
                    "measure": o.measure.to_dict(),
                    "attempts": o.attempts,
                }
                for o in items
            ],
            "gaps": gaps,
            "sample_rate": cfg.sample_rate,
        }
        track_key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
        timing_path = paths.track_timing(name)
        meta = _read_json_or_none(timing_path)
        # Reuse needs the same inputs and the very file that was written (FR-7): a track WAV
        # edited or replaced on disk, or a timing JSON from before `wav_sha256`, is rebuilt.
        if (
            meta is not None
            and meta.get("track_key") == track_key
            and paths.track(name).is_file()
            and meta.get("wav_sha256") == sha256_file(paths.track(name))
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
                "wav_sha256": sha256_file(paths.track(name)),
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


def build_preview(track_names: list[str], paths: AudioPaths, cfg: VoiceConfig) -> bool:
    """Join the tracks; rebuild only when a track (key or audio), the order or the gap changed,
    or preview.wav no longer matches the file that was written (FR-7)."""
    keyed = []
    for name in track_names:
        timing = json.loads(paths.track_timing(name).read_text(encoding="utf-8"))
        keyed.append([name, timing["track_key"], timing.get("wav_sha256")])
    payload = [keyed, cfg.pauses["between_tracks"], cfg.sample_rate]
    preview_key = hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:32]
    meta = _read_json_or_none(paths.preview_meta)
    if (
        paths.preview.is_file()
        and meta is not None
        and meta.get("preview_key") == preview_key
        and meta.get("wav_sha256") == sha256_file(paths.preview)
    ):
        return False
    tracks = [sf.read(paths.track(name), dtype="float32")[0] for name in track_names]
    gaps = [cfg.pauses["between_tracks"]] * max(0, len(tracks) - 1)
    audio, _ = stitch(tracks, gaps, cfg.sample_rate)
    write_wav_atomic(paths.preview, audio, cfg.sample_rate)
    write_atomic(  # after the wav, so an interrupted run leaves no matching record
        paths.preview_meta,
        {"preview_key": preview_key, "wav_sha256": sha256_file(paths.preview), "tracks": keyed},
    )
    return True


class UnreadableRecord(Exception):
    """A status.json or preview.json that is not a readable JSON object."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} is unreadable; re-run synthesis")
        self.path = path


def read_record(path: Path) -> dict:
    """A JSON object written by synthesis; a corrupt file raises UnreadableRecord."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise UnreadableRecord(path) from None
    if not isinstance(data, dict):
        raise UnreadableRecord(path)
    return data


def _read_json_or_none(path: Path) -> dict | None:
    """A record, or None when it is missing or corrupt (synthesis then rebuilds it)."""
    try:
        return read_record(path)
    except (FileNotFoundError, UnreadableRecord):
        return None


def current_preview_key(paths: AudioPaths) -> str | None:
    """The key of the preview on disk, or None when there is no preview.

    A corrupt preview.json raises UnreadableRecord."""
    if not (paths.preview.exists() and paths.preview_meta.exists()):
        return None
    return read_record(paths.preview_meta).get("preview_key")


def track_hashes(paths: AudioPaths, track_names: list[str]) -> dict[str, dict[str, str | None]]:
    """sha256 of each track's audio and timing JSON (None for a missing file), so an approval
    covers the exact files that were listened to (spec 003 AC-2.2)."""

    def sha(path: Path) -> str | None:
        return sha256_file(path) if path.is_file() else None

    return {
        name: {"wav": sha(paths.track(name)), "json": sha(paths.track_timing(name))}
        for name in track_names
    }


REVIEW_NOTICE = (
    "Listening review required: play {preview} (each track is in {tracks}). Listen for "
    "robotic or garbled speech, mispronounced names and emotion tags read aloud, then approve "
    "with: uv run python -m video_pipeline.synthesize_voices approve --book {book}"
)


class ApproveError(Exception):
    """The audio cannot be approved yet."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def settings_hash(cfg: VoiceConfig, script: dict, prons: dict[str, str]) -> str:
    payload = {"voices": cfg.raw, "cast": script["cast"], "pronunciations": prons}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_review(
    paths: AudioPaths,
    outcomes: list[ClipOutcome],
    track_names: list[str],
    title: str,
    status: str,
) -> None:
    groups = _track_groups(outcomes)
    lines = [
        f"# Listening review: {title}",
        "",
        f"Status: **{status}**. Full episode: `preview.wav`. Tracks: `tracks/`.",
        "",
    ]
    for name in track_names:
        duration = json.loads(paths.track_timing(name).read_text(encoding="utf-8"))["duration"]
        lines += [
            f"## {name} ({duration:.1f} s)",
            "",
            "| Line | Speaker | Duration (s) | Peak | Longest silence (s) | Words/s | Attempts | Text |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for o in groups[name]:
            m = o.measure
            lines.append(
                f"| {o.job.line_id} | {o.job.speaker} | {m.duration:.2f} | {m.peak:.2f} | "
                f"{m.longest_silence:.2f} | {m.wps:.2f} | {o.attempts} | {_cell(o.job.text)} |"
            )
        lines.append("")
    paths.review.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.review.with_name(paths.review.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, paths.review)  # noqa: PTH105


def next_status(
    paths: AudioPaths,
    script_sha: str,
    settings_sha: str,
    preview_key: str | None,
    any_synthesized: bool,
    track_sha: dict[str, dict[str, str | None]],
) -> dict:
    """The status this run will commit; keeps an approval only if nothing changed.

    The approval belongs to the preview that was listened to: clips re-synthesized by an
    earlier interrupted run change the preview key even when this run synthesizes nothing.
    """
    previous = _read_json_or_none(paths.status)
    keep = (
        previous is not None
        and previous.get("status") == "approved"
        and not any_synthesized
        and preview_key is not None
        and previous.get("preview_key") == preview_key
        and previous.get("script_sha256") == script_sha
        and previous.get("settings_sha256") == settings_sha
        and previous.get("track_sha256") == track_sha
    )
    status = (
        previous
        if keep
        else {
            "status": "pending review",
            "script_sha256": script_sha,
            "settings_sha256": settings_sha,
            "preview_key": preview_key,
            "track_sha256": track_sha,
            "synthesized_at": _now(),
            "approved_at": None,
        }
    )
    return status


APPROVE_COMMAND = "uv run python -m video_pipeline.synthesize_voices approve"


def _validity_problems(
    paths: AudioPaths, status: dict, script_path: Path, settings_sha: str | None, fix: str
) -> list[str]:
    """Why the synthesized audio no longer matches the script, voice settings, preview or tracks."""
    if not script_path.exists():
        return [f"episode script not found: {script_path}"]
    since = "approval" if status.get("status") == "approved" else "synthesis"
    problems: list[str] = []
    if script_sha256(script_path) != status.get("script_sha256"):
        problems.append(f"{script_path} changed since the audio was synthesized; {fix}")
    if settings_sha != status.get("settings_sha256"):
        problems.append(f"voice settings changed since the audio was synthesized; {fix}")
    try:
        preview_key = current_preview_key(paths)
    except UnreadableRecord as exc:
        problems.append(str(exc))
    else:
        if preview_key != status.get("preview_key"):
            problems.append(f"the preview changed since synthesis finished; {fix}")
    recorded = status.get("track_sha256")
    if not isinstance(recorded, dict) or not recorded:
        problems.append(f"{since} predates track checks; {fix}")
    else:
        current = track_hashes(paths, list(recorded))
        for name in recorded:
            missing = [
                path
                for path, sha in (
                    (paths.track(name), current[name]["wav"]),
                    (paths.track_timing(name), current[name]["json"]),
                )
                if sha is None
            ]
            if missing:
                problems.append(f"missing audio for {name}: {missing[0]}")  # AC-2.3
            elif current[name] != recorded[name]:
                problems.append(f"audio track {name} changed since {since}; {fix}")
    return problems


def approval_problems(
    paths: AudioPaths, script_path: Path, settings_sha: str | None, book_path: Path | None = None
) -> list[str]:
    """Why the audio may not be used downstream (spec 003 AC-2.1, AC-2.2); empty when approved
    and the approval still holds for the current script, voice settings and preview."""
    if not paths.status.exists():
        return [f"no synthesized audio in {paths.root}; run synthesis first"]
    try:
        status = read_record(paths.status)
    except UnreadableRecord as exc:
        return [str(exc)]
    problems = _validity_problems(
        paths, status, script_path, settings_sha, "re-run synthesis and approve again"
    )
    if not problems and status.get("status") != "approved":
        book = f" --book {book_path}" if book_path else ""
        problems.append(
            f"the audio in {paths.root} is not approved (status: {status.get('status')}); "
            f"listen to it, then approve it with: {APPROVE_COMMAND}{book}"
        )
    return problems


def approve(
    paths: AudioPaths, script_path: Path, settings_sha: str | None, by: str | None = None
) -> dict:
    """Approve audio that still matches the script and voice settings. `by` ("autopilot", spec
    021 AC-3.2) records who approved it; a person's approval has no `by`. No hash includes it."""
    if not paths.status.exists():
        raise ApproveError(f"no synthesized audio in {paths.root}; run synthesis first")
    try:
        status = read_record(paths.status)
    except UnreadableRecord as exc:
        raise ApproveError(str(exc)) from None
    problems = _validity_problems(
        paths, status, script_path, settings_sha, "re-run synthesis first"
    )
    if problems:
        raise ApproveError(problems[0])
    status.update(status="approved", approved_at=_now())
    if by:
        status["by"] = by
    else:
        status.pop("by", None)
    write_atomic(paths.status, status)
    return status


def create_engine(cfg: VoiceConfig):
    return KokoroVoice(cfg.model, cfg.raw.get("voice_blends"))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.synthesize_voices",
        description="Speak a reviewed episode script with the local voice model (spec 002).",
        allow_abbrev=False,
    )
    parser.add_argument(
        "command", nargs="?", default="synthesize", choices=("synthesize", "approve", "download")
    )
    parser.add_argument("--book", help="book slug, e.g. the-snail-mail-express")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="confirm a person has reviewed the episode script (spec 001 AC-7.4)",
    )
    parser.add_argument("--force", action="store_true", help="re-synthesize every line")
    args = parser.parse_args(argv)
    if args.command != "download" and not args.book:
        parser.error("--book is required for synthesis and approval")
    return args


def _synthesize(
    args: argparse.Namespace,
    cfg: VoiceConfig,
    book: dict,
    script_path: Path,
    paths: AudioPaths,
) -> int:
    if not script_path.exists():
        print(f"error: episode script not found: {script_path}", file=sys.stderr)
        return 1
    emotions = load_emotions(args.root / "config")  # bad feelings fail here, at start
    script = json.loads(script_path.read_text(encoding="utf-8"))
    terms = load_disallowed_terms(args.root / "config" / "disallowed_terms.txt")
    errors = validate_script(script, book, terms, emotions=emotions.names)
    if errors:
        print(f"error: {script_path} fails validation:", file=sys.stderr)
        for problem in errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if not args.reviewed:
        print(
            "error: the episode script must be reviewed by a person before voice synthesis "
            f"(spec 001 AC-7.4). After reviewing {script_path}, re-run with --reviewed.",
            file=sys.stderr,
        )
        return 1
    prons = load_pronunciations(book, args.book)
    jobs = plan_jobs(script, cfg, prons, emotions)
    print(f"synthesizing {len(jobs)} lines into {paths.root}")
    outcomes = synthesize_clips(
        jobs,
        paths,
        lambda: create_engine(cfg),
        cfg,
        force=args.force,
        shift=lambda audio, sr, semitones, speed, gain_db: pitch_shift(
            audio, sr, semitones, speed, gain_db
        ),
        on_progress=lambda o: (
            o.synthesized
            and print(f"  {o.job.line_id}: {o.measure.duration:.1f} s, attempt {o.attempts}")
        ),
    )
    names, rebuilt = build_tracks(outcomes, paths, cfg)
    build_preview(names, paths, cfg)
    any_synthesized = any(o.synthesized for o in outcomes)
    script_sha, settings_sha = script_sha256(script_path), settings_hash(cfg, script, prons)
    status = next_status(
        paths,
        script_sha,
        settings_sha,
        current_preview_key(paths),
        any_synthesized,
        track_hashes(paths, names),
    )
    write_review(paths, outcomes, names, script["title"], status["status"])
    write_atomic(paths.status, status)  # status.json is the commit point: written last
    synthesized = sum(o.synthesized for o in outcomes)
    print(
        f"synthesized {synthesized}, reused {len(outcomes) - synthesized} clips; "
        f"rebuilt {len(rebuilt)} of {len(names)} tracks; status: {status['status']}"
    )
    if status["status"] != "approved":
        print(REVIEW_NOTICE.format(preview=paths.preview, tracks=paths.tracks, book=args.book))
    return 0


def current_settings_sha(
    cfg: VoiceConfig, book: dict, book_path: Path, script_path: Path
) -> str | None:
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None  # approve then reports the changed or missing script
    if not isinstance(script, dict) or not isinstance(script.get("cast"), dict):
        return None
    return settings_hash(cfg, script, load_pronunciations(book, book_path))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths: AudioPaths | None = None
    try:
        cfg = load_voice_config(args.root / "config" / "voices.json")
        if args.command == "download":
            download_model(cfg.model)
            create_engine(cfg)
            print(f"voice model ready: {cfg.model.repo}@{cfg.model.revision}")
            return 0
        folder = book_folder(args.book, args.root)
        book = load_book(folder.book_json)
        script_path = folder.script_path
        paths = AudioPaths(folder.build_dir / "audio")
        if args.command == "approve":
            approve(
                paths, script_path, current_settings_sha(cfg, book, folder.book_json, script_path)
            )
            print(f"approved the audio in {paths.root}")
            return 0
        return _synthesize(args, cfg, book, script_path, paths)
    except (
        BookError,
        ConfigError,
        VoiceUnavailable,
        PitchShiftError,
        ApproveError,
        LineError,
        LibraryError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LineFailed as exc:
        print(f"error: {exc}; nothing stitched:", file=sys.stderr)
        for job, problems in exc.failures:
            print(f"  - {describe(job)}: {'; '.join(problems)}", file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            where = paths.root if paths else args.root
            print(f"error: not enough disk space to write audio in {where}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
