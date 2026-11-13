"""Editions fit each book (spec 020).

Pure helpers first (page plan, names, spine, activity content), then real builds of a small
fixture book and of the pilot (golden check, AC-1.4).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from tests.helpers import MINI_BOOK, PILOT_BOOK, ROOT

BANNED = ("Whisperwood", "Postmaster", "Clara", "Barnaby", "Snail Mail")
PILOT_SLUG = "the-snail-mail-express"
GOLDEN = ROOT / "tests" / "fixtures" / "pilot_editions_golden.json"


def load_build():
    spec = importlib.util.spec_from_file_location("build_book", ROOT / "scripts" / "build_book.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_build()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def story_book(pages: int, *, characters: list[str] | None = None) -> dict:
    """A book with `pages` story pages and full metadata."""
    book = read_json(MINI_BOOK)
    defaults = read_json(ROOT / "config" / "book_defaults.json")["metadata"]
    book["metadata"] = {
        **defaults,
        "title": "The Lantern Bug Parade",
        "subtitle": "A Glowmoss Meadow Tale",
        "author": "Test Author",
        "age_range": "Ages 3-6",
        "blurb": "Tilly learns that a flicker is a light that dances.",
        "dedication": "For every little light.",
    }
    base = book["pages"]
    book["pages"] = [
        {
            **base[i % len(base)],
            "page_number": i + 1,
            "title": f"Glow Step {i + 1}",
            "layout": "image_top_text_bottom",
            "illustration_file": f"page_{i + 1:02d}.png",
        }
        for i in range(pages)
    ]
    if characters is not None:
        book["style_bible"] = {"characters": [{"name": n, "look": "small"} for n in characters]}
    return book


def extras_for(book: dict) -> dict:
    return build.load_extras(ROOT, book)


# --- Phase 1: pure helpers -------------------------------------------------------------------


def kinds(plan) -> list[str]:
    return [p.kind for p in plan]


def test_pilot_plan_grows_to_26_pages() -> None:
    """Spec 023 AC-3.1: front, stories, activity, Goodnight, drawings to max(24, front+N+2)."""
    plan = build.plan_interior(read_json(PILOT_BOOK), has_map=True)
    assert len(plan) == 26
    assert kinds(plan)[:3] == ["half_title", "title", "map"]
    assert plan[3].kind == "story" and plan[3].story["page_number"] == 1
    assert kinds(plan)[23:] == ["activity", "goodnight", "drawing"]
    assert "blank" not in kinds(plan)
    assert [p.number for p in plan] == list(range(1, 27))


def test_ten_page_book_is_padded_to_kdp_minimum() -> None:
    """AC-3.1: 3 front + 10 stories + activity + Goodnight + 9 drawing pages = 24."""
    plan = build.plan_interior(story_book(10), has_map=False)
    assert len(plan) == 24
    assert kinds(plan)[:3] == ["half_title", "title", "blank"]
    assert plan[3].kind == "story" and plan[3].story["page_number"] == 1
    assert kinds(plan).count("story") == 10
    assert kinds(plan)[13:15] == ["activity", "goodnight"]
    assert kinds(plan)[15:] == ["drawing"] * 9


def test_long_book_gets_no_padding_beyond_even_rounding() -> None:
    plan = build.plan_interior(story_book(30), has_map=False)
    assert len(plan) == 36  # 2 front + blank + 30 + activity + Goodnight, rounded up to even
    assert kinds(plan)[-3:] == ["activity", "goodnight", "drawing"]
    odd = build.plan_interior(story_book(31), has_map=False)
    assert len(odd) == 36 and "drawing" not in kinds(odd)
    assert kinds(odd)[-2:] == ["activity", "goodnight"]


def test_story_pages_keep_order_and_bleed_side_by_parity() -> None:
    """AC-2.3: story pages in page_number order; even interior page = left bleed."""
    plan = build.plan_interior(story_book(10), has_map=False)
    story = [p for p in plan if p.kind == "story"]
    assert [p.story["page_number"] for p in story] == list(range(1, 11))
    assert story[0].number % 2 == 0
    assert build.story_bleed(4, 9.0) == (9.0, 0.0)
    assert build.story_bleed(5, 9.0) == (0.0, 9.0)


def test_edition_names_state_real_counts() -> None:
    """AC-3.1."""
    names = build.edition_names("Jack_and_the_Topsy_Turvy_Dream", 10, 24)
    assert names["digital"] == "Jack_and_the_Topsy_Turvy_Dream_10Page_Digital_Edition.pdf"
    assert names["kdp"] == "Jack_and_the_Topsy_Turvy_Dream_KDP_24Page_Print_Interior.pdf"
    pilot = build.edition_names("The_Snail_Mail_Express", 20, 24)
    assert pilot["digital"] == "The_Snail_Mail_Express_20Page_Digital_Edition.pdf"
    assert pilot["kdp"] == "The_Snail_Mail_Express_KDP_24Page_Print_Interior.pdf"


def test_spine_and_wrap_size_follow_interior_pages() -> None:
    """AC-2.2: P=24 keeps today's 5191 x 2625 px wrap; more pages widen the spine."""
    assert build.spine_inches(24) == pytest.approx(24 * 0.002252)
    w, h = build.wrap_size_inches(24)
    assert (round(w * 300), round(h * 300)) == (5191, 2625)
    assert build.wrap_size_inches(36)[0] > w


def test_character_names_from_sheet_else_page_titles() -> None:
    sheet = story_book(10, characters=[f"Bug {i}" for i in range(8)])
    assert build.character_names(sheet, limit=6) == [f"Bug {i}" for i in range(6)]
    plain = story_book(4)
    assert build.character_names(plain, limit=6) == [f"Glow Step {i}" for i in range(1, 5)]


def test_pilot_activity_comes_from_its_book_data() -> None:
    book = read_json(PILOT_BOOK)
    activity = build.activity_content(book, extras_for(book))
    assert activity == {**book["print_extras"]["activity"], **activity}
    assert activity["title"] == "The Junior Postmaster's Field Guide"
    assert len(activity["items"]) == 4 and all(i["text"] for i in activity["items"])
    assert len(activity["questions"]) == 3


def test_generic_activity_is_made_from_the_book() -> None:
    """AC-1.3: 'Can you find…?' list of the book's characters + 2–3 filled questions."""
    book = story_book(10, characters=["Tilly", "Granny Dusk"])
    activity = build.activity_content(book, extras_for(book))
    assert activity["title"] == "Can you find…?"
    assert [i["title"] for i in activity["items"]] == ["Tilly", "Granny Dusk"]
    assert 2 <= len(activity["questions"]) <= 3
    joined = " ".join(activity["questions"])
    assert "Tilly" in joined and "Granny Dusk" in joined and "The Lantern Bug Parade" in joined
    assert "{" not in joined and "}" not in joined
    for word in BANNED:
        assert word not in json.dumps(activity)


def test_one_character_skips_two_character_templates() -> None:
    book = story_book(10, characters=["Tilly"])
    activity = build.activity_content(book, extras_for(book))
    assert 2 <= len(activity["questions"]) <= 3
    assert all("{" not in q for q in activity["questions"])


def test_no_sheet_lists_page_titles_but_names_no_title_as_a_character() -> None:
    """Error scenario: no character sheet → the list uses page titles."""
    book = story_book(10)
    activity = build.activity_content(book, extras_for(book))
    assert [i["title"] for i in activity["items"]][:2] == ["Glow Step 1", "Glow Step 2"]
    assert 2 <= len(activity["questions"]) <= 3
    assert not any("Glow Step" in q for q in activity["questions"])


def test_missing_config_key_is_named(tmp_path: Path) -> None:
    config = read_json(ROOT / "config" / "book_extras.json")
    del config["activity_title"]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "book_extras.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(build.ExtrasError, match=r"book_extras\.json.*activity_title"):
        build.load_extras(tmp_path, story_book(4))
    with pytest.raises(build.ExtrasError, match=r"book_extras\.json"):
        build.load_extras(tmp_path / "nowhere", story_book(4))


def test_book_print_extras_override_config() -> None:
    book = story_book(4)
    book["print_extras"] = {"bookplate_line": "Hello!"}
    extras = build.load_extras(ROOT, book)
    assert extras["bookplate_line"] == "Hello!"
    assert extras["activity_title"] == "Can you find…?"


DEAD_EXTRAS = ("bookplate_label", "front_footer", "back_cover_headline")


def test_extras_need_no_dead_keys(tmp_path: Path) -> None:
    """Spec 023 draws no bookplate label, front footer or back-cover headline."""
    config = read_json(ROOT / "config" / "book_extras.json")
    for key in DEAD_EXTRAS:
        assert key not in config
        assert key not in build.EXTRAS_KEYS
        config.pop(key, None)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "book_extras.json").write_text(json.dumps(config), encoding="utf-8")
    assert build.load_extras(tmp_path, story_book(4))["bookplate_line"]


def test_stale_editions_lists_other_counts_only(tmp_path: Path) -> None:
    """AC-3.2."""
    keep = {"X_10Page_Digital_Edition.pdf", "X_KDP_24Page_Print_Interior.pdf"}
    names = [
        *keep,
        "X_20Page_Digital_Edition.pdf",
        "X_KDP_14Page_Print_Interior.pdf",
        "Y_20Page_Digital_Edition.pdf",
        "X_Y_20Page_Digital_Edition.pdf",
        "X_Complete_Reader.pdf",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"%PDF")
    stale = build.stale_editions(tmp_path, "X", keep)
    assert sorted(p.name for p in stale) == [
        "X_20Page_Digital_Edition.pdf",
        "X_KDP_14Page_Print_Interior.pdf",
    ]


def test_copyright_lines_use_the_books_ages() -> None:
    book = story_book(4)
    lines = build.copyright_lines(book, extras_for(book))
    assert "Reading Age: 3-6 years • Grade Level: 1–3 • Juvenile Fiction" in lines
    pilot = read_json(PILOT_BOOK)
    assert (
        "Reading Age: 6–8 years • Grade Level: 1–3 • Juvenile Fiction / Animals / Woodland"
        in build.copyright_lines(pilot, extras_for(pilot))
    )


def test_build_script_has_no_pilot_words() -> None:
    """FR-1: pilot text lives in the pilot's book data, not code."""
    source = (ROOT / "scripts" / "build_book.py").read_text(encoding="utf-8")
    for word in BANNED:
        assert word not in source, word
    assert "map_whisperwood" not in source


# --- Phase 2/3: real builds ------------------------------------------------------------------


def tiny_png(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (48, 48), color).save(path)


def make_root(root: Path, slug: str, book: dict, *, map_file: str | None = None) -> Path:
    folder = root / "books" / slug
    (folder / "illustrations").mkdir(parents=True)
    (folder / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    for i, page in enumerate(book["pages"]):
        tiny_png(folder / "illustrations" / page["illustration_file"], (20 * i % 255, 120, 90))
    tiny_png(folder / "illustrations" / "cover_front.png", (200, 80, 60))
    tiny_png(folder / "illustrations" / "cover_front_print.png", (200, 80, 60))  # spec 022
    tiny_png(folder / "illustrations" / "cover_back.png", (60, 80, 200))
    if map_file:
        tiny_png(folder / "illustrations" / map_file, (90, 160, 90))
    (root / "config").mkdir(exist_ok=True)
    shutil.copy(ROOT / "config" / "book_extras.json", root / "config" / "book_extras.json")
    return folder / "build"


def pdf_texts(path: Path) -> list[str]:
    with pymupdf.open(path) as doc:
        return [page.get_text() for page in doc]


def all_edition_text(build_dir: Path) -> str:
    pdfs = sorted(build_dir.glob("The_Lantern_Bug_Parade*.pdf"))
    parts = ["\n".join(pdf_texts(p)) for p in pdfs]
    for epub in build_dir.glob("*.epub"):
        with zipfile.ZipFile(epub) as zf:
            parts += [zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith("xhtml")]
    parts.append((build_dir / "index.html").read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def ten_page_build(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("ten")
    book = story_book(10, characters=["Tilly", "Granny Dusk"])
    build_dir = make_root(root, "lantern-bug-parade", book)
    build_dir.mkdir(parents=True)
    stem = "The_Lantern_Bug_Parade"
    (build_dir / f"{stem}_20Page_Digital_Edition.pdf").write_bytes(b"%PDF old")
    (build_dir / f"{stem}_KDP_14Page_Print_Interior.pdf").write_bytes(b"%PDF old")
    (build_dir / "Other_Book_20Page_Digital_Edition.pdf").write_bytes(b"%PDF other")
    assert build.main(["--book", "lantern-bug-parade", "--root", str(root)]) == 0
    return build_dir


def test_generic_book_has_only_its_own_content(ten_page_build: Path) -> None:
    """AC-1.1, AC-1.2."""
    text = all_edition_text(ten_page_build)
    for word in BANNED:
        assert word not in text, word
    kdp = pdf_texts(ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf")
    front = " ".join("\n".join(kdp[:3]).split())  # titles may wrap across lines
    assert "The Lantern Bug Parade" in front and "A Glowmoss Meadow Tale" in front
    assert "Test Author" in front and "For every little light." in front
    assert "Giggle & Snooze" in front and "2026" in front
    assert not any("Map of" in t for t in kdp)  # no map_*.png → no map page
    activity, goodnight = kdp[13], kdp[14]  # spec 023 AC-3.1: after the story pages
    assert "Can you find…?" in activity and "Tilly" in activity
    assert "Goodnight" in goodnight and "For every little light." in goodnight  # no blueprint
    assert not any("THE END" in t for t in kdp)


def test_kdp_interior_is_padded_with_drawing_pages(ten_page_build: Path) -> None:
    """AC-2.1."""
    kdp = pdf_texts(ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf")
    assert len(kdp) == 24
    drawing = [t for t in kdp if "My drawing" in t]
    assert len(drawing) == 9
    assert all("The Lantern Bug Parade" in t for t in drawing)
    assert kdp[3].strip().startswith("Glow Step 1")


def test_story_pages_alternate_bleed_side(ten_page_build: Path) -> None:
    """Spec 023 AC-3.6: the picture covers the whole page; the words shift by the bleed (and,
    FR-3, by the folio gap, which sits on the outer side)."""
    path = ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf"
    with pymupdf.open(path) as doc:
        for i in (3, 4):
            page = doc[i]
            assert (page.rect.width, page.rect.height) == pytest.approx((621.0, 630.0))
            bbox = page.get_image_info()[0]["bbox"]
            assert bbox == pytest.approx((0, 0, 621, 630), abs=0.01)
        texts = [[b for b in doc[i].get_text("dict")["blocks"] if b["type"] == 0] for i in (3, 4)]
        xs = [blocks[0]["bbox"][0] for blocks in texts]
    gap = build.load_design()["page"]["folio_gap_u"] * 6.12  # FR-3: the folio gap goes outside
    assert xs[0] - xs[1] == pytest.approx(0.125 * 72 + gap, abs=0.01)


def page_pixel(path: Path, index: int, at: tuple[float, float]) -> tuple[int, int, int]:
    with pymupdf.open(path) as doc:
        pix = doc[index].get_pixmap(dpi=36)
    x, y = at
    return pix.pixel(round(x * pix.width), round(y * pix.height))


def test_drawing_pages_are_light_and_goodnight_is_dark(ten_page_build: Path) -> None:
    """Spec 023 AC-3.3, AC-3.4."""
    path = ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf"
    for index in (15, 23):
        assert min(page_pixel(path, index, (0.5, 0.05))) > 230
    assert max(page_pixel(path, 14, (0.5, 0.2))) < 80


def test_pdfs_embed_only_the_bundled_faces(ten_page_build: Path) -> None:
    """Spec 023 AC-7.1: Young Serif and Andika only; no Georgia or Times."""
    for pdf in ten_page_build.glob("The_Lantern_Bug_Parade*.pdf"):
        with pymupdf.open(pdf) as doc:
            fonts = {f[3] for i in range(len(doc)) for f in doc.get_page_fonts(i)}
        if "Wrap_Cover" in pdf.name:
            continue
        assert fonts, pdf.name
        for name in fonts:
            assert "YoungSerif" in name or "Andika" in name, (pdf.name, name)


def test_missing_font_stops_before_any_edition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Spec 023 AC-7.2."""
    build_dir = make_root(tmp_path, "lantern-bug-parade", story_book(4))
    design = build.load_design()
    broken = {**design, "fonts": {**design["fonts"], "display": "assets/fonts/Missing-Face.ttf"}}
    monkeypatch.setattr(build, "load_design", lambda *a, **k: broken)
    assert build.main(["--book", "lantern-bug-parade", "--root", str(tmp_path)]) == 2
    assert "Missing-Face.ttf" in capsys.readouterr().err
    assert not build_dir.exists() or not any(build_dir.iterdir())


def test_impossible_story_text_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Spec 023: every page is laid out (and fitted) before any edition file is written."""
    book = story_book(10)
    book["pages"][6]["text"] = " ".join(["A very long story sentence that never ends."] * 80)
    build_dir = make_root(tmp_path, "lantern-bug-parade", book)
    build_dir.mkdir(parents=True)
    stem = "The_Lantern_Bug_Parade"
    before = {
        f"{stem}_10Page_Digital_Edition.pdf": b"%PDF old digital",
        f"{stem}_Complete_Reader.pdf": b"%PDF old reader",
        f"{stem}_Front_Cover.png": b"old front",
        f"{stem}_Back_Cover.png": b"old back",
        f"{stem}.epub": b"old epub",
        "index.html": b"<p>old reader</p>",
    }
    for name, data in before.items():
        (build_dir / name).write_bytes(data)
    assert build.main(["--book", "lantern-bug-parade", "--root", str(tmp_path)]) == 2
    assert "page 7" in capsys.readouterr().err
    after = {p.name: p.read_bytes() for p in build_dir.iterdir()}
    assert after == before


def plan_file(build_dir: Path, page: int, sha: str, boxes: list[list[int]]) -> Path:
    """An animation plan (spec 009) for one page of the tmp book; 48 x 48 px picture."""
    from video_pipeline.motion_plan import PLAN_DIR

    path = build_dir.parent / PLAN_DIR / f"page_{page:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "picture": {"sha256": sha, "width": 48, "height": 48},
        "layers": [{"box": box} for box in boxes],
    }
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_story_focus_reads_the_pages_animation_plan(tmp_path: Path) -> None:
    """AC-1.3/1.4: a current plan file picks crop and side; a stale or broken one is ignored."""
    from video_pipeline.file_hash import sha256_file
    from video_pipeline.library import book_folder

    book = story_book(4)
    build_dir = make_root(tmp_path, "lantern-bug-parade", book)
    paths = book_folder("lantern-bug-parade", tmp_path)
    page = sorted(book["pages"], key=lambda p: p["page_number"])[1]
    assert page["layout"] == "image_top_text_bottom"  # the layout alone says bottom
    sha = sha256_file(paths.illustrations / page["illustration_file"])
    plan = plan_file(build_dir, 2, sha, [[0, 30, 24, 48], [10, 34, 30, 46], [0, 0, 2, 2]])
    focus_x, side = build.story_focus(paths, page)
    assert side == "top"  # the subject fills the bottom, so the words go to the top
    assert focus_x == pytest.approx(15 / 48)
    plan_file(build_dir, 2, "0" * 64, [[0, 30, 24, 48]])  # stale: another picture
    assert build.story_focus(paths, page) == (0.5, "bottom")
    plan.write_text("{not json", encoding="utf-8")
    assert build.story_focus(paths, page) == (0.5, "bottom")


def test_goodnight_page_uses_the_blueprints_outro_hook(tmp_path: Path) -> None:
    """AC-3.3: a real blueprint.json with qa.outro_hook puts it on the Goodnight page."""
    build_dir = make_root(tmp_path, "lantern-bug-parade", story_book(10))
    hook = "Close your eyes and count the glowing bugs."
    blueprint = {"qa": {"outro_hook": hook, "format": "gentle"}, "mood": {}}
    (build_dir.parent / "blueprint.json").write_text(json.dumps(blueprint), encoding="utf-8")
    assert build.main(["--book", "lantern-bug-parade", "--root", str(tmp_path)]) == 0
    kdp = pdf_texts(build_dir / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf")
    goodnight = " ".join(kdp[14].split())
    assert "Goodnight" in goodnight and hook in goodnight
    assert "For every little light." not in goodnight


def test_wrap_cover_spine_is_the_night_colour(ten_page_build: Path) -> None:
    """AC-4.4: back + night spine + front."""
    night = build.pub.color(build.load_design(), "night")
    w, _ = build.wrap_size_inches(24)
    with Image.open(ten_page_build / "The_Lantern_Bug_Parade_KDP_Print_Wrap_Cover.png") as im:
        rgb = im.convert("RGB")
        mid = rgb.width // 2
        assert rgb.width == round(w * 300)
        for y in range(0, rgb.height, rgb.height // 10):
            pixel = rgb.getpixel((mid, y))
            assert all(abs(a - b) <= 2 for a, b in zip(pixel, night, strict=True)), (y, pixel)


def test_wrap_cover_uses_interior_page_count(ten_page_build: Path) -> None:
    """AC-2.2."""
    w, h = build.wrap_size_inches(24)
    with pymupdf.open(ten_page_build / "The_Lantern_Bug_Parade_KDP_Print_Wrap_Cover.pdf") as doc:
        assert doc[0].rect.width == pytest.approx(w * 72, abs=0.01)
        assert doc[0].rect.height == pytest.approx(h * 72, abs=0.01)
    with Image.open(ten_page_build / "The_Lantern_Bug_Parade_KDP_Print_Wrap_Cover.png") as im:
        assert im.size == (round(w * 300), round(h * 300))


def test_files_are_named_by_real_counts_and_stale_ones_removed(ten_page_build: Path) -> None:
    """AC-3.1, AC-3.2."""
    names = {p.name for p in ten_page_build.iterdir()}
    assert "The_Lantern_Bug_Parade_10Page_Digital_Edition.pdf" in names
    assert "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf" in names
    assert "The_Lantern_Bug_Parade_20Page_Digital_Edition.pdf" not in names
    assert "The_Lantern_Bug_Parade_KDP_14Page_Print_Interior.pdf" not in names
    assert "Other_Book_20Page_Digital_Edition.pdf" in names
    digital = pdf_texts(ten_page_build / "The_Lantern_Bug_Parade_10Page_Digital_Edition.pdf")
    assert len(digital) == 10


def test_reader_links_match_written_files(ten_page_build: Path) -> None:
    """AC-3.1, spec 023 AC-5.1: hrefs point at the files written; link texts and the page
    counter use the real page counts."""
    html = (ten_page_build / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'href="([^"]+\.(?:pdf|epub))"', html)
    assert hrefs and all((ten_page_build / h).is_file() for h in hrefs)
    assert "20Page" not in html and "Digital edition, 10 pages" in html
    assert "20 pages" not in html and html.count("data-page=") == 10
    assert "../illustrations/" in html and "../assets/illustrations/" not in html


def test_map_page_appears_only_with_a_map_picture(tmp_path: Path) -> None:
    """AC-1.2."""
    book = story_book(10)
    build_dir = make_root(tmp_path, "lantern-bug-parade", book, map_file="map_meadow.png")
    assert build.main(["--book", "lantern-bug-parade", "--root", str(tmp_path)]) == 0
    kdp = pdf_texts(build_dir / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf")
    assert len(kdp) == 24
    assert "Map of The Lantern Bug Parade" in kdp[2]
    assert kdp[3].strip().startswith("Glow Step 1")
    assert sum("My drawing" in t for t in kdp) == 9  # the map page replaces the blank


# --- Pilot golden (AC-1.4) -------------------------------------------------------------------


@pytest.fixture(scope="module")
def pilot_build(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("pilot")
    folder = root / "books" / PILOT_SLUG
    folder.mkdir(parents=True)
    shutil.copy(PILOT_BOOK, folder / "book.json")
    # Text and page sizes do not depend on pixels: tiny stand-ins keep this test fast.
    (folder / "illustrations").mkdir()
    for picture in sorted((PILOT_BOOK.parent / "illustrations").glob("*.png")):
        tiny_png(folder / "illustrations" / picture.name, (180, 140, 90))
    (root / "assets").symlink_to(ROOT / "assets")
    (root / "config").mkdir()
    shutil.copy(ROOT / "config" / "book_extras.json", root / "config" / "book_extras.json")
    assert build.main(["--book", PILOT_SLUG, "--root", str(root)]) == 0
    return folder / "build"


def test_pilot_editions_keep_their_content(pilot_build: Path) -> None:
    golden = read_json(GOLDEN)
    assert sorted(p.name for p in pilot_build.iterdir() if p.is_file()) == golden["files"]
    for name, expected in golden.items():
        if name == "files":
            continue
        if name.endswith(".png"):
            with Image.open(pilot_build / name) as im:
                assert list(im.size) == expected["size"], name
            continue
        with pymupdf.open(pilot_build / name) as doc:
            assert [p.get_text() for p in doc] == expected["pages"], name
            sizes = [[round(p.rect.width, 2), round(p.rect.height, 2)] for p in doc]
            assert sizes == expected["sizes"], name


def test_pilot_reader_links_unchanged(pilot_build: Path) -> None:
    html = (pilot_build / "index.html").read_text(encoding="utf-8")
    assert 'href="The_Snail_Mail_Express_20Page_Digital_Edition.pdf"' in html
    assert 'href="The_Snail_Mail_Express_Complete_Reader.pdf"' in html
    assert 'href="The_Snail_Mail_Express.epub"' in html
    assert "KDP" not in html  # spec 023 AC-5.1: the print files are not for readers
