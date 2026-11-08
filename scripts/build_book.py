#!/usr/bin/env python3
"""
Production Book Builder for "The Snail Mail Express"
Generates:
1. The_Snail_Mail_Express_20Page_Digital_Edition.pdf (Strict 20-page story edition, 8.5" x 8.5")
2. The_Snail_Mail_Express_Complete_Reader.pdf (Front Cover + 20 Story Pages + Back Cover)
3. The_Snail_Mail_Express_KDP_24Page_Print_Interior.pdf (Amazon KDP Print-Ready 24-page interior with bleed: 8.625" x 8.75")
4. The_Snail_Mail_Express_KDP_Print_Wrap_Cover.pdf & .png (Amazon KDP Paperback Wrap Cover with bleed & barcode zone)
5. The_Snail_Mail_Express_Front_Cover.png & Back_Cover.png (300 DPI standalone covers)
6. The_Snail_Mail_Express.epub (EPUB 3 for Amazon Kindle KDP, Apple Books, Kobo)
7. build/index.html (Interactive HTML5 Flipbook Preview)
"""

import json
import math
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path("/Users/swiveltech/Disk/Swivel/kiddo")
ILLUSTRATIONS_DIR = ROOT / "assets" / "illustrations"
BUILD_DIR = ROOT / "build"
BOOK_DATA_PATH = ROOT / "book_data.json"

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

    img_path = ILLUSTRATIONS_DIR / page_data["illustration_file"]
    layout = page_data.get("layout", "image_top_text_bottom")
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


def render_front_matter_pages(c: canvas.Canvas, book_data: dict, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float, fonts: dict):
    """Renders the 3 Front Matter pages for the 24-page KDP Print-Ready Interior."""
    trim_w = page_w - bleed_left - bleed_right
    trim_h = page_h - bleed_top - bleed_bottom
    trim_x = bleed_left
    trim_y = bleed_bottom
    cx = trim_x + trim_w / 2.0
    margin = 42.0

    # --- PAGE i: Half-Title & Ex-Libris Bookplate ---
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_botanical_frame(c, trim_x + margin - 8, trim_y + margin - 8, trim_w - 2 * margin + 16, trim_h - 2 * margin + 16)

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 26)
    c.drawCentredString(cx, trim_y + trim_h - 115, book_data["metadata"]["title"])
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 14)
    c.drawCentredString(cx, trim_y + trim_h - 140, book_data["metadata"]["subtitle"])
    draw_ornamental_divider(c, cx, trim_y + trim_h - 158, width=180)

    # Ex-Libris Box
    box_w, box_h = 280, 250
    box_x = cx - box_w / 2.0
    box_y = trim_y + 135
    c.setFillColor(HexColor("#FAF1DF"))
    c.setStrokeColor(GOLD_ACCENT)
    c.setLineWidth(1.5)
    c.roundRect(box_x, box_y, box_w, box_h, 12, stroke=1, fill=1)

    p1_img = ILLUSTRATIONS_DIR / "page_01.png"
    if p1_img.exists():
        c.drawImage(str(p1_img), cx - 65, box_y + 95, width=130, height=130, preserveAspectRatio=True, mask="auto")
        c.setStrokeColor(GOLD_ACCENT)
        c.roundRect(cx - 65, box_y + 95, 130, 130, 4, stroke=1, fill=0)

    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["bold_italic"], 12)
    c.drawCentredString(cx, box_y + 68, "EX LIBRIS • OFFICIAL POSTMASTER LOG")
    c.setFont(fonts["regular"], 11)
    c.drawCentredString(cx, box_y + 46, "This storybook belongs to:")
    c.setStrokeColor(SOFT_BROWN)
    c.setLineWidth(0.8)
    c.line(box_x + 35, box_y + 22, box_x + box_w - 35, box_y + 22)

    c.setFillColor(SOFT_BROWN)
    c.setFont(fonts["italic"], 10)
    c.drawCentredString(cx, trim_y + 75, "Whisperwood Hollow Postal Service • Established under the Great Oak")
    c.showPage()

    # --- PAGE ii: Full Title, Imprint & Copyright Page ---
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_botanical_frame(c, trim_x + margin - 8, trim_y + margin - 8, trim_w - 2 * margin + 16, trim_h - 2 * margin + 16)

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 28)
    c.drawCentredString(cx, trim_y + trim_h - 110, book_data["metadata"]["title"])
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 15)
    c.drawCentredString(cx, trim_y + trim_h - 135, book_data["metadata"]["subtitle"])
    draw_ornamental_divider(c, cx, trim_y + trim_h - 155, width=200)

    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["bold"], 13)
    c.drawCentredString(cx, trim_y + trim_h - 190, f"Written & Produced by {book_data['metadata']['author']}")
    c.setFont(fonts["italic"], 11.5)
    c.drawCentredString(cx, trim_y + trim_h - 210, book_data["metadata"]["illustrator"])

    # Dedication Box
    c.setFillColor(HexColor("#FAF1DF"))
    c.setStrokeColor(BORDER_TAN)
    c.roundRect(cx - 190, trim_y + trim_h - 285, 380, 52, 8, stroke=1, fill=1)
    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["italic"], 11)
    c.drawCentredString(cx, trim_y + trim_h - 255, f"“{book_data['metadata']['dedication']}”")

    # Copyright block at bottom
    copy_lines = [
        f"Copyright © {book_data['metadata']['publication_year']} by {book_data['metadata']['publisher']}. All rights reserved.",
        "Published by Whisperwood Storybook Press, an imprint of Swivel Kids Press.",
        "No part of this publication may be reproduced, distributed, or transmitted in any form",
        "without prior written permission of the publisher, except for brief quotations in reviews.",
        "",
        f"ISBN: {book_data['metadata']['isbn_placeholder']} (Print Edition) | 8.5 x 8.5 in Square Trim",
        "Reading Age: 6–8 years • Grade Level: 1–3 • Juvenile Fiction / Animals / Woodland",
        "First Edition • Printed in the United States of America",
    ]
    cy = trim_y + 185
    c.setFont(fonts["regular"], 9.5)
    c.setFillColor(SOFT_BROWN)
    for line in copy_lines:
        c.drawCentredString(cx, cy, line)
        cy -= 14
    c.showPage()

    # --- PAGE iii: Illustrated Map of Whisperwood Hollow ---
    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_botanical_frame(c, trim_x + margin - 8, trim_y + margin - 8, trim_w - 2 * margin + 16, trim_h - 2 * margin + 16)

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 18)
    c.drawCentredString(cx, trim_y + trim_h - 72, "Map of Whisperwood Hollow")
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 11.5)
    c.drawCentredString(cx, trim_y + trim_h - 90, "The Daily Route of Clara & Barnaby's Snail Mail Express")
    draw_ornamental_divider(c, cx, trim_y + trim_h - 104, width=180)

    map_img = ILLUSTRATIONS_DIR / "map_whisperwood.png"
    map_size = 360
    if map_img.exists():
        c.drawImage(str(map_img), cx - map_size / 2.0, trim_y + 125, width=map_size, height=map_size, preserveAspectRatio=True, mask="auto")
        c.setStrokeColor(GOLD_ACCENT)
        c.setLineWidth(1.5)
        c.roundRect(cx - map_size / 2.0, trim_y + 125, map_size, map_size, 6, stroke=1, fill=0)

    # Legend below map
    legend_items = [
        "1. The Great Oak Post Office (Start)     2. Bramble Pond & Millie's Rock",
        "3. Whispering Birch Grove (Tea Stop)     4. The Great Hollow Elm (Bramwell's Bakery)",
        "5. Mist-Stone Bridge (Fergus's Crossing)  6. The Old Weeping Willow (Fleet's Perch)",
    ]
    ly = trim_y + 98
    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["regular"], 9.8)
    for item in legend_items:
        c.drawCentredString(cx, ly, item)
        ly -= 15
    c.showPage()


def render_back_matter_page(c: canvas.Canvas, book_data: dict, page_w: float, page_h: float, bleed_left: float, bleed_right: float, bleed_top: float, bleed_bottom: float, fonts: dict):
    """Renders Page 24: Postmaster's Field Guide & Activity Page for KDP 24-page compliance."""
    trim_w = page_w - bleed_left - bleed_right
    trim_h = page_h - bleed_top - bleed_bottom
    trim_x = bleed_left
    trim_y = bleed_bottom
    cx = trim_x + trim_w / 2.0
    margin = 42.0

    c.setFillColor(CREAM_BG)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)
    draw_botanical_frame(c, trim_x + margin - 8, trim_y + margin - 8, trim_w - 2 * margin + 16, trim_h - 2 * margin + 16)

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold"], 19)
    c.drawCentredString(cx, trim_y + trim_h - 78, "The Junior Postmaster's Field Guide")
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["italic"], 12)
    c.drawCentredString(cx, trim_y + trim_h - 96, "Did you spot all the woodland treasures along Fern-Fiddle Lane?")
    draw_ornamental_divider(c, cx, trim_y + trim_h - 112, width=200)

    treasures = [
        ("The Silver Dewdrop Thimble", "Returned to Millie the Seamstress Ant at Bramble Pond so she could stitch her maple-leaf boat sail."),
        ("The Ticking Acorn Pocket-Watch", "Cleaned and wound inside Barnaby's cottage shell, then delivered to Bramwell the Truffle Boar right on time."),
        ("The Birch-Bark Wind-Chime Scroll", "Hung beside Barnaby's glowing lantern so the evening breeze could play Fleet the Falcon's lost lullaby."),
        ("Clara's Teal Postmaster Logbook", "Where every clue, thread, and thankful neighbor in Whisperwood Hollow is carefully recorded!"),
    ]

    y_pos = trim_y + trim_h - 148
    for title, desc in treasures:
        c.setFillColor(HexColor("#FAF1DF"))
        c.setStrokeColor(GOLD_ACCENT)
        c.roundRect(trim_x + margin + 16, y_pos - 42, trim_w - 2 * margin - 32, 54, 8, stroke=1, fill=1)

        # Checkbox square
        c.setStrokeColor(BERRY_RED)
        c.setLineWidth(1.3)
        c.rect(trim_x + margin + 30, y_pos - 24, 16, 16, stroke=1, fill=0)

        c.setFillColor(WARM_BROWN)
        c.setFont(fonts["bold"], 11.5)
        c.drawString(trim_x + margin + 56, y_pos - 8, title)
        c.setFont(fonts["regular"], 10)
        c.setFillColor(SOFT_BROWN)
        wrapped_desc = wrap_text_lines(c, desc, fonts["regular"], 10, trim_w - 2 * margin - 105)
        for d_idx, d_line in enumerate(wrapped_desc[:2]):
            c.drawString(trim_x + margin + 56, y_pos - 24 - d_idx * 13, d_line)
        y_pos -= 68

    # Story Reflection Questions
    c.setFillColor(FOREST_GREEN)
    c.setFont(fonts["bold_italic"], 13.5)
    c.drawCentredString(cx, y_pos - 10, "Bedtime Questions for Young Readers")
    draw_ornamental_divider(c, cx, y_pos - 22, width=150)

    questions = [
        "1. Why was Clara able to spot the red polka-dot thread when faster animals missed it?",
        "2. Which room inside Barnaby's cozy cottage shell would be your favorite place to visit?",
        "3. What is something special you noticed today when you took your time and looked closely?",
    ]
    qy = y_pos - 45
    c.setFillColor(WARM_BROWN)
    c.setFont(fonts["regular"], 10.5)
    for q in questions:
        c.drawCentredString(cx, qy, q)
        qy -= 20

    c.setFillColor(BERRY_RED)
    c.setFont(fonts["bold_italic"], 12)
    c.drawCentredString(cx, trim_y + 52, "THE END")
    c.showPage()


def create_front_cover_image(book_data: dict, out_png: Path, size_px: int = 2550):
    """Creates a 300 DPI Front Cover PNG (2550x2550 for 8.5x8.5in) with rich storybook typography."""
    raw_cover = ILLUSTRATIONS_DIR / "cover_front.png"
    if not raw_cover.exists():
        raw_cover = ILLUSTRATIONS_DIR / "page_04.png"

    raw_Img = Image.open(raw_cover).convert("RGBA")
    # Shift artwork down slightly on warm cream background so top 20% is open above Clara's head
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
    author_font = get_pil_font(int(size_px * 0.025), bold=True)

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

    # Bottom author ribbon
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


def create_back_cover_image(book_data: dict, out_png: Path, size_px: int = 2550):
    """Creates a 300 DPI Back Cover PNG with story blurb, age badge, and KDP barcode zone."""
    raw_back = ILLUSTRATIONS_DIR / "cover_back.png"
    if not raw_back.exists():
        raw_back = ILLUSTRATIONS_DIR / "page_20.png"

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

    headline = "“No Rush, No Fuss—Always Delivered!”"
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
    pill_text = "AGES 6–8  •  GRADES 1–3  •  WHISPERWOOD STORYBOOK PRESS"
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


def create_kdp_wrap_cover(front_img: Image.Image, back_img: Image.Image, out_png: Path, out_pdf: Path):
    """
    Creates the full Amazon KDP Print Wrap Cover (Back + Spine + Front + 0.125" bleed on all edges).
    For 8.5" x 8.5" trim, 24 pages color paper:
    Spine width = 24 * 0.002252" = 0.054"
    Total width = 0.125 + 8.5 + 0.054 + 8.5 + 0.125 = 17.304" (5191 px at 300 DPI)
    Total height = 0.125 + 8.5 + 0.125 = 8.75" (2625 px at 300 DPI)
    """
    dpi = 300
    wrap_w_in = 17.304
    wrap_h_in = 8.75
    wrap_w_px = int(round(wrap_w_in * dpi))
    wrap_h_px = int(round(wrap_h_in * dpi))
    panel_px = int(round(8.625 * dpi))  # 8.5" trim + 0.125" outer bleed

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


def build_pdfs(book_data: dict, fonts: dict):
    """Builds all PDF editions (20-Page Digital, Complete Reader, and 24-Page KDP Print Interior)."""
    trim_w = 8.5 * inch
    trim_h = 8.5 * inch

    # 1. Strict 20-Page Digital Edition (8.5" x 8.5", zero bleed)
    pdf_20_path = BUILD_DIR / "The_Snail_Mail_Express_20Page_Digital_Edition.pdf"
    c20 = canvas.Canvas(str(pdf_20_path), pagesize=(trim_w, trim_h))
    c20.setTitle(book_data["metadata"]["title"])
    c20.setAuthor(book_data["metadata"]["author"])
    for page in book_data["pages"]:
        render_story_page(c20, page, trim_w, trim_h, 0, 0, 0, 0, fonts)
        c20.showPage()
    c20.save()

    # 2. Complete Reader Edition (Front Cover + 20 Story Pages + Back Cover)
    pdf_reader_path = BUILD_DIR / "The_Snail_Mail_Express_Complete_Reader.pdf"
    creader = canvas.Canvas(str(pdf_reader_path), pagesize=(trim_w, trim_h))
    creader.setTitle(book_data["metadata"]["title"] + " (Complete Reader Edition)")
    creader.setAuthor(book_data["metadata"]["author"])
    front_png = BUILD_DIR / "The_Snail_Mail_Express_Front_Cover.png"
    back_png = BUILD_DIR / "The_Snail_Mail_Express_Back_Cover.png"

    if front_png.exists():
        creader.drawImage(str(front_png), 0, 0, width=trim_w, height=trim_h)
        creader.showPage()
    for page in book_data["pages"]:
        render_story_page(creader, page, trim_w, trim_h, 0, 0, 0, 0, fonts)
        creader.showPage()
    if back_png.exists():
        creader.drawImage(str(back_png), 0, 0, width=trim_w, height=trim_h)
        creader.showPage()
    creader.save()

    # 3. Amazon KDP 24-Page Print-Ready Interior with Bleed (8.625" x 8.75")
    bleed = 0.125 * inch
    kdp_w = 8.625 * inch
    kdp_h = 8.75 * inch
    pdf_kdp_path = BUILD_DIR / "The_Snail_Mail_Express_KDP_24Page_Print_Interior.pdf"
    ckdp = canvas.Canvas(str(pdf_kdp_path), pagesize=(kdp_w, kdp_h))
    ckdp.setTitle(book_data["metadata"]["title"] + " (KDP Print Interior)")
    ckdp.setAuthor(book_data["metadata"]["author"])

    # Pages 1-3: Front Matter
    render_front_matter_pages(ckdp, book_data, kdp_w, kdp_h, bleed / 2, bleed / 2, bleed, bleed, fonts)
    # Pages 4-23: 20 Story Pages
    for idx, page in enumerate(book_data["pages"]):
        # Right-hand (odd KDP page) has bleed on right; left-hand (even KDP page) has bleed on left
        kdp_page_num = idx + 4
        b_left = bleed if (kdp_page_num % 2 == 0) else 0.0
        b_right = bleed if (kdp_page_num % 2 == 1) else 0.0
        render_story_page(ckdp, page, kdp_w, kdp_h, b_left, b_right, bleed, bleed, fonts)
        ckdp.showPage()
    # Page 24: Back Matter Activity & Field Guide Page
    render_back_matter_page(ckdp, book_data, kdp_w, kdp_h, bleed, 0.0, bleed, bleed, fonts)
    ckdp.save()


def build_epub(book_data: dict):
    """Builds a valid, standards-compliant EPUB 3 file for Amazon Kindle KDP & Apple Books."""
    epub_path = BUILD_DIR / "The_Snail_Mail_Express.epub"
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
        front_png = BUILD_DIR / "The_Snail_Mail_Express_Front_Cover.png"
        if front_png.exists():
            im = Image.open(front_png).convert("RGB").resize((1400, 1400), Image.Resampling.LANCZOS)
            tmp_cov = BUILD_DIR / "_tmp_cov.jpg"
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
            img_file = ILLUSTRATIONS_DIR / page["illustration_file"]
            jpg_name = f"page_{pnum:02d}.jpg"
            if img_file.exists():
                im = Image.open(img_file).convert("RGB").resize((1400, 1400), Image.Resampling.LANCZOS)
                tmp_jpg = BUILD_DIR / f"_tmp_{pnum}.jpg"
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


def build_interactive_flipbook(book_data: dict):
    """Builds a standalone interactive HTML5 Flipbook in build/index.html."""
    slides_json = json.dumps(book_data["pages"], indent=2)
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
    <a class="btn" href="The_Snail_Mail_Express_20Page_Digital_Edition.pdf" target="_blank">📄 20-Page PDF</a>
    <a class="btn" href="The_Snail_Mail_Express_KDP_24Page_Print_Interior.pdf" target="_blank">🖨️ KDP 24-Page Print PDF</a>
    <a class="btn" href="The_Snail_Mail_Express_KDP_Print_Wrap_Cover.pdf" target="_blank">📕 KDP Wrap Cover</a>
    <a class="btn" href="The_Snail_Mail_Express.epub" target="_blank">📱 Kindle EPUB</a>
  </div>

  <div class="book-stage">
    <div class="inner-frame" id="stageContent"></div>
  </div>

  <script>
    const pages = {slides_json};
    let idx = 0; // 0 = Front Cover, 1..20 = Story Pages, 21 = Back Cover

    function render() {{
      const el = document.getElementById('stageContent');
      const ind = document.getElementById('pageIndicator');
      if (idx === 0) {{
        ind.textContent = 'Front Cover';
        el.innerHTML = `<div class="art-box cover-mode"><img src="The_Snail_Mail_Express_Front_Cover.png" alt="Front Cover"/></div>`;
      }} else if (idx === 21) {{
        ind.textContent = 'Back Cover';
        el.innerHTML = `<div class="art-box cover-mode"><img src="The_Snail_Mail_Express_Back_Cover.png" alt="Back Cover"/></div>`;
      }} else {{
        const p = pages[idx - 1];
        ind.textContent = `Page ${{p.page_number}} of 20`;
        const paras = p.text.split('\\n\\n').map((t, i) => {{
          if (i === 0) return `<p><span class="dropcap">${{t.charAt(0)}}</span>${{t.slice(1)}}</p>`;
          return `<p>${{t}}</p>`;
        }}).join('');
        el.innerHTML = `
          <div class="art-box"><img src="../assets/illustrations/${{p.illustration_file}}" alt="${{p.title}}"/></div>
          <div class="chapter-title">${{p.title}}</div>
          <div class="divider">✦ ❖ ✦</div>
          <div class="story-text">${{paras}}</div>
          <div class="page-badge">— ${{p.page_number}} —</div>
        `;
      }}
    }}
    function prevSlide() {{ if (idx > 0) {{ idx--; render(); }} }}
    function nextSlide() {{ if (idx < 21) {{ idx++; render(); }} }}
    window.addEventListener('keydown', (e) => {{
      if (e.key === 'ArrowLeft') prevSlide();
      if (e.key === 'ArrowRight') nextSlide();
    }});
    render();
  </script>
</body>
</html>"""
    (BUILD_DIR / "index.html").write_text(html, encoding="utf-8")


def main():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    book_data = json.loads(BOOK_DATA_PATH.read_text(encoding="utf-8"))
    fonts = register_fonts()

    print("1. Compositing 300 DPI Front & Back Covers...")
    front_png = BUILD_DIR / "The_Snail_Mail_Express_Front_Cover.png"
    back_png = BUILD_DIR / "The_Snail_Mail_Express_Back_Cover.png"
    front_img = create_front_cover_image(book_data, front_png)
    back_img = create_back_cover_image(book_data, back_png)

    print("2. Building Amazon KDP Print Wrap Cover (PDF & PNG)...")
    wrap_png = BUILD_DIR / "The_Snail_Mail_Express_KDP_Print_Wrap_Cover.png"
    wrap_pdf = BUILD_DIR / "The_Snail_Mail_Express_KDP_Print_Wrap_Cover.pdf"
    create_kdp_wrap_cover(front_img, back_img, wrap_png, wrap_pdf)

    print("3. Building 20-Page Digital PDF, Complete Reader PDF, and 24-Page KDP Print Interior PDF...")
    build_pdfs(book_data, fonts)

    print("4. Building Kindle / Universal EPUB 3 package...")
    build_epub(book_data)

    print("5. Building Interactive HTML5 Flipbook...")
    build_interactive_flipbook(book_data)

    print("Done! All production deliverables written to:", BUILD_DIR)


if __name__ == "__main__":
    main()
