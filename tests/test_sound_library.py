"""Phase 1 of spec 007: the sound library index, `list` and `check`."""

import json

import pytest

from tests.helpers import ROOT
from video_pipeline.sound_library import (
    LibraryError,
    check_library,
    load_library,
    main,
)

CC0 = "CC0 1.0 (public domain dedication): https://creativecommons.org/publicdomain/zero/1.0/"


def _entry(file, kind="ambience", seconds=10.0, **extra):
    return {
        "file": file,
        "title": "A title",
        "author": "someone",
        "url": "https://freesound.org/people/someone/sounds/1/",
        "kind": kind,
        "seconds": seconds,
        **extra,
    }


def make_lib(tmp_path, entries, license=CC0, create_files=True):
    """Write index.json (same shape as assets/sound_library/index.json) plus empty audio files."""
    index = {"license": license, "source": "test", "sounds": entries}
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    if create_files:
        for entry in entries.values():
            (tmp_path / entry["file"]).write_bytes(b"")
    return tmp_path


@pytest.fixture
def good(tmp_path):
    return make_lib(
        tmp_path,
        {
            "rain": _entry("rain.mp3", "ambience", 73.4),
            "pop": _entry("pop.mp3", "sfx", 0.4),
        },
    )


def test_loads_valid_index_and_gets_sound(good):
    lib = load_library(good)
    sound = lib.get("rain")
    assert sound.kind == "ambience"
    assert sound.seconds == 73.4
    assert sound.file == good / "rain.mp3"
    assert sound.license == CC0


def test_get_is_case_insensitive(good):
    assert load_library(good).get("RAIN").name == "rain"


def test_unknown_name_raises_library_error_with_name(good):
    with pytest.raises(LibraryError, match="thunderstorm"):
        load_library(good).get("thunderstorm")


def test_check_good_library_has_no_problems(good):
    assert check_library(good) == []


def test_check_reports_missing_file(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3")}, create_files=False)
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "rain" in problems[0]
    assert "rain.mp3" in problems[0]
    assert "missing" in problems[0]


def test_check_reports_orphan_audio_file(good):
    (good / "stray.mp3").write_bytes(b"")
    problems = check_library(good)
    assert problems == ["stray.mp3: audio file has no index entry"]


def test_check_reports_non_cc0_license(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3", license="CC BY 4.0")})
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "rain" in problems[0]
    assert "CC0" in problems[0]


def test_entry_license_overrides_index_license(tmp_path):
    make_lib(
        tmp_path,
        {"rain": _entry("rain.mp3", license=CC0)},
        license="All rights reserved",
    )
    assert check_library(tmp_path) == []


def test_invalid_json_names_index_json(tmp_path):
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(LibraryError, match=r"index\.json"):
        load_library(tmp_path)


def test_bad_kind_names_index_json_and_field(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3", kind="noise")})
    with pytest.raises(LibraryError) as exc:
        load_library(tmp_path)
    assert "index.json" in str(exc.value)
    assert "kind" in str(exc.value)


def test_missing_index_raises_library_error(tmp_path):
    with pytest.raises(LibraryError, match=r"index\.json"):
        load_library(tmp_path)


def test_check_reports_invalid_index_as_problem(tmp_path):
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "index.json" in problems[0]


def test_main_check_exits_0_when_ok(good, capsys):
    assert main(["check", "--root", str(good)]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_main_check_exits_1_with_problems(good, capsys):
    (good / "stray.mp3").write_bytes(b"")
    assert main(["check", "--root", str(good)]) == 1
    out = capsys.readouterr().out
    assert "stray.mp3" in out
    assert "1 problem(s)" in out


def test_main_list_prints_name_kind_length_author(good, capsys):
    assert main(["list", "--root", str(good)]) == 0
    out = capsys.readouterr().out
    rain = next(line for line in out.splitlines() if line.startswith("rain"))
    assert "ambience" in rain
    assert "73.4" in rain
    assert "someone" in rain
    assert "pop" in out


def test_main_list_invalid_index_exits_1(tmp_path, capsys):
    assert main(["list", "--root", str(tmp_path)]) == 1
    assert "index.json" in capsys.readouterr().err


def test_real_library_is_clean():
    assert check_library(ROOT / "assets" / "sound_library") == []
