"""Print editions use a titled book's painted print cover as is (spec 022 AC-2.2, AC-2.3)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageOps

from tests import test_book_editions as editions
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


# --- AC-2.2 / AC-2.3: every print edition takes the fitted print cover, nothing drawn on top ---


def _blocks(size: int = 600) -> Image.Image:
    """A print cover of four solid colour blocks: any plaque or lettering would break them."""
    half = size // 2
    im = Image.new("RGB", (size, size), (210, 40, 40))
    im.paste((40, 170, 60), (half, 0, size, half))
    im.paste((40, 70, 200), (0, half, half, size))
    im.paste((240, 210, 30), (half, half, size, size))
    return im


def _mean_diff(a: Image.Image, b: Image.Image, size: int = 64) -> float:
    a = np.asarray(a.convert("RGB").resize((size, size), Image.Resampling.BOX), dtype=float)
    b = np.asarray(b.convert("RGB").resize((size, size), Image.Resampling.BOX), dtype=float)
    return float(np.abs(a - b).mean())


@pytest.fixture(scope="module")
def titled_build(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, Image.Image]:
    root = tmp_path_factory.mktemp("titled")
    book = editions.story_book(10)
    assert book["metadata"]["cover_style"] == "titled"
    build_dir = editions.make_root(root, "lantern-bug-parade", book)
    _blocks().save(build_dir.parent / "illustrations" / "cover_front_print.png")
    assert editions.build.main(["--book", "lantern-bug-parade", "--root", str(root)]) == 0
    fitted = ImageOps.fit(_blocks(), (2550, 2550), Image.Resampling.LANCZOS)
    return build_dir, "The_Lantern_Bug_Parade", fitted


def test_front_cover_png_is_the_fitted_print_cover(titled_build):  # AC-2.2
    build_dir, stem, fitted = titled_build
    with Image.open(build_dir / f"{stem}_Front_Cover.png") as im:
        assert im.convert("RGB").tobytes() == fitted.tobytes()


def test_wrap_front_panel_is_the_fitted_print_cover(titled_build):  # AC-2.2
    build_dir, stem, fitted = titled_build
    with Image.open(build_dir / f"{stem}_KDP_Print_Wrap_Cover.png") as im:
        wrap = np.asarray(im.convert("RGB"))
    height, width = wrap.shape[:2]
    panel = round((8.5 + 0.125) * 300)
    expected = np.asarray(fitted.resize((panel, height), Image.Resampling.LANCZOS))
    # the spine strip's right edge covers the panel's first column, so compare from column 1
    assert np.array_equal(wrap[:, width - panel + 1 :], expected[:, 1:])


def test_reader_pdf_opens_on_the_fitted_print_cover(titled_build):  # AC-2.2
    build_dir, stem, fitted = titled_build
    with pymupdf.open(build_dir / f"{stem}_Complete_Reader.pdf") as doc:
        pix = doc[0].get_pixmap(dpi=36)
        page = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    assert _mean_diff(page, fitted) < 3


def test_epub_cover_is_the_fitted_print_cover(titled_build):  # AC-2.2
    build_dir, stem, fitted = titled_build
    (epub,) = build_dir.glob("*.epub")
    with (
        zipfile.ZipFile(epub) as zf,
        Image.open(io.BytesIO(zf.read("OEBPS/images/cover.jpg"))) as im,
    ):
        assert im.size == (1400, 1400)
        assert _mean_diff(im, fitted) < 3


def test_flipbook_front_cover_is_the_fitted_print_cover(titled_build):  # AC-2.3
    build_dir, stem, fitted = titled_build
    html = (build_dir / "index.html").read_text(encoding="utf-8")
    assert f'<img src="{stem}_Front_Cover.png" alt="Front Cover"/>' in html
    with Image.open(build_dir / f"{stem}_Front_Cover.png") as im:
        assert im.convert("RGB").tobytes() == fitted.tobytes()
