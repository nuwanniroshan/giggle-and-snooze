"""The storybook video cover (spec 015 Story 1).

The book's front picture fills the frame; its title sits on a soft scalloped plate in the top
part of the frame, lettered like a picture-book cover (rounded letters, a dark outline and a
small 3-D extrude) in colours taken from the book's blueprint palette, with the subtitle under
it. The channel logo is a small badge in the bottom-right corner. The bottom band of the frame
(where the captions go) is left exactly as the picture.
"""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from video_pipeline.frames import PictureError, _save_png_atomic
from video_pipeline.timeline import RenderConfig

RGB = tuple[int, int, int]

CREAM: RGB = (255, 243, 208)
GOLD: RGB = (240, 170, 60)
MIN_CONTRAST = 4.5
_MIN_ACCENT_SATURATION = 0.25

_TITLE_FONTS = (
    ("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", 0),
    ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 0),
)
_SUBTITLE_FONTS = (
    ("/System/Library/Fonts/Supplemental/ChalkboardSE.ttc", 2),  # Chalkboard SE Bold
    ("/System/Library/Fonts/Supplemental/ChalkboardSE.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Georgia Italic.ttf", 0),
)

# Layout, as fractions of the frame (height unless named otherwise).
_PLATE_TOP = 0.04  # top of the plate's scallops
_PLATE_MAX_BOTTOM = 0.56  # the plate never reaches further down than this
_TITLE_MAX_WIDTH = 0.6  # of the frame width
_TITLE_SIZE = 0.105
_TITLE_MIN_SIZE = 0.05
_SHADE_END = 0.70  # the soft darkening behind the plate fades out above this
_BADGE_WIDTH = 0.12  # logo width, of the frame width (spec cap: 18 % with its glow)
_BADGE_GLOW = 0.012  # glow margin around the logo, of the frame width
_BADGE_MARGIN = 0.02  # gap to the frame edges, of the frame width
_SUPERSAMPLE = 3  # the plate and its ornaments are drawn larger, then scaled down (smooth edges)


@dataclass(frozen=True)
class CoverPalette:
    fill: RGB  # title lettering
    outline: RGB  # title outline
    plate: RGB  # title plate
    accent: RGB  # extrude, stitch border, sparkles


DEFAULT_PALETTE = CoverPalette(fill=CREAM, outline=(38, 30, 70), plate=(38, 30, 70), accent=GOLD)

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class CoverLayout:
    plate: Box  # the plate with its scallops
    title_lines: list[Box]  # each line with its outline and extrude
    subtitle: Box | None
    badge: Box | None  # the logo with its glow


# --- colour ----------------------------------------------------------------------------------


def _luminance(c: RGB) -> float:
    def lin(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """WCAG 2 contrast ratio between two sRGB colours (1 to 21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _saturation(c: RGB) -> float:
    return colorsys.rgb_to_hsv(*(v / 255 for v in c))[1]


def _scale(c: RGB, k: float) -> RGB:
    return tuple(max(0, min(255, round(v * k))) for v in c)  # type: ignore[return-value]


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _palette_colours(blueprint: object) -> list[RGB]:
    try:
        swatches = blueprint["art"]["palette"]  # type: ignore[index]
    except (TypeError, KeyError):
        return []
    if not isinstance(swatches, list):
        return []
    colours = []
    for swatch in swatches:
        value = swatch.get("hex") if isinstance(swatch, dict) else None
        if isinstance(value, str) and _HEX.fullmatch(value):
            colours.append(tuple(int(value[i : i + 2], 16) for i in (1, 3, 5)))
    return colours  # type: ignore[return-value]


def cover_palette(blueprint: dict | None) -> CoverPalette:
    """Cover colours from the blueprint palette, chosen by lightness (FR-1): lettering = the
    lightest colour (cream when it is not light enough against the outline), outline and plate =
    the darkest, accent = the most saturated of the others (warm gold when none is). The default
    palette when the blueprint or its palette is missing or malformed (AC-1.2)."""
    colours = _palette_colours(blueprint)
    if len(colours) < 2:
        return DEFAULT_PALETTE
    ordered = sorted(colours, key=_luminance)
    dark, light = ordered[0], ordered[-1]
    fill = light if contrast_ratio(light, dark) >= MIN_CONTRAST else CREAM
    while contrast_ratio(fill, dark) < MIN_CONTRAST:
        dark = _scale(dark, 0.85)
    rest = ordered[1:-1]
    accent = max(rest, key=_saturation, default=None)
    if accent is None or _saturation(accent) < _MIN_ACCENT_SATURATION:
        accent = GOLD
    return CoverPalette(fill=fill, outline=dark, plate=dark, accent=accent)


# --- text ------------------------------------------------------------------------------------


def _font(choices: tuple[tuple[str, int], ...], size: int):
    for path, index in choices:
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size)


def balanced_lines(words: list[str], font, max_lines: int, max_width: float) -> list[str] | None:
    """The fewest lines (up to max_lines) whose widest line fits max_width, split as evenly as
    possible; None when no split fits."""
    for count in range(1, min(max_lines, len(words)) + 1):
        best: tuple[float, list[str]] | None = None
        for cuts in combinations(range(1, len(words)), count - 1):
            bounds = [0, *cuts, len(words)]
            lines = [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:], strict=False)]
            widest = max(font.getlength(x) for x in lines)
            if best is None or widest < best[0]:
                best = (widest, lines)
        if best and best[0] <= max_width:
            return best[1]
    return None


# --- shapes ----------------------------------------------------------------------------------


def _rounded_rect_points(box: tuple[float, ...], radius: float, step: float) -> list[tuple]:
    """Evenly spaced points along a rounded rectangle's outline (for the dotted stitch)."""
    x0, y0, x1, y1 = box
    r = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    segments: list[tuple] = []  # ("line", a, b) or ("arc", centre, start angle)
    corners = [
        (x1 - r, y0 + r, -90),
        (x1 - r, y1 - r, 0),
        (x0 + r, y1 - r, 90),
        (x0 + r, y0 + r, 180),
    ]
    edges = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r))]
    edges += [((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
    for (a, b), (cx, cy, start) in zip(edges, corners, strict=True):
        segments.append(("line", a, b, math.dist(a, b)))
        segments.append(("arc", (cx, cy), start, math.pi * r / 2))
    total = sum(s[-1] for s in segments)
    count = max(4, round(total / step))
    points = []
    for i in range(count):
        d = i * total / count
        for seg in segments:
            if d <= seg[-1]:
                if seg[0] == "line":
                    (ax, ay), (bx, by) = seg[1], seg[2]
                    t = d / seg[-1] if seg[-1] else 0
                    points.append((ax + (bx - ax) * t, ay + (by - ay) * t))
                else:
                    (cx, cy), start = seg[1], seg[2]
                    angle = math.radians(start + 90 * (d / seg[-1] if seg[-1] else 0))
                    points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
                break
            d -= seg[-1]
    return points


def _sparkle(draw: ImageDraw.ImageDraw, centre: tuple[float, float], size: float, fill) -> None:
    """A 4-point star."""
    cx, cy = centre
    inner = size * 0.28
    pts = []
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        r = size if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=fill)


def _plate_shape(core: tuple[float, ...], radius: float, bump: float, ss: int, size) -> Image.Image:
    """The plate's mask: a rounded rectangle with a row of scallops along its top and bottom."""
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    x0, y0, x1, y1 = (v * ss for v in core)
    r, b = radius * ss, bump * ss
    d.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=255)
    span = (x1 - x0) - 2 * r
    count = max(2, round(span / (b * 1.75)))
    for i in range(count + 1):
        cx = x0 + r + span * i / count
        for cy in (y0 + b * 0.35, y1 - b * 0.35):
            d.ellipse((cx - b, cy - b, cx + b, cy + b), fill=255)
    return mask


# --- cover -----------------------------------------------------------------------------------


def _fit_title(title: str, w: int, h: int, size: int):
    words = title.split() or [""]
    two_line_floor = size * 0.8  # a slightly smaller title on two lines beats a tall plate
    while True:
        font = _font(_TITLE_FONTS, size)
        most = 2 if size > two_line_floor else 3
        lines = balanced_lines(words, font, most, w * _TITLE_MAX_WIDTH)
        if lines is not None or size <= h * _TITLE_MIN_SIZE:
            break
        size = round(size * 0.94)
    if lines is None:  # very long words: three lines, shrunk until the widest fits
        lines = balanced_lines(words, font, 3, float("inf")) or words
        while max(font.getlength(x) for x in lines) > w * 0.8 and size > 8:
            size = round(size * 0.94)
            font = _font(_TITLE_FONTS, size)
    return font, lines


def _badge(logo: Path, w: int, h: int) -> tuple[Image.Image, tuple[int, int]] | None:
    try:
        with Image.open(logo) as im:
            mark = im.convert("RGBA")
    except (OSError, UnidentifiedImageError):
        return None
    lw = round(w * _BADGE_WIDTH)
    lh = max(1, round(mark.height * lw / mark.width))
    mark = mark.resize((lw, lh), Image.Resampling.LANCZOS)
    pad = round(w * _BADGE_GLOW)
    badge = Image.new("RGBA", (lw + 2 * pad, lh + 2 * pad), (0, 0, 0, 0))
    alpha = Image.new("L", badge.size, 0)
    alpha.paste(mark.getchannel("A"), (pad, pad))
    spread = max(3, (pad // 2) | 1)
    halo = alpha.filter(ImageFilter.MaxFilter(spread)).filter(ImageFilter.GaussianBlur(pad / 3))
    halo = halo.point(lambda v: round(v * 0.8))
    badge.paste((*CREAM, 255), (0, 0), halo)
    badge.alpha_composite(mark, (pad, pad))
    margin = round(w * _BADGE_MARGIN)
    pos = (w - margin - badge.width, h - round(margin * 1.2) - badge.height)
    return badge, pos


@dataclass
class _Measure:
    font: object
    lines: list[str]
    size: int
    stroke: int
    depth: int
    ascent: int
    pitch: int
    sub: str
    sfont: object
    sub_gap: int
    pad_y: int
    bump: int
    core_w: int
    core: tuple[float, float, float, float]
    radius: float
    plate_box: Box


def _measure(title: str, subtitle: str, w: int, h: int, start: int) -> _Measure:
    font, lines = _fit_title(title, w, h, start)
    size = font.size
    stroke = max(2, round(size * 0.075))
    depth = max(2, round(size * 0.07))  # extrude steps (pixels)
    ascent, descent = font.getmetrics()
    pitch = round((ascent + descent) * 0.93)
    sub = (subtitle or "").strip()
    sfont = _font(_SUBTITLE_FONTS, max(10, round(size * 0.4))) if sub else None
    title_w = max(font.getlength(x) for x in lines) + 2 * stroke + depth
    sub_w = sfont.getlength(sub) if sfont else 0
    if sfont and sub_w > w * 0.7:
        sfont = _font(_SUBTITLE_FONTS, max(10, round(sfont.size * w * 0.7 / sub_w)))
        sub_w = sfont.getlength(sub)
    sub_gap = round(size * 0.12)
    sub_h = round(sum(sfont.getmetrics()) * 1.0) if sfont else 0
    block_h = pitch * (len(lines) - 1) + ascent + descent + stroke * 2 + depth
    block_h += (sub_gap + sub_h) if sfont else 0

    pad_x, pad_y = round(size * 0.5), round(size * 0.22)
    bump = round(size * 0.3)  # scallop radius
    core_w = round(max(title_w, sub_w) + 2 * pad_x)
    core_h = block_h + 2 * pad_y
    top = round(h * _PLATE_TOP) + bump
    core = ((w - core_w) / 2, top, (w + core_w) / 2, top + core_h)
    radius = min(core_h / 2.6, size * 0.6)
    plate_box = (
        math.floor(core[0]),
        math.floor(core[1] - bump * 0.65),
        math.ceil(core[2]),
        math.ceil(core[3] + bump * 0.65),
    )

    return _Measure(
        font,
        lines,
        size,
        stroke,
        depth,
        ascent,
        pitch,
        sub,
        sfont,
        sub_gap,
        pad_y,
        bump,
        core_w,
        core,
        radius,
        plate_box,
    )


def compose_cover(
    picture: Path,
    title: str,
    subtitle: str,
    palette: CoverPalette,
    logo: Path | None,
    cfg: RenderConfig,
    out: Path,
) -> CoverLayout:
    """Write the W×H storybook cover to `out` (PNG, atomically) and return where things went."""
    picture = Path(picture)
    w, h = cfg.width, cfg.height
    try:
        with Image.open(picture) as im:
            art = ImageOps.fit(im.convert("RGB"), (w, h), Image.Resampling.LANCZOS)
    except (OSError, UnidentifiedImageError) as exc:
        raise PictureError(picture, str(exc)) from None

    # --- measure the lettering (smaller until the plate stays in the top part) ---
    start = round(h * _TITLE_SIZE)
    while True:
        m = _measure(title, subtitle, w, h, start)
        if m.plate_box[3] <= h * _PLATE_MAX_BOTTOM or start <= h * _TITLE_MIN_SIZE:
            break
        start = round(start * 0.94)
    font, lines, size, stroke, depth = m.font, m.lines, m.size, m.stroke, m.depth
    pitch, sub, sfont, sub_gap = m.pitch, m.sub, m.sfont, m.sub_gap
    pad_y, bump, core_w, core, radius, plate_box = (
        m.pad_y,
        m.bump,
        m.core_w,
        m.core,
        m.radius,
        m.plate_box,
    )
    # --- soft darkening behind the plate only ---
    shade = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shade)
    cx, cy = w / 2, (plate_box[1] + plate_box[3]) / 2
    rx, ry = core_w * 0.75, (plate_box[3] - plate_box[1]) * 0.95
    sd.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=round(255 * 0.32))
    shade = shade.filter(ImageFilter.GaussianBlur(h * 0.06))
    ramp = np.clip((h * _SHADE_END - np.arange(h)) / (h * 0.12), 0, 1)[:, None]
    shade = Image.fromarray((np.asarray(shade) * ramp).astype(np.uint8), "L")
    art = Image.composite(Image.new("RGB", (w, h), _scale(palette.plate, 0.5)), art, shade)
    canvas = art.convert("RGBA")

    # --- plate, shadow, stitch and sparkles (drawn large on a local layer) ---
    ss = _SUPERSAMPLE
    margin = round(size * 0.45)
    ox, oy = plate_box[0] - margin, plate_box[1] - margin
    lw_, lh_ = plate_box[2] - plate_box[0] + 2 * margin, plate_box[3] - plate_box[1] + 2 * margin
    local = (core[0] - ox, core[1] - oy, core[2] - ox, core[3] - oy)
    big = (lw_ * ss, lh_ * ss)
    shape = _plate_shape(local, radius, bump, ss, big).resize((lw_, lh_), Image.Resampling.LANCZOS)

    shadow = Image.new("RGBA", (lw_, lh_), (0, 0, 0, 0))
    shadow.paste((*_scale(palette.plate, 0.35), 255), (0, 0), shape.point(lambda v: v * 0.55))
    blur = size * 0.09
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (ox, oy + round(size * 0.06)))

    deco = Image.new("RGBA", big, (0, 0, 0, 0))
    plate_alpha = Image.new("L", big, 0)
    plate_alpha.paste(_plate_shape(local, radius, bump, ss, big).point(lambda v: v * 0.8))
    deco.paste((*palette.plate, 255), (0, 0), plate_alpha)
    dd = ImageDraw.Draw(deco)
    inset = size * 0.13
    stitch = tuple(
        v * ss for v in (local[0] + inset, local[1] + inset, local[2] - inset, local[3] - inset)
    )
    dot = max(1.2, size * 0.022) * ss
    for px, py in _rounded_rect_points(stitch, (radius - inset) * ss, dot * 3.2):
        dd.ellipse((px - dot, py - dot, px + dot, py + dot), fill=(*palette.accent, 230))
    spark = size * 0.17 * ss
    lx0, ly0, lx1, ly1 = (v * ss for v in local)
    for (sx, sy), k, colour in (
        ((lx0 - spark * 0.2, ly0 + spark * 0.3), 1.0, palette.accent),
        ((lx0 + spark * 0.9, ly0 - spark * 0.7), 0.55, palette.fill),
        ((lx1 + spark * 0.15, ly1 - spark * 0.4), 0.85, palette.accent),
        ((lx1 - spark * 1.0, ly1 + spark * 0.65), 0.5, palette.fill),
    ):
        _sparkle(dd, (sx, sy), spark * k, (*colour, 255))
    deco = deco.resize((lw_, lh_), Image.Resampling.LANCZOS)
    canvas.alpha_composite(deco, (ox, oy))

    # --- title: extrude, outline, fill ---
    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(text)
    extrude = _mix(_scale(palette.accent, 0.8), palette.outline, 0.15)
    dark, body = (*palette.outline, 255), (*extrude, 255)
    y = core[1] + pad_y + stroke
    boxes: list[Box] = []
    for line in lines:
        x = w / 2 - depth / 2
        inner = max(1, round(stroke * 0.45))
        for k in range(depth + 1):  # dark outer outline around letters and extrude
            td.text(
                (x + k, y + k),
                line,
                font=font,
                anchor="ma",
                fill=dark,
                stroke_width=stroke,
                stroke_fill=dark,
            )
        for k in range(depth, 0, -1):  # the extrude, stepping down-right
            td.text(
                (x + k, y + k),
                line,
                font=font,
                anchor="ma",
                fill=body,
                stroke_width=inner,
                stroke_fill=body,
            )
        td.text(
            (x, y),
            line,
            font=font,
            anchor="ma",
            fill=(*palette.fill, 255),
            stroke_width=inner,
            stroke_fill=dark,
        )
        bx0, by0, bx1, by1 = td.textbbox((x, y), line, font=font, anchor="ma", stroke_width=stroke)
        boxes.append(
            (math.floor(bx0), math.floor(by0), math.ceil(bx1 + depth), math.ceil(by1 + depth))
        )
        y += pitch
    sub_box: Box | None = None
    if sfont:
        sy = boxes[-1][3] + sub_gap
        sub_colour = _mix(palette.accent, palette.fill, 0.55)
        if contrast_ratio(sub_colour, palette.plate) < MIN_CONTRAST:
            sub_colour = palette.fill
        sstroke = max(1, round(sfont.size * 0.06))
        td.text(
            (w / 2, sy),
            sub,
            font=sfont,
            anchor="ma",
            fill=(*sub_colour, 255),
            stroke_width=sstroke,
            stroke_fill=(*palette.outline, 255),
        )
        sb = td.textbbox((w / 2, sy), sub, font=sfont, anchor="ma", stroke_width=sstroke)
        sub_box = (math.floor(sb[0]), math.floor(sb[1]), math.ceil(sb[2]), math.ceil(sb[3]))
    soft = text.getchannel("A").filter(ImageFilter.GaussianBlur(stroke))
    glow = Image.new("RGBA", (w, h), (*_scale(palette.outline, 0.5), 0))
    glow.putalpha(soft.point(lambda v: v * 0.5))
    canvas.alpha_composite(glow, (round(stroke * 0.5), round(stroke * 0.8)))
    canvas.alpha_composite(text)

    # --- logo badge ---
    badge_box: Box | None = None
    made = _badge(Path(logo), w, h) if logo else None
    if made:
        badge, (bx, by) = made
        canvas.alpha_composite(badge, (bx, by))
        badge_box = (bx, by, bx + badge.width, by + badge.height)

    _save_png_atomic(canvas.convert("RGB"), Path(out))
    return CoverLayout(plate=plate_box, title_lines=boxes, subtitle=sub_box, badge=badge_box)
