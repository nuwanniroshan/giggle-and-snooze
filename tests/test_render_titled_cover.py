"""Render shows a titled (painted) cover as is; older blueprint books still get the lettered
cover (spec 022 AC-4.1-AC-4.3). FFmpeg is never run: these tests read `_load_inputs`."""

import json

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import MINI_BOOK, load_json
from tests.render_helpers import make_world, picture
from video_pipeline import render_episode as re_
from video_pipeline.episode_tools import write_atomic


def _world(tmp_path, monkeypatch, titled: bool) -> dict:
    book = load_json(MINI_BOOK)
    if titled:
        book["metadata"]["cover_style"] = "titled"
    source = tmp_path / "source" / "book.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(book, indent=2), encoding="utf-8")
    world = make_world(tmp_path / "root", monkeypatch, source)
    write_atomic(world["folder"].blueprint_path, valid_blueprint())
    return world


@pytest.fixture
def composed(monkeypatch):
    calls: list = []

    def fake_compose(cover, title, subtitle, palette, logo, cfg, out):
        calls.append(cover)
        picture(out, (10, 10, 10))

    monkeypatch.setattr(re_, "compose_cover", fake_compose)
    return calls


def _inputs(world):
    return re_._load_inputs(world["folder"], world["root"])


def test_titled_book_render_skips_lettered_cover(tmp_path, monkeypatch, composed):  # AC-4.1
    world = _world(tmp_path, monkeypatch, titled=True)
    inputs = _inputs(world)
    assert composed == []
    assert inputs.pictures.files["cover_front"] == world["folder"].illustrations / "cover_front.png"
    assert inputs.pictures.by_section["intro"] == "cover_front"
    assert not (world["video"] / "frames" / "cover_front_titled.png").exists()


def test_old_book_render_still_letters_cover(tmp_path, monkeypatch, composed):
    world = _world(tmp_path, monkeypatch, titled=False)
    inputs = _inputs(world)
    assert composed == [world["folder"].illustrations / "cover_front.png"]
    assert inputs.pictures.files["cover_front"] == (
        world["video"] / "frames" / "cover_front_titled.png"
    )


def test_old_book_keys_are_unchanged_by_spec_022(tmp_path, monkeypatch, composed):  # AC-4.2
    world = _world(tmp_path, monkeypatch, titled=False)
    now = _inputs(world)
    monkeypatch.setattr(re_, "titled_covers", lambda book: False)  # as if the marker check
    before = _inputs(world)  # did not exist
    assert now.keys == before.keys and now.still_keys == before.still_keys
    assert now.trans_keys == before.trans_keys
    assert re_._final_key(now, "turn") == re_._final_key(before, "turn")
    titled = world["video"] / "frames" / "cover_front_titled.png"
    assert now.pictures.by_section["intro"] == "cover_front"
    assert now.pictures.files["cover_front"] == titled


def test_repainted_titled_cover_rebuilds_intro_only(tmp_path, monkeypatch, composed):  # AC-4.3
    world = _world(tmp_path, monkeypatch, titled=True)
    before = _inputs(world)
    picture(world["folder"].illustrations / "cover_front.png", (5, 90, 200))
    after = _inputs(world)
    names = [s.name for s in before.sections]
    changed = [n for n, a, b in zip(names, before.keys, after.keys, strict=True) if a != b]
    assert changed == ["intro"]
    assert re_.render_check(world["folder"], world["root"]) == "not_rendered"
