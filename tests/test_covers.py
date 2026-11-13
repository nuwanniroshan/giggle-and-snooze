"""Cover settings and the titled-cover marker (spec 022, phase 0)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import ROOT
from video_pipeline import covers


def test_settings_load_from_config():
    settings = covers.load_cover_settings(ROOT / "config")
    assert settings.logo == Path("assets/brand/giggle_and_snooze_logo.png")
    assert (ROOT / settings.logo).is_file()
    assert settings.logo_name == "Giggle & Snooze"
    assert (settings.logo_width_pct, settings.safe_side_pct, settings.safe_top_bottom_pct) == (
        14,
        12,
        5,
    )
    assert settings.tries == 3
    assert tuple(settings.shapes) == covers.TITLED_STEMS
    video = settings.shapes["cover_front"]
    assert video.ratio == (4, 3) and video.accept == (1.0, None)
    print_shape = settings.shapes["cover_front_print"]
    assert print_shape.ratio == (1, 1) and print_shape.accept == (0.77, 1.3)
    assert print_shape.label == "print cover" and "1024x1024" in print_shape.frame
    assert len(settings.layout_rules) == 4


def _copy_config(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(ROOT / "config" / "covers.json", config / "covers.json")
    return config


def test_missing_or_bad_settings_name_the_field(tmp_path):
    with pytest.raises(covers.CoverError, match="covers.json"):
        covers.load_cover_settings(tmp_path)  # no file at all

    config = _copy_config(tmp_path)
    path = config / "covers.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    del data["tries"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(covers.CoverError, match="tries"):
        covers.load_cover_settings(config)

    data = json.loads((ROOT / "config" / "covers.json").read_text(encoding="utf-8"))
    data["shapes"]["cover_front"]["ratio"] = [4, 0]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(covers.CoverError, match="cover_front.*ratio"):
        covers.load_cover_settings(config)


def test_titled_covers_only_with_marker():
    assert covers.titled_covers({"metadata": {"cover_style": "titled"}})
    assert not covers.titled_covers({"metadata": {}})
    assert not covers.titled_covers({"metadata": {"cover_style": "plain"}})
    assert not covers.titled_covers({})
    assert not covers.titled_covers({"metadata": None})


def test_book_defaults_mark_new_books_titled():
    defaults = json.loads((ROOT / "config" / "book_defaults.json").read_text(encoding="utf-8"))
    assert defaults["metadata"]["cover_style"] == "titled"
