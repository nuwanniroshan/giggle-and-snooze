# Plan: spec 007 – sound library and ambience in episodes

## Context
Spec `docs/specs/007-sound-library/spec.md` (Approved). 26 CC0 sounds already sit in `assets/sound_library/` with `index.json`. Nothing uses them. Add: a library module with `list`/`check`, an optional `ambience` list in `episode_script.json`, and a numpy ambience bed mixed into `master.wav`.

Findings that shape the plan:
- Audio is mixed once (`frames.mix_master` → `build/video/audio/master.wav`); video sections are silent clips keyed by `timeline.section_key`. So a sound change rebuilds only master audio + final mux; video clips stay reused. Spec AC-4.1 says "audio and video sections" – fix wording first (sdd-spec-update).
- `render_episode._final_key` is the final-video key; add ambience to it **only when present**, so existing renders stay up to date (AC-1.4).
- `timeline._typed` lets optional dataclass fields keep defaults → new audio settings are optional.
- Later background music (memory `background-music-later`): `Cue` carries the sound's `kind`; gains are looked up per kind, so a `music` layer is a new gain + scope, not a rewrite.

Gates: ≤3 components (sound_library.py, contract/render changes, config); no new dependency (numpy, pydantic, soundfile exist); tests first, no real ffmpeg or network in unit tests.

## Files
- New `video_pipeline/sound_library.py`: index loading, `check`, CLI, cues, bed mixing.
- New `tests/test_sound_library.py`.
- Edit `video_pipeline/contract.py` (validate `ambience`), `video_pipeline/timeline.py` (`AudioSettings` fields), `video_pipeline/frames.py` (`mix_master(..., ambience=None)`), `video_pipeline/render_episode.py` (inputs, key, bed), `config/render.json`, `CLAUDE.md`, `docs/specs/007-sound-library/spec.md` (AC-4.1 wording).
- Tests extended: `tests/test_contract.py`, `tests/test_frames.py`, `tests/test_render_*.py`.

## Phase 1 – Library, `list`, `check`
**Implements:** FR-1, FR-4 | **Satisfies:** AC-3.1–3.4 | **Interfaces:** `load_library(root) -> Library`, `Library.get(name) -> Sound`, `check_library(root) -> list[str]`

Tests (write first, `tests/test_sound_library.py`; helper `make_lib(tmp_path, entries)` writes `index.json` and empty audio files):
- loads valid index; `get("rain")` returns `Sound(kind="ambience", seconds=…)`; unknown name → `LibraryError` containing the name.
- `check_library` returns `[]` for a good library; reports missing file, orphan `*.mp3` without entry, and a non-CC0 license (per-entry `license` overrides the index's top-level one).
- invalid JSON / bad `kind` → `LibraryError` naming `index.json` and the field.
- `main(["check", "--root", d])` exits 1 with problems, 0 without; `main(["list", ...])` prints name, kind, length, author.

Code:
```python
# video_pipeline/sound_library.py
from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ValidationError

AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac"}
INDEX = "index.json"

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
    root = Path(root)
    path = root / INDEX
    try:
        index = _Index.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"sound library index not found: {path}") from None
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LibraryError(f"{path}: {exc}") from None
    sounds = {
        name.lower(): Sound(name.lower(), root / e.file, e.kind, e.seconds, e.author,
                            e.license or index.license)
        for name, e in index.sounds.items()
    }
    return Library(root, sounds)

def check_library(root: Path) -> list[str]:
    try:
        lib = load_library(root)
    except LibraryError as exc:
        return [str(exc)]
    problems = [f"{s.name}: file {s.file.name} is missing"
                for s in lib.sounds.values() if not s.file.is_file()]
    problems += [f"{s.name}: license is not CC0 ({s.license})"
                 for s in lib.sounds.values() if "CC0" not in s.license]
    listed = {s.file.name for s in lib.sounds.values()}
    problems += [f"{p.name}: audio file has no index entry"
                 for p in sorted(Path(root).iterdir())
                 if p.suffix.lower() in AUDIO_SUFFIXES and p.name not in listed]
    return problems

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "assets" / "sound_library"

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
```
Verify: `uv run pytest tests/test_sound_library.py -v` (expect all PASSED); `uv run python -m video_pipeline.sound_library check` → `ok`, exit 0 (real library). Commit.

## Phase 2 – `ambience` field in the script
**Implements:** FR-2 | **Satisfies:** AC-1.5 (type part), AC-1.4 | **Interfaces:** `contract._ambience_errors(where, block) -> list[str]`

Tests (`tests/test_contract.py`, using the existing valid-script fixture there): script with `"ambience": ["rain_soft"]` on intro, a page and outro validates; `"ambience": "rain"` (string), `[""]`, `[3]` each give an error containing `page 3: ambience` (or `intro:`/`outro:`); script without the field still valid.

Code in `contract.py`, called from `_bookend_errors` (with `name`) and the page loop in `_page_errors` (with `f"page {number}"`):
```python
def _ambience_errors(where: str, block: dict) -> list[str]:
    if "ambience" not in block:
        return []
    names = block["ambience"]
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        return [f"{where}: ambience must be a list of sound names"]
    return []
```
Verify: `uv run pytest tests/test_contract.py -v`. Commit.

## Phase 3 – Cues and bed mixing (pure numpy)
**Implements:** FR-3 | **Satisfies:** AC-1.1–1.3, AC-2.1–2.6 | **Interfaces:** `AudioSettings` + 3 fields, `build_cues(script, sections, times, library) -> list[Cue]`, `ambience_problems(script, library) -> list[str]`, `loop_fill(sound, n, xf)`, `render_bed(cues, decoded, length, sample_rate, audio) -> ndarray`, `mix_master(..., ambience=None)`

Config: `timeline.AudioSettings` gets optional `ambience_gain_db: float = -22.0`, `ambience_fade: float = 1.5`, `loop_crossfade: float = 1.0`; add the same keys to `config/render.json` `audio`. (Test: `load_render_config` reads them; a config without them still loads.)

Tests (`tests/test_sound_library.py`, sections built with `timeline.Section`, 1 s of 100 Hz sine as "sound", sample rate 8000):
- `build_cues`: page 3 `["rain"]`, page 4 `["rain"]` → one cue spanning both sections (AC-2.4); page 5 `["wind"]` → rain cue ends at page 4's end, wind starts at page 5's start; `back-cover` inherits the last page's list; sfx on a page → its own cue per page.
- `ambience_problems`: unknown name → `["page 3: unknown sound 'rainy'"]`.
- `loop_fill`: output length n; a 1-second sound filled to 3.5 s has no sample-to-sample jump larger than the sound's own max step ×2 at the joins (AC-2.2); sound longer than n is cut.
- `render_bed`: silent outside cue windows; level inside equals `10**(gain/20)` × sound RMS within 2%; ramp is monotone across a boundary between two different cues and the two envelopes sum to ~1 at the boundary midpoint (AC-2.3); a loop cue starting at 0 begins at 0 amplitude; sfx cue plays once at section start then silence, cut with a 0.1 s fade at section end.
- `mix_master`: `ambience=None` returns exactly the old output (AC-1.4); with a bed, peak ≤ limit and speech samples unchanged when the bed is zero (AC-2.5).

Code:
```python
@dataclass(frozen=True)
class Cue:
    sound: Sound
    start: float  # seconds in the final video
    end: float

def _names(script: dict, section: str, previous: list[str]) -> list[str]:
    if section == "back-cover":
        return previous
    if section in ("intro", "outro"):
        block = script[section]
    else:
        number = int(section.removeprefix("page-"))
        block = next(p for p in script["pages"] if p["page_number"] == number)
    return [n.lower() for n in block.get("ambience", [])]

def ambience_problems(script: dict, library: Library) -> list[str]:
    problems = []
    blocks = [("intro", script["intro"]), *[(f"page {p['page_number']}", p) for p in script["pages"]],
              ("outro", script["outro"])]
    for where, block in blocks:
        for name in block.get("ambience", []):
            if name.lower() not in library.sounds:
                problems.append(f"{where}: unknown sound {name!r}")
    return problems

def build_cues(script, sections, times, library) -> list[Cue]:
    cues: list[Cue] = []
    open_runs: dict[str, int] = {}   # loopable name -> index in cues
    previous: list[str] = []
    for section, (start, end) in zip(sections, times, strict=True):
        names = _names(script, section.name, previous)
        previous = names
        for name in list(open_runs):
            if name not in names:
                del open_runs[name]
        for name in names:
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

def loop_fill(sound: np.ndarray, n: int, xf: int) -> np.ndarray:
    """`sound` repeated to exactly n samples, crossfading xf samples at each join."""
    if len(sound) >= n:
        return sound[:n].astype(np.float64)
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

def _db(db: float) -> float:
    return 10 ** (db / 20)

def render_bed(cues, decoded, length, sample_rate, audio) -> np.ndarray:
    """Mono float64 ambience bed, `length` samples (spec 007 FR-3)."""
    bed = np.zeros(length)
    fade = max(1, round(audio.ambience_fade * sample_rate))
    half = fade // 2
    xf = round(audio.loop_crossfade * sample_rate)
    for cue in cues:
        data = decoded[cue.sound.name]
        start, end = round(cue.start * sample_rate), min(length, round(cue.end * sample_rate))
        if cue.sound.kind == "sfx":
            tail = min(len(data), end - start)
            piece = data[:tail].astype(np.float64) * _db(audio.sfx_gain_db)
            ramp = min(tail, round(0.1 * sample_rate))
            if ramp:
                piece[-ramp:] *= np.linspace(1.0, 0.0, ramp)
            bed[start : start + tail] += piece
            continue
        lo = start - half if start > 0 else 0
        hi = end + half if end < length else length
        lo, hi = max(0, lo), min(length, hi)
        t = np.arange(lo, hi)
        rise_from = start - half if start > 0 else start
        fall_to = end + half if end < length else end
        env = np.clip((t - rise_from) / fade, 0, 1) * np.clip((fall_to - t) / fade, 0, 1)
        bed[lo:hi] += loop_fill(data, hi - lo, xf) * env * _db(audio.ambience_gain_db)
    return bed
```
`frames.mix_master` gains `ambience: np.ndarray | None = None` and does `if ambience is not None: mix += ambience[:length]` just before the peak limit. Add `decode_sound(sound, rate)` in `sound_library.py` = `frames._decode_with_ffmpeg(sound.file, rate)`.

Verify: `uv run pytest tests/test_sound_library.py tests/test_frames.py tests/test_timeline.py -v`. Commit.

## Phase 4 – Render integration and cache
**Implements:** FR-3 (cache), FR-1 (offline) | **Satisfies:** AC-1.1–1.5, AC-4.1, AC-4.2 | **Interfaces:** `_Inputs.ambience: list[Cue]`, `_Inputs.ambience_key: list`

Tests (existing render fixtures in `tests/render_helpers.py`; monkeypatch `sound_library.decode_sound` to return a sine; fake runner as in `test_render_cli.py`):
- script with no `ambience` → `_final_key` equals the pre-change value and `mix_master` called with `ambience=None` (AC-1.4).
- page lists `rain` → `master.wav` differs from the no-ambience master and is not louder than the peak limit; video section clips are `reused` on a second render after changing only `ambience` (AC-4.1).
- unknown sound → `render_video` raises `RenderError` mentioning `page 3` and the sound, before any section clip is made; `render_check_detail` returns `("blocked", message)` (AC-1.5).
- changing the library file content (new sha) → `final_key` changes (AC-4.2); changing only an unrelated library file does not.

Code in `render_episode.py`:
- In `_load_inputs`, after `sections` and `times = section_times(sections, cfg)`:
```python
    cues: list[Cue] = []
    if any("ambience" in b for b in [script["intro"], script["outro"], *script["pages"]]):
        try:
            library = load_library(Path(root) / "assets" / "sound_library")
        except LibraryError as exc:
            raise RenderError(str(exc)) from None
        problems = ambience_problems(script, library)
        if problems:
            raise RenderError("; ".join(problems))
        cues = build_cues(script, sections, times, library)
        missing = [c.sound.name for c in cues if not c.sound.file.is_file()]
        if missing:
            raise RenderError(f"sound file missing: {', '.join(sorted(set(missing)))}")
    ambience_key = [[c.sound.name, sha256_file(c.sound.file), c.start, c.end] for c in cues]
```
  and pass `cues`, `ambience_key` as new defaulted `_Inputs` fields.
- `_final_key` payload: `**({"ambience": inputs.ambience_key} if inputs.ambience_key else {})`.
- `render_video`, before `mix_master`:
```python
        decoded = {}
        for cue in inputs.ambience:
            if cue.sound.name not in decoded:
                decoded[cue.sound.name] = decode_sound(cue.sound, sample_rate)
        length = round(total_frames(sections, cfg) / cfg.fps * sample_rate)
        bed = render_bed(inputs.ambience, decoded, length, sample_rate, cfg.audio) if decoded else None
        master_audio = mix_master(sections, tracks, page_turn, cfg, sample_rate, ambience=bed)
```
  and catch `frames.SoundError` for a bad sound file as `RenderError` naming the sound (reuse the existing `except` for page-turn `SoundError` if present).
- Docs: add a CLAUDE.md line (library commands, `ambience` field, "background music: later"); add an `ambience` example to the script description.

Verify: `uv run pytest` (full suite, expect all pass, real-provider tests skipped); `uv run ruff check video_pipeline tests`.

## Integration verification (manual, real files)
1. `uv run python -m video_pipeline.sound_library check` → `ok`; `list` shows 26 sounds.
2. In `books/the-snail-mail-express/episode/episode_script.json` add `"ambience": ["birds_morning"]` to page 1–2, `["rain_soft"]` to a middle page, `["campfire", "door_knock"]` to a later page.
3. `uv run python -m video_pipeline.render_episode --book the-snail-mail-express` → section clips `reused`, only master + mux run; listen to the result around the page changes (fade, no click, voices clearly above).
4. Remove all `ambience` fields → output identical to the earlier render.
5. `uv run pytest -m "ffmpeg"` still passes.

## Notes
- No Freesound client, no network use at render time.
- Music layer (later): add `music_gain_db`, a `scope` for page ranges, same `Cue`/`render_bed` path.
- Library audio (26 MB, CC0) should be committed with `index.json` so a clone has it: confirm when committing.
