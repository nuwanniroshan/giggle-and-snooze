"""Book production scripts take --book and write only inside books/<slug>/ (spec 005, AC-2.x).

The renderers are stubbed: these tests pin paths and refusals, not pixels. The pilot's
end-to-end comparison (AC-3.2) is run separately during cut-over.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import MINI_BOOK, ROOT


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script("build_book")
compose = load_script("compose_missing_illustrations")


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def make_book(root: Path, slug: str, title: str) -> Path:
    folder = root / "books" / slug
    folder.mkdir(parents=True)
    book = json.loads(MINI_BOOK.read_text(encoding="utf-8"))
    book["metadata"]["title"] = title
    (folder / "book.json").write_text(json.dumps(book, indent=2), encoding="utf-8")
    return folder


def test_build_requires_book(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as err:
        build.main([])
    assert err.value.code == 2
    assert "--book" in capsys.readouterr().err


def test_compose_requires_book() -> None:
    with pytest.raises(SystemExit) as err:
        compose.main([])
    assert err.value.code == 2


def test_unknown_slug_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    make_book(tmp_path, "other-book", "Other Book")
    before = snapshot(tmp_path)

    assert build.main(["--book", "nope", "--root", str(tmp_path)]) == 2
    assert compose.main(["--book", "nope", "--root", str(tmp_path)]) == 2

    assert snapshot(tmp_path) == before


def test_build_writes_only_into_the_book_build_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = make_book(tmp_path, "moon-kite-race", "Moon Kite Race")
    other = make_book(tmp_path, "other-book", "Other Book")
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "book_extras.json", tmp_path / "config" / "book_extras.json")
    before_other = snapshot(other)
    calls: dict[str, object] = {}

    def record(name: str):
        def stub(*args, **kwargs):
            calls[name] = (args, kwargs)
            return None

        return stub

    monkeypatch.setattr(build, "create_front_cover_image", record("front"))
    monkeypatch.setattr(build, "create_back_cover_image", record("back"))
    monkeypatch.setattr(build, "create_kdp_wrap_cover", record("wrap"))
    monkeypatch.setattr(build, "build_pdfs", record("pdfs"))
    monkeypatch.setattr(build, "build_epub", record("epub"))
    monkeypatch.setattr(build, "build_reader", record("reader"))
    monkeypatch.setattr(build.Publication, "check", lambda self, plan: None)  # spec 023 fit

    assert build.main(["--book", "moon-kite-race", "--root", str(tmp_path)]) == 0

    assert set(calls) == {"front", "back", "wrap", "pdfs", "epub", "reader"}
    for name in ["front", "back", "pdfs", "epub", "reader"]:
        _, kwargs = calls[name]
        assert kwargs["paths"].folder == folder, name
    # The wrap cover takes explicit output paths; both must be inside the book's build folder.
    _, _, wrap_png, wrap_pdf, interior_pages = calls["wrap"][0]
    assert interior_pages == 24  # 8 story pages, padded to KDP's minimum (spec 020)
    assert wrap_png.parent == folder / "build" and wrap_pdf.parent == folder / "build"
    assert wrap_pdf.name == "Moon_Kite_Race_KDP_Print_Wrap_Cover.pdf"
    assert (folder / "build").is_dir()
    assert snapshot(other) == before_other  # the other book is untouched


def test_output_stem_comes_from_the_title() -> None:
    # The stem appears in every output name; the pilot keeps its existing filenames.
    from video_pipeline.library import file_stem

    assert file_stem("Moon Kite Race") == "Moon_Kite_Race"
    assert file_stem("The Snail Mail Express") == "The_Snail_Mail_Express"


def test_build_script_has_no_pilot_paths() -> None:
    source = (ROOT / "scripts" / "build_book.py").read_text(encoding="utf-8")
    compose_source = (ROOT / "scripts" / "compose_missing_illustrations.py").read_text(
        encoding="utf-8"
    )
    for text in (source, compose_source):
        assert "/Users/swiveltech" not in text
        assert "The_Snail_Mail_Express" not in text
