"""Phase 4 of spec 007: ambience in the render (cache keys, the bed in the master, errors).

FFmpeg is replaced by `FakeRunner`; `sound_library.decode_sound` returns a sine."""

import json

import numpy as np
import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, load_json
from tests.render_helpers import FakeRunner, cli_args, make_world
from tests.test_sound_library import _entry, make_lib
from video_pipeline import frames
from video_pipeline import render_episode as re_
from video_pipeline import sound_library as sl
from video_pipeline import synthesize_voices as sv
from video_pipeline.library import BookFolder
from video_pipeline.timeline import page_section_name

SECTIONS = ["intro", *[page_section_name(n) for n in range(1, 9)], "back-cover", "outro"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, MINI_BOOK)
    (w["assets"] / "sound_library").mkdir(parents=True)
    lib = make_lib(
        w["assets"] / "sound_library",
        {
            "rain": _entry("rain.mp3", "ambience", 30.0),
            "wind": _entry("wind.mp3", "ambience", 30.0),
        },
    )
    (lib / "rain.mp3").write_bytes(b"rain-v1")
    (lib / "wind.mp3").write_bytes(b"wind-v1")
    (lib / "extra.mp3").write_bytes(b"unrelated")
    monkeypatch.setattr(
        sl,
        "decode_sound",
        lambda sound, rate: 0.5 * np.sin(2 * np.pi * 220 * np.arange(rate * 3) / rate),
    )
    return w


def set_ambience(world, page, names):
    data = load_json(world["script_path"])
    block = data["pages"][page - 1]
    if names is None:
        block.pop("ambience", None)
    else:
        block["ambience"] = names
    world["script_path"].write_text(json.dumps(data, indent=2), encoding="utf-8")
    # the approval mark hashes the whole script file, so an edit needs approving again
    common = ["--book", world["slug"], "--root", str(world["root"])]
    assert sv.main(["--reviewed", *common]) == 0
    assert sv.main(["approve", *common]) == 0


def render(world, *extra):
    runner = FakeRunner()
    return re_.main(cli_args(world, *extra), run=runner), runner


def master(world):
    samples, _ = sf.read(world["video"] / "audio" / "master.wav", dtype="float32")
    return samples


def inputs(world):
    return re_._load_inputs(BookFolder(world["slug"], world["root"]), world["root"])


def test_no_ambience_key_has_no_ambience_and_mix_gets_none(world, monkeypatch):  # AC-1.4
    seen = {}
    real = frames.mix_master

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(re_, "mix_master", spy)
    assert render(world)[0] == 0
    assert seen.get("ambience") is None
    ins = inputs(world)
    assert ins.ambience == [] and ins.ambience_key == []
    # no library access at all when no script block has an ambience field
    (world["assets"] / "sound_library" / "index.json").unlink()
    assert re_._load_inputs(BookFolder(world["slug"], world["root"]), world["root"]).ambience == []


def test_final_key_payload_unchanged_without_ambience(world, monkeypatch):
    payloads = []
    monkeypatch.setattr(re_, "_digest", lambda payload: payloads.append(payload) or "k")
    re_._final_key(inputs(world), "pt")
    assert "ambience" not in payloads[-1]
    set_ambience(world, 3, ["rain"])
    re_._final_key(inputs(world), "pt")
    assert payloads[-1]["ambience"][0][0] == "rain"


def test_ambience_changes_master_within_peak_limit(world):  # AC-1.1
    assert render(world)[0] == 0
    plain = master(world)
    set_ambience(world, 3, ["rain"])
    assert render(world)[0] == 0
    with_bed = master(world)
    assert len(with_bed) == len(plain)
    assert not np.array_equal(with_bed, plain)
    assert np.max(np.abs(with_bed)) <= 1.0


def test_changing_only_ambience_reuses_video_sections(world, capsys):  # AC-4.1
    set_ambience(world, 3, ["rain"])
    assert render(world)[0] == 0
    set_ambience(world, 3, ["wind"])
    capsys.readouterr()
    code, runner = render(world)
    assert code == 0
    assert runner.whats("section ") == []
    assert runner.whats("transition ") == []
    assert runner.whats("mux") == ["mux"]
    assert f"0 rendered, {len(SECTIONS)} reused" in capsys.readouterr().out


def test_unknown_sound_fails_before_any_section(world, capsys):  # AC-1.5
    set_ambience(world, 3, ["thunder"])
    code, runner = render(world)
    assert code == 1
    err = capsys.readouterr().err
    assert "page 3" in err and "thunder" in err
    assert runner.calls == []
    state, message = re_.render_check_detail(
        BookFolder(world["slug"], world["root"]), world["root"]
    )
    assert state == "blocked" and "page 3" in message and "thunder" in message


def test_missing_sound_file_is_named(world, capsys):
    set_ambience(world, 3, ["rain"])
    (world["assets"] / "sound_library" / "rain.mp3").unlink()
    code, runner = render(world)
    assert code == 1 and "rain" in capsys.readouterr().err
    assert runner.calls == []


def test_missing_library_is_render_error(world, capsys):
    set_ambience(world, 3, ["rain"])
    (world["assets"] / "sound_library" / "index.json").unlink()
    assert render(world)[0] == 1
    assert "index" in capsys.readouterr().err


def test_undecodable_sound_is_named(world, monkeypatch, capsys):
    set_ambience(world, 3, ["rain"])

    def bad(sound, rate):
        raise sl.LibraryError(f"sound {sound.name!r}: cannot decode")

    monkeypatch.setattr(sl, "decode_sound", bad)
    assert render(world)[0] == 1
    assert "rain" in capsys.readouterr().err


def test_library_file_content_changes_final_key_but_unrelated_does_not(world):  # AC-4.2
    set_ambience(world, 3, ["rain"])
    lib = world["assets"] / "sound_library"
    before = re_._final_key(inputs(world), "pt")
    (lib / "extra.mp3").write_bytes(b"other")
    (lib / "wind.mp3").write_bytes(b"wind-v2")
    assert re_._final_key(inputs(world), "pt") == before
    (lib / "rain.mp3").write_bytes(b"rain-v2")
    assert re_._final_key(inputs(world), "pt") != before
