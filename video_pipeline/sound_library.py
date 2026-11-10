"""Spec 007: the CC0 sound library (`assets/sound_library/`), its index, `list` and `check`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from video_pipeline.timeline import AudioSettings, Section

AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac"}
INDEX = "index.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "assets" / "sound_library"


class LibraryError(Exception):
    """The sound library index is missing or invalid, or a sound is unknown."""


class _Entry(BaseModel):
    file: str
    title: str
    author: str
    url: str
    kind: Literal["ambience", "music", "sfx"]
    seconds: float
    license: str | None = None


class _Index(BaseModel):
    license: str
    sounds: dict[str, _Entry]


@dataclass(frozen=True)
class Sound:
    name: str
    file: Path
    kind: str
    seconds: float
    author: str
    license: str


@dataclass(frozen=True)
class Library:
    root: Path
    sounds: dict[str, Sound]

    def get(self, name: str) -> Sound:
        try:
            return self.sounds[name.lower()]
        except KeyError:
            raise LibraryError(f"unknown sound {name!r}") from None


def load_library(root: Path) -> Library:
    """Read and validate `<root>/index.json`; fail fast naming the file and field."""
    root = Path(root)
    path = root / INDEX
    try:
        index = _Index.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"sound library index not found: {path}") from None
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LibraryError(f"{path}: {exc}") from None
    sounds = {
        name.lower(): Sound(
            name.lower(),
            root / entry.file,
            entry.kind,
            entry.seconds,
            entry.author,
            entry.license or index.license,
        )
        for name, entry in index.sounds.items()
    }
    return Library(root, sounds)


def check_library(root: Path) -> list[str]:
    """Return the library's problems: bad index, missing files, orphans, non-CC0 licences."""
    try:
        lib = load_library(root)
    except LibraryError as exc:
        return [str(exc)]
    problems = [
        f"{s.name}: file {s.file.name} is missing"
        for s in lib.sounds.values()
        if not s.file.is_file()
    ]
    problems += [
        f"{s.name}: license is not CC0 ({s.license})"
        for s in lib.sounds.values()
        if "CC0" not in s.license
    ]
    listed = {s.file.name for s in lib.sounds.values()}
    problems += [
        f"{p.name}: audio file has no index entry"
        for p in sorted(Path(root).iterdir())
        if p.suffix.lower() in AUDIO_SUFFIXES and p.name not in listed
    ]
    return problems


# --- cues and bed mixing (spec 007 FR-3) --------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    """One sound playing from `start` to `end` seconds of the final video."""

    sound: Sound
    start: float
    end: float


def _block(script: dict, section: str) -> dict:
    if section in ("intro", "outro"):
        return script[section]
    number = int(section.removeprefix("page-"))
    return next(p for p in script["pages"] if p["page_number"] == number)


def _names(script: dict, section: str, previous: list[str]) -> list[str]:
    if section == "back-cover":
        return previous  # the back cover keeps what the last page had
    return [n.lower() for n in _block(script, section).get("ambience", [])]


def ambience_problems(script: dict, library: Library) -> list[str]:
    """One line per `ambience` name that is not in the library, naming the page."""
    blocks = [
        ("intro", script["intro"]),
        *[(f"page {p['page_number']}", p) for p in script["pages"]],
        ("outro", script["outro"]),
    ]
    return [
        f"{where}: unknown sound {name!r}"
        for where, block in blocks
        for name in block.get("ambience", [])
        if name.lower() not in library.sounds
    ]


def build_cues(
    script: dict,
    sections: list[Section],
    times: list[tuple[float, float]],
    library: Library,
) -> list[Cue]:
    """Cues for the episode: a loopable sound on consecutive sections is one cue (AC-2.4);
    an `sfx` gets its own cue per section so it plays once at each page's start (AC-2.6)."""
    cues: list[Cue] = []
    open_runs: dict[str, int] = {}  # loopable name -> index in cues
    previous: list[str] = []
    for section, (start, end) in zip(sections, times, strict=True):
        names = _names(script, section.name, previous)
        previous = names
        for name in list(open_runs):
            if name not in names:
                del open_runs[name]
        for name in dict.fromkeys(names):
            sound = library.get(name)
            if sound.kind == "sfx":
                cues.append(Cue(sound, start, end))
            elif name in open_runs:
                old = cues[open_runs[name]]
                cues[open_runs[name]] = Cue(old.sound, old.start, end)
            else:
                open_runs[name] = len(cues)
                cues.append(Cue(sound, start, end))
    return cues


def decode_sound(sound: Sound, sample_rate: int) -> np.ndarray:
    """The sound file as mono float32 at `sample_rate`; failures name the sound."""
    from video_pipeline import frames  # lazy: frames pulls in the TTS-side modules

    try:
        return frames._decode_with_ffmpeg(sound.file, sample_rate)
    except frames.SoundError as exc:
        raise LibraryError(f"sound {sound.name!r}: {exc}") from None


def loop_fill(sound: np.ndarray, n: int, xf: int) -> np.ndarray:
    """`sound` repeated to exactly n samples, crossfading xf samples at each join."""
    if len(sound) >= n:
        return sound[:n].astype(np.float64)
    if len(sound) == 0:
        return np.zeros(n)
    xf = max(0, min(xf, len(sound) // 2))
    ramp = np.linspace(0.0, 1.0, xf)
    out = np.empty(n)
    out[: len(sound)] = sound
    pos = len(sound)
    while pos < n:
        out[pos - xf : pos] = out[pos - xf : pos] * (1 - ramp) + sound[:xf] * ramp
        take = min(len(sound) - xf, n - pos)
        out[pos : pos + take] = sound[xf : xf + take]
        pos += take
    return out


REFERENCE_VOICE_DBFS = -20.0  # nominal speech RMS; cue levels are "dB below the voices"
LOOP_PEAK_CEILING_DBFS = -14.0
SFX_PEAK_CEILING_DBFS = -8.0


def normalize_sound(
    data: np.ndarray, target_rms_db: float, peak_ceiling_db: float, max_boost_db: float = 36.0
) -> np.ndarray:
    """`data` scaled to `target_rms_db` RMS, boosted by at most `max_boost_db`. The ceiling gain
    uses the 99.9th percentile of |x|, so a rare spike or sparse transient does not decide the
    level; the output is then hard-limited to twice the ceiling (about +6 dB), so every sample
    stays at or below `peak_ceiling_db` + 6 dB. Silence is returned unchanged."""
    x = np.asarray(data, dtype=np.float64)
    peak = float(np.percentile(np.abs(x), 99.9)) if len(x) else 0.0
    # The RMS ignores spikes beyond twice that peak, which the output limiter removes anyway.
    rms = float(np.sqrt(np.mean(np.square(np.clip(x, -2 * peak, 2 * peak))))) if len(x) else 0.0
    if rms < 1e-9 or peak < 1e-9:
        return x
    ceiling = 10 ** (peak_ceiling_db / 20)
    gain = min(10 ** (target_rms_db / 20) / rms, 10 ** (max_boost_db / 20), ceiling / peak)
    return np.clip(x * gain, -2 * ceiling, 2 * ceiling)


def _normalized(kind: str, data: np.ndarray, audio: AudioSettings) -> np.ndarray:
    """The decoded sound at its cue level; `music` shares the ambience setting until it gets
    its own."""
    if kind == "sfx":
        target, ceiling = REFERENCE_VOICE_DBFS + audio.sfx_gain_db, SFX_PEAK_CEILING_DBFS
    else:
        target, ceiling = REFERENCE_VOICE_DBFS + audio.ambience_gain_db, LOOP_PEAK_CEILING_DBFS
    return normalize_sound(data, target, ceiling)


def render_bed(
    cues: list[Cue],
    decoded: dict[str, np.ndarray],
    length: int,
    sample_rate: int,
    audio: AudioSettings,
) -> np.ndarray:
    """Mono float64 bed of `length` samples. Loopable cues fade in and out centred on their
    boundaries (so neighbours crossfade), but not beyond the episode's start and end; an `sfx`
    plays once from its start, cut with a 0.1 s fade if its section ends first."""
    bed = np.zeros(length)
    fade = max(1, round(audio.ambience_fade * sample_rate))
    half = fade // 2
    xf = round(audio.loop_crossfade * sample_rate)
    for cue in cues:
        data = _normalized(cue.sound.kind, decoded[cue.sound.name], audio)
        start = round(cue.start * sample_rate)
        end = min(length, round(cue.end * sample_rate))
        if end <= start:
            continue
        if cue.sound.kind == "sfx":
            tail = min(len(data), end - start)
            piece = data[:tail].copy()
            ramp = min(tail, round(0.1 * sample_rate))
            if tail < len(data) and ramp:  # cut short by the section, so fade the cut
                piece[-ramp:] *= np.linspace(1.0, 0.0, ramp)
            bed[start : start + tail] += piece
            continue
        rise_from = start - half if start > 0 else start
        fall_to = end + half if end < length else end
        lo, hi = max(0, rise_from), min(length, fall_to)
        t = np.arange(lo, hi)
        env = np.clip((t - rise_from) / fade, 0, 1) * np.clip((fall_to - t) / fade, 0, 1)
        bed[lo:hi] += loop_fill(data, hi - lo, xf) * env
    return bed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sound_library")
    parser.add_argument("command", choices=["list", "check"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    if args.command == "check":
        problems = check_library(args.root)
        for line in problems:
            print(line)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    try:
        lib = load_library(args.root)
    except LibraryError as exc:
        print(exc, file=sys.stderr)
        return 1
    for s in sorted(lib.sounds.values(), key=lambda s: (s.kind, s.name)):
        print(f"{s.name:18} {s.kind:9} {s.seconds:7.1f}s  {s.author}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
