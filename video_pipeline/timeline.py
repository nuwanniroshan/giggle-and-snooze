"""Stage 3 timeline (spec 003): render config, captions, sections, camera math and cache keys.

Pure: no I/O beyond reading the render configuration JSON and hashing the caption fonts. Camera
coordinates are fractions of the 16:9 canvas: `z` is the zoom (1 = whole canvas), `cx`/`cy` the
centre of the visible crop.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

from video_pipeline.contract import TAG_RE, ConfigError
from video_pipeline.file_hash import sha256_file

Point = tuple[float, float, float]  # (z, cx, cy)
SectionKind = Literal["intro", "page", "beat", "outro"]

CENTRE: Point = (1.0, 0.5, 0.5)
# Part of every section, transition and final-video cache key: bump it when a code change alters
# the rendered pixels or sound, so stale clips are re-rendered.
RENDER_VERSION = 3  # 2: readable caption breaks (spec 003 v1.1.0); 3: sentence ends win, dashes
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,!?;:…)\]’”])")


# --- render configuration -------------------------------------------------------------------


@dataclass(frozen=True)
class MotionSettings:
    push_zoom: float
    pan_zoom: float
    pan_travel: float
    qa_ease: float
    intro_zoom: float
    outro_zoom: float
    outro_fade: float


@dataclass(frozen=True)
class BackdropSettings:
    blur: float
    darken: float


@dataclass(frozen=True)
class CaptionStyle:
    font_size: int
    max_chars: int
    max_lines: int
    band_opacity: float
    margin: int
    padding: int
    font: str
    fallback_font: str
    min_card_seconds: float = 1.2  # AC-4.6; optional in render.json


@dataclass(frozen=True)
class AudioSettings:
    peak_dbfs: float
    sfx_gain_db: float
    ambience_gain_db: float = -20.0  # spec 007/012: dB below the reference voice level
    limiter_ms: float = 30.0  # spec 012: look-ahead of the peak limiter on the summed mix
    voice_gain_db: float = 6.0  # spec 012 FR-6: gain on the voice tracks before mixing
    ambience_fade: float = 1.5  # seconds, centred on the page change
    loop_crossfade: float = 1.0  # seconds, where a short sound repeats


@dataclass(frozen=True)
class VideoSettings:
    crf: int
    preset: str
    audio_bitrate: str
    audio_rate: int


@dataclass(frozen=True)
class EffectDefaults:
    """Per-effect defaults for the story-page animation (spec 010 FR-8), in the units of the
    ADR-010 spike (px are at a 1024 px shorter side and scale by side/1024): `count` particles at
    intensity 1; `speed` short sides per second for moving particles, or Hz / drift rate for
    distortion and haze; `size` px (streak length, dot, wavelength, noise or glow softness);
    `strength` opacity 0–1, px displacement (`water_ripple`) or additive gain (`glow`)."""

    count: int = 0
    speed: float = 0.0
    size: float = 0.0
    strength: float = 0.0
    colour: str = "#ffffff"


# The recommended per-effect defaults (tuned in the ADR-010 spike, also in config/render.json),
# used when render.json's animation block has no `effects` map (spec 010 FR-8).
DEFAULT_EFFECTS: dict[str, EffectDefaults] = {
    "rain": EffectDefaults(count=260, speed=1.6, size=40.0, strength=0.6, colour="#dfe6f0"),
    "snow": EffectDefaults(count=160, speed=0.12, size=3.0, strength=0.85, colour="#ffffff"),
    "fireflies": EffectDefaults(count=40, speed=0.25, size=7.0, strength=0.9, colour="#ffe08a"),
    "falling_leaves": EffectDefaults(
        count=14, speed=0.08, size=14.0, strength=1.0, colour="#c8862e"
    ),
    "water_ripple": EffectDefaults(count=0, speed=0.5, size=38.0, strength=4.0, colour="#ffffff"),
    "smoke": EffectDefaults(count=6, speed=0.03, size=80.0, strength=0.35, colour="#d9d4cc"),
    "mist": EffectDefaults(count=0, speed=0.01, size=220.0, strength=0.25, colour="#e8eef2"),
    "glow": EffectDefaults(count=0, speed=0.2, size=60.0, strength=0.25, colour="#fff2c4"),
}


@dataclass(frozen=True)
class AnimationSettings:
    """The `animation` block of render.json (spec 010 FR-8). It never enters a cache key here:
    animated sections add their own plan identity to their keys (spec 010 NFR-5)."""

    enabled: bool = True
    intensity: float = 1.0
    ease_in_s: float = 1.0
    parallax_max_shift: float = 0.006  # fraction of the picture's shorter side
    parallax_period_s: float = 9.0
    override_min: float = 0.25
    override_max: float = 2.0
    effects: dict[str, EffectDefaults] = field(default_factory=lambda: dict(DEFAULT_EFFECTS))


@dataclass(frozen=True)
class GradeSettings:
    """The final grade (spec 013 FR-1): colour, sharpness, vignette, fades and loudness. The
    defaults are the hand-enhanced Pip episode (`manual-edits/pip/NOTES.md`); neutral values
    (contrast 1, saturation 1, gamma 1, brightness 0, balance 0, amounts 0) add no filter."""

    enabled: bool = True  # master switch
    picture: bool = True  # picture grade on/off
    sound: bool = True  # sound grade on/off
    contrast: float = 1.05
    saturation: float = 1.12
    gamma: float = 1.02
    brightness: float = 0.0
    rs: float = 0.0  # colour balance: shadows, midtones, highlights x red, green, blue
    gs: float = 0.0
    bs: float = 0.0
    rm: float = 0.0
    gm: float = 0.0
    bm: float = 0.0
    rh: float = 0.0
    gh: float = 0.0
    bh: float = 0.0
    unsharp_size: int = 5  # odd box size, luma and chroma
    unsharp_amount: float = 0.5  # luma amount; 0 and chroma 0 = no sharpening
    unsharp_chroma_amount: float = 0.0
    vignette_angle: float = 0.448799  # radians (PI/7); 0 = none
    picture_fade_in: float = 0.8  # seconds; 0 = no fade
    picture_fade_out: float = 2.0
    sound_fade_in: float = 0.8
    sound_fade_out: float = 2.0
    highpass_hz: float = 70.0  # 0 = off
    compressor_threshold_db: float = -24.0
    compressor_ratio: float = 3.0  # 1 = no compression
    compressor_attack_ms: float = 15.0
    compressor_release_ms: float = 250.0
    compressor_makeup: float = 2.0  # linear gain
    loudness_lufs: float = -16.0
    true_peak_dbfs: float = -1.5
    loudness_range: float = 7.0


_GRADE_BOOLS = ("enabled", "picture", "sound")
_BALANCE = ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh")
# name -> (low, high) inclusive; fades are seconds (negative refused), the rest as named.
GRADE_RANGES: dict[str, tuple[float, float]] = {
    "contrast": (0.1, 3.0),
    "saturation": (0.0, 3.0),
    "gamma": (0.1, 3.0),
    "brightness": (-0.5, 0.5),
    **dict.fromkeys(_BALANCE, (-1.0, 1.0)),
    "unsharp_size": (3, 23),
    "unsharp_amount": (-2.0, 5.0),
    "unsharp_chroma_amount": (-2.0, 5.0),
    "vignette_angle": (0.0, math.pi / 2),
    "picture_fade_in": (0.0, 30.0),
    "picture_fade_out": (0.0, 30.0),
    "sound_fade_in": (0.0, 30.0),
    "sound_fade_out": (0.0, 30.0),
    "highpass_hz": (0.0, 1000.0),
    "compressor_threshold_db": (-60.0, 0.0),
    "compressor_ratio": (1.0, 20.0),
    "compressor_attack_ms": (0.01, 2000.0),
    "compressor_release_ms": (0.01, 9000.0),
    "compressor_makeup": (1.0, 64.0),
    "loudness_lufs": (-70.0, -5.0),
    "true_peak_dbfs": (-9.0, 0.0),
    "loudness_range": (1.0, 20.0),
}


def parse_grade(block: object, where: str, base: GradeSettings | None = None) -> GradeSettings:
    """`base` (default: the built-in defaults) with the settings in `block` applied. `where` names
    the file and block ("config/render.json: grade"); an unknown name, a wrong type or a value
    outside its range raises ConfigError naming `where.field` and what is allowed (spec 013 FR-3)."""
    base = base or GradeSettings()
    if block is None:
        return base
    if not isinstance(block, dict):
        raise ConfigError(f"{where} must be an object")
    known = {f.name for f in fields(GradeSettings)}
    unknown = sorted(set(block) - known)
    if unknown:
        raise ConfigError(
            f"{where}: unknown setting {', '.join(repr(u) for u in unknown)} "
            f"(allowed: {', '.join(sorted(known))})"
        )
    values: dict[str, object] = {}
    for name, value in block.items():
        if name in _GRADE_BOOLS:
            if not isinstance(value, bool):
                raise ConfigError(f"{where}.{name} must be true or false, got {value!r}")
            values[name] = value
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ConfigError(f"{where}.{name} must be a number, got {value!r}")
        low, high = GRADE_RANGES[name]
        if not low <= value <= high:
            raise ConfigError(f"{where}.{name} must be between {low:g} and {high:g}, got {value!r}")
        if name == "unsharp_size":
            if value != int(value) or int(value) % 2 == 0:
                raise ConfigError(f"{where}.{name} must be an odd whole number, got {value!r}")
            values[name] = int(value)
        else:
            values[name] = float(value)
    return GradeSettings(**{**asdict(base), **values})


@dataclass(frozen=True)
class RenderConfig:
    width: int
    height: int
    fps: int
    upscale: int
    lead_in: float
    hold: float
    transition: float
    transition_style: str
    back_cover_seconds: float
    motion: MotionSettings
    backdrop: BackdropSettings
    captions: CaptionStyle
    audio: AudioSettings
    video: VideoSettings
    animation: AnimationSettings = field(default_factory=AnimationSettings)
    channel_name: str = ""
    channel_intro: str = ""  # a branded clip played before the cover reveal ("" = none)
    channel_logo: str = ""  # the channel logo, a badge on the video cover ("" = none, spec 015)
    channel_open_clip: str = ""  # bedtime clip played after the channel intro ("" = none)
    channel_close_clip: str = ""  # bedtime clip played after the last section ("" = none)
    grade: GradeSettings = field(default_factory=GradeSettings)  # spec 013 (shared settings)
    channel_blend_seconds: float = 1.5  # cross-dissolve at each join of logo/clips/body (spec 012)


_NESTED = {
    "motion": MotionSettings,
    "backdrop": BackdropSettings,
    "captions": CaptionStyle,
    "audio": AudioSettings,
    "video": VideoSettings,
}


def _typed(cls: type, data: dict) -> object:
    values = {}
    for f in fields(cls):
        caster = {"int": int, "float": float, "str": str}.get(str(f.type))
        if f.name not in data:
            continue  # optional field: the dataclass default applies
        values[f.name] = caster(data[f.name]) if caster else data[f.name]
    return cls(**values)


def load_render_config(path: Path) -> RenderConfig:
    """Read `config/render.json`; a missing key raises ConfigError naming it (dotted)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"render configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"render configuration is not valid JSON: {path} ({exc})") from None
    missing: list[str] = []
    for f in fields(RenderConfig):
        if f.name in (
            "animation",
            "channel_name",
            "channel_intro",
            "channel_logo",
            "channel_open_clip",
            "channel_close_clip",
            "channel_blend_seconds",
            "grade",
        ):
            continue  # optional block, parsed by _animation_settings
        if f.name not in data:
            missing.append(f.name)
        elif f.name in _NESTED:
            block = data[f.name]
            if not isinstance(block, dict):
                missing.append(f.name)
                continue
            missing += [
                f"{f.name}.{g.name}"
                for g in fields(_NESTED[f.name])
                if g.name not in block and g.default is MISSING
            ]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}")
    nested = {name: _typed(cls, data[name]) for name, cls in _NESTED.items()}
    nested["animation"] = _animation_settings(path, data.get("animation", {}))
    nested["grade"] = parse_grade(data.get("grade"), f"{path}: grade")
    config = _typed(RenderConfig, data | nested)
    min_card = config.captions.min_card_seconds
    if not math.isfinite(min_card) or min_card < 0:
        raise ConfigError(
            f"{path}: captions.min_card_seconds must be a number ≥ 0, got {min_card!r}"
        )
    return config


_COLOUR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _number(path: Path, dotted: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ConfigError(f"{path}: {dotted} must be a number, got {value!r}")
    return float(value)


def _effect_defaults(path: Path, name: str, block: object) -> EffectDefaults:
    where = f"animation.effects.{name}"
    if not isinstance(block, dict):
        raise ConfigError(f"{path}: {where} must be an object")
    values: dict[str, object] = {}
    for key in ("speed", "size", "strength"):
        if key in block:
            values[key] = _number(path, f"{where}.{key}", block[key])
            if values[key] < 0:
                raise ConfigError(f"{path}: {where}.{key} must be ≥ 0, got {block[key]!r}")
    if "count" in block:
        count = block["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ConfigError(f"{path}: {where}.count must be a whole number ≥ 0, got {count!r}")
        values["count"] = count
    if "colour" in block:
        colour = block["colour"]
        if not isinstance(colour, str) or not _COLOUR_RE.fullmatch(colour):
            raise ConfigError(f"{path}: {where}.colour must be #rrggbb, got {colour!r}")
        values["colour"] = colour
    return EffectDefaults(**values)


def _animation_settings(path: Path, block: object) -> AnimationSettings:
    """Parse the optional `animation` block (spec 010 FR-8); a bad value raises ConfigError
    naming the dotted field. A missing block or field takes the dataclass default."""
    # imported here: motion_plan pulls in the agent runner and Pillow, which timeline must not
    from video_pipeline.motion_plan import EFFECTS

    if not isinstance(block, dict):
        raise ConfigError(f"{path}: animation must be an object")
    values: dict[str, object] = {}
    if "enabled" in block:
        if not isinstance(block["enabled"], bool):
            raise ConfigError(f"{path}: animation.enabled must be true or false")
        values["enabled"] = block["enabled"]
    for f in fields(AnimationSettings):
        if f.name in ("enabled", "effects") or f.name not in block:
            continue
        values[f.name] = _number(path, f"animation.{f.name}", block[f.name])
    settings = AnimationSettings(**values)
    for name in ("intensity", "parallax_period_s", "override_min"):
        if getattr(settings, name) <= 0:
            raise ConfigError(f"{path}: animation.{name} must be > 0, got {block[name]!r}")
    for name in ("ease_in_s", "parallax_max_shift"):
        if getattr(settings, name) < 0:
            raise ConfigError(f"{path}: animation.{name} must be ≥ 0, got {block[name]!r}")
    if settings.override_min > settings.override_max:
        raise ConfigError(
            f"{path}: animation.override_min ({settings.override_min}) must not exceed "
            f"animation.override_max ({settings.override_max})"
        )
    if "effects" not in block:
        return settings
    effects = block["effects"]
    if not isinstance(effects, dict):
        raise ConfigError(f"{path}: animation.effects must be an object")
    for name in effects:
        if name not in EFFECTS:
            raise ConfigError(
                f"{path}: unknown effect animation.effects.{name} (one of {', '.join(EFFECTS)})"
            )
    missing = [name for name in EFFECTS if name not in effects]
    if missing:
        raise ConfigError(
            f"{path}: missing animation.effects.{', animation.effects.'.join(missing)}"
        )
    parsed = {name: _effect_defaults(path, name, effects[name]) for name in EFFECTS}
    return AnimationSettings(**{**values, "effects": parsed})


# --- captions -------------------------------------------------------------------------------


def strip_tags(text: str) -> str:
    """Caption text: every `<...>` tag removed, whitespace collapsed, no space before punctuation."""
    plain = " ".join(TAG_RE.sub(" ", text).split())
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", plain)


# A dash joining two words with no spaces ("Falcon—the") may end a line or a card: the token
# splits right after the dash, and the halves rejoin with no space when they stay together.
_DASH_SPLIT_RE = re.compile(r"(?<=[^\s—–][—–])(?=[^\s—–])")


def _tokens(text: str) -> list[tuple[str, bool]]:
    """Break units of `text`: (token, attached), where `attached` marks the part after a dash
    inside a word, which joins the previous token without a space."""
    tokens: list[tuple[str, bool]] = []
    for word in text.split():
        for index, part in enumerate(_DASH_SPLIT_RE.split(word)):
            tokens.append((part, index > 0))
    return tokens


def _join(tokens: list[tuple[str, bool]]) -> str:
    """Text of consecutive tokens; a leading attached token starts the text with no space."""
    return "".join(
        part if attached or index == 0 else f" {part}"
        for index, (part, attached) in enumerate(tokens)
    )


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap into lines of at most `max_chars`; an over-long word gets its own line.
    A line may also end right after a dash inside a word ("Falcon—" / "the")."""
    lines: list[str] = []
    current = ""
    for part, attached in _tokens(text):
        candidate = f"{current}{'' if attached else ' '}{part}" if current else part
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = part
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class CaptionCard:
    text: str
    start: float
    end: float


_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)\]]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—–-][\"'”’)\]]*$")
# Title abbreviations end in a full stop but never end a sentence ("Mrs. Hedgehog").
_ABBREVIATIONS = frozenset({"mr.", "mrs.", "ms.", "dr.", "st.", "mt.", "prof.", "sr.", "jr."})
# Caption break order (AC-4.5): the fewest cards always win; among those, splits compare
# lexicographically by (breaks not after a sentence end, bare word breaks, imbalance), so one
# more sentence-end break always beats any number of clause breaks and any balance gain, and the
# squared imbalance of card lengths only chooses between splits with the same kinds of break.


def _break_kind(word: str) -> str:
    """How good a card break after `word` is: a sentence end, clause punctuation, or neither."""
    if _SENTENCE_END_RE.search(word) and word.lower() not in _ABBREVIATIONS:
        return "sentence"
    if _CLAUSE_END_RE.search(word):
        return "clause"
    return "word"


def _partition(
    tokens: list[tuple[str, bool]],
    max_chars: int,
    max_lines: int,
    allowed: Callable[[int, int], bool],
) -> list[tuple[int, int]] | None:
    """Best split of `tokens` into contiguous cards [i, j) that each wrap into ≤ `max_lines`
    lines and pass `allowed(i, j)`: fewest cards, then fewest breaks that are not sentence ends,
    then fewest bare word breaks, then card lengths closest to even. None when no split exists."""
    n = len(tokens)
    # fits[i] = largest j such that tokens[i:j] fits one card (wrapping is monotone in j).
    fits = []
    for i in range(n):
        j = i + 1
        while j < n and len(wrap_lines(_join(tokens[i : j + 1]), max_chars)) <= max_lines:
            j += 1
        fits.append(j)
    # Fewest cards first: the card count is what fixes the even length every card aims for.
    fewest: list[float] = [0.0] + [math.inf] * n
    for j in range(1, n + 1):
        for i in range(j):
            if j <= fits[i] and allowed(i, j) and fewest[i] + 1 < fewest[j]:
                fewest[j] = fewest[i] + 1
    if math.isinf(fewest[n]):
        return None
    count = int(fewest[n])
    ideal = len(_join(tokens)) / count
    worst = (math.inf, math.inf, math.inf)
    best: list[list[tuple[float, float, float]]] = [[worst] * (n + 1) for _ in range(count + 1)]
    back: list[list[int]] = [[-1] * (n + 1) for _ in range(count + 1)]
    best[0][0] = (0.0, 0.0, 0.0)
    for cards in range(1, count + 1):
        for j in range(1, n + 1):
            for i in range(j):
                if math.isinf(best[cards - 1][i][0]) or j > fits[i] or not allowed(i, j):
                    continue
                mid_sentence, word, balance = best[cards - 1][i]
                balance += ((len(_join(tokens[i:j])) - ideal) / max_chars) ** 2
                if j < n:
                    kind = _break_kind(tokens[j - 1][0])
                    mid_sentence += kind != "sentence"
                    word += kind == "word"
                cost = (mid_sentence, word, balance)
                if cost < best[cards][j]:
                    best[cards][j], back[cards][j] = cost, i
    spans: list[tuple[int, int]] = []
    j = n
    for cards in range(count, 0, -1):
        i = back[cards][j]
        spans.append((i, j))
        j = i
    return spans[::-1]


def caption_cards(
    text: str,
    start: float,
    end: float,
    max_chars: int,
    max_lines: int,
    *,
    min_seconds: float = 1.2,
) -> list[CaptionCard]:
    """Split `text` into cards of ≤ `max_lines` wrapped lines, timed by word share (AC-4.3).

    A line that fits one card stays whole. Otherwise the fewest cards are used; among those, a
    card ends mid-sentence only when no split with more sentence-end breaks exists, then clause
    punctuation (a dash inside "Falcon—the" included) beats a bare word gap, and card lengths
    are kept even (AC-4.5). No card is a single word or shorter than `min_seconds` (AC-4.6);
    when the line is too fast for that, the duration rule is relaxed first, then the one-word
    rule, but a card never exceeds `max_lines`.
    """
    tokens = _tokens(text)
    if not tokens:
        return []
    # Word share of each token: the halves of a dash-joined word ("Falcon—", "the") split one
    # word between them, so timing and the one-word rule count real words.
    parts = [len(_DASH_SPLIT_RE.split(word)) for word in text.split()]
    weights = [1 / count for count in parts for _ in range(count)]
    cumulative = [0.0]
    for weight in weights:
        cumulative.append(cumulative[-1] + weight)
    n = len(tokens)
    total = cumulative[n]
    span = end - start
    if len(wrap_lines(text, max_chars)) <= max_lines:
        spans = [(0, n)]
    else:

        def several_words(i: int, j: int) -> bool:
            return cumulative[j] - cumulative[i] > 1 + 1e-9

        def readable(i: int, j: int) -> bool:
            share = (cumulative[j] - cumulative[i]) / total
            return several_words(i, j) and span * share >= min_seconds - 1e-9

        spans = (
            _partition(tokens, max_chars, max_lines, readable)
            or _partition(tokens, max_chars, max_lines, several_words)
            or _partition(tokens, max_chars, max_lines, lambda i, j: True)
        )
        assert spans is not None  # one token per card always fits
    cards: list[CaptionCard] = []
    card_start = start
    for index, (i, j) in enumerate(spans):
        card_end = end if index == len(spans) - 1 else start + span * cumulative[j] / total
        cards.append(CaptionCard(_join(tokens[i:j]), card_start, card_end))
        card_start = card_end
    return cards


# --- camera ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Camera:
    kind: str
    frames: int
    start: Point
    end: Point
    qa_frame: int | None = None
    qa_target: Point | None = None
    ease_frames: int = 0
    fade_out_frames: int = 0


def narration_camera(motion: str, cfg: RenderConfig) -> tuple[Point, Point]:
    m = cfg.motion
    if motion == "static":
        return CENTRE, CENTRE
    if motion == "slow_push_in":
        return CENTRE, (m.push_zoom, 0.5, 0.5)
    if motion == "slow_pull_out":
        return (m.push_zoom, 0.5, 0.5), CENTRE
    left: Point = (m.pan_zoom, 0.5 + m.pan_travel, 0.5)
    right: Point = (m.pan_zoom, 0.5 - m.pan_travel, 0.5)
    if motion == "pan_left":
        return left, right
    if motion == "pan_right":
        return right, left
    raise ValueError(f"unknown narration motion: {motion}")


def smoothstep(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def _clamp_point(point: Point) -> Point:
    """Zoom at least 1 and the crop centre kept so the crop stays inside the canvas."""
    z = max(1.0, point[0])
    lo, hi = 1 / (2 * z), 1 - 1 / (2 * z)
    return (z, min(max(point[1], lo), hi), min(max(point[2], lo), hi))


def _lerp(a: Point, b: Point, s: float) -> Point:
    return (a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s, a[2] + (b[2] - a[2]) * s)


def _narration_span(cam: Camera) -> int:
    return max(1, (cam.qa_frame if cam.qa_frame is not None else cam.frames) - 1)


def camera_at(cam: Camera, frame: int) -> Point:
    """The camera at a frame of the section. Endpoints are clamped first, so the interpolated path
    stays inside the canvas and moves monotonically; the final clamp only guards rounding."""
    start, end = _clamp_point(cam.start), _clamp_point(cam.end)
    if cam.qa_frame is None or cam.qa_target is None or frame < cam.qa_frame:
        p = min(max(frame / _narration_span(cam), 0.0), 1.0)
        z, cx, cy = _lerp(start, end, p * p * (3 - 2 * p))
    else:
        q = min(max((frame - cam.qa_frame) / max(1, cam.ease_frames), 0.0), 1.0)
        z, cx, cy = _lerp(end, _clamp_point(cam.qa_target), q * q * (3 - 2 * q))
    lo, hi = 1 / (2 * z), 1 - 1 / (2 * z)
    return (z, min(max(cx, lo), hi), min(max(cy, lo), hi))


def _num(value: float) -> str:
    text = repr(float(value))
    if "e" in text or "inf" in text or "nan" in text:
        text = f"{float(value):.17f}"
    return f"({text})" if text.startswith("-") else text


def _smooth_expr(p: str) -> str:
    return f"({p}*{p}*(3-2*{p}))"


def _interp_expr(a: float, b: float, s: str) -> str:
    return f"{_num(a)}+{_num(b - a)}*{s}"


def zoompan_expr(cam: Camera) -> tuple[str, str, str]:
    """FFmpeg zoompan `z`, `x`, `y` expressions computing exactly what `camera_at` computes.

    Uses `on` (output frame index), `iw`, `ih` and `zoom` only; x/y are the crop's top-left in
    input pixels (`cx*iw - iw/zoom/2`). No quotes inside, so each can be wrapped in '...'.
    """
    start, end = _clamp_point(cam.start), _clamp_point(cam.end)
    p = f"clip(on/{_narration_span(cam)},0,1)"
    s_narr = _smooth_expr(p)
    narr = [_interp_expr(start[i], end[i], s_narr) for i in range(3)]
    if cam.qa_frame is None or cam.qa_target is None:
        values = narr
    else:
        target = _clamp_point(cam.qa_target)
        q = f"clip((on-{cam.qa_frame})/{max(1, cam.ease_frames)},0,1)"
        s_qa = _smooth_expr(q)
        values = [
            f"if(lt(on,{cam.qa_frame}),{narr[i]},{_interp_expr(end[i], target[i], s_qa)})"
            for i in range(3)
        ]
    bounds = "1/(2*zoom),1-1/(2*zoom)"
    z_expr = values[0]
    x_expr = f"clip({values[1]},{bounds})*iw-iw/zoom/2"
    y_expr = f"clip({values[2]},{bounds})*ih-ih/zoom/2"
    return z_expr, x_expr, y_expr


def qa_target_on_canvas(target: dict, cfg: RenderConfig) -> Point:
    """Map a Q&A target in illustration fractions to canvas fractions (picture centred, full height)."""
    side = cfg.height
    left = (cfg.width - side) / 2
    return (
        float(target["zoom"]),
        (left + float(target["x"]) * side) / cfg.width,
        float(target["y"]),
    )


def contain_size(image: tuple[int, int], size: tuple[int, int]) -> tuple[int, int]:
    """The size `ImageOps.contain(image, size)` resizes to (Pillow's arithmetic, so the still
    canvas stays byte-identical)."""
    im_ratio = image[0] / image[1]
    dest_ratio = size[0] / size[1]
    if im_ratio == dest_ratio:
        return size
    if im_ratio > dest_ratio:
        return size[0], round(image[1] / image[0] * size[0])
    return round(image[0] / image[1] * size[1]), size[1]


def grounded_target_on_canvas(
    target: dict, picture_size: tuple[int, int], cfg: RenderConfig
) -> Point:
    """Map a grounded Q&A target (x, y in picture fractions) to canvas fractions, for a picture
    drawn `contain`-fit and centred (spec 025). Equals `qa_target_on_canvas` for square pictures."""
    front_w, front_h = contain_size(picture_size, (cfg.width, cfg.height))
    left, top = (cfg.width - front_w) / 2, (cfg.height - front_h) / 2
    return (
        float(target["zoom"]),
        (left + float(target["x"]) * front_w) / cfg.width,
        (top + float(target["y"]) * front_h) / cfg.height,
    )


# --- sections -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    name: str
    kind: SectionKind
    picture: str
    frames: int
    track: str | None
    captions: list[CaptionCard] = field(default_factory=list)
    camera: Camera = field(default_factory=lambda: Camera("static", 1, CENTRE, CENTRE))


def _ceil_frames(seconds: float, fps: int) -> int:
    return math.ceil(round(seconds * fps, 6))


def page_section_name(page_number: int) -> str:
    return f"page-{page_number:02d}"


def default_pictures(script: dict) -> dict[str, str]:
    pictures = {"intro": "cover_front", "back-cover": "cover_back", "outro": "cover_back"}
    for page in script["pages"]:
        number = int(page["page_number"])
        pictures[page_section_name(number)] = f"page_{number:02d}"
    return pictures


def _section_captions(timing: dict, cfg: RenderConfig) -> list[CaptionCard]:
    style = cfg.captions
    cards: list[CaptionCard] = []
    for line in timing["lines"]:
        cards += caption_cards(
            strip_tags(line["text"]),
            cfg.lead_in + float(line["start"]),
            cfg.lead_in + float(line["end"]),
            style.max_chars,
            style.max_lines,
            min_seconds=style.min_card_seconds,
        )
    return cards


def _track_frames(timing: dict, cfg: RenderConfig) -> int:
    return _ceil_frames(cfg.lead_in + float(timing["duration"]) + cfg.hold, cfg.fps)


def page_section(page: dict, timing: dict, cfg: RenderConfig, picture: str) -> Section:
    """One story page's section from its script page and its track's timing alone: the same
    section `build_sections` makes for it (spec 010 FR-15, the page preview)."""
    name = page_section_name(int(page["page_number"]))
    frames = _track_frames(timing, cfg)
    camera_cfg = page.get("ffmpeg_camera", {})
    motion = camera_cfg.get("narration_motion", "static")
    start, end = narration_camera(motion, cfg)
    qa_line = next((line for line in timing["lines"] if line.get("role") != "narration"), None)
    target = camera_cfg.get("qa_zoom_target")
    qa_frame = qa_target = None
    if qa_line is not None and target:
        qa_frame = round((cfg.lead_in + float(qa_line["start"])) * cfg.fps)
        qa_target = qa_target_on_canvas(target, cfg)
    return Section(
        name,
        "page",
        picture,
        frames,
        name,
        _section_captions(timing, cfg),
        Camera(
            motion,
            frames,
            start,
            end,
            qa_frame,
            qa_target,
            round(cfg.motion.qa_ease * cfg.fps) if qa_frame is not None else 0,
        ),
    )


def build_sections(
    script: dict,
    timings: dict[str, dict],
    cfg: RenderConfig,
    pictures: dict[str, str] | None = None,
) -> list[Section]:
    """Sections in order: intro, pages by page number, back-cover beat, outro.

    `timings` maps track name (`intro`, `page-PP`, `outro`) to its timing JSON; `pictures` maps a
    section name to its logical picture id (defaults: front cover, `page_PP`, back cover).
    """
    chosen = default_pictures(script) | dict(pictures or {})

    def timing(name: str) -> dict:
        if name not in timings:
            raise ValueError(f"no timing for track {name}")
        return timings[name]

    def frames_for(name: str) -> int:
        return _track_frames(timing(name), cfg)

    m = cfg.motion
    sections: list[Section] = []
    frames = frames_for("intro")
    sections.append(
        Section(
            "intro",
            "intro",
            chosen["intro"],
            frames,
            "intro",
            _section_captions(timing("intro"), cfg),
            Camera("intro", frames, CENTRE, (m.intro_zoom, 0.5, 0.5)),
        )
    )
    for page in sorted(script["pages"], key=lambda p: int(p["page_number"])):
        name = page_section_name(int(page["page_number"]))
        sections.append(page_section(page, timing(name), cfg, chosen[name]))
    frames = round(cfg.back_cover_seconds * cfg.fps)
    sections.append(
        Section(
            "back-cover",
            "beat",
            chosen["back-cover"],
            frames,
            None,
            [],
            Camera("static", frames, CENTRE, CENTRE),
        )
    )
    frames = frames_for("outro")
    # The fade starts no earlier than the end of the last spoken outro line.
    outro_lines = timing("outro")["lines"]
    last_end = max((float(line["end"]) for line in outro_lines), default=0.0)
    fade_frames = max(
        0,
        min(round(m.outro_fade * cfg.fps), frames - _ceil_frames(cfg.lead_in + last_end, cfg.fps)),
    )
    sections.append(
        Section(
            "outro",
            "outro",
            chosen["outro"],
            frames,
            "outro",
            _section_captions(timing("outro"), cfg),
            Camera(
                "outro",
                frames,
                (m.outro_zoom, 0.5, 0.5),
                CENTRE,
                fade_out_frames=fade_frames,
            ),
        )
    )
    return sections


def transition_frames(cfg: RenderConfig) -> int:
    return round(cfg.transition * cfg.fps)


def section_times(sections: list[Section], cfg: RenderConfig) -> list[tuple[float, float]]:
    """(start, end) seconds of each section in the final video; one transition between each pair."""
    times: list[tuple[float, float]] = []
    frame = 0
    for index, section in enumerate(sections):
        if index:
            frame += transition_frames(cfg)
        times.append((frame / cfg.fps, (frame + section.frames) / cfg.fps))
        frame += section.frames
    return times


def chapter_starts(sections: list[Section], cfg: RenderConfig, offset_s: float) -> list[dict]:
    """Each section's name and start in the final video (spec 024 AC-2.3): its body start
    shifted by `offset_s`, the length of what plays before the body, rounded to 3 places."""
    return [
        {"name": section.name, "start": round(start + offset_s, 3)}
        for section, (start, _) in zip(sections, section_times(sections, cfg), strict=True)
    ]


def total_frames(sections: list[Section], cfg: RenderConfig) -> int:
    return sum(s.frames for s in sections) + max(0, len(sections) - 1) * transition_frames(cfg)


# --- cache keys -----------------------------------------------------------------------------


def _hash(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _font_identity(path: str) -> str:
    """sha256 of a font file (so replacing the file re-renders captions), else its path."""
    font = Path(path)
    if font.is_file():
        return sha256_file(font)
    return path


def _frame_settings(cfg: RenderConfig) -> dict:
    return {
        "render_version": RENDER_VERSION,
        "width": cfg.width,
        "height": cfg.height,
        "fps": cfg.fps,
        "upscale": cfg.upscale,
        "video": asdict(cfg.video),
    }


def section_key(section: Section, picture_sha: str, cfg: RenderConfig) -> str:
    """Identity of a section clip: everything that changes its pixels (AC-7.2)."""
    return _hash(
        {
            "kind": section.kind,
            "picture_sha": picture_sha,
            "frames": section.frames,
            "captions": [asdict(card) for card in section.captions],
            "camera": asdict(section.camera),
            "backdrop": asdict(cfg.backdrop),
            "caption_style": asdict(cfg.captions),
            "caption_fonts": [
                _font_identity(cfg.captions.font),
                _font_identity(cfg.captions.fallback_font),
            ],
            **_frame_settings(cfg),
        }
    )


def transition_key(key_a: str, key_b: str, cfg: RenderConfig) -> str:
    return _hash(
        {
            "from": key_a,
            "to": key_b,
            "transition": cfg.transition,
            "style": cfg.transition_style,
            **_frame_settings(cfg),
        }
    )
