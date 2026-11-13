"""Drawing side of the publication design (spec 023): fonts, background rasters, PDF and PNG.

Every renderer draws the same `PageLayout` from `publication.py`. Fonts are code assets, so
callers resolve them from PROJECT_ROOT even when a build runs with `--root` for tests.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from video_pipeline.library import PROJECT_ROOT
from video_pipeline.publication import (
    PageLayout,
    PublicationError,
    Scrim,
    crop_box,
    load_design,
)

FONT_NAMES = {"display": "GSDisplay", "body": "GSBody", "body_bold": "GSBody-Bold"}
DASH = (3.0, 2.5)  # dash and gap, as multiples of the line width


def font_paths(design: dict, root: Path = PROJECT_ROOT) -> dict[str, Path]:
    """Bundled font files by role. A missing file stops the build, naming it (AC-7.2)."""
    paths = {role: root / design["fonts"][role] for role in FONT_NAMES}
    for path in paths.values():
        if not path.is_file():
            raise PublicationError(f"font file not found: {path}")
    return paths


def register_fonts(design: dict, root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Register the bundled TTFs with reportlab once; returns role -> registered name."""
    paths = font_paths(design, root)
    registered = set(pdfmetrics.getRegisteredFontNames())
    for role, name in FONT_NAMES.items():
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, str(paths[role])))
    return dict(FONT_NAMES)


def measure(text: str, role: str, size: float) -> float:
    """Width of `text` in points. Call `register_fonts` first."""
    return pdfmetrics.stringWidth(text, FONT_NAMES[role], size)


# ---------------------------------------------------------------- background raster


def scrim_alpha(scrim: Scrim, ys: np.ndarray) -> np.ndarray:
    """Opacity of the scrim at page heights `ys` (points from the top); 0 outside the band."""
    if scrim.side == "top":
        xp = [scrim.y0, scrim.y1 - scrim.fade, scrim.y1]
        fp = [scrim.alpha_edge, scrim.alpha_text, 0.0]
    else:
        xp = [scrim.y0, scrim.y0 + scrim.fade, scrim.y1]
        fp = [0.0, scrim.alpha_text, scrim.alpha_edge]
    return np.interp(ys, xp, fp, left=0.0, right=0.0)


def _pixels(layout: PageLayout, dpi: float) -> tuple[int, int]:
    scale = dpi / 72
    return round(layout.width * scale), round(layout.height * scale)


def render_background(layout: PageLayout, dpi: float) -> Image.Image:
    """Ground colour, the picture cover-fitted into its box around focus_x, then the scrims."""
    scale = dpi / 72
    size = _pixels(layout, dpi)
    img = Image.new("RGB", size, tuple(layout.ground))
    if layout.picture is not None and layout.picture.is_file():
        bx, by, bw, bh = layout.picture_box
        target = (max(1, round(bw * scale)), max(1, round(bh * scale)))
        with Image.open(layout.picture) as im:
            src = im.convert("RGB")
        box = crop_box(src.width, src.height, bw, bh, layout.focus_x)
        art = src.resize(target, Image.Resampling.LANCZOS, box=box)
        img.paste(art, (round(bx * scale), round(by * scale)))
    if not layout.scrims:
        return img
    arr = np.asarray(img, dtype=np.float32).copy()
    ys = (np.arange(size[1]) + 0.5) / scale
    for scrim in layout.scrims:
        alpha = scrim_alpha(scrim, ys)
        rows = np.nonzero(alpha > 0)[0]
        if rows.size == 0:
            continue
        a = alpha[rows][:, None, None].astype(np.float32)
        tint = np.asarray(scrim.color, dtype=np.float32)
        arr[rows] = arr[rows] * (1 - a) + tint * a
    return Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGB")


def background_jpeg(layout: PageLayout, dpi: float, cache: dict, quality: int = 90) -> bytes:
    """The background raster as JPEG bytes, shared between editions with the same page."""
    picture = layout.picture
    mtime = picture.stat().st_mtime_ns if picture is not None and picture.is_file() else None
    key = (
        str(picture),
        mtime,
        layout.picture_box,
        layout.focus_x,
        layout.scrims,
        tuple(layout.ground),
        _pixels(layout, dpi),
        quality,
    )
    if key not in cache:
        buffer = BytesIO()
        render_background(layout, dpi).save(buffer, "JPEG", quality=quality, dpi=(dpi, dpi))
        cache[key] = buffer.getvalue()
    return cache[key]


# ---------------------------------------------------------------- PDF


def _rgb(c: Canvas, rgb: tuple[int, int, int], *, stroke: bool = False) -> None:
    r, g, b = (v / 255 for v in rgb)
    (c.setStrokeColorRGB if stroke else c.setFillColorRGB)(r, g, b)


def draw_pdf_page(
    c: Canvas, layout: PageLayout, dpi: float, cache: dict, quality: int = 90
) -> None:
    """Draw one page: background raster, then shapes, images and vector text. No showPage."""
    height = layout.height
    jpeg = background_jpeg(layout, dpi, cache, quality)
    c.drawImage(ImageReader(BytesIO(jpeg)), 0, 0, width=layout.width, height=height)
    for shape in layout.shapes:
        c.saveState()
        y = height - shape.y - shape.h
        if shape.kind == "rect":
            _rgb(c, shape.color)
            c.roundRect(shape.x, y, shape.w, shape.h, shape.radius, stroke=0, fill=1)
        else:
            _rgb(c, shape.color, stroke=True)
            c.setLineWidth(shape.line)
            if shape.dashed:
                c.setDash([shape.line * DASH[0], shape.line * DASH[1]])
            c.roundRect(shape.x, y, shape.w, shape.h, shape.radius, stroke=1, fill=0)
        c.restoreState()
    for image in layout.images:
        if image.path.is_file():
            c.drawImage(
                str(image.path),
                image.x,
                height - image.y - image.h,
                width=image.w,
                height=image.h,
                mask="auto",
            )
    for run in layout.texts:
        c.setFont(FONT_NAMES[run.role], run.size)
        _rgb(c, run.color)
        draw = c.drawRightString if run.align == "right" else c.drawString
        draw(run.x, height - run.y, run.text)


# ---------------------------------------------------------------- PNG


def render_png(layout: PageLayout, px: int, design: dict | None = None) -> Image.Image:
    """The page as an RGB image `px` wide (covers): background plus shapes, images and text."""
    design = design or load_design()
    scale = px / layout.width
    img = render_background(layout, px * 72 / layout.width).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for shape in layout.shapes:
        box = [
            shape.x * scale,
            shape.y * scale,
            (shape.x + shape.w) * scale,
            (shape.y + shape.h) * scale,
        ]
        if shape.kind == "rect":
            draw.rounded_rectangle(box, radius=shape.radius * scale, fill=tuple(shape.color))
        else:
            draw.rounded_rectangle(
                box,
                radius=shape.radius * scale,
                outline=tuple(shape.color),
                width=max(1, round(shape.line * scale)),
            )
    for image in layout.images:
        if not image.path.is_file():
            continue
        size = (max(1, round(image.w * scale)), max(1, round(image.h * scale)))
        with Image.open(image.path) as im:
            art = im.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
        img.alpha_composite(art, (round(image.x * scale), round(image.y * scale)))
    draw = ImageDraw.Draw(img)
    paths = font_paths(design)
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
    for run in layout.texts:
        key = (run.role, max(1, round(run.size * scale)))
        if key not in fonts:
            fonts[key] = ImageFont.truetype(str(paths[run.role]), key[1])
        draw.text(
            (run.x * scale, run.y * scale),
            run.text,
            font=fonts[key],
            fill=tuple(run.color),
            anchor="rs" if run.align == "right" else "ls",
        )
    return img.convert("RGB")
