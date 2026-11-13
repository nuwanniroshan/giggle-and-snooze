"""Channel brand in book data and the print book (spec 015, Story 2: AC-2.1 to AC-2.3)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.helpers import MINI_BOOK, ROOT
from video_pipeline.library import BookFolder

BRAND = "Giggle & Snooze"
LOGO = ROOT / "assets" / "brand" / "giggle_and_snooze_logo.png"
BOOK_FILES = sorted((ROOT / "books").glob("*/book.json"))


def load_build():
    spec = importlib.util.spec_from_file_location("build_book", ROOT / "scripts" / "build_book.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_build()


def test_there_are_books_to_check() -> None:
    assert BOOK_FILES


@pytest.mark.parametrize("path", [*BOOK_FILES, ROOT / "config" / "book_defaults.json"])
def test_book_data_names_the_channel_brand(path: Path) -> None:  # AC-2.1
    text = path.read_text(encoding="utf-8")
    # (the pilot's style_bible holds absolute machine paths that contain the folder name
    # "Swivel"; only the imprint names are brand text)
    assert "Swivel Kids" not in text
    assert "Whisperwood Storybook Press" not in text
    data = json.loads(text)
    meta = data.get("metadata", data)
    assert data.get("author", meta.get("author")) == BRAND
    assert meta["publisher"] == BRAND


def test_build_script_has_no_hardcoded_imprint() -> None:  # AC-2.3
    source = (ROOT / "scripts" / "build_book.py").read_text(encoding="utf-8")
    assert "Swivel" not in source
    assert "Whisperwood Storybook Press" not in source


def mini_data(publisher: str = BRAND) -> dict:
    data = json.loads(MINI_BOOK.read_text(encoding="utf-8"))
    data["metadata"]["publisher"] = publisher
    data["metadata"]["author"] = BRAND
    data["metadata"].update(publication_year=2026, isbn_placeholder="979-8-00000-000-0")
    data["metadata"].setdefault("subtitle", "A test story")
    return data


def test_copyright_lines_name_the_publisher_field() -> None:  # AC-2.3
    lines = build.copyright_lines(mini_data("Some Press"))
    assert "Published by Some Press." in lines
    assert any("by Some Press. All rights reserved." in line for line in lines)
    assert not any("imprint" in line for line in lines)


def make_cover_book(root: Path) -> tuple[dict, BookFolder]:
    folder = BookFolder("mini", root)
    folder.illustrations.mkdir(parents=True)
    Image.new("RGB", (64, 64), (90, 140, 200)).save(folder.illustrations / "cover_front.png")
    return mini_data(), folder


def test_front_cover_draws_without_the_logo_using_the_author(tmp_path: Path) -> None:  # AC-2.2
    data, folder = make_cover_book(tmp_path)
    out = tmp_path / "cover.png"
    img = build.create_front_cover_image(data, out, size_px=400, paths=folder)
    assert img.size == (400, 400)
    assert out.is_file()


def test_front_cover_places_the_logo_badge_near_the_bottom(tmp_path: Path) -> None:  # AC-2.2
    data, folder = make_cover_book(tmp_path)
    plain = build.create_front_cover_image(data, tmp_path / "a.png", size_px=400, paths=folder)
    logo_dir = tmp_path / "assets" / "brand"
    logo_dir.mkdir(parents=True)
    Image.new("RGBA", (1256, 733), (230, 40, 40, 255)).save(logo_dir / LOGO.name)
    badged = build.create_front_cover_image(data, tmp_path / "b.png", size_px=400, paths=folder)
    assert badged.size == (400, 400)
    # the badge sits centred in the bottom band, about 30% of the width
    centre = badged.getpixel((200, 365))
    assert centre[0] > 200 and centre[1] < 90  # the red logo pixels
    assert badged.getpixel((200, 365)) != plain.getpixel((200, 365))


@pytest.mark.skipif(not LOGO.is_file(), reason="brand logo not present")
def test_front_cover_with_the_real_logo(tmp_path: Path) -> None:  # AC-2.2
    data, folder = make_cover_book(tmp_path)
    logo_dir = tmp_path / "assets" / "brand"
    logo_dir.mkdir(parents=True)
    (logo_dir / LOGO.name).write_bytes(LOGO.read_bytes())
    img = build.create_front_cover_image(data, tmp_path / "c.png", size_px=400, paths=folder)
    assert img.size == (400, 400)
