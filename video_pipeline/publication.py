"""Giggle & Snooze publication design: tokens, colour maths, ramp and page math (spec 023).

Pure: no reportlab or Pillow. Drawing lives in `publication_render.py`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.library import PROJECT_ROOT

CONFIG_PATH = Path("config") / "publication.json"

REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "colors": (
        "giggle",
        "snooze",
        "ink",
        "ink_soft",
        "moon",
        "moon_soft",
        "day",
        "dusk",
        "night",
        "paper",
    ),
    "fonts": ("display", "body", "body_bold"),
    "ramp": ("scale", "night_threshold", "night_floor"),
    "page": (
        "safe_u",
        "fade_u",
        "folio_gap_u",
        "words_max",
        "column_chars",
        "size_floor",
        "scrim_alpha_edge",
        "scrim_alpha_text",
        "title_gap_u",
    ),
    "type": (
        "story_u",
        "story_leading",
        "title_u",
        "folio_u",
        "cover_title_u",
        "cover_sub_u",
        "display_u",
        "lead_u",
        "small_u",
        "list_u",
        "blurb_u",
    ),
    "logo": ("cover_u", "plain_u"),
    "dpi": ("print", "digital", "epub_px"),
}

RGB = tuple[int, int, int]
LCH = tuple[float, float, float]

# The words block sits in a band at the top or bottom of the picture (fractions of its height).
TOP_BAND = (0.0, 0.4)
BOTTOM_BAND = (0.6, 1.0)
LINE_BOX = 1.25  # title line height, as a multiple of the title size


class PublicationError(ValueError):
    """A design, font or page problem. The message names the file and the key or page."""


# ---------------------------------------------------------------- config


def load_design(root: Path = PROJECT_ROOT) -> dict:
    path = root / CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PublicationError(f"{path}: file not found") from None
    except (OSError, json.JSONDecodeError) as err:
        raise PublicationError(f"{path}: cannot read ({err})") from err
    if not isinstance(data, dict):
        raise PublicationError(f"{path}: expected a JSON object")
    for section, keys in REQUIRED_KEYS.items():
        block = data.get(section)
        if not isinstance(block, dict):
            raise PublicationError(f"{path}: missing key '{section}'")
        for key in keys:
            if key not in block:
                raise PublicationError(f"{path}: missing key '{section}.{key}'")
    return data


# ---------------------------------------------------------------- colour


def oklch_to_srgb(lch: Sequence[float]) -> RGB:
    """OKLCH -> OKLab -> linear sRGB -> gamma sRGB, clipped and rounded to 0-255."""
    lightness, chroma, hue = lch
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )
    r, g, bl = (round(_encode(c) * 255) for c in linear)
    return (r, g, bl)


def _encode(linear: float) -> float:
    c = min(1.0, max(0.0, linear))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def mix_oklch(a: Sequence[float], b: Sequence[float], w: float) -> LCH:
    """Like CSS color-mix(in oklch, a (1-w), b w): linear L and C, hue along the shorter arc."""
    delta = (b[2] - a[2] + 180) % 360 - 180
    return (
        a[0] + (b[0] - a[0]) * w,
        a[1] + (b[1] - a[1]) * w,
        (a[2] + delta * w) % 360,
    )


def relative_luminance(rgb: Sequence[int]) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1: Sequence[int], rgb2: Sequence[int]) -> float:
    """WCAG 2 contrast ratio, 1 to 21."""
    hi, lo = sorted((relative_luminance(rgb1), relative_luminance(rgb2)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- ramp and tone


def ramp(n: int, total: int, design: dict) -> float:
    """Day -> night position of story page n of total: 0 on page 1, eased so early pages stay bright."""
    r = design["ramp"]
    x = (n - 1) / (total - 1) if total > 1 else 0.0
    t = x * x * r["scale"]
    if t >= r["night_threshold"]:
        t = max(t, r["night_floor"])
    return round(t, 3)


@dataclass(frozen=True)
class Tone:
    """The colours of one page: solid scrim colour, ink, softer ink, and whether it is night."""

    scrim: RGB
    ink: RGB
    ink_soft: RGB
    night: bool


def page_tone(t: float, design: dict) -> Tone:
    colors = design["colors"]
    r = design["ramp"]
    night = t >= r["night_threshold"]
    if night:
        t = max(t, r["night_floor"])
    a = min(1.0, max(0.0, 2 * t))
    b = min(1.0, max(0.0, 2 * t - 1))
    scrim = mix_oklch(mix_oklch(colors["day"], colors["dusk"], a), colors["night"], b)
    ink_key, soft_key = ("moon", "moon_soft") if night else ("ink", "ink_soft")
    return Tone(
        scrim=oklch_to_srgb(scrim),
        ink=oklch_to_srgb(colors[ink_key]),
        ink_soft=oklch_to_srgb(colors[soft_key]),
        night=night,
    )


# ---------------------------------------------------------------- crop and focus


def crop_box(
    src_w: int,
    src_h: int,
    dst_w: float,
    dst_h: float,
    focus_x: float,
    focus_y: float = 0.5,
) -> tuple[int, int, int, int]:
    """Largest source region with the destination aspect, centred on the focus, inside the source."""
    aspect = dst_w / dst_h
    if src_w / src_h > aspect:
        crop_w, crop_h = src_h * aspect, float(src_h)
    else:
        crop_w, crop_h = float(src_w), src_w / aspect
    x0 = min(max(focus_x * src_w - crop_w / 2, 0.0), src_w - crop_w)
    y0 = min(max(focus_y * src_h - crop_h / 2, 0.0), src_h - crop_h)
    left, top = round(x0), round(y0)
    return (
        left,
        top,
        min(src_w, left + round(crop_w)),
        min(src_h, top + round(crop_h)),
    )


def subject_box(
    plan: dict | None, picture_sha: str | None
) -> tuple[float, float, float, float] | None:
    """Union of the two largest layer boxes of an animation plan, as fractions of the picture.

    None when there is no usable plan for this picture. Never raises.
    """
    try:
        if not isinstance(plan, dict) or plan["picture"]["sha256"] != picture_sha:
            return None
        width, height = float(plan["picture"]["width"]), float(plan["picture"]["height"])
        if width <= 0 or height <= 0:
            return None
        boxes = []
        for layer in plan["layers"]:
            x0, y0, x1, y1 = (float(v) for v in layer["box"])
            boxes.append((max(0.0, (x1 - x0) * (y1 - y0)), x0, y0, x1, y1))
        if not boxes:
            return None
        biggest = sorted(boxes, reverse=True)[:2]
        x0 = min(b[1] for b in biggest) / width
        y0 = min(b[2] for b in biggest) / height
        x1 = max(b[3] for b in biggest) / width
        y1 = max(b[4] for b in biggest) / height
        return (x0, y0, x1, y1)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _overlap(lo: float, hi: float, band: tuple[float, float]) -> float:
    return max(0.0, min(hi, band[1]) - max(lo, band[0]))


def _layout_side(page: dict) -> str:
    """`full_bleed_card_top_*` keeps its words card at the top; every other layout at the bottom.

    (`image_top_text_bottom` has the picture on top, so its words are at the bottom.)
    """
    return "top" if str(page.get("layout", "")).startswith("full_bleed_card_top") else "bottom"


def choose_focus_side(page: dict, plan: dict | None, picture_sha: str | None) -> tuple[float, str]:
    """Where to centre the crop (0-1) and which end of the page the words take.

    The subject comes from the animation plan when it matches the picture; otherwise the crop is
    centred and the side follows the page's `layout`. `print_layout` overrides what it sets.
    """
    box = subject_box(plan, picture_sha)
    if box is None:
        focus_x, side = 0.5, _layout_side(page)
    else:
        focus_x = (box[0] + box[2]) / 2
        top = _overlap(box[1], box[3], TOP_BAND)
        bottom = _overlap(box[1], box[3], BOTTOM_BAND)
        if abs(top - bottom) < 1e-9:
            side = _layout_side(page)
        else:
            side = "top" if top < bottom else "bottom"
    override = page.get("print_layout")
    if isinstance(override, dict):
        fx = override.get("focus_x")
        if isinstance(fx, (int, float)) and not isinstance(fx, bool) and 0 <= fx <= 1:
            focus_x = float(fx)
        if override.get("words") in ("top", "bottom"):
            side = override["words"]
    return focus_x, side


# ---------------------------------------------------------------- words


def wrap_lines(text: str, width: float, measure: Callable[[str], float]) -> list[str]:
    """Greedy word wrap. A word wider than the column gets its own line."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and measure(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class WordsFit:
    """A page's words laid out: the scale used, sizes, column width, lines and block height (pt)."""

    scale: float
    size: float
    title_size: float
    column: float
    title_lines: list[str]
    text_lines: list[str]
    height: float


def _scales(floor: float) -> list[float]:
    scales = []
    step = 1
    while (s := round(1 - 0.05 * step, 2)) > floor + 1e-9:
        scales.append(s)
        step += 1
    scales.append(floor)
    return scales


def fit_words(
    title: str,
    text: str,
    *,
    measure: Callable[[str, str, float], float],
    base_size: float,
    title_size: float,
    leading: float,
    title_gap: float,
    column_chars: int,
    full_column: float,
    max_height: float,
    floor: float,
    label: str,
) -> WordsFit:
    """Fit title + text in `max_height`: narrow column, then full column, then smaller sizes."""

    def attempt(scale: float, column: float) -> WordsFit:
        size, t_size = base_size * scale, title_size * scale
        title_lines = wrap_lines(title, column, lambda s: measure(s, "body_bold", t_size))
        text_lines = wrap_lines(text, column, lambda s: measure(s, "body", size))
        height = (
            len(title_lines) * t_size * LINE_BOX
            + (title_gap * scale if title_lines else 0.0)
            + len(text_lines) * size * leading
        )
        return WordsFit(scale, size, t_size, column, title_lines, text_lines, height)

    narrow = min(full_column, measure("x" * column_chars, "body", base_size))
    tries = [(1.0, narrow), (1.0, full_column)] + [(s, full_column) for s in _scales(floor)]
    smallest = None
    for scale, column in tries:
        fit = attempt(scale, column)
        if fit.height <= max_height:
            return fit
        smallest = fit
    assert smallest is not None
    raise PublicationError(
        f"{label}: words do not fit: {smallest.height:.0f} pt needed at {floor:.0%} size, "
        f"{max_height:.0f} pt available; shorten the text"
    )
