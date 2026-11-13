#!/usr/bin/env python3
"""
Production book builder: renders one book's print and digital editions (spec 005, 020).

Generates, in books/<slug>/build/ (N = story pages, P = KDP interior pages):
1. <Title>_<N>Page_Digital_Edition.pdf (story pages only, 8.5" x 8.5")
2. <Title>_Complete_Reader.pdf (covers, belongs-to and title pages, story pages, activity,
   Goodnight)
3. <Title>_KDP_<P>Page_Print_Interior.pdf (KDP interior with bleed, 8.625" x 8.75";
   P = max(24, front + N + 2) rounded up to even, padded with "My drawing" pages)
4. <Title>_KDP_Print_Wrap_Cover.pdf & .png (spine width from P)
5. <Title>_Front_Cover.png & Back_Cover.png (300 DPI standalone covers)
6. <Title>.epub (EPUB 3)
7. build/index.html (interactive HTML5 flipbook preview)

Every page is laid out once by video_pipeline/publication.py (spec 023: full-bleed pictures,
words on a scrim, day to night) and drawn by video_pipeline/publication_render.py with the
bundled typefaces. Book-specific extras (bookplate label, map legend, activity page...) come from the book's
optional `print_extras` object, over the generic templates in config/book_extras.json.
"""

import argparse
import json
import re
import string
import sys
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_pipeline import publication as pub  # noqa: E402
from video_pipeline.covers import titled_covers  # noqa: E402
from video_pipeline.file_hash import sha256_file  # noqa: E402
from video_pipeline.library import (  # noqa: E402
    PROJECT_ROOT,
    BookFolder,
    LibraryError,
    book_folder,
    file_stem,
)
from video_pipeline.motion_plan import plan_path  # noqa: E402
from video_pipeline.publication import PublicationError, load_design  # noqa: E402
from video_pipeline.publication_render import (  # noqa: E402
    FONT_NAMES,
    draw_pdf_page,
    measure,
    register_fonts,
    render_png,
)

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
    kind: str  # half_title | title | map | blank | story | activity | goodnight | drawing
    number: int  # 1-based interior page number
    story: dict | None = None


def plan_interior(book: dict, has_map: bool) -> list[InteriorPage]:
    """The KDP interior, page by page (spec 023 AC-3.1).

    Front matter, a blank filler when needed so story page 1 lands on an even (left) page,
    the story pages in page_number order, the activity and Goodnight pages, then "My drawing"
    pages up to P = max(24, front + N + 2) rounded up to even."""
    front = ["half_title", "title"] + (["map"] if has_map else [])
    if len(front) % 2 == 0:
        front.append("blank")
    stories = sorted(book["pages"], key=lambda page: page["page_number"])
    back = ["activity", "goodnight"]
    total = max(KDP_MIN_PAGES, len(front) + len(stories) + len(back))
    total += total % 2
    drawings = total - len(front) - len(stories) - len(back)
    kinds = [(k, None) for k in front] + [("story", page) for page in stories]
    kinds += [(k, None) for k in back] + [("drawing", None)] * drawings
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


def sorted_stories(book: dict) -> list[dict]:
    return sorted(book["pages"], key=lambda page: page["page_number"])


def brand_logo(paths: BookFolder) -> pub.Logo | None:
    """The channel logo with its aspect, or None when the file is missing."""
    path = paths.root / BRAND_LOGO
    if not path.is_file():
        return None
    with Image.open(path) as im:
        return pub.Logo(path, im.height / im.width)


def read_blueprint(paths: BookFolder) -> dict | None:
    try:
        data = json.loads(paths.blueprint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def story_focus(paths: BookFolder, page: dict) -> tuple[float, str]:
    """Crop focus and words side from the page's animation plan when it matches the picture."""
    picture = paths.illustrations / page["illustration_file"]
    try:
        plan = json.loads(plan_path(paths, page["page_number"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = None
    sha = sha256_file(picture) if plan is not None and picture.is_file() else None
    return pub.choose_focus_side(page, plan, sha)


def cover_focus(meta: dict) -> float:
    value = meta.get("cover_focus_x", 0.5)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    return float(value) if ok else 0.5


@dataclass(frozen=True)
class Publication:
    """What every page layout of one book needs."""

    design: dict
    book: dict
    paths: BookFolder
    extras: dict
    logo: pub.Logo | None

    @classmethod
    def load(cls, design: dict, book: dict, paths: BookFolder, extras: dict) -> "Publication":
        return cls(design, book, paths, extras, brand_logo(paths))

    def layout(self, kind: str, frame: pub.Frame, story: dict | None = None) -> pub.PageLayout:
        design, book, extras = self.design, self.book, self.extras
        meta = book["metadata"]
        if kind == "story":
            assert story is not None
            stories = sorted_stories(book)
            n = [p["page_number"] for p in stories].index(story["page_number"]) + 1
            focus_x, side = story_focus(self.paths, story)
            return pub.layout_story(
                design,
                frame,
                story,
                n=n,
                total=len(stories),
                focus_x=focus_x,
                side=side,
                picture=self.paths.illustrations / story["illustration_file"],
                measure=measure,
            )
        if kind == "half_title":
            return pub.layout_half_title(
                design,
                frame,
                title=meta["title"],
                subtitle=meta.get("subtitle", ""),
                bookplate_line=extras["bookplate_line"],
                logo=self.logo,
                measure=measure,
            )
        if kind == "title":
            return pub.layout_title(
                design,
                frame,
                dedication=meta.get("dedication", ""),
                author=meta["author"],
                copyright_lines=copyright_lines(book, extras),
                measure=measure,
            )
        if kind == "map":
            info = map_content(book, extras, map_pictures(self.paths.illustrations))
            picture = self.paths.illustrations / info["file"]
            with Image.open(picture) as im:
                size = im.size
            return pub.layout_map(
                design,
                frame,
                title=info["title"],
                subtitle=info["subtitle"],
                legend=info["legend"],
                picture=picture,
                picture_size=size,
                measure=measure,
            )
        if kind == "activity":
            return pub.layout_activity(design, frame, activity_content(book, extras), measure=measure)
        if kind == "goodnight":
            message = pub.goodnight_message(book, read_blueprint(self.paths))
            return pub.layout_goodnight(design, frame, message=message, logo=self.logo, measure=measure)
        if kind == "drawing":
            return pub.layout_drawing(
                design, frame, caption=extras["drawing_caption"], title=meta["title"], measure=measure
            )
        return pub.layout_blank(design, frame)


def create_front_cover_image(book_data: dict, out_png: Path, size_px: int = 2550, *, paths: BookFolder):
    """The 300 DPI front cover PNG (2550 px square for 8.5 in): picture full bleed, title and
    subtitle on a light scrim at the top, logo bottom right (spec 023 AC-4.1)."""
    if titled_covers(book_data):  # spec 022: the painted print cover already carries the lettering
        raw = paths.illustrations / "cover_front_print.png"
        if not raw.is_file():
            raise FileNotFoundError(f"print cover not found: {raw} (run illustrate --cover print)")
        with Image.open(raw) as im:
            final_img = ImageOps.fit(
                im.convert("RGB"), (size_px, size_px), Image.Resampling.LANCZOS
            )
        final_img.save(out_png, quality=98, dpi=(300, 300))
        return final_img
    design = load_design()
    register_fonts(design)
    meta = book_data["metadata"]
    raw_cover = paths.illustrations / "cover_front.png"
    if not raw_cover.exists():
        raw_cover = paths.illustrations / sorted_stories(book_data)[0]["illustration_file"]
    layout = pub.layout_front_cover(
        design,
        pub.Frame.digital(),
        title=meta["title"],
        subtitle=meta.get("subtitle", ""),
        picture=raw_cover,
        focus_x=cover_focus(meta),
        logo=brand_logo(paths),
        publisher=meta.get("publisher", ""),
        measure=measure,
    )
    final_img = render_png(layout, size_px, design)
    final_img.save(out_png, dpi=(COVER_DPI, COVER_DPI))
    return final_img


def create_back_cover_image(book_data: dict, out_png: Path, size_px: int = 2550, *, paths: BookFolder, extras: dict):
    """The 300 DPI back cover PNG: cover_back.png full bleed (else the night colour), a night
    scrim with the blurb, the logo and an empty barcode slot (spec 023 AC-4.3)."""
    design = load_design()
    register_fonts(design)
    meta = book_data["metadata"]
    raw_back = paths.illustrations / "cover_back.png"
    layout = pub.layout_back_cover(
        design,
        pub.Frame.digital(),
        blurb=meta.get("blurb", ""),
        picture=raw_back if raw_back.is_file() else None,
        logo=brand_logo(paths),
        publisher=meta.get("publisher", ""),
        measure=measure,
    )
    final_img = render_png(layout, size_px, design)
    final_img.save(out_png, dpi=(COVER_DPI, COVER_DPI))
    return final_img


def new_canvas(path: Path, size: tuple[float, float], title: str, author: str) -> canvas.Canvas:
    """A PDF canvas whose only font is the bundled body face (AC-7.1)."""
    c = canvas.Canvas(str(path), pagesize=size, initialFontName=FONT_NAMES["body"])
    c.setTitle(title)
    c.setAuthor(author)
    return c


def create_kdp_wrap_cover(front_img: Image.Image, back_img: Image.Image, out_png: Path, out_pdf: Path, interior_pages: int):
    """
    Creates the full Amazon KDP Print Wrap Cover (Back + Spine + Front + bleed on all edges).
    The spine width comes from the interior page count P (AC-2.2): P * 0.002252" on colour
    paper; for P = 24 the wrap is 17.304" x 8.75" (5191 x 2625 px at 300 DPI). The spine is
    the night colour (spec 023 AC-4.4).
    """
    dpi = COVER_DPI
    wrap_w_in, wrap_h_in = wrap_size_inches(interior_pages)
    wrap_w_px = int(round(wrap_w_in * dpi))
    wrap_h_px = int(round(wrap_h_in * dpi))
    panel_px = int(round((TRIM_IN + BLEED_IN) * dpi))  # trim + outer bleed

    wrap = Image.new("RGB", (wrap_w_px, wrap_h_px), pub.color(load_design(), "night"))
    wrap.paste(back_img.resize((panel_px, wrap_h_px), Image.Resampling.LANCZOS), (0, 0))
    front_resized = front_img.resize((panel_px, wrap_h_px), Image.Resampling.LANCZOS)
    wrap.paste(front_resized, (wrap_w_px - panel_px, 0))
    wrap.save(out_png, dpi=(dpi, dpi))

    c = new_canvas(out_pdf, (wrap_w_in * inch, wrap_h_in * inch), out_pdf.stem, "")
    c.drawImage(str(out_png), 0, 0, width=wrap_w_in * inch, height=wrap_h_in * inch)
    c.showPage()
    c.save()


def draw_cover_page(c: canvas.Canvas, png: Path, size: tuple[float, float], quality: int) -> None:
    """A cover PNG as a full page, embedded as JPEG to keep the reader PDF small."""
    buffer = BytesIO()
    with Image.open(png) as im:
        im.convert("RGB").save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    c.drawImage(ImageReader(buffer), 0, 0, width=size[0], height=size[1])
    c.showPage()


def build_pdfs(book_data: dict, design: dict, *, paths: BookFolder, extras: dict, plan: list[InteriorPage]) -> dict[str, str]:
    """Builds the PDF editions (N-page digital, complete reader, P-page KDP interior) in the
    spec 023 look and returns the file names written."""
    meta = book_data["metadata"]
    stem = file_stem(meta["title"])
    stories = sorted_stories(book_data)
    names = edition_names(stem, len(stories), len(plan))
    book = Publication.load(design, book_data, paths, extras)
    dpi, quality = design["dpi"], design["dpi"]["jpeg_quality"]
    cache: dict = {}  # background rasters shared by the digital edition and the reader
    trim = (TRIM_IN * inch, TRIM_IN * inch)
    digital = pub.Frame.digital()

    def page(c: canvas.Canvas, layout: pub.PageLayout, page_dpi: float) -> None:
        draw_pdf_page(c, layout, page_dpi, cache, quality)
        c.showPage()

    # 1. Digital edition: the story pages only (8.5" x 8.5", no bleed)
    c = new_canvas(paths.build_dir / names["digital"], trim, meta["title"], meta["author"])
    for story in stories:
        page(c, book.layout("story", digital, story), dpi["digital"])
    c.save()

    # 2. Complete reader: front cover, belongs-to, title, stories, activity, Goodnight, back cover
    c = new_canvas(
        paths.build_dir / names["reader"],
        trim,
        meta["title"] + " (Complete Reader Edition)",
        meta["author"],
    )
    front_png = paths.build_dir / names["front_png"]
    back_png = paths.build_dir / names["back_png"]
    if front_png.exists():
        draw_cover_page(c, front_png, trim, quality)
    kinds = ["half_title", "title"] + ["story"] * len(stories) + ["activity", "goodnight"]
    story_iter = iter(stories)
    for kind in kinds:
        story = next(story_iter) if kind == "story" else None
        page(c, book.layout(kind, digital, story), dpi["digital"])
    if back_png.exists():
        draw_cover_page(c, back_png, trim, quality)
    c.save()

    # 3. KDP interior with bleed (8.625" x 8.75"): outer-edge bleed, page by page from the plan
    kdp_size = ((TRIM_IN + BLEED_IN) * inch, (TRIM_IN + 2 * BLEED_IN) * inch)
    c = new_canvas(
        paths.build_dir / names["kdp"], kdp_size, meta["title"] + " (KDP Print Interior)", meta["author"]
    )
    for entry in plan:
        bleed_left = story_bleed(entry.number, BLEED_IN)[0] > 0
        frame = pub.Frame.kdp(bleed_left=bleed_left)
        page(c, book.layout(entry.kind, frame, entry.story), dpi["print"])
    c.save()
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
    try:  # fonts and design come from the project, never from --root (AC-7.2)
        design = load_design()
        register_fonts(design)
    except PublicationError as exc:
        print(f"build stopped: {exc}", file=sys.stderr)
        return 2
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    try:
        return build_all(book_data, design, paths=paths, extras=extras)
    except PublicationError as exc:
        print(f"build stopped: {exc}", file=sys.stderr)
        return 2


def build_all(book_data: dict, design: dict, *, paths: BookFolder, extras: dict) -> int:
    stem = file_stem(book_data["metadata"]["title"])
    plan = plan_interior(book_data, has_map=bool(map_pictures(paths.illustrations)))
    story_count = len(book_data["pages"])

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
    written = build_pdfs(book_data, design, paths=paths, extras=extras, plan=plan)

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
