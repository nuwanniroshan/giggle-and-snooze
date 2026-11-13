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


def test_pilot_plan_keeps_todays_24_pages() -> None:
    plan = build.plan_interior(read_json(PILOT_BOOK), has_map=True)
    assert len(plan) == 24
    assert kinds(plan)[:3] == ["half_title", "title", "map"]
    assert plan[3].kind == "story" and plan[3].story["page_number"] == 1
    assert plan[-1].kind == "activity" and plan[-1].number == 24
    assert "drawing" not in kinds(plan) and "blank" not in kinds(plan)
    assert [p.number for p in plan] == list(range(1, 25))


def test_ten_page_book_is_padded_to_kdp_minimum() -> None:
    """AC-2.1: max(24, front + N + back), padded with drawing pages before the activity."""
    plan = build.plan_interior(story_book(10), has_map=False)
    assert len(plan) == 24
    assert kinds(plan)[:3] == ["half_title", "title", "blank"]
    assert plan[3].kind == "story" and plan[3].story["page_number"] == 1
    assert kinds(plan).count("story") == 10
    assert kinds(plan).count("drawing") == 10
    assert kinds(plan)[14:23] == ["drawing"] * 9
    assert plan[-1].kind == "activity"


def test_long_book_gets_no_padding_beyond_even_rounding() -> None:
    plan = build.plan_interior(story_book(30), has_map=False)
    assert len(plan) == 34  # 2 front + blank + 30 + activity
    assert "drawing" not in kinds(plan)
    odd = build.plan_interior(story_book(31), has_map=False)
    assert len(odd) == 36 and kinds(odd).count("drawing") == 1
    assert odd[-2].kind == "drawing" and odd[-1].kind == "activity"


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
    book["print_extras"] = {"back_cover_headline": "Hello!"}
    extras = build.load_extras(ROOT, book)
    assert extras["back_cover_headline"] == "Hello!"
    assert extras["activity_title"] == "Can you find…?"


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
    front = "\n".join(kdp[:3])
    assert "The Lantern Bug Parade" in front and "A Glowmoss Meadow Tale" in front
    assert "Test Author" in front and "For every little light." in front
    assert "Giggle & Snooze" in front and "2026" in front
    assert not any("Map of" in t for t in kdp)  # no map_*.png → no map page
    assert "Can you find…?" in kdp[-1] and "Tilly" in kdp[-1] and "THE END" in kdp[-1]


def test_kdp_interior_is_padded_with_drawing_pages(ten_page_build: Path) -> None:
    """AC-2.1."""
    kdp = pdf_texts(ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf")
    assert len(kdp) == 24
    drawing = [t for t in kdp if "My drawing" in t]
    assert len(drawing) == 10
    assert all("The Lantern Bug Parade" in t for t in drawing)
    assert kdp[3].strip().startswith("Glow Step 1")


def test_story_pages_alternate_bleed_side(ten_page_build: Path) -> None:
    """AC-2.3: the picture shifts by the bleed between facing story pages, as today."""
    path = ten_page_build / "The_Lantern_Bug_Parade_KDP_24Page_Print_Interior.pdf"
    with pymupdf.open(path) as doc:
        xs = [doc[i].get_image_info()[0]["bbox"][0] for i in (3, 4)]
        size = (doc[3].rect.width, doc[3].rect.height)
    assert size == pytest.approx((621.0, 630.0))
    assert xs[0] - xs[1] == pytest.approx(0.125 * 72, abs=0.01)


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


def test_flipbook_links_match_written_files(ten_page_build: Path) -> None:
    """AC-3.1: hrefs point at the files written; the counter uses the real page count."""
    html = (ten_page_build / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'href="([^"]+\.(?:pdf|epub))"', html)
    assert hrefs and all((ten_page_build / h).is_file() for h in hrefs)
    assert "20Page" not in html and "24-Page" in html and "10-Page" in html
    assert "of 20" not in html and "pages.length" in html
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
    assert sum("My drawing" in t for t in kdp) == 10  # the map page replaces the blank


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


def test_pilot_flipbook_links_unchanged(pilot_build: Path) -> None:
    html = (pilot_build / "index.html").read_text(encoding="utf-8")
    assert 'href="The_Snail_Mail_Express_20Page_Digital_Edition.pdf"' in html
    assert 'href="The_Snail_Mail_Express_KDP_24Page_Print_Interior.pdf"' in html
