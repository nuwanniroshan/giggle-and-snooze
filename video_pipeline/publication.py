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
    "dpi": ("print", "digital", "epub_px", "jpeg_quality"),
    "leading": ("display", "lead", "small", "list", "blurb", "cover_sub"),
    "plain": (
        "gap_u",
        "display_ch",
        "lead_ch",
        "small_ch",
        "blurb_ch",
        "item_gap_u",
        "rule_u",
        "rule_width",
        "rule_gap_u",
        "map_title_u",
    ),
    "drawing": ("top_u", "line_u", "radius_u"),
    "cover": ("scrim_band", "scrim_alpha", "scrim_solid", "title_lines", "sub_gap_u"),
    "back": ("scrim_band", "alpha_edge", "alpha_mid", "barcode_u", "barcode_radius_u"),
}

RGB = tuple[int, int, int]
LCH = tuple[float, float, float]

# The words block sits in a band at the top or bottom of the picture (fractions of its height).
TOP_BAND = (0.0, 0.4)
BOTTOM_BAND = (0.6, 1.0)
LINE_BOX = 1.25  # title line height, as a multiple of the title size
LOGO_ALT = "Giggle and Snooze"


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


# ---------------------------------------------------------------- page layouts
#
# One layout per page, drawn by every renderer (`publication_render.py`). All coordinates are
# points from the top-left corner of the page, bleed included.

TRIM_PT = 8.5 * 72
BLEED_PT = 0.125 * 72
BASELINE_DROP = 0.38  # baseline below the middle of a line box, as a multiple of the size
DIGIT_CAP = 0.72  # height of a display-face digit, as a multiple of the size


@dataclass(frozen=True)
class TextRun:
    """One line of text. `y` is the baseline; `x` is the left end, or the right end for "right"."""

    text: str
    role: str  # "display" | "body" | "body_bold"
    size: float
    x: float
    y: float
    color: RGB
    align: str = "left"  # "left" | "right"


@dataclass(frozen=True)
class ImageRun:
    """A picture drawn whole into a top-left box (logo, contained map), with its alt text."""

    path: Path
    x: float
    y: float
    w: float
    h: float
    alt: str = ""


@dataclass(frozen=True)
class Shape:
    """A filled ("rect") or stroked ("frame") rectangle, optionally rounded and dashed."""

    kind: str
    x: float
    y: float
    w: float
    h: float
    color: RGB
    dashed: bool = False
    line: float = 0.0
    radius: float = 0.0


@dataclass(frozen=True)
class Scrim:
    """A full-width colour band over the picture, opaque `alpha_edge` at the page edge.

    "top": alpha_edge at y0, alpha_text at y1 - fade, 0 at y1. "bottom" mirrors it: alpha_edge at
    y1, alpha_text at y0 + fade, 0 at y0. Linear in between.
    """

    y0: float
    y1: float
    side: str
    color: RGB
    alpha_edge: float
    alpha_text: float
    fade: float


@dataclass(frozen=True)
class PageLayout:
    width: float
    height: float
    ground: RGB
    picture: Path | None
    picture_box: tuple[float, float, float, float]
    focus_x: float
    scrims: tuple[Scrim, ...] = ()
    texts: tuple[TextRun, ...] = ()
    images: tuple[ImageRun, ...] = ()
    shapes: tuple[Shape, ...] = ()
    alt: str = ""


@dataclass(frozen=True)
class Frame:
    """Page size and where the 8.5 in trim sits on it (bleed is the rest)."""

    width: float
    height: float
    trim_x: float = 0.0
    trim_y: float = 0.0
    trim: float = TRIM_PT

    @classmethod
    def digital(cls) -> Frame:
        """Digital and reader pages: the trim, no bleed."""
        return cls(TRIM_PT, TRIM_PT)

    @classmethod
    def kdp(cls, *, bleed_left: bool) -> Frame:
        """KDP interior page: bleed on the outer side (left on even pages), top and bottom."""
        return cls(
            TRIM_PT + BLEED_PT, TRIM_PT + 2 * BLEED_PT, BLEED_PT if bleed_left else 0.0, BLEED_PT
        )

    @property
    def u(self) -> float:
        return self.trim / 100

    def safe_box(self, design: dict) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) of the safe area: trim edges inset by `safe_u`."""
        safe = design["page"]["safe_u"] * self.u
        return (
            self.trim_x + safe,
            self.trim_y + safe,
            self.trim_x + self.trim - safe,
            self.trim_y + self.trim - safe,
        )


@dataclass(frozen=True)
class Logo:
    path: Path
    aspect: float  # height / width


Measure = Callable[[str, str, float], float]


def color(design: dict, name: str) -> RGB:
    return oklch_to_srgb(design["colors"][name])


def goodnight_message(book: dict, blueprint: dict | None) -> str:
    """The blueprint's outro hook when there is one, else the book's dedication (AC-3.3)."""
    hook = ((blueprint or {}).get("qa") or {}).get("outro_hook")
    if isinstance(hook, str) and hook.strip():
        return hook.strip()
    return str((book.get("metadata") or {}).get("dedication") or "")


def _baseline(line_top: float, size: float, leading: float) -> float:
    return line_top + size * leading / 2 + BASELINE_DROP * size


# --- stacks of blocks (plain pages)


@dataclass(frozen=True)
class _Text:
    lines: tuple[str, ...]
    role: str
    size: float
    leading: float
    color: RGB
    gap: float = 0.0
    indent: float = 0.0
    marker: str = ""

    @property
    def height(self) -> float:
        return len(self.lines) * self.size * self.leading


@dataclass(frozen=True)
class _Pic:
    path: Path
    w: float
    h: float
    gap: float = 0.0
    alt: str = ""

    @property
    def height(self) -> float:
        return self.h


@dataclass(frozen=True)
class _Bar:
    w: float
    h: float
    color: RGB
    gap: float = 0.0

    @property
    def height(self) -> float:
        return self.h


_Block = _Text | _Pic | _Bar


def _stack_height(blocks: Sequence[_Block]) -> float:
    return sum(b.height + (b.gap if i else 0.0) for i, b in enumerate(blocks))


def _place(
    blocks: Sequence[_Block], x: float, top: float
) -> tuple[list[TextRun], list[ImageRun], list[Shape]]:
    texts: list[TextRun] = []
    images: list[ImageRun] = []
    shapes: list[Shape] = []
    y = top
    for i, block in enumerate(blocks):
        if i:
            y += block.gap
        if isinstance(block, _Text):
            for n, line in enumerate(block.lines):
                base = _baseline(y + n * block.size * block.leading, block.size, block.leading)
                if n == 0 and block.marker:
                    texts.append(
                        TextRun(block.marker, block.role, block.size, x, base, block.color)
                    )
                texts.append(
                    TextRun(line, block.role, block.size, x + block.indent, base, block.color)
                )
        elif isinstance(block, _Pic):
            images.append(ImageRun(block.path, x, y, block.w, block.h, block.alt))
        else:
            shapes.append(Shape("rect", x, y, block.w, block.h, block.color))
        y += block.height
    return texts, images, shapes


def _wrap(text: str, role: str, size: float, width: float, measure: Measure) -> tuple[str, ...]:
    return tuple(wrap_lines(text, width, lambda s: measure(s, role, size)))


def _measure_ch(chars: int, role: str, size: float, measure: Measure) -> float:
    """CSS `ch`: the width of `chars` zeros."""
    return measure("0" * chars, role, size)


def _plain(
    design: dict,
    frame: Frame,
    ground: RGB,
    blocks: Sequence[_Block],
    align: str,
) -> PageLayout:
    x0, y0, _, y1 = frame.safe_box(design)
    height = _stack_height(blocks)
    top = {"start": y0, "center": frame.trim_y + (frame.trim - height) / 2, "end": y1 - height}[
        align
    ]
    texts, images, shapes = _place(blocks, x0, top)
    return PageLayout(
        frame.width,
        frame.height,
        ground,
        None,
        (0.0, 0.0, frame.width, frame.height),
        0.5,
        texts=tuple(texts),
        images=tuple(images),
        shapes=tuple(shapes),
    )


# --- story pages


def layout_story(
    design: dict,
    frame: Frame,
    page: dict,
    *,
    n: int,
    total: int,
    focus_x: float,
    side: str,
    picture: Path | None,
    measure: Measure,
) -> PageLayout:
    """Picture over the whole page, words at the top or bottom on a scrim that hugs them."""
    u = frame.u
    p, ty = design["page"], design["type"]
    tone = page_tone(ramp(n, total, design), design)
    x0, y0, x1, y1 = frame.safe_box(design)
    gap = p["folio_gap_u"] * u
    outer_left = frame.trim_x > 0  # a left-hand print page: bleed and outer edge on the left
    column_x = x0 + gap if outer_left else x0
    fit = fit_words(
        str(page.get("title", "")),
        str(page.get("text", "")),
        measure=measure,
        base_size=ty["story_u"] * u,
        title_size=ty["title_u"] * u,
        leading=ty["story_leading"],
        title_gap=p["title_gap_u"] * u,
        column_chars=p["column_chars"],
        full_column=x1 - gap - x0,
        max_height=p["words_max"] * frame.trim,
        floor=p["size_floor"],
        label=f"page {page.get('page_number', n)}",
    )
    top = y0 if side == "top" else y1 - fit.height
    blocks: list[_Block] = []
    if fit.title_lines:
        blocks.append(
            _Text(tuple(fit.title_lines), "body_bold", fit.title_size, LINE_BOX, tone.ink)
        )
    blocks.append(
        _Text(
            tuple(fit.text_lines),
            "body",
            fit.size,
            ty["story_leading"],
            tone.ink,
            gap=p["title_gap_u"] * u * fit.scale,
        )
    )
    texts, _, _ = _place(blocks, column_x, top)
    folio_size = ty["folio_u"] * u
    folio_y = y1 if side == "bottom" else y0 + DIGIT_CAP * folio_size
    texts.append(
        TextRun(
            str(page.get("page_number", n)),
            "display",
            folio_size,
            x0 if outer_left else x1,
            folio_y,
            tone.ink_soft,
            align="left" if outer_left else "right",
        )
    )
    fade = p["fade_u"] * u
    edge, solid = p["scrim_alpha_edge"], p["scrim_alpha_text"]
    if side == "top":
        scrim = Scrim(0.0, top + fit.height + fade, "top", tone.scrim, edge, solid, fade)
    else:
        scrim = Scrim(top - fade, frame.height, "bottom", tone.scrim, edge, solid, fade)
    return PageLayout(
        frame.width,
        frame.height,
        tone.scrim,
        picture,
        (0.0, 0.0, frame.width, frame.height),
        focus_x,
        scrims=(scrim,),
        texts=tuple(texts),
        alt=str(page.get("prompt", "")),
    )


# --- front and back matter


def _day(design: dict) -> RGB:
    return color(design, "day")


def layout_blank(design: dict, frame: Frame) -> PageLayout:
    return _plain(design, frame, _day(design), [], "start")


def layout_half_title(
    design: dict,
    frame: Frame,
    *,
    title: str,
    subtitle: str,
    bookplate_line: str,
    logo: Logo | None,
    measure: Measure,
) -> PageLayout:
    """Belongs-to page: logo, title, subtitle, the bookplate line and a line to write on."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, _, x1, _ = frame.safe_box(design)
    width = x1 - x0
    gap = plain["gap_u"] * u
    ink, soft = color(design, "ink"), color(design, "ink_soft")
    blocks: list[_Block] = []
    if logo is not None:
        w = design["logo"]["plain_u"] * u
        blocks.append(_Pic(logo.path, w, w * logo.aspect, alt=LOGO_ALT))
    size = ty["display_u"] * u
    display_w = min(width, _measure_ch(plain["display_ch"], "display", size, measure))
    blocks.append(
        _Text(
            _wrap(title, "display", size, display_w, measure),
            "display",
            size,
            lead["display"],
            color(design, "snooze"),
            gap=gap,
        )
    )
    if subtitle:
        size = ty["lead_u"] * u
        lead_w = min(width, _measure_ch(plain["lead_ch"], "body", size, measure))
        blocks.append(
            _Text(
                _wrap(subtitle, "body", size, lead_w, measure),
                "body",
                size,
                lead["lead"],
                ink,
                gap=gap,
            )
        )
    size = ty["small_u"] * u
    blocks.append(
        _Text(
            _wrap(bookplate_line, "body", size, width, measure),
            "body",
            size,
            lead["small"],
            soft,
            gap=gap,
        )
    )
    blocks.append(
        _Bar(plain["rule_width"] * width, plain["rule_u"] * u, soft, gap=plain["rule_gap_u"] * u)
    )
    return _plain(design, frame, _day(design), blocks, "center")


def _small_lines(
    design: dict,
    frame: Frame,
    lines: Sequence[str],
    color_: RGB,
    gap: float,
    measure: Measure,
    *,
    full_width: bool = False,
) -> list[_Block]:
    """Small print, one block per non-empty line; an empty line adds a line of space."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, _, x1, _ = frame.safe_box(design)
    size = ty["small_u"] * u
    width = x1 - x0
    if not full_width:
        width = min(width, _measure_ch(plain["small_ch"], "body", size, measure))
    blocks: list[_Block] = []
    pending = gap
    for line in lines:
        if not line.strip():
            pending += size * lead["small"]
            continue
        wrapped = _wrap(line, "body", size, width, measure)
        blocks.append(_Text(wrapped, "body", size, lead["small"], color_, gap=pending))
        pending = 0.0
    return blocks


def _paragraphs(lines: Sequence[str]) -> list[str]:
    """Rejoin a sentence broken across lines (the next line starts in lower case); an empty
    line stays as a line of space."""
    out: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            if current and not line.strip()[0].islower():
                out.append(" ".join(current))
                current = []
            current.append(line.strip())
            continue
        if current:
            out.append(" ".join(current))
            current = []
        out.append("")
    if current:
        out.append(" ".join(current))
    return out


def layout_title(
    design: dict,
    frame: Frame,
    *,
    dedication: str,
    author: str,
    copyright_lines: Sequence[str],
    measure: Measure,
) -> PageLayout:
    """Title page, bottom-aligned: dedication, author line, copyright small print."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, _, x1, _ = frame.safe_box(design)
    gap = plain["gap_u"] * u
    blocks: list[_Block] = []
    if dedication:
        size = ty["lead_u"] * u
        width = min(x1 - x0, _measure_ch(plain["lead_ch"], "body", size, measure))
        blocks.append(
            _Text(
                _wrap(dedication, "body", size, width, measure),
                "body",
                size,
                lead["lead"],
                color(design, "ink"),
            )
        )
    soft = color(design, "ink_soft")
    blocks += _small_lines(design, frame, [f"By {author}"], color(design, "ink"), gap, measure)
    blocks += _small_lines(design, frame, _paragraphs(copyright_lines), soft, gap, measure)
    return _plain(design, frame, _day(design), blocks, "end")


def layout_map(
    design: dict,
    frame: Frame,
    *,
    title: str,
    subtitle: str,
    legend: Sequence[str],
    picture: Path,
    picture_size: tuple[int, int],
    measure: Measure,
) -> PageLayout:
    """Map page: title and subtitle at the top, the whole map below them, legend at the foot."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, y0, x1, y1 = frame.safe_box(design)
    width = x1 - x0
    gap = plain["gap_u"] * u
    ink = color(design, "ink")
    size = plain["map_title_u"] * u
    head: list[_Block] = [
        _Text(_wrap(title, "display", size, width, measure), "display", size, lead["display"], ink)
    ]
    if subtitle:
        size = ty["list_u"] * u
        head.append(
            _Text(
                _wrap(subtitle, "body", size, width, measure),
                "body",
                size,
                lead["list"],
                color(design, "ink_soft"),
                gap=gap / 2,
            )
        )
    foot = _small_lines(design, frame, legend, ink, 0.0, measure, full_width=True)
    texts, _, _ = _place(head, x0, y0)
    foot_h = _stack_height(foot)
    foot_texts, _, _ = _place(foot, x0, y1 - foot_h)
    top = y0 + _stack_height(head) + gap
    bottom = y1 - foot_h - (gap if foot else 0.0)
    pw, ph = picture_size
    scale = min(width / pw, max(0.0, bottom - top) / ph)
    w, h = pw * scale, ph * scale
    image = ImageRun(
        picture, x0 + (width - w) / 2, top + (bottom - top - h) / 2, w, h, f"Map: {title}"
    )
    return PageLayout(
        frame.width,
        frame.height,
        _day(design),
        None,
        (0.0, 0.0, frame.width, frame.height),
        0.5,
        texts=tuple(texts + foot_texts),
        images=(image,),
    )


def layout_activity(design: dict, frame: Frame, activity: dict, *, measure: Measure) -> PageLayout:
    """Activity page on dusk: title, subtitle, items, questions; scaled down together to fit."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, y0, x1, y1 = frame.safe_box(design)
    width = x1 - x0
    tone = page_tone(0.5, design)
    ink = tone.ink

    def blocks_at(scale: float) -> list[_Block]:
        gap = plain["gap_u"] * u * scale
        item_gap = plain["item_gap_u"] * u * scale
        display = ty["display_u"] * u * scale
        size = ty["list_u"] * u * scale
        # the full safe width (not a measure in ch): long back matter then fits at a larger size
        text_w = display_w = width
        out: list[_Block] = [
            _Text(
                _wrap(str(activity.get("title", "")), "display", display, display_w, measure),
                "display",
                display,
                lead["display"],
                ink,
            )
        ]
        if activity.get("subtitle"):
            out.append(
                _Text(
                    _wrap(str(activity["subtitle"]), "body", size, text_w, measure),
                    "body",
                    size,
                    lead["list"],
                    ink,
                    gap=gap,
                )
            )
        for i, item in enumerate(activity.get("items") or []):
            out.append(
                _Text(
                    _wrap(str(item.get("title", "")), "body_bold", size, text_w, measure),
                    "body_bold",
                    size,
                    lead["list"],
                    ink,
                    gap=gap if i == 0 else item_gap,
                )
            )
            if item.get("text"):
                out.append(
                    _Text(
                        _wrap(str(item["text"]), "body", size, text_w, measure),
                        "body",
                        size,
                        lead["list"],
                        ink,
                    )
                )
        questions = list(activity.get("questions") or [])
        if questions:
            q_size = ty["lead_u"] * u * scale
            out.append(
                _Text(
                    _wrap(
                        str(activity.get("questions_title", "")),
                        "body_bold",
                        q_size,
                        width,
                        measure,
                    ),
                    "body_bold",
                    q_size,
                    lead["list"],
                    ink,
                    gap=gap,
                )
            )
            indent = measure("00. ", "body", size)
            for i, question in enumerate(questions, start=1):
                out.append(
                    _Text(
                        _wrap(str(question), "body", size, text_w - indent, measure),
                        "body",
                        size,
                        lead["list"],
                        ink,
                        gap=item_gap / 2 if i > 1 else gap / 2,
                        indent=indent,
                        marker=f"{i}.",
                    )
                )
        return out

    for scale in [1.0, *_scales(design["page"]["size_floor"])]:
        blocks = blocks_at(scale)
        if _stack_height(blocks) <= y1 - y0:
            return _plain(design, frame, tone.scrim, blocks, "end")
    raise PublicationError(
        f"activity page: does not fit the safe area at {design['page']['size_floor']:.0%} size; "
        "shorten print_extras.activity"
    )


def layout_goodnight(
    design: dict, frame: Frame, *, message: str, logo: Logo | None, measure: Measure
) -> PageLayout:
    """Goodnight page on night: the word, the outro hook (or dedication) and the logo."""
    u, ty, lead, plain = frame.u, design["type"], design["leading"], design["plain"]
    x0, _, x1, _ = frame.safe_box(design)
    tone = page_tone(1.0, design)
    gap = plain["gap_u"] * u
    size = ty["display_u"] * u
    blocks: list[_Block] = [
        _Text(("Goodnight",), "display", size, lead["display"], tone.ink),
    ]
    if message:
        size = ty["lead_u"] * u
        width = min(x1 - x0, _measure_ch(plain["lead_ch"], "body", size, measure))
        blocks.append(
            _Text(
                _wrap(message, "body", size, width, measure),
                "body",
                size,
                lead["lead"],
                tone.ink,
                gap=gap,
            )
        )
    if logo is not None:
        w = design["logo"]["plain_u"] * u
        blocks.append(_Pic(logo.path, w, w * logo.aspect, gap=gap, alt=LOGO_ALT))
    return _plain(design, frame, tone.scrim, blocks, "end")


def layout_drawing(
    design: dict, frame: Frame, *, caption: str, title: str, measure: Measure
) -> PageLayout:
    """A light page with a dashed frame to draw in; caption left and book title right on top."""
    u, size = frame.u, design["type"]["small_u"] * frame.u
    d = design["drawing"]
    x0, y0, x1, y1 = frame.safe_box(design)
    soft = color(design, "ink_soft")
    base = y0 + 0.8 * size
    top = y0 + d["top_u"] * u
    return PageLayout(
        frame.width,
        frame.height,
        _day(design),
        None,
        (0.0, 0.0, frame.width, frame.height),
        0.5,
        texts=(
            TextRun(caption, "body", size, x0, base, soft),
            TextRun(title, "body", size, x1, base, soft, align="right"),
        ),
        shapes=(
            Shape(
                "frame",
                x0,
                top,
                x1 - x0,
                y1 - top,
                color(design, "ink"),
                dashed=True,
                line=d["line_u"] * u,
                radius=d["radius_u"] * u,
            ),
        ),
    )


# --- covers


def layout_front_cover(
    design: dict,
    frame: Frame,
    *,
    title: str,
    subtitle: str,
    picture: Path | None,
    focus_x: float,
    logo: Logo | None,
    publisher: str,
    measure: Measure,
) -> PageLayout:
    """Cover picture full bleed, title and subtitle on a light scrim at the top, logo bottom right."""
    u, ty, lead, cov = frame.u, design["type"], design["leading"], design["cover"]
    x0, y0, x1, y1 = frame.safe_box(design)
    width = x1 - x0
    snooze, ink = color(design, "snooze"), color(design, "ink")
    lines: tuple[str, ...] = ()
    size = ty["cover_title_u"] * u
    for scale in [1.0, *_scales(design["page"]["size_floor"])]:
        size = ty["cover_title_u"] * u * scale
        lines = _wrap(title, "display", size, width, measure)
        if len(lines) <= cov["title_lines"]:
            break
    blocks: list[_Block] = [_Text(lines, "display", size, lead["display"], snooze)]
    if subtitle:
        sub = ty["cover_sub_u"] * u
        blocks.append(
            _Text(
                _wrap(subtitle, "body", sub, width, measure),
                "body",
                sub,
                lead["cover_sub"],
                ink,
                gap=cov["sub_gap_u"] * u,
            )
        )
    texts, _, _ = _place(blocks, x0, y0)
    images: tuple[ImageRun, ...] = ()
    if logo is not None:
        w = design["logo"]["cover_u"] * u
        h = w * logo.aspect
        images = (ImageRun(logo.path, x1 - w, y1 - h, w, h, LOGO_ALT),)
    else:
        texts.append(
            TextRun(publisher, "display", ty["cover_sub_u"] * u * 1.4, x1, y1, snooze, "right")
        )
    band = cov["scrim_band"] * frame.height
    scrim = Scrim(
        0.0,
        band,
        "top",
        _day(design),
        cov["scrim_alpha"],
        cov["scrim_alpha"],
        band * (1 - cov["scrim_solid"]),
    )
    return PageLayout(
        frame.width,
        frame.height,
        _day(design),
        picture,
        (0.0, 0.0, frame.width, frame.height),
        focus_x,
        scrims=(scrim,),
        texts=tuple(texts),
        images=images,
        alt=f"Cover of {title}",
    )


def layout_back_cover(
    design: dict,
    frame: Frame,
    *,
    blurb: str,
    picture: Path | None,
    logo: Logo | None,
    publisher: str,
    measure: Measure,
) -> PageLayout:
    """Back picture full bleed, a night scrim at the bottom with the blurb, logo and barcode slot.

    The blurb is fitted like the activity page (narrow column, then the full safe width, then
    smaller sizes down to the floor) between the safe top and the logo. The scrim is solid from
    the page bottom to the blurb's top, then fades over `fade_u`, never shorter than the band."""
    u, ty, lead, plain, back = (
        frame.u,
        design["type"],
        design["leading"],
        design["plain"],
        design["back"],
    )
    x0, y0, x1, y1 = frame.safe_box(design)
    night, moon = color(design, "night"), color(design, "moon")
    bw, bh = (v * u for v in back["barcode_u"])
    shapes = (
        Shape(
            "rect",
            x1 - bw,
            y1 - bh,
            bw,
            bh,
            color(design, "paper"),
            radius=back["barcode_radius_u"] * u,
        ),
    )
    texts: list[TextRun] = []
    images: tuple[ImageRun, ...] = ()
    foot = bh
    if logo is not None:
        w = design["logo"]["plain_u"] * u
        h = w * logo.aspect
        images = (ImageRun(logo.path, x0, y1 - h, w, h, LOGO_ALT),)
        foot = max(foot, h)
    else:
        texts.append(TextRun(publisher, "display", ty["cover_sub_u"] * u * 1.4, x0, y1, moon))
    base = ty["blurb_u"] * u
    bottom = y1 - foot - plain["gap_u"] * u
    narrow = min(x1 - x0, _measure_ch(plain["blurb_ch"], "body", base, measure))
    tries = [(1.0, narrow), (1.0, x1 - x0)]
    tries += [(s, x1 - x0) for s in _scales(design["page"]["size_floor"])]
    block = None
    for scale, width in tries:
        size = base * scale
        block = _Text(_wrap(blurb, "body", size, width, measure), "body", size, lead["blurb"], moon)
        if block.height <= bottom - y0:
            break
    else:
        raise PublicationError(
            f"back cover: metadata.blurb does not fit the safe area at "
            f"{design['page']['size_floor']:.0%} size; shorten metadata.blurb"
        )
    top = bottom - block.height
    blurb_texts, _, _ = _place([block], x0, top)
    fade = design["page"]["fade_u"] * u
    band = back["scrim_band"] * frame.height
    scrim = Scrim(
        min(top - fade, frame.height - band),
        frame.height,
        "bottom",
        night,
        back["alpha_edge"],
        back["alpha_mid"],
        fade,
    )
    return PageLayout(
        frame.width,
        frame.height,
        night,
        picture,
        (0.0, 0.0, frame.width, frame.height),
        0.5,
        scrims=(scrim,),
        texts=tuple(blurb_texts + texts),
        images=images,
        shapes=shapes,
        alt="Back cover",
    )
