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
6. <Title>.epub (EPUB 3, fixed layout: the complete reader's pages)
7. build/index.html + build/reader/ (full-screen web reader)

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
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_pipeline import publication as pub  # noqa: E402
from video_pipeline import publication_web as web  # noqa: E402
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
    background_jpeg,
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


READER_KINDS_FRONT = ("half_title", "title")
READER_KINDS_BACK = ("activity", "goodnight")


def reader_kinds(book_data: dict) -> list[tuple[str, dict | None]]:
    """The complete reader's laid-out pages between its covers, with each story page."""
    stories = sorted_stories(book_data)
    return (
        [(k, None) for k in READER_KINDS_FRONT]
        + [("story", s) for s in stories]
        + [(k, None) for k in READER_KINDS_BACK]
    )


def reader_page_count(book_data: dict, *, paths: BookFolder) -> int:
    """Pages of the complete reader PDF (and of the EPUB): covers that exist + laid-out pages."""
    names = edition_names(file_stem(book_data["metadata"]["title"]), 0, 0)
    covers = sum((paths.build_dir / names[k]).is_file() for k in ("front_png", "back_png"))
    return covers + len(reader_kinds(book_data))


def build_epub(book_data: dict, design: dict, *, paths: BookFolder, extras: dict) -> Path:
    """The fixed-layout EPUB 3 (spec 023 AC-6.1-6.3): the complete reader's pages in order,
    each a 1200 px background with the layout's words, shapes and logo placed on it."""
    meta = book_data["metadata"]
    stem = file_stem(meta["title"])
    names = edition_names(stem, 0, 0)
    book = Publication.load(design, book_data, paths, extras)
    quality = design["dpi"]["jpeg_quality"]
    px = design["dpi"]["epub_px"]
    frame = pub.Frame.digital()
    dpi = px * 72 / frame.width
    cache: dict = {}
    pages: list[web.EpubPage] = []
    nav: list[tuple[str, int]] = []
    front_png = paths.build_dir / names["front_png"]
    back_png = paths.build_dir / names["back_png"]
    if front_png.is_file():
        nav.append(("Cover", len(pages)))
        pages.append(
            web.EpubPage("cover", "Cover", web.cover_jpeg(front_png, quality, px), f"Cover of {meta['title']}")
        )
    for kind, story in reader_kinds(book_data):
        layout = book.layout(kind, frame, story)
        background = background_jpeg(layout, dpi, cache, quality)
        if kind == "story":
            assert story is not None
            if not any(label == "Story" for label, _ in nav):
                nav.append(("Story", len(pages)))
            name, title = f"page_{story['page_number']:02d}", story.get("title") or meta["title"]
            alt = str(story.get("prompt", ""))
        else:
            if kind == "goodnight":
                nav.append(("Goodnight", len(pages)))
            name, title, alt = kind, kind.replace("_", " ").capitalize(), ""
        pages.append(web.EpubPage(name, title, background, alt, layout))
    if back_png.is_file():
        pages.append(
            web.EpubPage(
                "back_cover", "Back cover", web.cover_jpeg(back_png, quality, px),
                f"Back cover of {meta['title']}",
            )
        )
    out = paths.build_dir / names["epub"]
    web.write_epub(out, pages, meta=meta, design=design, nav=nav, px=px)
    return out


def build_reader(book_data: dict, design: dict, *, paths: BookFolder, extras: dict) -> Path:
    """The full-screen web reader, build/index.html + build/reader/ (spec 023 AC-5.1-5.5)."""
    meta = book_data["metadata"]
    stem = file_stem(meta["title"])
    stories = sorted_stories(book_data)
    names = edition_names(stem, len(stories), 0)
    total = reader_page_count(book_data, paths=paths)
    scenes = []
    for n, story in enumerate(stories, start=1):
        t = pub.ramp(n, len(stories), design)
        focus_x, side = story_focus(paths, story)
        scenes.append(
            web.StoryScene(
                page_number=story["page_number"],
                title=str(story.get("title", "")),
                text=str(story.get("text", "")),
                picture=f"../illustrations/{story['illustration_file']}",
                alt=str(story.get("prompt", "")),
                t=t,
                focus_x=focus_x,
                side=side,
                night=pub.page_tone(t, design).night,
            )
        )
    titled = titled_covers(book_data)
    cover = "cover_front_print.png" if titled else "cover_front.png"
    if not titled and not (paths.illustrations / cover).is_file():
        cover = stories[0]["illustration_file"]
    candidates = [
        (names["digital"], f"Digital edition, {len(stories)} pages"),
        (names["reader"], f"Complete reader, {total} pages"),
        (names["epub"], f"EPUB, {total} pages"),
    ]
    links = tuple((href, text) for href, text in candidates if (paths.build_dir / href).is_file())
    tracks = paths.build_dir / "audio" / "tracks"
    audio: dict[str, str] = {}
    if (tracks / "page-01.wav").is_file():  # AC-5.3: no tracks, no "Read to me"
        keys = ["intro", *(f"page-{s['page_number']:02d}" for s in stories), "outro"]
        audio = {k: f"audio/tracks/{k}.wav" for k in keys if (tracks / f"{k}.wav").is_file()}
    logo = paths.root / BRAND_LOGO
    content = web.ReaderContent(
        title=meta["title"],
        subtitle=str(meta.get("subtitle") or ""),
        blurb=str(meta.get("blurb") or ""),
        lang=str(meta.get("language") or "en"),
        cover=f"../illustrations/{cover}",
        cover_focus_x=cover_focus(meta),
        titled=titled,
        stories=tuple(scenes),
        goodnight=pub.goodnight_message(book_data, read_blueprint(paths)),
        logo=logo.is_file(),
        links=links,
        audio=audio,
    )
    return web.write_reader(paths.build_dir, content, design, logo=logo if logo.is_file() else None)


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

    print("4. Building the fixed-layout EPUB 3...")
    build_epub(book_data, design, paths=paths, extras=extras)

    print("5. Building the web reader (index.html + reader/)...")
    build_reader(book_data, design, paths=paths, extras=extras)

    keep = set((written or edition_names(stem, story_count, len(plan))).values())
    for old in stale_editions(paths.build_dir, stem, keep):
        print("Removing stale edition:", old.name)
        old.unlink()

    print("Done! All production deliverables written to:", paths.build_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
