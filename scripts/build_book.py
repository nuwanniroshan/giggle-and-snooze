#!/usr/bin/env python3
"""
Production book builder: renders one book's print and digital editions (spec 005, 020).

Generates, in books/<slug>/build/ (N = story pages, P = KDP interior pages):
1. <Title>_<N>Page_Digital_Edition.pdf (story pages only, 8.5" x 8.5")
2. <Title>_Complete_Reader.pdf (Front Cover + story pages + Back Cover)
3. <Title>_KDP_<P>Page_Print_Interior.pdf (KDP interior with bleed, 8.625" x 8.75";
   P = max(24, front + N + back) rounded up to even, padded with "My drawing" pages)
4. <Title>_KDP_Print_Wrap_Cover.pdf & .png (spine width from P)
5. <Title>_Front_Cover.png & Back_Cover.png (300 DPI standalone covers)
6. <Title>.epub (EPUB 3)
7. build/index.html (interactive HTML5 flipbook preview)

Book-specific extras (bookplate label, map legend, activity page...) come from the book's
optional `print_extras` object, over the generic templates in config/book_extras.json.
"""

import argparse
import json
import math
import re
import string
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_pipeline.library import (  # noqa: E402
    PROJECT_ROOT,
    BookFolder,
    LibraryError,
    book_folder,
    file_stem,
)

# Color Palette (Cozy Whimsical Gouache)
CREAM_BG = HexColor("#FDF8EE")
PARCHMENT_CARD = Color(0.992, 0.973, 0.933, alpha=0.93)
WARM_BROWN = HexColor("#4A3222")
SOFT_BROWN = HexColor("#7A583E")
GOLD_ACCENT = HexColor("#C88A32")
BERRY_RED = HexColor("#B83B35")
FOREST_GREEN = HexColor("#3E6B48")
TEAL_ACCENT = HexColor("#2F6F6D")
BORDER_TAN = HexColor("#D8C3A5")

# Print geometry (inches) and KDP rules, in one place.
TRIM_IN = 8.5
BLEED_IN = 0.125
KDP_MIN_PAGES = 24
KDP_COLOR_PAGE_INCHES = 0.002252  # spine width per page, premium colour paper
COVER_DPI = 300
EXTRAS_CONFIG = Path("config") / "book_extras.json"
EXTRAS_KEYS = (
    "bookplate_label",
    "bookplate_line",
    "front_footer",
    "back_cover_headline",
    "copyright_genre",
    "map_title",
    "activity_title",
    "activity_subtitle",
    "questions_title",
    "question_templates",
    "max_characters",
    "max_questions",
    "drawing_caption",
)


class ExtrasError(ValueError):
    """config/book_extras.json is missing or incomplete. The message names the file and key."""


@dataclass(frozen=True)
class InteriorPage:
    kind: str  # "half_title" | "title" | "map" | "blank" | "story" | "drawing" | "activity"
    number: int  # 1-based interior page number
    story: dict | None = None


def plan_interior(book: dict, has_map: bool) -> list[InteriorPage]:
    """The KDP interior, page by page (AC-2.1, AC-2.3).

    Front matter, a blank filler when needed so story page 1 lands on an even (left) page,
    the story pages in page_number order, "My drawing" pages, and the activity page last.
    P = max(24, total) rounded up to even."""
    front = ["half_title", "title"] + (["map"] if has_map else [])
    if len(front) % 2 == 0:
        front.append("blank")
    stories = sorted(book["pages"], key=lambda page: page["page_number"])
    total = max(KDP_MIN_PAGES, len(front) + len(stories) + 1)
    total += total % 2
    drawings = total - len(front) - len(stories) - 1
    kinds = [(k, None) for k in front] + [("story", page) for page in stories]
    kinds += [("drawing", None)] * drawings + [("activity", None)]
    return [InteriorPage(kind, n, story) for n, (kind, story) in enumerate(kinds, start=1)]


def story_bleed(number: int, bleed: float) -> tuple[float, float]:
    """(left, right) bleed for an interior page: even pages bleed left, odd pages right."""
    return (bleed, 0.0) if number % 2 == 0 else (0.0, bleed)


def edition_names(stem: str, story_pages: int, interior_pages: int) -> dict[str, str]:
    """Edition file names stating the real page counts (AC-3.1)."""
    return {
        "digital": f"{stem}_{story_pages}Page_Digital_Edition.pdf",
        "kdp": f"{stem}_KDP_{interior_pages}Page_Print_Interior.pdf",
        "reader": f"{stem}_Complete_Reader.pdf",
        "wrap_pdf": f"{stem}_KDP_Print_Wrap_Cover.pdf",
        "wrap_png": f"{stem}_KDP_Print_Wrap_Cover.png",
        "front_png": f"{stem}_Front_Cover.png",
        "back_png": f"{stem}_Back_Cover.png",
        "epub": f"{stem}.epub",
    }


def stale_editions(build_dir: Path, stem: str, keep: set[str]) -> list[Path]:
    """Edition PDFs of this book with other page counts (AC-3.2)."""
    pattern = re.compile(
        rf"^{re.escape(stem)}_(?:\d+Page_Digital_Edition|KDP_\d+Page_Print_Interior)\.pdf$"
    )
    return sorted(
        p for p in build_dir.glob("*.pdf") if pattern.match(p.name) and p.name not in keep
    )


def spine_inches(interior_pages: int) -> float:
    return interior_pages * KDP_COLOR_PAGE_INCHES


def wrap_size_inches(interior_pages: int) -> tuple[float, float]:
    """KDP wrap cover: back + spine + front, with bleed on every outer edge (AC-2.2)."""
    width = 2 * (TRIM_IN + BLEED_IN) + spine_inches(interior_pages)
    return width, TRIM_IN + 2 * BLEED_IN


def map_pictures(illustrations: Path) -> list[Path]:
    return sorted(illustrations.glob("map_*.png"))


def load_extras(root: Path, book: dict) -> dict:
    """Generic templates from config/book_extras.json, overlaid by the book's print_extras."""
    path = root / EXTRAS_CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ExtrasError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise ExtrasError(f"{path}: not valid JSON ({exc})") from None
    for key in EXTRAS_KEYS:
        if key not in config:
            raise ExtrasError(f"{path}: missing key {key!r}")
    own = book.get("print_extras") or {}
    return {**config, **own}


def character_names(book: dict, limit: int) -> list[str]:
    """The character sheet's names (spec 018), else the page titles."""
    sheet = (book.get("style_bible") or {}).get("characters") or []
    names = [str(c["name"]).strip() for c in sheet if isinstance(c, dict) and c.get("name")]
    if not names:
        names = [str(p["title"]).strip() for p in book["pages"] if p.get("title")]
    return names[:limit]


def fill_questions(templates: list[str], title: str, characters: list[str], count: int) -> list[str]:
    """The first `count` templates whose placeholders can all be filled."""
    values = {"title": title}
    if characters:
        values["character"] = characters[0]
    if len(characters) > 1:
        values["character2"] = characters[1]
    questions = []
    for template in templates:
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        if fields <= values.keys():
            questions.append(template.format(**values))
        if len(questions) == count:
            break
    return questions


def activity_content(book: dict, extras: dict) -> dict:
    """The back-matter activity page (AC-1.3): the book's print_extras.activity, else a
    "Can you find…?" list of its characters and talk-about questions from the templates.

    Without a character sheet the list uses page titles, but questions never name a page
    title as a character."""
    limit = int(extras["max_characters"])
    sheet = bool((book.get("style_bible") or {}).get("characters"))
    names = character_names(book, limit)
    generic = {
        "title": extras["activity_title"],
        "subtitle": extras["activity_subtitle"],
        "items": [{"title": name, "text": ""} for name in names],
        "questions_title": extras["questions_title"],
        "questions": fill_questions(
            extras["question_templates"],
            book["metadata"]["title"],
            names if sheet else [],
            int(extras["max_questions"]),
        ),
    }
    return {**generic, **(extras.get("activity") or {})}


def map_content(book: dict, extras: dict, pictures: list[Path]) -> dict:
    own = extras.get("map") or {}
    names = [p.name for p in pictures]
    file = own.get("file") if own.get("file") in names else names[0]
    return {
        "file": file,
        "title": own.get("title") or extras["map_title"].format(title=book["metadata"]["title"]),
        "subtitle": own.get("subtitle", ""),
        "legend": list(own.get("legend", [])),
    }


def register_fonts():
    """Register macOS Georgia / Baskerville fonts for warm storybook typography, with fallback."""
    font_dir = Path("/System/Library/Fonts/Supplemental")
    georgia_regular = font_dir / "Georgia.ttf"
    georgia_bold = font_dir / "Georgia Bold.ttf"
    georgia_italic = font_dir / "Georgia Italic.ttf"
    georgia_bi = font_dir / "Georgia Bold Italic.ttf"

    if georgia_regular.exists():
        pdfmetrics.registerFont(TTFont("StorySerif", str(georgia_regular)))
        pdfmetrics.registerFont(TTFont("StorySerif-Bold", str(georgia_bold)))
        pdfmetrics.registerFont(TTFont("StorySerif-Italic", str(georgia_italic)))
        pdfmetrics.registerFont(TTFont("StorySerif-BoldItalic", str(georgia_bi)))
        return {
            "regular": "StorySerif",
            "bold": "StorySerif-Bold",
            "italic": "StorySerif-Italic",
            "bold_italic": "StorySerif-BoldItalic",
        }
    return {
        "regular": "Times-Roman",
        "bold": "Times-Bold",
        "italic": "Times-Italic",
        "bold_italic": "Times-BoldItalic",
    }


def get_pil_font(size: int, bold: bool = False, italic: bool = False):
    font_dir = Path("/System/Library/Fonts/Supplemental")
    candidates = []
    if bold and italic:
        candidates.append(font_dir / "Georgia Bold Italic.ttf")
    elif bold:
        candidates.append(font_dir / "Georgia Bold.ttf")
    elif italic:
        candidates.append(font_dir / "Georgia Italic.ttf")
    candidates.append(font_dir / "Georgia.ttf")
    candidates.append(Path("/System/Library/Fonts/NewYork.ttf"))
    for c in candidates:
        if c.exists():
            try:
                return ImageFont.truetype(str(c), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_botanical_frame(c: canvas.Canvas, x: float, y: float, w: float, h: float, color=BORDER_TAN, accent=GOLD_ACCENT):
    """Draws a warm double-line storybook border with corner leaf flourishes."""
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(1.2)
    c.roundRect(x, y, w, h, 8, stroke=1, fill=0)
    c.setLineWidth(0.6)
    c.roundRect(x + 4, y + 4, w - 8, h - 8, 6, stroke=1, fill=0)

    # Corner botanical dots
    c.setFillColor(accent)
    for cx, cy in [(x + 8, y + 8), (x + w - 8, y + 8), (x + 8, y + h - 8), (x + w - 8, y + h - 8)]:
        c.circle(cx, cy, 2.2, stroke=0, fill=1)
    c.restoreState()


def draw_ornamental_divider(c: canvas.Canvas, cx: float, y: float, width: float = 140):
    """Draws a delicate storybook divider with a berry-red diamond and gold leaves."""
    c.saveState()
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(0.9)
    half = width / 2.0
    c.line(cx - half, y, cx - 14, y)
    c.line(cx + 14, y, cx + half, y)

    c.setFillColor(BERRY_RED)
    p = c.beginPath()
    p.moveTo(cx, y + 4)
    p.lineTo(cx + 5, y)
    p.lineTo(cx, y - 4)
    p.lineTo(cx - 5, y)
    p.close()
    c.drawPath(p, stroke=0, fill=1)

    c.setFillColor(FOREST_GREEN)
    c.circle(cx - 9, y, 2.0, stroke=0, fill=1)
    c.circle(cx + 9, y, 2.0, stroke=0, fill=1)
    c.restoreState()


def draw_page_number_badge(c: canvas.Canvas, cx: float, y: float, page_num: int, fonts: dict):
    """Draws a warm circular badge at the bottom center with the page number."""
    c.saveState()
    c.setFillColor(HexColor("#F5EBD6"))
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(1.0)
    c.circle(cx, y + 4, 12, stroke=1, fill=1)

    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["bold"], 10)
    c.drawCentredString(cx, y + 0.5, str(page_num))
    c.restoreState()


def wrap_text_lines(c: canvas.Canvas, text: str, font_name: str, font_size: float, max_width: float):
    """Word-wraps a paragraph into lines fitting within max_width."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test_line = " ".join(current + [word])
        if c.stringWidth(test_line, font_name, font_size) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def draw_story_text_block(
    c: canvas.Canvas,
    text: str,
    x: float,
    top_y: float,
    max_width: float,
    fonts: dict,
    font_size: float = 12.2,
    leading: float = 17.5,
    drop_cap: bool = True,
):
    """Renders multi-paragraph story text with an illuminated berry-red drop cap on the first paragraph."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    cur_y = top_y

    for p_idx, para in enumerate(paragraphs):
        if p_idx == 0 and drop_cap and len(para) > 1:
            first_char = para[0]
            rest_para = para[1:]
            # If the paragraph starts with a quote mark, include the quote + first letter
            if first_char in ('"', "“") and len(para) > 2:
                first_char = para[:2]
                rest_para = para[2:]

            drop_font_size = font_size * 2.65
            drop_w = c.stringWidth(first_char, fonts["bold"], drop_font_size) + 6

            # Draw drop cap
            c.saveState()
            c.setFillColor(BERRY_RED)
            c.setFont(fonts["bold"], drop_font_size)
            c.drawString(x, cur_y - leading * 1.45, first_char)
            c.restoreState()

            # Wrap first 2 lines indented by drop_w, then remaining lines at full width
            words = rest_para.split()
            line_idx = 0
            current = []
            for word in words:
                avail_w = (max_width - drop_w) if line_idx < 2 else max_width
                test_line = " ".join(current + [word])
                if c.stringWidth(test_line, fonts["regular"], font_size) <= avail_w:
                    current.append(word)
                else:
                    line_x = (x + drop_w) if line_idx < 2 else x
                    c.setFillColor(WARM_BROWN)
                    c.setFont(fonts["regular"], font_size)
                    c.drawString(line_x, cur_y, " ".join(current))
                    cur_y -= leading
                    line_idx += 1
                    current = [word]
            if current:
                line_x = (x + drop_w) if line_idx < 2 else x
                c.setFillColor(WARM_BROWN)
                c.setFont(fonts["regular"], font_size)
                c.drawString(line_x, cur_y, " ".join(current))
                cur_y -= leading
                line_idx += 1

            # Ensure cur_y clears the drop cap if paragraph was very short
            if line_idx < 2:
                cur_y -= leading * (2 - line_idx)
        else:
            lines = wrap_text_lines(c, para, fonts["regular"], font_size, max_width)
            c.setFillColor(WARM_BROWN)
            c.setFont(fonts["regular"], font_size)
            for line in lines:
                c.drawString(x, cur_y, line)
                cur_y -= leading

        cur_y -= leading * 0.42

    return cur_y


def render_story_page(
    c: canvas.Canvas,
    page_data: dict,
    page_w: float,
    page_h: float,
    bleed_left: float,
    bleed_right: float,
    bleed_top: float,
    bleed_bottom: float,
    fonts: dict,
    *,
    paths: BookFolder,
):
    """
    Renders a single story page onto the ReportLab canvas.
    Supports both 'image_top_text_bottom' (classic framed storybook page)
    and 'full_bleed_card_*' (lush full-page artwork with a warm parchment story card).
    """
    trim_w = page_w - bleed_left - bleed_right
    trim_h = page_h - bleed_top - bleed_bottom
    trim_x = bleed_left
    trim_y = bleed_bottom

    # Fill entire page including bleed with warm cream background
    c.saveState()
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    c.restoreState()

    img_path = paths.illustrations / page_data["illustration_file"]
    pnum = page_data["page_number"]

    # Safe margin inside trim (at least 0.5" = 36pt from trim edges for KDP compliance)
    margin = 38.0
    safe_x = trim_x + margin
    safe_y = trim_y + margin
    safe_w = trim_w - 2 * margin
    safe_h = trim_h - 2 * margin

    # Classic Unobstructed Storybook Page (100% of square illustration visible on top, rich typography below)
    draw_botanical_frame(c, safe_x - 8, safe_y - 8, safe_w + 16, safe_h + 16)

    # Alternate subtle warm parchment card behind text on even pages for visual rhythm
    if pnum % 2 == 0:
        c.saveState()
        c.setFillColor(HexColor("#FAF1DF"))
        c.setStrokeColor(BORDER_TAN)
        c.setLineWidth(0.8)
        c.roundRect(safe_x + 6, safe_y + 18, safe_w - 12, safe_h * 0.40, 8, stroke=1, fill=1)
        c.restoreState()

    # Image frame box (top 55% of safe area, 100% unobstructed)
    img_h = safe_h * 0.55
    img_w = img_h  # 1:1 square artwork
    img_x = trim_x + (trim_w - img_w) / 2.0
    img_y = safe_y + safe_h - img_h - 6

    # Soft shadow & gold frame around illustration
    c.saveState()
    c.setFillColor(HexColor("#EFE4CE"))
    c.roundRect(img_x - 4, img_y - 4, img_w + 8, img_h + 8, 6, stroke=0, fill=1)
    if img_path.exists():
        c.drawImage(str(img_path), img_x, img_y, width=img_w, height=img_h, preserveAspectRatio=True, mask="auto")
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(1.5)
    c.roundRect(img_x, img_y, img_w, img_h, 4, stroke=1, fill=0)
    c.restoreState()

    # Title & ornamental divider below image
    title_y = img_y - 19
    c.saveState()
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["bold_italic"], 12.8)
    c.drawCentredString(trim_x + trim_w / 2.0, title_y, page_data["title"])
    c.restoreState()

    draw_ornamental_divider(c, trim_x + trim_w / 2.0, title_y - 8, width=160)

    # Story text block below divider
    text_top_y = title_y - 25
    text_margin_x = safe_x + 18
    text_max_w = safe_w - 36
    draw_story_text_block(
        c,
        page_data["text"],
        x=text_margin_x,
        top_y=text_top_y,
        max_width=text_max_w,
        fonts=fonts,
        font_size=11.3,
        leading=15.6,
        drop_cap=True,
    )

    # Page number badge at bottom center
    draw_page_number_badge(c, trim_x + trim_w / 2.0, safe_y - 2, pnum, fonts)


def start_framed_page(c: canvas.Canvas, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float):
    """Cream page with the botanical frame; returns (trim_x, trim_y, trim_w, trim_h, cx, margin)."""
    trim_w = page_w - bleed_left - bleed_right
    trim_h = page_h - bleed_top - bleed_bottom
    trim_x = bleed_left
    trim_y = bleed_bottom
    margin = 42.0
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_botanical_frame(c, trim_x + margin - 8, trim_y + margin - 8, trim_w - 2 * margin + 16, trim_h - 2 * margin + 16)
    return trim_x, trim_y, trim_w, trim_h, trim_x + trim_w / 2.0, margin


def render_front_matter_pages(c: canvas.Canvas, book_data: dict, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float, fonts: dict, *, paths: BookFolder, extras: dict, map_info: dict | None):
    """Renders the front matter of the KDP interior: half-title, title/copyright and,
    only when the book has a map picture, the map page (AC-1.2)."""
    meta = book_data["metadata"]
    geometry = (page_w, page_h, bleed_left, bleed_right, bleed_top, bleed_bottom)

    # --- PAGE i: Half-Title & Ex-Libris Bookplate ---
    trim_x, trim_y, trim_w, trim_h, cx, margin = start_framed_page(c, *geometry)
    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 26)
    c.drawCentredString(cx, trim_y + trim_h - 115, meta["title"])
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 14)
    c.drawCentredString(cx, trim_y + trim_h - 140, meta.get("subtitle", ""))
    draw_ornamental_divider(c, cx, trim_y + trim_h - 158, width=180)

    # Ex-Libris Box
    box_w, box_h = 280, 250
    box_x = cx - box_w / 2.0
    box_y = trim_y + 135
    c.setFillColor(HexColor("#FAF1DF"))
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(1.5)
    c.roundRect(box_x, box_y, box_w, box_h, 12, stroke=1, fill=1)

    first = min(book_data["pages"], key=lambda page: page["page_number"])
    p1_img = paths.illustrations / first["illustration_file"]
    if p1_img.exists():
        c.drawImage(str(p1_img), cx - 65, box_y + 95, width=130, height=130, preserveAspectRatio=True, mask="auto")
        c.setStrokeColor(GOLD_ACCENT)
        c.roundRect(cx - 65, box_y + 95, 130, 130, 4, stroke=1, fill=0)

    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["bold_italic"], 12)
    c.drawCentredString(cx, box_y + 68, extras["bookplate_label"])
    c.setFont(fonts["regular"], 11)
    c.drawCentredString(cx, box_y + 46, extras["bookplate_line"])
    c.setStrokeColor(SOFT_BROWN)
    c.setLineWidth(0.8)
    c.line(box_x + 35, box_y + 22, box_x + box_w - 35, box_y + 22)

    if extras["front_footer"]:
        c.setFillColor(SOFT_BROWN)
        c.setFont(fonts["italic"], 10)
        c.drawCentredString(cx, trim_y + 75, extras["front_footer"])
    c.showPage()

    # --- PAGE ii: Full Title, Imprint & Copyright Page ---
    trim_x, trim_y, trim_w, trim_h, cx, margin = start_framed_page(c, *geometry)
    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 28)
    c.drawCentredString(cx, trim_y + trim_h - 110, meta["title"])
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 15)
    c.drawCentredString(cx, trim_y + trim_h - 135, meta.get("subtitle", ""))
    draw_ornamental_divider(c, cx, trim_y + trim_h - 155, width=200)

    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["bold"], 13)
    c.drawCentredString(cx, trim_y + trim_h - 190, f"Written & Produced by {meta['author']}")
    c.setFont(fonts["italic"], 11.5)
    c.drawCentredString(cx, trim_y + trim_h - 210, meta.get("illustrator", ""))

    # Dedication Box
    if meta.get("dedication"):
        c.setFillColor(HexColor("#FAF1DF"))
        c.setStrokeColor(BORDER_TAN)
        c.roundRect(cx - 190, trim_y + trim_h - 285, 380, 52, 8, stroke=1, fill=1)
        c.setFillColor(WARM_BROWN)
        c.setFont(fonts["italic"], 11)
        c.drawCentredString(cx, trim_y + trim_h - 255, f"“{meta['dedication']}”")

    # Copyright block at bottom
    cy = trim_y + 185
    c.setFont(fonts["regular"], 9.5)
    c.setFillColor(SOFT_BROWN)
    for line in copyright_lines(book_data, extras):
        c.drawCentredString(cx, cy, line)
        cy -= 14
    c.showPage()

    if map_info is None:
        return

    # --- PAGE iii: Illustrated Map ---
    trim_x, trim_y, trim_w, trim_h, cx, margin = start_framed_page(c, *geometry)
    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 18)
    c.drawCentredString(cx, trim_y + trim_h - 72, map_info["title"])
    if map_info["subtitle"]:
        c.setFillColor(FOREST_GREEN)
        c.setFont(fonts["italic"], 11.5)
        c.drawCentredString(cx, trim_y + trim_h - 90, map_info["subtitle"])
    draw_ornamental_divider(c, cx, trim_y + trim_h - 104, width=180)

    map_img = paths.illustrations / map_info["file"]
    map_size = 360
    if map_img.exists():
        c.drawImage(str(map_img), cx - map_size / 2.0, trim_y + 125, width=map_size, height=map_size, preserveAspectRatio=True, mask="auto")
        c.setStrokeColor(GOLD_ACCENT)
        c.setLineWidth(1.5)
        c.roundRect(cx - map_size / 2.0, trim_y + 125, map_size, map_size, 6, stroke=1, fill=0)

    # Legend below map
    ly = trim_y + 98
    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["regular"], 9.8)
    for item in map_info["legend"]:
        c.drawCentredString(cx, ly, item)
        ly -= 15
    c.showPage()


def render_blank_page(c: canvas.Canvas, page_w: float, page_h: float):
    """A cream filler page that keeps story spreads facing as they always have."""
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    c.showPage()


def render_drawing_page(c: canvas.Canvas, book_data: dict, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float, fonts: dict, *, extras: dict):
    """A framed blank area for the reader's own picture, padding the interior to KDP's minimum."""
    trim_x, trim_y, trim_w, trim_h, cx, margin = start_framed_page(c, page_w, page_h, bleed_left, bleed_right, bleed_top, bleed_bottom)
    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold_italic"], 18)
    c.drawCentredString(cx, trim_y + trim_h - 78, extras["drawing_caption"])
    draw_ornamental_divider(c, cx, trim_y + trim_h - 92, width=160)

    box_x = trim_x + margin + 16
    box_w = trim_w - 2 * margin - 32
    box_y = trim_y + 88
    box_h = trim_h - 92 - 18 - 88
    c.setFillColor(HexColor("#FFFDF7"))
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(1.5)
    c.roundRect(box_x, box_y, box_w, box_h, 10, stroke=1, fill=1)

    c.setFillColor(SOFT_BROWN)
    c.setFont(fonts["italic"], 10)
    c.drawCentredString(cx, trim_y + 60, book_data["metadata"]["title"])
    c.showPage()


def render_back_matter_page(c: canvas.Canvas, book_data: dict, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float, fonts: dict, *, activity: dict):
    """The last interior page: the activity checklist, talk-about questions and THE END (AC-1.3)."""
    trim_x, trim_y, trim_w, trim_h, cx, margin = start_framed_page(c, page_w, page_h, bleed_left, bleed_right, bleed_top, bleed_bottom)

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 19)
    c.drawCentredString(cx, trim_y + trim_h - 78, activity["title"])
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 12)
    c.drawCentredString(cx, trim_y + trim_h - 96, activity["subtitle"])
    draw_ornamental_divider(c, cx, trim_y + trim_h - 112, width=200)

    items = activity["items"]
    described = any(item.get("text") for item in items)
    y_pos = trim_y + trim_h - 148
    if described:
        for item in items:
            c.setFillColor(HexColor("#FAF1DF"))
            c.setStrokeColor(GOLD_ACCENT)
            c.roundRect(trim_x + margin + 16, y_pos - 42, trim_w - 2 * margin - 32, 54, 8, stroke=1, fill=1)

            # Checkbox square
            c.setStrokeColor(BERRY_RED)
            c.setLineWidth(1.3)
            c.rect(trim_x + margin + 30, y_pos - 24, 16, 16, stroke=1, fill=0)

            c.setFillColor(WARM_BROWN)
            c.setFont(fonts["bold"], 11.5)
            c.drawString(trim_x + margin + 56, y_pos - 8, item["title"])
            c.setFont(fonts["regular"], 10)
            c.setFillColor(SOFT_BROWN)
            wrapped_desc = wrap_text_lines(c, item.get("text", ""), fonts["regular"], 10, trim_w - 2 * margin - 105)
            for d_idx, d_line in enumerate(wrapped_desc[:2]):
                c.drawString(trim_x + margin + 56, y_pos - 24 - d_idx * 13, d_line)
            y_pos -= 68
    else:
        # Names only: two columns of checkbox rows
        col_w = (trim_w - 2 * margin - 32) / 2.0
        rows = math.ceil(len(items) / 2)
        for i, item in enumerate(items):
            col, row = i // rows if rows else 0, i % rows if rows else 0
            x = trim_x + margin + 16 + col * col_w
            y = y_pos - row * 34
            c.setFillColor(HexColor("#FAF1DF"))
            c.setStrokeColor(GOLD_ACCENT)
            c.roundRect(x, y - 22, col_w - 8, 28, 8, stroke=1, fill=1)
            c.setStrokeColor(BERRY_RED)
            c.setLineWidth(1.3)
            c.rect(x + 10, y - 16, 14, 14, stroke=1, fill=0)
            c.setFillColor(WARM_BROWN)
            c.setFont(fonts["bold"], 11.5)
            c.drawString(x + 34, y - 13, item["title"])
        y_pos -= rows * 34 + 20

    # Story Reflection Questions
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["bold_italic"], 13.5)
    c.drawCentredString(cx, y_pos - 10, activity["questions_title"])
    draw_ornamental_divider(c, cx, y_pos - 22, width=150)

    qy = y_pos - 45
    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["regular"], 10.5)
    for number, question in enumerate(activity["questions"], start=1):
        for line in wrap_text_lines(c, f"{number}. {question}", fonts["regular"], 10.5, trim_w - 2 * margin - 24):
            c.drawCentredString(cx, qy, line)
            qy -= 14
        qy -= 6

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold_italic"], 12)
    c.drawCentredString(cx, trim_y + 52, "THE END")
    c.showPage()


BRAND_LOGO = Path("assets") / "brand" / "giggle_and_snooze_logo.png"


def strip_prefix(value: str, prefix: str) -> str:
    value = value.strip()
    return value[len(prefix):].strip() if value.lower().startswith(prefix.lower()) else value


def reading_line(meta: dict, genre: str) -> str:
    """'Reading Age: 3-6 years • Grade Level: 1–3 • <genre>' from the book's metadata."""
    parts = []
    if meta.get("age_range"):
        parts.append(f"Reading Age: {strip_prefix(meta['age_range'], 'Ages')} years")
    if meta.get("grade_level"):
        parts.append(f"Grade Level: {strip_prefix(meta['grade_level'], 'Grades')}")
    if genre:
        parts.append(genre)
    return " • ".join(parts)


def copyright_lines(book_data: dict, extras: dict | None = None) -> list[str]:
    """The copyright page lines; publisher, ages and genre come from the book's data."""
    meta = book_data["metadata"]
    if extras is None:
        extras = load_extras(PROJECT_ROOT, book_data)
    return [
        f"Copyright © {meta['publication_year']} by {meta['publisher']}. All rights reserved.",
        f"Published by {meta['publisher']}.",
        "No part of this publication may be reproduced, distributed, or transmitted in any form",
        "without prior written permission of the publisher, except for brief quotations in reviews.",
        "",
        f"ISBN: {meta['isbn_placeholder']} (Print Edition) | 8.5 x 8.5 in Square Trim",
        reading_line(meta, extras["copyright_genre"]),
        "First Edition • Printed in the United States of America",
    ]


def draw_cover_brand_badge(base: Image.Image, overlay: Image.Image, draw: ImageDraw.ImageDraw, book_data: dict, size_px: int, *, paths: BookFolder) -> None:
    """The channel logo as a centred badge near the bottom of the front cover.

    Falls back to the author's name on a ribbon when the logo file is missing."""
    logo_path = paths.root / BRAND_LOGO
    if logo_path.is_file():
        with Image.open(logo_path) as src:
            logo = src.convert("RGBA")
        badge_w = int(size_px * 0.30)
        badge_h = round(badge_w * logo.height / logo.width)
        logo = logo.resize((badge_w, badge_h), Image.Resampling.LANCZOS)
        pad = int(size_px * 0.012)
        x1 = (size_px - badge_w) // 2
        y1 = int(size_px * 0.955) - badge_h - pad
        draw.rounded_rectangle(
            [x1 - pad, y1 - pad, x1 + badge_w + pad, y1 + badge_h + pad],
            radius=int(size_px * 0.015),
            fill=(253, 248, 238, 225),
            outline=(200, 138, 50, 255),
            width=max(2, size_px // 425),
        )
        overlay.alpha_composite(logo, (x1, y1))
        return
    author_font = get_pil_font(int(size_px * 0.025), bold=True)
    rib_w = int(size_px * 0.56)
    rib_h = int(size_px * 0.058)
    rib_x1 = (size_px - rib_w) // 2
    rib_y1 = int(size_px * 0.90)
    draw.rounded_rectangle(
        [rib_x1, rib_y1, rib_x1 + rib_w, rib_y1 + rib_h],
        radius=24,
        fill=(253, 248, 238, 235),
        outline=(200, 138, 50, 255),
        width=6,
    )
    auth_text = book_data["metadata"]["author"]
    ab = draw.textbbox((0, 0), auth_text, font=author_font)
    aw = ab[2] - ab[0]
    ah = ab[3] - ab[1]
    draw.text(
        ((size_px - aw) // 2, rib_y1 + (rib_h - ah) // 2 - 4),
        auth_text,
        font=author_font,
        fill=(74, 50, 34, 255),
    )


def create_front_cover_image(book_data: dict, out_png: Path, size_px: int = 2550, *, paths: BookFolder):
    """Creates a 300 DPI Front Cover PNG (2550x2550 for 8.5x8.5in) with rich storybook typography."""
    raw_cover = paths.illustrations / "cover_front.png"
    if not raw_cover.exists():
        first = min(book_data["pages"], key=lambda page: page["page_number"])
        raw_cover = paths.illustrations / first["illustration_file"]

    raw_Img = Image.open(raw_cover).convert("RGBA")
    # Shift artwork down slightly on warm cream background so the top is open for the title
    base = Image.new("RGBA", (size_px, size_px), (253, 248, 238, 255))
    art_scaled = raw_Img.resize((size_px, int(size_px * 0.90)), Image.Resampling.LANCZOS)
    base.paste(art_scaled, (0, int(size_px * 0.10)))

    overlay = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Top warm cream title plaque with double storybook border
    plaque_x1, plaque_y1 = int(size_px * 0.06), int(size_px * 0.035)
    plaque_x2, plaque_y2 = int(size_px * 0.94), int(size_px * 0.215)
    draw.rounded_rectangle(
        [plaque_x1, plaque_y1, plaque_x2, plaque_y2],
        radius=34,
        fill=(253, 248, 238, 246),
        outline=(200, 138, 50, 255),
        width=8,
    )
    draw.rounded_rectangle(
        [plaque_x1 + 14, plaque_y1 + 14, plaque_x2 - 14, plaque_y2 - 14],
        radius=24,
        fill=None,
        outline=(184, 59, 53, 220),
        width=4,
    )

    title_font = get_pil_font(int(size_px * 0.048), bold=True)
    sub_font = get_pil_font(int(size_px * 0.025), italic=True)

    # Draw Title
    title_text = book_data["metadata"]["title"].upper()
    tb = draw.textbbox((0, 0), title_text, font=title_font)
    tw = tb[2] - tb[0]
    tx = (size_px - tw) // 2
    ty = plaque_y1 + int(size_px * 0.036)
    # Soft drop shadow
    draw.text((tx + 4, ty + 4), title_text, font=title_font, fill=(216, 195, 165, 255))
    draw.text((tx, ty), title_text, font=title_font, fill=(184, 59, 53, 255))

    # Draw Subtitle with vector diamond ornaments (no missing glyph boxes)
    sub_text = book_data["metadata"]["subtitle"]
    sb = draw.textbbox((0, 0), sub_text, font=sub_font)
    sw = sb[2] - sb[0]
    sh = sb[3] - sb[1]
    sx = (size_px - sw) // 2
    sy = ty + int(size_px * 0.072)
    draw.text((sx, sy), sub_text, font=sub_font, fill=(62, 107, 72, 255))

    # Gold diamond ornaments flanking subtitle
    dy_mid = sy + sh // 2 + 6
    for dx_center in [sx - 36, sx + sw + 36]:
        draw.polygon(
            [(dx_center, dy_mid - 10), (dx_center + 10, dy_mid), (dx_center, dy_mid + 10), (dx_center - 10, dy_mid)],
            fill=(200, 138, 50, 255),
        )

    draw_cover_brand_badge(base, overlay, draw, book_data, size_px, paths=paths)

    # Outer gold inset border
    inset = int(size_px * 0.025)
    draw.rounded_rectangle(
        [inset, inset, size_px - inset, size_px - inset],
        radius=20,
        fill=None,
        outline=(200, 138, 50, 220),
        width=5,
    )

    final_img = Image.alpha_composite(base, overlay).convert("RGB")
    final_img.save(out_png, quality=98, dpi=(300, 300))
    return final_img


def create_back_cover_image(book_data: dict, out_png: Path, size_px: int = 2550, *, paths: BookFolder, extras: dict):
    """Creates a 300 DPI Back Cover PNG with story blurb, age badge, and KDP barcode zone."""
    raw_back = paths.illustrations / "cover_back.png"
    if not raw_back.exists():
        last = max(book_data["pages"], key=lambda page: page["page_number"])
        raw_back = paths.illustrations / last["illustration_file"]

    base = Image.open(raw_back).convert("RGBA").resize((size_px, size_px), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Warm cream parchment panel in upper 68%
    px1, py1 = int(size_px * 0.08), int(size_px * 0.07)
    px2, py2 = int(size_px * 0.92), int(size_px * 0.70)
    draw.rounded_rectangle(
        [px1, py1, px2, py2],
        radius=36,
        fill=(253, 248, 238, 242),
        outline=(200, 138, 50, 255),
        width=8,
    )
    draw.rounded_rectangle(
        [px1 + 16, py1 + 16, px2 - 16, py2 - 16],
        radius=26,
        fill=None,
        outline=(216, 195, 165, 255),
        width=4,
    )

    head_font = get_pil_font(int(size_px * 0.038), bold=True, italic=True)
    body_font = get_pil_font(int(size_px * 0.027))
    badge_font = get_pil_font(int(size_px * 0.024), bold=True)

    headline = extras["back_cover_headline"]
    if headline:
        hb = draw.textbbox((0, 0), headline, font=head_font)
        hw = hb[2] - hb[0]
        draw.text(((size_px - hw) // 2, py1 + int(size_px * 0.05)), headline, font=head_font, fill=(184, 59, 53, 255))

    # Wrap blurb text
    blurb = book_data["metadata"]["blurb"]
    words = blurb.split()
    lines = []
    cur = []
    max_w = (px2 - px1) - int(size_px * 0.12)
    for w in words:
        test = " ".join(cur + [w])
        tb = draw.textbbox((0, 0), test, font=body_font)
        if (tb[2] - tb[0]) <= max_w:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))

    ty = py1 + int(size_px * 0.13)
    line_h = int(size_px * 0.042)
    for line in lines:
        lb = draw.textbbox((0, 0), line, font=body_font)
        lw = lb[2] - lb[0]
        draw.text(((size_px - lw) // 2, ty), line, font=body_font, fill=(74, 50, 34, 255))
        ty += line_h

    # Age / Grade Pill inside bottom of parchment card
    meta = book_data["metadata"]
    pill_parts = [meta.get("age_range", ""), meta.get("grade_level", ""), meta["publisher"]]
    pill_text = "  •  ".join(part.upper() for part in pill_parts if part)
    pb = draw.textbbox((0, 0), pill_text, font=badge_font)
    pw = pb[2] - pb[0]
    draw.text(((size_px - pw) // 2, py2 - int(size_px * 0.065)), pill_text, font=badge_font, fill=(62, 107, 72, 255))

    # Standard Amazon KDP ISBN Barcode Box (2.0" x 1.2" = 600x360 px at 300 DPI) in bottom right safe zone
    bc_w, bc_h = int(2.0 * 300), int(1.2 * 300)
    bc_x = size_px - int(size_px * 0.08) - bc_w
    bc_y = size_px - int(size_px * 0.07) - bc_h
    draw.rounded_rectangle(
        [bc_x, bc_y, bc_x + bc_w, bc_y + bc_h],
        radius=12,
        fill=(255, 255, 255, 255),
        outline=(200, 138, 50, 255),
        width=4,
    )
    small_font = get_pil_font(int(size_px * 0.0145), bold=True)
    isbn_label = f"ISBN {book_data['metadata']['isbn_placeholder']}"
    ib = draw.textbbox((0, 0), isbn_label, font=small_font)
    draw.text((bc_x + (bc_w - (ib[2] - ib[0])) // 2, bc_y + 28), isbn_label, font=small_font, fill=(74, 50, 34, 255))

    # Simulated barcode bars for visual completeness (KDP can also place theirs right over this white box)
    bar_x = bc_x + 70
    for i in range(42):
        bw = 4 if i % 3 == 0 else (8 if i % 5 == 0 else 2)
        draw.rectangle([bar_x, bc_y + 85, bar_x + bw, bc_y + bc_h - 40], fill=(40, 40, 40, 255))
        bar_x += bw + 6

    final_img = Image.alpha_composite(base, overlay).convert("RGB")
    final_img.save(out_png, quality=98, dpi=(300, 300))
    return final_img


def create_kdp_wrap_cover(front_img: Image.Image, back_img: Image.Image, out_png: Path, out_pdf: Path, interior_pages: int):
    """
    Creates the full Amazon KDP Print Wrap Cover (Back + Spine + Front + bleed on all edges).
    The spine width comes from the interior page count P (AC-2.2): P * 0.002252" on colour
    paper; for P = 24 the wrap is 17.304" x 8.75" (5191 x 2625 px at 300 DPI).
    """
    dpi = COVER_DPI
    wrap_w_in, wrap_h_in = wrap_size_inches(interior_pages)
    wrap_w_px = int(round(wrap_w_in * dpi))
    wrap_h_px = int(round(wrap_h_in * dpi))
    panel_px = int(round((TRIM_IN + BLEED_IN) * dpi))  # trim + outer bleed

    wrap = Image.new("RGB", (wrap_w_px, wrap_h_px), (74, 50, 34))
    back_resized = back_img.resize((panel_px, wrap_h_px), Image.Resampling.LANCZOS)
    front_resized = front_img.resize((panel_px, wrap_h_px), Image.Resampling.LANCZOS)

    wrap.paste(back_resized, (0, 0))
    wrap.paste(front_resized, (wrap_w_px - panel_px, 0))

    # Center gold spine strip
    draw = ImageDraw.Draw(wrap)
    spine_x1 = panel_px
    spine_x2 = wrap_w_px - panel_px
    draw.rectangle([spine_x1, 0, spine_x2, wrap_h_px], fill=(184, 59, 53))
    wrap.save(out_png, quality=98, dpi=(300, 300))

    # Also save as exact KDP PDF
    c = canvas.Canvas(str(out_pdf), pagesize=(wrap_w_in * inch, wrap_h_in * inch))
    c.drawImage(str(out_png), 0, 0, width=wrap_w_in * inch, height=wrap_h_in * inch)
    c.showPage()
    c.save()


def build_pdfs(book_data: dict, fonts: dict, *, paths: BookFolder, extras: dict, plan: list[InteriorPage]) -> dict[str, str]:
    """Builds the PDF editions (N-page digital, complete reader, P-page KDP interior) and
    returns the file names written."""
    stem = file_stem(book_data["metadata"]["title"])
    stories = sorted(book_data["pages"], key=lambda page: page["page_number"])
    names = edition_names(stem, len(stories), len(plan))
    trim_w = TRIM_IN * inch
    trim_h = TRIM_IN * inch

    # 1. Digital edition: the story pages only (8.5" x 8.5", zero bleed)
    c_digital = canvas.Canvas(str(paths.build_dir / names["digital"]), pagesize=(trim_w, trim_h))
    c_digital.setTitle(book_data["metadata"]["title"])
    c_digital.setAuthor(book_data["metadata"]["author"])
    for page in stories:
        render_story_page(c_digital, page, trim_w, trim_h, 0, 0, 0, 0, fonts, paths=paths)
        c_digital.showPage()
    c_digital.save()

    # 2. Complete Reader Edition (Front Cover + Story Pages + Back Cover)
    creader = canvas.Canvas(str(paths.build_dir / names["reader"]), pagesize=(trim_w, trim_h))
    creader.setTitle(book_data["metadata"]["title"] + " (Complete Reader Edition)")
    creader.setAuthor(book_data["metadata"]["author"])
    front_png = paths.build_dir / names["front_png"]
    back_png = paths.build_dir / names["back_png"]

    if front_png.exists():
        creader.drawImage(str(front_png), 0, 0, width=trim_w, height=trim_h)
        creader.showPage()
    for page in stories:
        render_story_page(creader, page, trim_w, trim_h, 0, 0, 0, 0, fonts, paths=paths)
        creader.showPage()
    if back_png.exists():
        creader.drawImage(str(back_png), 0, 0, width=trim_w, height=trim_h)
        creader.showPage()
    creader.save()

    # 3. Amazon KDP Print-Ready Interior with Bleed (8.625" x 8.75"), page by page from the plan
    bleed = BLEED_IN * inch
    kdp_w = (TRIM_IN + BLEED_IN) * inch
    kdp_h = (TRIM_IN + 2 * BLEED_IN) * inch
    ckdp = canvas.Canvas(str(paths.build_dir / names["kdp"]), pagesize=(kdp_w, kdp_h))
    ckdp.setTitle(book_data["metadata"]["title"] + " (KDP Print Interior)")
    ckdp.setAuthor(book_data["metadata"]["author"])

    pictures = map_pictures(paths.illustrations)
    map_info = map_content(book_data, extras, pictures) if pictures else None
    activity = activity_content(book_data, extras)
    for page in plan:
        # Right-hand (odd) pages bleed on the right; left-hand (even) pages on the left
        b_left, b_right = story_bleed(page.number, bleed)
        if page.kind == "half_title":
            # Front matter keeps half the bleed on each side
            render_front_matter_pages(ckdp, book_data, kdp_w, kdp_h, bleed / 2, bleed / 2, bleed, bleed, fonts, paths=paths, extras=extras, map_info=map_info)
        elif page.kind == "blank":
            render_blank_page(ckdp, kdp_w, kdp_h)
        elif page.kind == "story":
            render_story_page(ckdp, page.story, kdp_w, kdp_h, b_left, b_right, bleed, bleed, fonts, paths=paths)
            ckdp.showPage()
        elif page.kind == "drawing":
            render_drawing_page(ckdp, book_data, kdp_w, kdp_h, b_left, b_right, bleed, bleed, fonts, extras=extras)
        elif page.kind == "activity":
            render_back_matter_page(ckdp, book_data, kdp_w, kdp_h, b_left, b_right, bleed, bleed, fonts, activity=activity)
    ckdp.save()
    return names


def build_epub(book_data: dict, *, paths: BookFolder):
    """Builds a valid, standards-compliant EPUB 3 file for Amazon Kindle KDP & Apple Books."""
    stem = file_stem(book_data["metadata"]["title"])
    epub_path = paths.build_dir / f"{stem}.epub"
    book_uuid = str(uuid.uuid4())

    with zipfile.ZipFile(epub_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. mimetype MUST be first and uncompressed
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. META-INF/container.xml
        container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        zf.writestr("META-INF/container.xml", container_xml)

        # 3. Stylesheet
        css = """
body {
  background-color: #FDF8EE;
  color: #4A3222;
  font-family: Georgia, "Times New Roman", serif;
  margin: 0;
  padding: 1.2em;
  line-height: 1.55;
}
.page-card {
  max-width: 680px;
  margin: 0 auto;
  background: #FDF8EE;
  border: 2px solid #C88A32;
  border-radius: 12px;
  padding: 1.2em;
  box-sizing: border-box;
}
.illustration {
  text-align: center;
  margin-bottom: 1em;
}
.illustration img {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
  border: 2px solid #C88A32;
}
h1, h2 {
  color: #3E6B48;
  text-align: center;
  font-style: italic;
  margin: 0.4em 0;
}
.divider {
  text-align: center;
  color: #B83B35;
  margin: 0.4em 0 0.8em 0;
  font-size: 0.9em;
}
p {
  font-size: 1.08em;
  margin: 0 0 0.85em 0;
  text-align: left;
}
.dropcap {
  float: left;
  color: #B83B35;
  font-size: 2.7em;
  line-height: 0.85;
  padding-right: 0.12em;
  font-weight: bold;
}
.page-num {
  text-align: center;
  color: #7A583E;
  font-weight: bold;
  font-size: 0.9em;
  margin-top: 1em;
}
"""
        zf.writestr("OEBPS/styles/storybook.css", css)

        # Copy cover and page images (resized to crisp 1400x1400 JPEG for fast ereader loading)
        manifest_items = [
            '<item id="css" href="styles/storybook.css" media-type="text/css"/>',
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        ]
        spine_items = []

        # Cover image & XHTML
        front_png = paths.build_dir / f"{stem}_Front_Cover.png"
        if front_png.exists():
            im = Image.open(front_png).convert("RGB").resize((1400, 1400), Image.Resampling.LANCZOS)
            tmp_cov = paths.build_dir / "_tmp_cov.jpg"
            im.save(tmp_cov, "JPEG", quality=92)
            zf.write(tmp_cov, "OEBPS/images/cover.jpg")
            tmp_cov.unlink()
            manifest_items.append('<item id="cover-img" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>')

        cover_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
  <title>{book_data['metadata']['title']}</title>
  <link rel="stylesheet" type="text/css" href="styles/storybook.css"/>
</head>
<body>
  <div class="illustration"><img src="images/cover.jpg" alt="Front Cover"/></div>
</body>
</html>"""
        zf.writestr("OEBPS/cover.xhtml", cover_xhtml)
        manifest_items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append('<itemref idref="cover"/>')

        # Story pages
        nav_li = []
        ncx_navpoints = []
        for page in book_data["pages"]:
            pnum = page["page_number"]
            img_file = paths.illustrations / page["illustration_file"]
            jpg_name = f"page_{pnum:02d}.jpg"
            if img_file.exists():
                im = Image.open(img_file).convert("RGB").resize((1400, 1400), Image.Resampling.LANCZOS)
                tmp_jpg = paths.build_dir / f"_tmp_{pnum}.jpg"
                im.save(tmp_jpg, "JPEG", quality=90)
                zf.write(tmp_jpg, f"OEBPS/images/{jpg_name}")
                tmp_jpg.unlink()
                manifest_items.append(f'<item id="img-{pnum:02d}" href="images/{jpg_name}" media-type="image/jpeg"/>')

            paras = [p.strip() for p in page["text"].split("\n\n") if p.strip()]
            html_paras = []
            for idx, p in enumerate(paras):
                p_esc = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                if idx == 0 and len(p_esc) > 1:
                    html_paras.append(f'<p><span class="dropcap">{p_esc[0]}</span>{p_esc[1:]}</p>')
                else:
                    html_paras.append(f"<p>{p_esc}</p>")

            xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
  <title>Page {pnum}: {page['title']}</title>
  <link rel="stylesheet" type="text/css" href="styles/storybook.css"/>
</head>
<body>
  <div class="page-card">
    <div class="illustration"><img src="images/{jpg_name}" alt="{page['title']}"/></div>
    <h2>{page['title']}</h2>
    <div class="divider">✦ ❖ ✦</div>
    {''.join(html_paras)}
    <div class="page-num">— {pnum} —</div>
  </div>
</body>
</html>"""
            fname = f"page_{pnum:02d}.xhtml"
            zf.writestr(f"OEBPS/{fname}", xhtml)
            manifest_items.append(f'<item id="page-{pnum:02d}" href="{fname}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'<itemref idref="page-{pnum:02d}"/>')
            nav_li.append(f'<li><a href="{fname}">Page {pnum}: {page["title"]}</a></li>')
            ncx_navpoints.append(f"""
    <navPoint id="navPoint-{pnum}" playOrder="{pnum}">
      <navLabel><text>Page {pnum}: {page["title"]}</text></navLabel>
      <content src="{fname}"/>
    </navPoint>""")

        # nav.xhtml
        nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
<head><title>Table of Contents</title><link rel="stylesheet" type="text/css" href="styles/storybook.css"/></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{book_data['metadata']['title']}</h1>
    <ol>
      <li><a href="cover.xhtml">Cover</a></li>
      {''.join(nav_li)}
    </ol>
  </nav>
</body>
</html>"""
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)

        # toc.ncx
        toc_ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
  </head>
  <docTitle><text>{book_data['metadata']['title']}</text></docTitle>
  <navMap>
    {''.join(ncx_navpoints)}
  </navMap>
</ncx>"""
        zf.writestr("OEBPS/toc.ncx", toc_ncx)

        # content.opf
        content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">urn:uuid:{book_uuid}</dc:identifier>
    <dc:title>{book_data['metadata']['title']}: {book_data['metadata']['subtitle']}</dc:title>
    <dc:creator>{book_data['metadata']['author']}</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>{book_data['metadata']['publisher']}</dc:publisher>
    <dc:description>{book_data['metadata']['blurb']}</dc:description>
    <meta property="dcterms:modified">2026-11-07T00:00:00Z</meta>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
    {''.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {''.join(spine_items)}
  </spine>
</package>"""
        zf.writestr("OEBPS/content.opf", content_opf)


def build_interactive_flipbook(book_data: dict, *, paths: BookFolder, interior_pages: int):
    """Builds a standalone interactive HTML5 Flipbook in build/index.html (links from AC-3.1)."""
    stem = file_stem(book_data["metadata"]["title"])
    stories = sorted(book_data["pages"], key=lambda page: page["page_number"])
    names = edition_names(stem, len(stories), interior_pages)
    slides_json = json.dumps(stories, indent=2)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{book_data['metadata']['title']} — Interactive Picture Book Preview</title>
  <style>
    :root {{
      --cream: #FDF8EE;
      --parchment: #FAF1DF;
      --brown: #4A3222;
      --soft-brown: #7A583E;
      --gold: #C88A32;
      --berry: #B83B35;
      --forest: #3E6B48;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top, #2E241D 0%, #19130E 100%);
      color: var(--brown);
      font-family: Georgia, "Times New Roman", serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px;
    }}
    header {{
      text-align: center;
      color: var(--cream);
      margin-bottom: 16px;
    }}
    header h1 {{
      margin: 0;
      font-size: 1.9rem;
      color: #F6D58E;
      letter-spacing: 0.5px;
    }}
    header p {{
      margin: 4px 0 0;
      font-style: italic;
      color: #D8C3A5;
      font-size: 0.95rem;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: center;
      margin-bottom: 18px;
    }}
    button, a.btn {{
      background: var(--cream);
      color: var(--brown);
      border: 2px solid var(--gold);
      border-radius: 999px;
      padding: 8px 18px;
      font-family: Georgia, serif;
      font-weight: bold;
      font-size: 0.9rem;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.2s ease;
    }}
    button:hover, a.btn:hover {{
      background: var(--berry);
      color: #fff;
      border-color: #fff;
    }}
    .book-stage {{
      width: min(92vw, 760px);
      aspect-ratio: 1 / 1;
      background: var(--cream);
      border: 6px solid var(--gold);
      border-radius: 16px;
      box-shadow: 0 24px 60px rgba(0,0,0,0.65);
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      padding: 22px;
    }}
    .inner-frame {{
      border: 2px solid #D8C3A5;
      border-radius: 10px;
      flex: 1;
      display: flex;
      flex-direction: column;
      padding: 16px 22px;
      position: relative;
      overflow-y: auto;
    }}
    .art-box {{
      width: 58%;
      aspect-ratio: 1 / 1;
      margin: 0 auto 12px;
      border: 3px solid var(--gold);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 6px 18px rgba(74,50,34,0.18);
      flex-shrink: 0;
    }}
    .art-box.cover-mode {{
      width: 92%;
      margin: auto;
    }}
    .art-box img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .chapter-title {{
      text-align: center;
      color: var(--forest);
      font-style: italic;
      font-weight: bold;
      font-size: 1.15rem;
      margin: 2px 0 4px;
    }}
    .divider {{
      text-align: center;
      color: var(--berry);
      font-size: 0.85rem;
      margin-bottom: 8px;
    }}
    .story-text {{
      font-size: 1.02rem;
      line-height: 1.52;
      color: var(--brown);
    }}
    .story-text p {{
      margin: 0 0 10px 0;
    }}
    .dropcap {{
      float: left;
      color: var(--berry);
      font-size: 2.5rem;
      line-height: 0.85;
      padding-right: 8px;
      font-weight: bold;
    }}
    .page-badge {{
      margin-top: auto;
      text-align: center;
      padding-top: 6px;
      font-weight: bold;
      color: var(--soft-brown);
      font-size: 0.9rem;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{book_data['metadata']['title']}</h1>
    <p>{book_data['metadata']['subtitle']} • {book_data['metadata']['age_range']} • 8.5" × 8.5" Square Edition</p>
  </header>

  <div class="toolbar">
    <button onclick="prevSlide()">◀ Previous Page</button>
    <span id="pageIndicator" style="color:#FDF8EE; align-self:center; font-weight:bold; min-width:150px; text-align:center;">Cover</span>
    <button onclick="nextSlide()">Next Page ▶</button>
    <a class="btn" href="{names['digital']}" target="_blank">📄 {len(stories)}-Page PDF</a>
    <a class="btn" href="{names['kdp']}" target="_blank">🖨️ KDP {interior_pages}-Page Print PDF</a>
    <a class="btn" href="{names['wrap_pdf']}" target="_blank">📕 KDP Wrap Cover</a>
    <a class="btn" href="{names['epub']}" target="_blank">📱 Kindle EPUB</a>
  </div>

  <div class="book-stage">
    <div class="inner-frame" id="stageContent"></div>
  </div>

  <script>
    const pages = {slides_json};
    let idx = 0; // 0 = Front Cover, 1..pages.length = Story Pages, pages.length + 1 = Back Cover
    const last = pages.length + 1;

    function render() {{
      const el = document.getElementById('stageContent');
      const ind = document.getElementById('pageIndicator');
      if (idx === 0) {{
        ind.textContent = 'Front Cover';
        el.innerHTML = `<div class="art-box cover-mode"><img src="{stem}_Front_Cover.png" alt="Front Cover"/></div>`;
      }} else if (idx === last) {{
        ind.textContent = 'Back Cover';
        el.innerHTML = `<div class="art-box cover-mode"><img src="{stem}_Back_Cover.png" alt="Back Cover"/></div>`;
      }} else {{
        const p = pages[idx - 1];
        ind.textContent = `Page ${{p.page_number}} of ${{pages.length}}`;
        const paras = p.text.split('\\n\\n').map((t, i) => {{
          if (i === 0) return `<p><span class="dropcap">${{t.charAt(0)}}</span>${{t.slice(1)}}</p>`;
          return `<p>${{t}}</p>`;
        }}).join('');
        el.innerHTML = `
          <div class="art-box"><img src="../illustrations/${{p.illustration_file}}" alt="${{p.title}}"/></div>
          <div class="chapter-title">${{p.title}}</div>
          <div class="divider">✦ ❖ ✦</div>
          <div class="story-text">${{paras}}</div>
          <div class="page-badge">— ${{p.page_number}} —</div>
        `;
      }}
    }}
    function prevSlide() {{ if (idx > 0) {{ idx--; render(); }} }}
    function nextSlide() {{ if (idx < last) {{ idx++; render(); }} }}
    window.addEventListener('keydown', (e) => {{
      if (e.key === 'ArrowLeft') prevSlide();
      if (e.key === 'ArrowRight') nextSlide();
    }});
    render();
  </script>
</body>
</html>"""
    (paths.build_dir / "index.html").write_text(html, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the print and digital editions of one book.")
    parser.add_argument("--book", required=True, help="book slug, e.g. the-snail-mail-express")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        paths = book_folder(args.book, args.root)
    except LibraryError as exc:
        print(f"build stopped: {exc}", file=sys.stderr)
        return 2
    book_data = json.loads(paths.book_json.read_text(encoding="utf-8"))
    try:
        extras = load_extras(paths.root, book_data)
    except ExtrasError as exc:
        print(f"build stopped: {exc}", file=sys.stderr)
        return 2
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem(book_data["metadata"]["title"])
    plan = plan_interior(book_data, has_map=bool(map_pictures(paths.illustrations)))
    story_count = len(book_data["pages"])
    fonts = register_fonts()

    print("1. Compositing 300 DPI Front & Back Covers...")
    front_png = paths.build_dir / f"{stem}_Front_Cover.png"
    back_png = paths.build_dir / f"{stem}_Back_Cover.png"
    front_img = create_front_cover_image(book_data, front_png, paths=paths)
    back_img = create_back_cover_image(book_data, back_png, paths=paths, extras=extras)

    print(f"2. Building Amazon KDP Print Wrap Cover for {len(plan)} interior pages (PDF & PNG)...")
    wrap_png = paths.build_dir / f"{stem}_KDP_Print_Wrap_Cover.png"
    wrap_pdf = paths.build_dir / f"{stem}_KDP_Print_Wrap_Cover.pdf"
    create_kdp_wrap_cover(front_img, back_img, wrap_png, wrap_pdf, len(plan))

    print(f"3. Building {story_count}-Page Digital PDF, Complete Reader PDF, and {len(plan)}-Page KDP Print Interior PDF...")
    written = build_pdfs(book_data, fonts, paths=paths, extras=extras, plan=plan)

    print("4. Building Kindle / Universal EPUB 3 package...")
    build_epub(book_data, paths=paths)

    print("5. Building Interactive HTML5 Flipbook...")
    build_interactive_flipbook(book_data, paths=paths, interior_pages=len(plan))

    keep = set((written or edition_names(stem, story_count, len(plan))).values())
    for old in stale_editions(paths.build_dir, stem, keep):
        print("Removing stale edition:", old.name)
        old.unlink()

    print("Done! All production deliverables written to:", paths.build_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
