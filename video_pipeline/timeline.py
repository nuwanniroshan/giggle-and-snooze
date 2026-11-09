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
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

from video_pipeline.contract import TAG_RE, ConfigError

Point = tuple[float, float, float]  # (z, cx, cy)
SectionKind = Literal["intro", "page", "beat", "outro"]

CENTRE: Point = (1.0, 0.5, 0.5)
# Part of every section, transition and final-video cache key: bump it when a code change alters
# the rendered pixels or sound, so stale clips are re-rendered.
RENDER_VERSION = 1
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


@dataclass(frozen=True)
class AudioSettings:
    peak_dbfs: float
    sfx_gain_db: float


@dataclass(frozen=True)
class VideoSettings:
    crf: int
    preset: str
    audio_bitrate: str
    audio_rate: int


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
        if f.name not in data:
            missing.append(f.name)
        elif f.name in _NESTED:
            block = data[f.name]
            if not isinstance(block, dict):
                missing.append(f.name)
                continue
            missing += [
                f"{f.name}.{g.name}" for g in fields(_NESTED[f.name]) if g.name not in block
            ]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}")
    nested = {name: _typed(cls, data[name]) for name, cls in _NESTED.items()}
    return _typed(RenderConfig, data | nested)


# --- captions -------------------------------------------------------------------------------


def strip_tags(text: str) -> str:
    """Caption text: every `<...>` tag removed, whitespace collapsed, no space before punctuation."""
    plain = " ".join(TAG_RE.sub(" ", text).split())
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", plain)


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap into lines of at most `max_chars`; an over-long word gets its own line."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class CaptionCard:
    text: str
    start: float
    end: float


def caption_cards(
    text: str, start: float, end: float, max_chars: int, max_lines: int
) -> list[CaptionCard]:
    """Split `text` into cards of ≤ `max_lines` wrapped lines, timed by word share (AC-4.3)."""
    lines = wrap_lines(text, max_chars)
    if not lines:
        return []
    groups = [" ".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]
    counts = [len(group.split()) for group in groups]
    total = sum(counts)
    span = end - start
    cards: list[CaptionCard] = []
    done = 0
    card_start = start
    for index, (group, count) in enumerate(zip(groups, counts, strict=True)):
        done += count
        card_end = end if index == len(groups) - 1 else start + span * done / total
        cards.append(CaptionCard(group, card_start, card_end))
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
        )
    return cards


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
        return _ceil_frames(cfg.lead_in + float(timing(name)["duration"]) + cfg.hold, cfg.fps)

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
        frames = frames_for(name)
        camera_cfg = page.get("ffmpeg_camera", {})
        motion = camera_cfg.get("narration_motion", "static")
        start, end = narration_camera(motion, cfg)
        qa_line = next(
            (line for line in timing(name)["lines"] if line.get("role") != "narration"), None
        )
        target = camera_cfg.get("qa_zoom_target")
        qa_frame = qa_target = None
        if qa_line is not None and target:
            qa_frame = round((cfg.lead_in + float(qa_line["start"])) * cfg.fps)
            qa_target = qa_target_on_canvas(target, cfg)
        sections.append(
            Section(
                name,
                "page",
                chosen[name],
                frames,
                name,
                _section_captions(timing(name), cfg),
                Camera(
                    motion,
                    frames,
                    start,
                    end,
                    qa_frame,
                    qa_target,
                    round(m.qa_ease * cfg.fps) if qa_frame is not None else 0,
                ),
            )
        )
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
        return hashlib.sha256(font.read_bytes()).hexdigest()
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
