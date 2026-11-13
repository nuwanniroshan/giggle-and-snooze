"""Spec 023 Phase 3: the fixed-layout EPUB (Story 6) and the full-screen web reader (Story 5)."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tests import test_book_editions as editions
from video_pipeline import publication as pub
from video_pipeline import publication_web as web

STEM = "The_Lantern_Bug_Parade"
OPF = "{http://www.idpf.org/2007/opf}"
XHTML = "{http://www.w3.org/1999/xhtml}"
DESIGN = pub.load_design()


# --- pure helpers -----------------------------------------------------------------------------


def test_font_metrics_come_from_the_font_files() -> None:
    paths = {role: editions.ROOT / DESIGN["fonts"][role] for role in ("display", "body")}
    display = web.font_metrics(paths["display"])
    body = web.font_metrics(paths["body"])
    assert display == pytest.approx((1.046, 0.366), abs=1e-3)
    assert body == pytest.approx((2500 / 2048, 800 / 2048), abs=1e-3)


def test_tokens_css_comes_from_the_config() -> None:
    css = web.tokens_css(DESIGN)
    lightness, chroma, hue = DESIGN["colors"]["giggle"]
    assert f"--color-giggle: oklch({lightness} {chroma} {hue});" in css
    for name in DESIGN["colors"]:
        assert f"--color-{name.replace('_', '-')}:" in css
    assert 'url("fonts/YoungSerif-Regular.ttf")' in css
    assert 'url("fonts/Andika-Bold.ttf")' in css and "font-weight: 700" in css
    assert "http" not in css


def test_text_run_baseline_sits_at_its_y() -> None:
    """A run's box starts one ascent above the baseline and its line box is ascent + descent,
    so the baseline lands exactly on the layout's y."""
    metrics = {"display": (1.0, 0.25), "body": (1.2, 0.4), "body_bold": (1.2, 0.4)}
    run = pub.TextRun("Hello", "body", 10.0, 50.0, 100.0, (1, 2, 3))
    span = web.text_span(run, scale=2.0, page_width=600.0, metrics=metrics)
    assert "top:176.00px" in span  # (100 - 1.2 * 10) * 2
    assert "left:100.00px" in span
    assert "line-height:32.00px" in span  # (1.2 + 0.4) * 10 * 2
    assert "font-size:20.00px" in span
    right = pub.TextRun("7", "display", 10.0, 590.0, 100.0, (1, 2, 3), align="right")
    span = web.text_span(right, scale=2.0, page_width=600.0, metrics=metrics)
    assert "right:20.00px" in span and "left:" not in span


def test_epub_images_get_their_own_alt_text(tmp_path: Path) -> None:  # AC-6.2
    """The logo reads "Giggle and Snooze", a map "Map: <title>", any other image alt=""."""
    from PIL import Image

    files = {}
    for name in ("giggle_and_snooze_logo.png", "map_meadow.png", "sticker.png"):
        files[name] = tmp_path / name
        Image.new("RGB", (8, 8), (10, 20, 30)).save(files[name])
    logo = pub.Logo(files["giggle_and_snooze_logo.png"], 1.0)
    frame = pub.Frame.digital()

    def measure(text: str, role: str, size: float) -> float:
        return len(text) * size * 0.5

    goodnight = pub.layout_goodnight(DESIGN, frame, message="Sleep", logo=logo, measure=measure)
    mapped = pub.layout_map(
        DESIGN,
        frame,
        title="Map of Glowmoss",
        subtitle="",
        legend=[],
        picture=files["map_meadow.png"],
        picture_size=(8, 8),
        measure=measure,
    )
    other = pub.PageLayout(
        612, 612, (0, 0, 0), None, (0, 0, 612, 612), 0.5,
        images=(pub.ImageRun(files["sticker.png"], 10, 10, 50, 50),),
    )
    pages = [
        web.EpubPage("cover", "Cover", b"jpeg", "Cover of Glowmoss"),
        web.EpubPage("goodnight", "Goodnight", b"jpeg", "", goodnight),
        web.EpubPage("map", "Map", b"jpeg", "", mapped),
        web.EpubPage("other", "Other", b"jpeg", "", other),
    ]
    out = tmp_path / "book.epub"
    web.write_epub(out, pages, meta={"title": "Glowmoss", "author": "A"}, design=DESIGN, nav=[])
    with zipfile.ZipFile(out) as zf:
        page = {n: zf.read(f"OEBPS/{n}.xhtml").decode("utf-8") for n in ("goodnight", "map", "other")}
    assert 'src="images/giggle_and_snooze_logo.png" alt="Giggle and Snooze"' in page["goodnight"]
    assert 'src="images/map_meadow.png" alt="Map: Map of Glowmoss"' in page["map"]
    assert 'src="images/sticker.png" alt=""' in page["other"]


# --- real builds of a 10-page book ------------------------------------------------------------


def built(root: Path, book: dict, *, tracks: tuple[str, ...] = (), logo: bool = True) -> Path:
    build_dir = editions.make_root(root, "lantern-bug-parade", book)
    if logo:
        (root / "assets").symlink_to(editions.ROOT / "assets")
    if tracks:
        (build_dir / "audio" / "tracks").mkdir(parents=True)
        for name in tracks:
            (build_dir / "audio" / "tracks" / f"{name}.wav").write_bytes(b"RIFF")
    assert editions.build.main(["--book", "lantern-bug-parade", "--root", str(root)]) == 0
    return build_dir


@pytest.fixture(scope="module")
def titled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Titled (spec 022) book with the brand logo, no voice tracks."""
    book = editions.story_book(10, characters=["Tilly", "Granny Dusk"])
    assert book["metadata"]["cover_style"] == "titled"
    return built(tmp_path_factory.mktemp("web-titled"), book)


@pytest.fixture(scope="module")
def plain(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Older (lettered cover) book with intro, page 1 and outro voice tracks, no brand logo."""
    book = editions.story_book(10)
    book["metadata"].pop("cover_style", None)
    book["metadata"]["cover_focus_x"] = 0.3
    tracks = ("intro", "page-01", "outro")
    return built(tmp_path_factory.mktemp("web-plain"), book, tracks=tracks, logo=False)


def story_pages(build_dir: Path) -> list[dict]:
    import json

    book = json.loads((build_dir.parent / "book.json").read_text(encoding="utf-8"))
    return sorted(book["pages"], key=lambda p: p["page_number"])


# --- EPUB (AC-6.1-6.3) ------------------------------------------------------------------------


def epub_of(build_dir: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(build_dir / f"{STEM}.epub")


def opf_of(zf: zipfile.ZipFile) -> ET.Element:
    return ET.fromstring(zf.read("OEBPS/content.opf"))


def spine_files(zf: zipfile.ZipFile) -> list[str]:
    root = opf_of(zf)
    items = {i.get("id"): i.get("href") for i in root.iter(f"{OPF}item")}
    return ["OEBPS/" + items[r.get("idref")] for r in root.iter(f"{OPF}itemref")]


def test_epub_mimetype_first_and_stored(titled: Path) -> None:  # AC-6.3
    with epub_of(titled) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"


def test_epub_is_fixed_layout_with_metadata(titled: Path) -> None:  # AC-6.1
    with epub_of(titled) as zf:
        root = opf_of(zf)
        assert root.get("version") == "3.0"
        metas = {m.get("property"): (m.text or "") for m in root.iter(f"{OPF}meta")}
        assert metas["rendition:layout"] == "pre-paginated"
        assert metas["rendition:spread"] == "none"
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", metas["dcterms:modified"])
        text = zf.read("OEBPS/content.opf").decode("utf-8")
        for tag in ("dc:identifier", "dc:title", "dc:creator", "dc:language"):
            assert f"<{tag}" in text
        covers = [
            i for i in root.iter(f"{OPF}item") if "cover-image" in (i.get("properties") or "")
        ]
        assert len(covers) == 1


def test_epub_spine_is_the_complete_reader(titled: Path) -> None:  # AC-6.1, AC-6.3
    import pymupdf

    with pymupdf.open(titled / f"{STEM}_Complete_Reader.pdf") as doc:
        reader_pages = doc.page_count
    with epub_of(titled) as zf:
        files = spine_files(zf)
        assert len(files) == reader_pages == 16
        for name in files:
            page = zf.read(name).decode("utf-8")
            assert '<meta name="viewport" content="width=1200, height=1200"/>' in page
        texts = [zf.read(n).decode("utf-8") for n in files]
    assert "Glow Step 1" in texts[3] and "Glow Step 10" in texts[12]
    assert "Goodnight" in texts[14]


def test_epub_has_a_nav_document(titled: Path) -> None:  # AC-6.3
    with epub_of(titled) as zf:
        root = opf_of(zf)
        (nav,) = [
            i for i in root.iter(f"{OPF}item") if "nav" in (i.get("properties") or "").split()
        ]
        doc = zf.read("OEBPS/" + nav.get("href")).decode("utf-8")
        files = spine_files(zf)
    assert 'epub:type="toc"' in doc
    links = re.findall(r'href="([^"]+)"', doc)
    assert [("OEBPS/" + h) for h in links] == [files[0], files[3], files[14]]


def test_epub_embeds_both_typefaces(titled: Path) -> None:  # AC-6.2
    with epub_of(titled) as zf:
        names = zf.namelist()
        for font in ("YoungSerif-Regular.ttf", "Andika-Regular.ttf", "Andika-Bold.ttf"):
            assert f"OEBPS/fonts/{font}" in names
        fonts = [i for i in opf_of(zf).iter(f"{OPF}item") if i.get("href", "").endswith(".ttf")]
        assert len(fonts) == 3
        assert all(i.get("media-type") in ("font/ttf", "application/font-sfnt") for i in fonts)
        css = zf.read("OEBPS/epub.css").decode("utf-8")
    assert css.count("@font-face") == 3 and 'url("fonts/YoungSerif-Regular.ttf")' in css


def test_epub_pictures_have_alt_text(titled: Path) -> None:  # AC-6.2
    pages = story_pages(titled)
    with epub_of(titled) as zf:
        files = spine_files(zf)
        texts = [zf.read(n).decode("utf-8") for n in files]
    for xhtml in texts:
        for tag in re.findall(r"<img [^>]*>", xhtml):
            assert re.search(r'alt="[^"]*"', tag), tag
    assert 'alt="Cover of The Lantern Bug Parade"' in texts[0]
    assert 'alt="Back cover of The Lantern Bug Parade"' in texts[-1]
    from html import escape

    for page, xhtml in zip(pages, texts[3:13], strict=True):
        assert f'alt="{escape(page["prompt"])}"' in xhtml


def test_epub_pages_parse_as_xhtml(titled: Path) -> None:
    with epub_of(titled) as zf:
        for name in [n for n in zf.namelist() if n.endswith(".xhtml")]:
            ET.fromstring(zf.read(name))


def test_epub_cover_image_is_1200_px(titled: Path) -> None:
    from io import BytesIO

    from PIL import Image

    with epub_of(titled) as zf:
        (cover,) = [
            i for i in opf_of(zf).iter(f"{OPF}item") if i.get("properties") == "cover-image"
        ]
        with Image.open(BytesIO(zf.read("OEBPS/" + cover.get("href")))) as im:
            assert im.size == (1200, 1200) and im.format == "JPEG"


# --- web reader (AC-5.1-5.5) ------------------------------------------------------------------


READER_FILES = ("tokens.css", "reader.css", "reader.js", "logo.png")


def test_reader_files_are_written(titled: Path) -> None:  # FR-5
    for name in READER_FILES:
        assert (titled / "reader" / name).is_file(), name
    for name in (
        "YoungSerif-Regular.ttf",
        "Andika-Regular.ttf",
        "Andika-Bold.ttf",
        "OFL-Andika.txt",
        "OFL-YoungSerif.txt",
    ):
        assert (titled / "reader" / "fonts" / name).is_file(), name


def test_reader_loads_nothing_from_the_network(titled: Path) -> None:  # AC-5.5
    for path in [titled / "index.html", *(titled / "reader" / n for n in READER_FILES[:3])]:
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, path.name
    assert "Chubbo" not in (titled / "reader" / "reader.css").read_text(encoding="utf-8")


def test_reader_has_every_story_text_and_scene(titled: Path) -> None:  # AC-5.1, AC-5.4
    from html import escape

    html = (titled / "index.html").read_text(encoding="utf-8")
    pages = story_pages(titled)
    for page in pages:
        assert escape(page["text"]) in html
        assert f'src="../illustrations/{page["illustration_file"]}"' in html
        assert f'alt="{escape(page["prompt"])}"' in html
    assert html.count('class="scene ') == len(pages) + 2
    assert "Start reading" in html and "Read again" in html and "Goodnight" in html
    assert html.count("data-page=") == len(pages)
    assert 'href="reader/tokens.css"' in html and 'src="reader/reader.js"' in html


def test_reader_ramp_focus_and_night(titled: Path) -> None:  # AC-5.1
    html = (titled / "index.html").read_text(encoding="utf-8")
    first = re.search(r'<section class="scene ([^"]*)" style="--t:0;--fx:50%" data-page="1"', html)
    assert first and "words-bottom" in first.group(1)
    last = re.search(
        r'<section class="scene ([^"]*)" style="--t:([\d.]+);[^"]*" data-page="10"', html
    )
    assert last and "is-night" in last.group(1)
    assert float(last.group(2)) == pub.ramp(10, 10, DESIGN)


def test_reader_links_resolve_and_state_real_counts(titled: Path) -> None:  # AC-5.1
    html = (titled / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'href="([^"]+\.(?:pdf|epub))"', html)
    assert sorted(hrefs) == sorted(
        [f"{STEM}_10Page_Digital_Edition.pdf", f"{STEM}_Complete_Reader.pdf", f"{STEM}.epub"]
    )
    assert all((titled / h).is_file() for h in hrefs)
    assert "Digital edition, 10 pages" in html
    assert "Complete reader, 16 pages" in html and "EPUB, 16 pages" in html
    assert "KDP" not in html


def test_reader_without_tracks_has_no_read_to_me(titled: Path) -> None:  # AC-5.3
    html = (titled / "index.html").read_text(encoding="utf-8")
    assert "Read to me" not in html and "data-audio" not in html


def test_reader_with_tracks_reads_to_me(plain: Path) -> None:  # AC-5.3
    html = (plain / "index.html").read_text(encoding="utf-8")
    assert "Read to me" in html
    audio = re.findall(r'data-audio="([^"]+)"', html)
    assert audio == ["audio/tracks/intro.wav", "audio/tracks/page-01.wav", "audio/tracks/outro.wav"]
    assert all((plain / a).is_file() for a in audio)


def test_titled_reader_cover_is_the_print_cover(titled: Path) -> None:  # spec 022
    html = (titled / "index.html").read_text(encoding="utf-8")
    cover = re.search(r'<section class="scene scene--cover[^>]*>.*?</section>', html, re.S).group(0)
    assert 'src="../illustrations/cover_front_print.png"' in cover
    assert "The Lantern Bug Parade" in cover  # still there for screen readers


def test_plain_reader_cover_uses_cover_front_and_its_focus(plain: Path) -> None:
    html = (plain / "index.html").read_text(encoding="utf-8")
    cover = re.search(r'<section class="scene scene--cover[^>]*>.*?</section>', html, re.S).group(0)
    assert 'src="../illustrations/cover_front.png"' in cover and "--fx:30%" in cover
    assert '<h1 class="scene__display">The Lantern Bug Parade</h1>' in cover


def test_reader_logo_only_when_the_brand_logo_exists(titled: Path, plain: Path) -> None:
    assert 'src="reader/logo.png"' in (titled / "index.html").read_text(encoding="utf-8")
    assert "reader/logo.png" not in (plain / "index.html").read_text(encoding="utf-8")
    assert not (plain / "reader" / "logo.png").exists()


# --- reader.js in a real browser (Chrome headless, virtual time) ------------------------------

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
needs_chrome = pytest.mark.skipif(not CHROME.is_file(), reason="Google Chrome not installed")

HARNESS = """<!doctype html><html><body>
<header><button class="chrome__listen" aria-pressed="false">Read to me</button>
<span class="chrome__count"></span></header>
{scenes}
<button class="turn turn--prev"></button><button class="turn turn--next"></button>
<div class="progress__fill"></div><pre id="log"></pre>
<script src="reader.js"></script>
<script>
const log = (m) => {{ document.getElementById("log").textContent += m + ";"; }};
const cur = () => [...document.querySelectorAll(".scene")]
  .findIndex((s) => s.classList.contains("is-current"));
const listen = document.querySelector(".chrome__listen");
const state = (label) => log(`${{label}}=${{cur()}},${{listen.getAttribute("aria-pressed")}}`);
{steps}
</script></body></html>
"""


def run_reader(tmp_path: Path, scenes: str, steps: str, *, budget_ms: int = 9000) -> list[str]:
    """Load reader.js on a harness page in Chrome headless and return the logged states."""
    import shutil
    import subprocess

    shutil.copyfile(editions.ROOT / "assets" / "publication" / "reader.js", tmp_path / "reader.js")
    page = tmp_path / "harness.html"
    page.write_text(HARNESS.format(scenes=scenes, steps=steps), encoding="utf-8")
    result = subprocess.run(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            f"--user-data-dir={tmp_path / 'profile'}",
            f"--virtual-time-budget={budget_ms}",
            "--dump-dom",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    match = re.search(r'<pre id="log">([^<]*)</pre>', result.stdout)
    assert match, result.stdout[-2000:]
    return [s for s in match.group(1).split(";") if s]


SILENT = (
    '<section class="scene"></section>'
    '<section class="scene" data-page="1"></section>'
    '<section class="scene" data-page="2"></section>'
)


@needs_chrome
def test_read_to_me_moves_on_from_scenes_without_audio(tmp_path: Path) -> None:
    """A scene with no track waits 1.5 s and turns; at the last scene reading stops."""
    steps = """
listen.click();
setTimeout(() => state("t1000"), 1000);
setTimeout(() => state("t2000"), 2000);
setTimeout(() => state("t3500"), 3500);
setTimeout(() => state("t6000"), 6000);
setTimeout(() => log("button=" + listen.textContent), 6000);
"""
    log = run_reader(tmp_path, SILENT, steps)
    assert log == [
        "t1000=0,true",
        "t2000=1,true",
        "t3500=2,true",
        "t6000=2,false",
        "button=Read to me",
    ]


@needs_chrome
def test_read_to_me_timer_never_fires_after_the_reader_moves_on(tmp_path: Path) -> None:
    """Turning the page by hand restarts the wait: the old scene's timer is cleared."""
    steps = """
listen.click();
setTimeout(() => document.querySelector(".turn--next").click(), 1000);
setTimeout(() => state("t2000"), 2000);
setTimeout(() => state("t2400"), 2400);
setTimeout(() => state("t2600"), 2600);
setTimeout(() => {{ listen.click(); state("off"); }}, 2700);
setTimeout(() => {{ document.querySelector(".turn--prev").click(); }}, 2800);
setTimeout(() => state("t6000"), 6000);
"""
    log = run_reader(tmp_path, SILENT, steps.replace("{{", "{").replace("}}", "}"))
    assert log == ["t2000=1,true", "t2400=1,true", "t2600=2,true", "off=2,false", "t6000=1,false"]


@needs_chrome
def test_read_to_me_turns_the_page_when_a_track_ends(tmp_path: Path) -> None:  # AC-5.3
    """Real playback, in real time: the page's load event is held by a slow request from a
    local server, so the 0.3 s tracks play and end before Chrome dumps the page."""
    import functools
    import http.server
    import shutil
    import subprocess
    import threading
    import time
    import wave

    with wave.open(str(tmp_path / "track.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 2400)
    shutil.copyfile(editions.ROOT / "assets" / "publication" / "reader.js", tmp_path / "reader.js")
    scenes = (
        '<section class="scene" data-audio="track.wav"></section>'
        '<section class="scene" data-page="1" data-audio="track.wav"></section>'
        '<section class="scene" data-page="2"></section>'
    )
    steps = """
let last = "";
new MutationObserver(() => {
  const now = `${cur()},${listen.getAttribute("aria-pressed")}`;
  if (now !== last) { last = now; log(now); }
}).observe(document.body, { attributes: true, subtree: true });
listen.click();
"""
    page = HARNESS.format(scenes=scenes, steps=steps).replace(
        '<pre id="log"></pre>', '<pre id="log"></pre><img src="/hold" alt="">'
    )
    (tmp_path / "harness.html").write_text(page, encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/hold":
                time.sleep(4)
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(tmp_path))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = subprocess.run(
            [
                str(CHROME),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--autoplay-policy=no-user-gesture-required",
                f"--user-data-dir={tmp_path / 'profile'}",
                "--dump-dom",
                f"http://127.0.0.1:{server.server_address[1]}/harness.html",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()
    match = re.search(r'<pre id="log">([^<]*)</pre>', result.stdout)
    assert match, result.stdout[-2000:]
    log = [s for s in match.group(1).split(";") if s]
    # every change in order: reading starts on the cover, each track's end turns the page, and
    # the silent last scene stops reading after its pause (all well inside the 4 s hold)
    assert log == ["0,true", "1,true", "2,true", "2,false"]
