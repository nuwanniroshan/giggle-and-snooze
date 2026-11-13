"""Web side of the publication design (spec 023 Stories 5 and 6): fixed-layout EPUB and reader.

The EPUB draws the same `PageLayout`s as the PDFs: a background raster per page, then every
`TextRun` as an absolutely placed line whose baseline sits on the layout's `y`. The web reader is
a full-screen scene per page in the same look, made from `assets/publication/reader.css` and
`reader.js`, with tokens and font faces generated from `config/publication.json`. Neither loads
anything from the network.
"""

from __future__ import annotations

import html
import shutil
import uuid
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image
from reportlab.pdfbase.ttfonts import TTFontFile

from video_pipeline.library import PROJECT_ROOT
from video_pipeline.publication import PageLayout, Shape, TextRun
from video_pipeline.publication_render import font_paths

ASSETS = Path("assets") / "publication"
FONT_DIR = Path("assets") / "fonts"
EPUB_PX = 1200
FAMILIES = {  # role -> (CSS family, weight, EPUB class)
    "display": ("GS Display", 400, "d"),
    "body": ("GS Body", 400, "b"),
    "body_bold": ("GS Body", 700, "bb"),
}
FALLBACKS = {"display": "ui-serif, Georgia, serif", "body": "ui-sans-serif, sans-serif"}
Metrics = dict[str, tuple[float, float]]

e = html.escape


# ---------------------------------------------------------------- fonts and tokens


def font_metrics(path: Path) -> tuple[float, float]:
    """(ascent, descent) of a TTF as fractions of the em. The bundled faces set hhea = typo
    metrics with no line gap and USE_TYPO_METRICS, so every browser lays lines out with these."""
    face = TTFontFile(str(path))
    return face.ascent / 1000, -face.descent / 1000


def role_metrics(design: dict, root: Path = PROJECT_ROOT) -> Metrics:
    return {role: font_metrics(path) for role, path in font_paths(design, root).items()}


def font_face_css(design: dict, root: Path = PROJECT_ROOT) -> str:
    """@font-face rules for the bundled faces, with `src` in `fonts/` next to the stylesheet.

    The ascent/descent overrides restate the files' own metrics, so engines that honour them
    can never pick other (win) metrics and move the baseline."""
    rules = []
    for role, path in font_paths(design, root).items():
        family, weight, _ = FAMILIES[role]
        ascent, descent = font_metrics(path)
        rules.append(
            "@font-face {\n"
            f'  font-family: "{family}";\n'
            f'  src: url("fonts/{path.name}") format("truetype");\n'
            f"  font-weight: {weight};\n"
            "  font-style: normal;\n"
            f"  ascent-override: {ascent * 100:.4f}%;\n"
            f"  descent-override: {descent * 100:.4f}%;\n"
            "  line-gap-override: 0%;\n"
            "}\n"
        )
    return "".join(rules)


def tokens_css(design: dict, root: Path = PROJECT_ROOT) -> str:
    """The reader's tokens.css: font faces, brand and ramp colours as oklch(), the faces."""
    colors = "".join(
        f"  --color-{name.replace('_', '-')}: oklch({lch[0]} {lch[1]} {lch[2]});\n"
        for name, lch in design["colors"].items()
    )
    return (
        "/* Generated from config/publication.json by scripts/build_book.py (spec 023). */\n"
        + font_face_css(design, root)
        + "\n:root {\n"
        + colors
        + "  --color-focus: var(--color-giggle);\n"
        + f'  --font-display: "{FAMILIES["display"][0]}", {FALLBACKS["display"]};\n'
        + f'  --font-body: "{FAMILIES["body"][0]}", {FALLBACKS["body"]};\n'
        + "  --ease: cubic-bezier(0.2, 0.7, 0.2, 1);\n}\n"
    )


def font_files(design: dict, root: Path = PROJECT_ROOT) -> list[Path]:
    """The three faces and their licences (every OFL text in assets/fonts)."""
    faces = list(font_paths(design, root).values())
    return faces + sorted((root / FONT_DIR).glob("OFL*.txt"))


# ---------------------------------------------------------------- EPUB pages


def _px(value: float) -> str:
    return f"{value:.2f}px"


def _hex(rgb: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def text_span(run: TextRun, *, scale: float, page_width: float, metrics: Metrics) -> str:
    """One line of words. Its line box is ascent + descent tall (no half-leading), so its top
    one ascent above the baseline puts the baseline exactly on `run.y`."""
    ascent, descent = metrics[run.role]
    style = [
        f"top:{_px((run.y - ascent * run.size) * scale)}",
        f"font-size:{_px(run.size * scale)}",
        f"line-height:{_px((ascent + descent) * run.size * scale)}",
        f"color:{_hex(run.color)}",
    ]
    if run.align == "right":
        style.insert(0, f"right:{_px((page_width - run.x) * scale)}")
    else:
        style.insert(0, f"left:{_px(run.x * scale)}")
    cls = FAMILIES[run.role][2]
    return f'<span class="ln {cls}" style="{";".join(style)}">{e(run.text)}</span>'


def shape_div(shape: Shape, scale: float) -> str:
    """A filled or stroked rectangle; a frame's stroke is centred on its edge, as in the PDF."""
    radius = f"border-radius:{_px(shape.radius * scale)}"
    if shape.kind == "rect":
        box = (shape.x, shape.y, shape.w, shape.h)
        paint = f"background-color:{_hex(shape.color)}"
    else:
        half = shape.line / 2
        box = (shape.x - half, shape.y - half, shape.w + shape.line, shape.h + shape.line)
        style = "dashed" if shape.dashed else "solid"
        paint = f"border:{_px(shape.line * scale)} {style} {_hex(shape.color)}"
    x, y, w, h = (v * scale for v in box)
    return (
        f'<div class="sh" style="left:{_px(x)};top:{_px(y)};width:{_px(w)};height:{_px(h)};'
        f'{paint};{radius}"></div>'
    )


@dataclass(frozen=True)
class EpubPage:
    """One fixed-layout page: its background JPEG and, for laid-out pages, the layout on top."""

    name: str  # file stem inside OEBPS, e.g. "page_04"
    title: str
    background: bytes
    alt: str  # the background's alt text; "" when it is only colour
    layout: PageLayout | None = None


def page_xhtml(
    page: EpubPage,
    *,
    metrics: Metrics,
    image_alt: Callable[[Path], str],
    lang: str = "en",
    px: int = EPUB_PX,
) -> str:
    size = f"width:{px}px;height:{px}px"
    body = [f'<img class="bg" src="images/{page.name}.jpg" alt="{e(page.alt)}" style="{size}"/>']
    layout = page.layout
    if layout is not None:
        scale = px / layout.width
        body += [shape_div(shape, scale) for shape in layout.shapes]
        for image in layout.images:
            x, y, w, h = (v * scale for v in (image.x, image.y, image.w, image.h))
            body.append(
                f'<img class="im" src="images/{e(image.path.name)}" alt="{e(image_alt(image.path))}"'
                f' style="left:{_px(x)};top:{_px(y)};width:{_px(w)};height:{_px(h)}"/>'
            )
        body += [
            text_span(run, scale=scale, page_width=layout.width, metrics=metrics)
            for run in layout.texts
        ]
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
        f"<head>\n<title>{e(page.title)}</title>\n"
        f'<meta name="viewport" content="width={px}, height={px}"/>\n'
        '<link rel="stylesheet" type="text/css" href="epub.css"/>\n</head>\n'
        f'<body style="{size}">\n' + "\n".join(body) + "\n</body>\n</html>\n"
    )


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def cover_jpeg(png: Path, quality: int, px: int = EPUB_PX) -> bytes:
    """A cover PNG as a `px` square JPEG."""
    with Image.open(png) as im:
        art = im.convert("RGB").resize((px, px), Image.Resampling.LANCZOS)
    return jpeg_bytes(art, quality)


def book_identifier(meta: dict) -> str:
    """A stable id: the same book keeps its identifier across builds."""
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'gs-book:{meta["title"]}:{meta["author"]}')}"


CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n    <rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>\n  </rootfiles>\n</container>\n'
)


def write_epub(
    out: Path,
    pages: Sequence[EpubPage],
    *,
    meta: dict,
    design: dict,
    nav: Sequence[tuple[str, int]],
    px: int = EPUB_PX,
    root: Path = PROJECT_ROOT,
    now: datetime | None = None,
) -> None:
    """EPUB 3, fixed layout (pre-paginated, 1200 x 1200 viewport), fonts embedded (AC-6.1-6.3).

    `pages[0]` is the front cover; `nav` lists (label, page index) for the table of contents."""
    lang = str(meta.get("language") or "en")
    metrics = role_metrics(design, root)
    logo_alt = "Giggle and Snooze"
    images: dict[str, Path] = {}
    for page in pages:
        for image in page.layout.images if page.layout else ():
            images.setdefault(image.path.name, image.path)
    modified = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="epub.css" media-type="text/css"/>',
    ]
    fonts = list(font_paths(design, root).values())
    for i, font in enumerate(fonts):
        manifest.append(f'<item id="font{i}" href="fonts/{e(font.name)}" media-type="font/ttf"/>')
    for i, name in enumerate(images):
        manifest.append(f'<item id="im{i}" href="images/{e(name)}" media-type="image/png"/>')
    spine = []
    for i, page in enumerate(pages):
        props = ' properties="cover-image"' if i == 0 else ""
        manifest.append(
            f'<item id="bg-{page.name}" href="images/{page.name}.jpg" media-type="image/jpeg"{props}/>'
        )
        manifest.append(
            f'<item id="{page.name}" href="{page.name}.xhtml" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{page.name}"/>')
    title = meta["title"] + (f": {meta['subtitle']}" if meta.get("subtitle") else "")
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0" '
        'prefix="rendition: http://www.idpf.org/vocab/rendition/#">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="BookId">{book_identifier(meta)}</dc:identifier>\n'
        f"    <dc:title>{e(title)}</dc:title>\n"
        f"    <dc:creator>{e(str(meta.get('author', '')))}</dc:creator>\n"
        f"    <dc:language>{e(lang)}</dc:language>\n"
        + (
            f"    <dc:publisher>{e(meta['publisher'])}</dc:publisher>\n"
            if meta.get("publisher")
            else ""
        )
        + (
            f"    <dc:description>{e(meta['blurb'])}</dc:description>\n"
            if meta.get("blurb")
            else ""
        )
        + f'    <meta property="dcterms:modified">{modified}</meta>\n'
        '    <meta property="rendition:layout">pre-paginated</meta>\n'
        '    <meta property="rendition:spread">none</meta>\n'
        '    <meta property="rendition:orientation">auto</meta>\n'
        f'    <meta name="cover" content="bg-{pages[0].name}"/>\n'
        "  </metadata>\n  <manifest>\n    "
        + "\n    ".join(manifest)
        + "\n  </manifest>\n  <spine>\n    "
        + "\n    ".join(spine)
        + "\n  </spine>\n</package>\n"
    )
    links = "\n".join(
        f'      <li><a href="{pages[index].name}.xhtml">{e(label)}</a></li>' for label, index in nav
    )
    nav_doc = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'xml:lang="{lang}" lang="{lang}">\n<head><title>{e(meta["title"])}</title></head>\n<body>\n'
        f'  <nav epub:type="toc" id="toc">\n    <h1>{e(meta["title"])}</h1>\n    <ol>\n'
        f"{links}\n    </ol>\n  </nav>\n</body>\n</html>\n"
    )
    css = font_face_css(design, root) + (root / ASSETS / "epub.css").read_text(encoding="utf-8")

    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav_doc)
        zf.writestr("OEBPS/epub.css", css)
        for font in fonts:
            zf.write(font, f"OEBPS/fonts/{font.name}")
        for name, path in images.items():
            zf.write(path, f"OEBPS/images/{name}", compress_type=zipfile.ZIP_STORED)
        for page in pages:
            zf.writestr(
                f"OEBPS/images/{page.name}.jpg", page.background, compress_type=zipfile.ZIP_STORED
            )
            zf.writestr(
                f"OEBPS/{page.name}.xhtml",
                page_xhtml(page, metrics=metrics, image_alt=lambda _p: logo_alt, lang=lang, px=px),
            )
    tmp.replace(out)


# ---------------------------------------------------------------- web reader


@dataclass(frozen=True)
class StoryScene:
    page_number: int
    title: str
    text: str
    picture: str  # relative to index.html
    alt: str
    t: float
    focus_x: float
    side: str  # "top" | "bottom"
    night: bool


@dataclass(frozen=True)
class ReaderContent:
    title: str
    subtitle: str
    blurb: str
    lang: str
    cover: str  # relative to index.html
    cover_focus_x: float
    titled: bool  # the cover picture carries its own lettering (spec 022)
    stories: tuple[StoryScene, ...]
    goodnight: str
    logo: bool
    links: tuple[tuple[str, str], ...]  # (href, text)
    audio: dict[str, str]  # "intro" | "page-NN" | "outro" -> path relative to index.html


ARROW = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{d}" fill="none" '
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)
LOGO_SRC = "reader/logo.png"
LOGO_ALT = "Giggle and Snooze"


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _t(value: float) -> str:
    return f"{value:g}"


def reader_html(content: ReaderContent) -> str:
    """The reader page: cover, one scene per story page, Goodnight (AC-5.1). Every scene is
    in the HTML, so with scripting off they stack, readable, in order (AC-5.4)."""
    c = content

    def aud(key: str) -> str:
        return f' data-audio="{e(c.audio[key])}"' if key in c.audio else ""

    # a titled cover (spec 022) shows its own painted lettering: keep the words for screen readers
    title_cls, sub_cls = (
        ("visually-hidden", "visually-hidden") if c.titled else ("scene__display", "scene__lead")
    )
    head = f'<h1 class="{title_cls}">{e(c.title)}</h1>'
    if c.subtitle:
        head += f'<p class="{sub_cls}">{e(c.subtitle)}</p>'
    cover_cls = "scene scene--cover scene--titled" if c.titled else "scene scene--cover"
    scenes = [
        f'<section class="{cover_cls}" style="--fx:{_pct(c.cover_focus_x)}" aria-label="Cover"'
        f'{aud("intro")}><img class="scene__art" src="{e(c.cover)}" alt="">'
        f'<div class="scene__words">{head}'
        '<button class="btn" type="button" data-start>Start reading</button></div></section>'
    ]
    for s in c.stories:
        night = " is-night" if s.night else ""
        scenes.append(
            f'<section class="scene words-{s.side}{night}" style="--t:{_t(s.t)};--fx:{_pct(s.focus_x)}"'
            f' data-page="{s.page_number}" aria-label="Page {s.page_number}"'
            f"{aud(f'page-{s.page_number:02d}')}>"
            f'<img class="scene__art" src="{e(s.picture)}" alt="{e(s.alt)}" loading="lazy">'
            f'<div class="scene__words"><h2 class="scene__title">{e(s.title)}</h2>'
            f'<p class="scene__text">{e(s.text)}</p></div></section>'
        )
    links = "".join(f'<li><a href="{e(href)}">{e(text)}</a></li>' for href, text in c.links)
    scenes.append(
        f'<section class="scene scene--end is-night" aria-label="The end"{aud("outro")}>'
        '<div class="scene__words"><h2 class="scene__display">Goodnight</h2>'
        + (f'<p class="scene__lead">{e(c.goodnight)}</p>' if c.goodnight else "")
        + (f'<img class="scene__logo" src="{LOGO_SRC}" alt="{LOGO_ALT}">' if c.logo else "")
        + '<button class="btn" type="button" data-again>Read again</button>'
        + (f'<ul class="scene__links" aria-label="Editions">{links}</ul>' if links else "")
        + "</div></section>"
    )
    listen = (
        '<button class="chrome__btn chrome__listen" type="button" aria-pressed="false">'
        "Read to me</button>"
        if any(k.startswith("page-") for k in c.audio)
        else ""
    )
    logo = (
        f'<img class="chrome__logo" src="{LOGO_SRC}" alt="{LOGO_ALT}">'
        if c.logo
        else "<span></span>"
    )
    return (
        f'<!doctype html>\n<html lang="{e(c.lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{e(c.title)}</title><meta name="description" content="{e(c.blurb)}">'
        '<link rel="stylesheet" href="reader/tokens.css">'
        '<link rel="stylesheet" href="reader/reader.css"></head><body>\n'
        f'<header class="chrome">{logo}'
        f'<div class="chrome__right">{listen}<span class="chrome__count" aria-live="polite"></span>'
        "</div></header>\n<main>\n" + "\n".join(scenes) + "\n</main>\n"
        f'<button class="turn turn--prev" type="button" aria-label="Previous page">'
        f"{ARROW.format(d='M15 5l-7 7 7 7')}</button>"
        f'<button class="turn turn--next" type="button" aria-label="Next page">'
        f"{ARROW.format(d='M9 5l7 7-7 7')}</button>"
        '<div class="progress" aria-hidden="true"><div class="progress__fill"></div></div>\n'
        '<script src="reader/reader.js" defer></script>\n</body></html>\n'
    )


def write_reader(
    build_dir: Path,
    content: ReaderContent,
    design: dict,
    *,
    logo: Path | None,
    root: Path = PROJECT_ROOT,
) -> Path:
    """`build/index.html` plus `build/reader/` (tokens, styles, script, fonts, logo)."""
    reader = build_dir / "reader"
    if reader.exists():
        shutil.rmtree(reader)
    (reader / "fonts").mkdir(parents=True)
    (reader / "tokens.css").write_text(tokens_css(design, root), encoding="utf-8")
    for name in ("reader.css", "reader.js"):
        shutil.copyfile(root / ASSETS / name, reader / name)
    for path in font_files(design, root):
        shutil.copyfile(path, reader / "fonts" / path.name)
    if logo is not None and logo.is_file():
        shutil.copyfile(logo, reader / "logo.png")
    index = build_dir / "index.html"
    tmp = index.with_name("index.html.tmp")
    tmp.write_text(reader_html(content), encoding="utf-8")
    tmp.replace(index)
    return index
