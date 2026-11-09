"""Video commands read and write only inside their own book folder and the shared
project folders (spec 005, AC-2.4, AC-2.5)."""

from __future__ import annotations

from pathlib import Path

from tests.helpers import MINI_BOOK, PILOT_BOOK, make_root
from tests.render_helpers import FakeRunner, cli_args, make_world
from tests.voice_helpers import make_script, write_script
from video_pipeline import render_episode as re_


def snapshot(folder: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
    }


def test_rendering_one_book_leaves_the_other_untouched(tmp_path, monkeypatch):  # AC-2.4, AC-2.5
    world = make_world(tmp_path, monkeypatch, MINI_BOOK)
    other = make_root(tmp_path, PILOT_BOOK, "the-snail-mail-express")
    write_script(make_script(PILOT_BOOK), other)
    other_before = snapshot(other.folder)

    runner = FakeRunner()
    assert re_.main(cli_args(world), run=runner) == 0
    assert re_.render_check(world["folder"], tmp_path) == "up_to_date"

    assert snapshot(other.folder) == other_before
    assert world["final"].exists()
    assert not (tmp_path / "books" / "the-snail-mail-express" / "build" / "video").exists()
