"""Print editions use a titled book's painted print cover as is (spec 022 AC-4.1-4.3)."""

from __future__ import annotations

import pytest
from PIL import Image, ImageOps

from tests.helpers import MINI_BOOK, load_json
from tests.test_book_scripts_paths import build
from video_pipeline.library import BookFolder


def _titled(tmp_path) -> tuple[dict, BookFolder]:
    book = load_json(MINI_BOOK)
    book["metadata"]["cover_style"] = "titled"
    folder = BookFolder("titled-book", tmp_path)
    folder.illustrations.mkdir(parents=True)
    return book, folder


def test_titled_print_cover_used_without_lettering(tmp_path):
    book, folder = _titled(tmp_path)
    painted = Image.new("RGB", (300, 260), (30, 90, 160))
    painted.paste((240, 200, 40), (40, 40, 120, 120))
    painted.save(folder.illustrations / "cover_front_print.png")
    out = tmp_path / "front.png"

    returned = build.create_front_cover_image(book, out, size_px=200, paths=folder)

    expected = ImageOps.fit(painted, (200, 200), Image.Resampling.LANCZOS)
    with Image.open(out) as im:
        assert im.size == (200, 200)
        assert im.convert("RGB").tobytes() == expected.tobytes()
    assert returned.convert("RGB").tobytes() == expected.tobytes()


def test_titled_book_missing_print_cover_fails(tmp_path):
    book, folder = _titled(tmp_path)
    Image.new("RGB", (64, 64)).save(folder.illustrations / "cover_front.png")
    with pytest.raises(FileNotFoundError, match="cover_front_print.png"):
        build.create_front_cover_image(book, tmp_path / "front.png", size_px=200, paths=folder)
