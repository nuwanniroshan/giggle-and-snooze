"""Phase 4 of spec 003: approval gate, pictures, caching and the render CLI.

FFmpeg is replaced by `FakeRunner` (except the one `ffmpeg`-marked end-to-end test). Everything is
written under tmp_path."""

import errno
import json

import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from tests.render_helpers import FakeRunner, cli_args, make_world, picture
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.episode import load_book
from video_pipeline.timeline import page_section_name

MINI_SECTIONS = ["intro", *[page_section_name(n) for n in range(1, 9)], "back-cover", "outro"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, MINI_BOOK)


def render(world, *extra, runner=None):
    runner = runner if runner is not None else FakeRunner()
    return re_.main(cli_args(world, *extra), run=runner), runner


def sections_rendered(runner):
    return [what.removeprefix("section ") for what in runner.whats("section ")]


# --- happy path ------------------------------------------------------------------------------


def test_renders_an_approved_episode(world, capsys):  # AC-1.1, AC-1.3, FR-9
    code, runner = render(world)
    out = capsys.readouterr().out
    assert code == 0
    assert world["final"].exists()
    assert sections_rendered(runner) == MINI_SECTIONS
    transitions = runner.whats("transition ")
    pairs = zip(MINI_SECTIONS, MINI_SECTIONS[1:], strict=False)
    assert transitions == [f"transition {a}__{b}" for a, b in pairs]
    assert [w for w, _ in runner.calls[-2:]] == ["join", "mux"]
    assert str(world["final"]) in out
    assert "watch it before publishing" in out.lower()
    assert f"{len(MINI_SECTIONS)} rendered, 0 reused" in out
    for name in MINI_SECTIONS:
        assert f"{name}: rendered" in out
    record = load_json(world["video"] / f"{world['slug']}.json")
    assert record["final_key"] and [s["name"] for s in record["sections"]] == MINI_SECTIONS
    assert (world["video"] / "audio" / "master.wav").exists()
    assert not list(world["video"].rglob("*.tmp*"))


def test_master_audio_is_the_full_length_at_the_voice_rate(world):  # AC-1.4
    code, _ = render(world)
    assert code == 0
    record = load_json(world["video"] / f"{world['slug']}.json")
    info = sf.info(world["video"] / "audio" / "master.wav")
    assert info.samplerate == 24000
    assert abs(info.frames / info.samplerate - record["duration"]) < 0.001


def test_summary_duration_is_minutes_and_seconds(world, capsys):  # FR-9
    render(world)
    record = load_json(world["video"] / f"{world['slug']}.json")
    seconds = round(record["duration"])
    assert f"{seconds // 60}:{seconds % 60:02d}" in capsys.readouterr().out


def test_other_book_renders_with_the_same_command(tmp_path, monkeypatch):  # AC-1.5
    world = make_world(tmp_path, monkeypatch, PILOT_BOOK)
    code, runner = render(world)
    assert code == 0 and world["final"].exists()
    assert len(runner.whats("section ")) == 20 + 3


# --- approval gate ---------------------------------------------------------------------------


def test_unapproved_audio_is_refused(tmp_path, monkeypatch, capsys):  # AC-2.1
    world = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=False)
    capsys.readouterr()
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1
    assert "synthesize_voices approve" in err
    assert runner.calls == []
    assert not world["video"].exists()


def test_script_changed_after_approval_is_refused(world, capsys):  # AC-2.2
    script = load_json(world["script_path"])
    script["title"] = script["title"] + " (edited)"
    world["script_path"].write_text(json.dumps(script), encoding="utf-8")
    code, runner = render(world)
    assert code == 1
    assert "re-run synthesis and approve again" in capsys.readouterr().err
    assert runner.calls == [] and not world["video"].exists()


def test_audio_changed_after_approval_is_refused(world, capsys):  # AC-2.2
    meta_path = world["audio"] / "preview.json"
    meta = load_json(meta_path)
    meta["preview_key"] = "0" * 32
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    code, _ = render(world)
    assert code == 1
    assert "re-run synthesis and approve again" in capsys.readouterr().err


def test_missing_audio_folder_is_named(world, capsys):  # AC-2.3
    import shutil

    shutil.rmtree(world["audio"])
    code, _ = render(world)
    err = capsys.readouterr().err
    assert code == 1 and str(world["audio"]) in err


def test_missing_track_timing_is_named(world, capsys):  # AC-2.3
    timing = world["audio"] / "tracks" / "page-03.json"
    timing.unlink()
    code, runner = render(world)
    assert code == 1
    assert str(timing) in capsys.readouterr().err
    assert runner.calls == [] and not world["final"].exists()


def test_missing_track_audio_is_named(world, capsys):  # AC-2.3
    wav = world["audio"] / "tracks" / "outro.wav"
    wav.unlink()
    code, _ = render(world)
    assert code == 1 and str(wav) in capsys.readouterr().err


def test_approval_problems_lists_each_problem(world):
    paths = sv.AudioPaths(world["audio"])
    status = load_json(paths.status)
    assert sv.approval_problems(paths, world["script_path"], status["settings_sha256"]) == []
    problems = sv.approval_problems(paths, world["script_path"], "different")
    assert len(problems) == 1 and "re-run synthesis and approve again" in problems[0]
    paths.status.unlink()
    problems = sv.approval_problems(paths, world["script_path"], status["settings_sha256"])
    assert problems and "run synthesis first" in problems[0]


# --- pictures --------------------------------------------------------------------------------


def test_missing_illustration_names_page_and_file(world, capsys):  # Error Scenarios
    missing = world["assets"] / "illustrations" / "page_04.png"
    missing.unlink()
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1 and "page 4" in err and str(missing) in err
    assert runner.calls == []


def test_missing_cover_is_named(world, capsys):
    cover = world["assets"] / "illustrations" / "cover_back.png"
    cover.unlink()
    code, _ = render(world)
    assert code == 1 and str(cover) in capsys.readouterr().err


def test_unreadable_nook_is_named_not_skipped(world, capsys):  # Error Scenarios
    nook = world["episodes"] / world["slug"] / "intro_nook.png"
    nook.write_bytes(b"not a png")
    code, _ = render(world)
    err = capsys.readouterr().err
    assert code == 1 and str(nook) in err
    assert not world["final"].exists()


def test_nook_precedence(world):  # AC-6.1, AC-6.3
    book = load_book(world["book"])
    slug, episodes, assets = world["slug"], world["episodes"], world["assets"]

    resolved = re_.resolve_pictures(book, slug, episodes, assets)
    assert resolved.by_section["intro"] == "cover_front"
    assert resolved.by_section["outro"] == "cover_back"
    assert resolved.by_section["back-cover"] == "cover_back"
    assert "intro_nook" not in resolved.files

    shared_intro = picture(assets / "nook" / "intro_nook.png")
    shared_outro = picture(assets / "nook" / "outro_nook.png")
    resolved = re_.resolve_pictures(book, slug, episodes, assets)
    assert resolved.by_section["intro"] == "intro_nook"
    assert resolved.files["intro_nook"] == shared_intro
    assert resolved.files["outro_nook"] == shared_outro
    assert resolved.by_section["back-cover"] == "cover_back"

    own_intro = picture(episodes / slug / "intro_nook.png")
    resolved = re_.resolve_pictures(book, slug, episodes, assets)
    assert resolved.files["intro_nook"] == own_intro
    assert resolved.files["outro_nook"] == shared_outro
    assert resolved.files["page_03"] == assets / "illustrations" / "page_03.png"


def test_per_book_nook_is_used_in_the_render(world):  # AC-6.1
    own = picture(world["episodes"] / world["slug"] / "outro_nook.png")
    code, _ = render(world)
    assert code == 0
    record = load_json(world["video"] / f"{world['slug']}.json")
    outro = next(s for s in record["sections"] if s["name"] == "outro")
    assert outro["picture"] == str(own)


# --- caching ---------------------------------------------------------------------------------


def test_unchanged_rerun_is_up_to_date(world, capsys):  # AC-7.1
    assert render(world)[0] == 0
    capsys.readouterr()
    code, runner = render(world)
    assert code == 0
    assert runner.calls == []
    out = capsys.readouterr().out
    assert "up to date" in out and str(world["final"]) in out


def test_changed_illustration_rerenders_only_that_page(world, capsys):  # AC-7.2
    assert render(world)[0] == 0
    picture(world["assets"] / "illustrations" / "page_03.png", (1, 2, 3))
    capsys.readouterr()
    code, runner = render(world)
    assert code == 0
    assert sections_rendered(runner) == ["page-03"]
    assert runner.whats("transition ") == [
        "transition page-02__page-03",
        "transition page-03__page-04",
    ]
    assert runner.whats("join") == ["join"] and runner.whats("mux") == ["mux"]
    out = capsys.readouterr().out
    assert f"1 rendered, {len(MINI_SECTIONS) - 1} reused" in out
    assert "page-02: reused" in out


def test_force_rerenders_every_section(world):  # AC-7.3
    assert render(world)[0] == 0
    code, runner = render(world, "--force")
    assert code == 0 and sections_rendered(runner) == MINI_SECTIONS


def test_failing_section_leaves_nothing_reusable(world, capsys):  # AC-7.4
    code, runner = render(world, runner=FakeRunner(fail_on="section page-05"))
    err = capsys.readouterr().err
    assert code == 1 and "page-05" in err
    assert not world["final"].exists()
    assert not (world["video"] / "sections" / "page-05.json").exists()
    assert (world["video"] / "sections" / "page-04.json").exists()
    assert not list(world["video"].rglob("*.tmp*"))

    code, runner = render(world)
    assert code == 0 and world["final"].exists()
    assert sections_rendered(runner) == ["page-05", "page-06", "page-07", "page-08"] + [
        "back-cover",
        "outro",
    ]


def test_failing_mux_leaves_no_final_video(world, capsys):  # AC-7.4
    code, _ = render(world, runner=FakeRunner(fail_on="mux"))
    assert code == 1 and "mux" in capsys.readouterr().err
    assert not world["final"].exists()
    assert not (world["video"] / f"{world['slug']}.json").exists()
    assert not list(world["video"].rglob("*.tmp*"))


def test_disk_full_names_the_output_folder(world, capsys):  # Error Scenarios
    def full(cmd, what):
        raise OSError(errno.ENOSPC, "No space left on device")

    code, _ = render(world, runner=full)
    err = capsys.readouterr().err
    assert code == 1
    assert f"not enough disk space to write video in {world['video']}" in err


def test_missing_render_config_is_an_error(world, capsys):
    (world["config"] / "render.json").unlink()
    code, _ = render(world)
    assert code == 1 and "render.json" in capsys.readouterr().err


# --- real FFmpeg -----------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_real_render_of_the_mini_book(world, capsys):  # AC-1.1, AC-1.2, AC-1.4
    assert re_.main(cli_args(world)) == 0
    info = re_.probe(world["final"])
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    record = load_json(world["video"] / f"{world['slug']}.json")
    assert (video["width"], video["height"], video["pix_fmt"]) == (320, 180, "yuv420p")
    assert int(video["nb_frames"]) == round(record["duration"] * 10)
    assert abs(float(video["duration"]) - record["duration"]) < 0.1
    assert abs(float(audio["duration"]) - float(video["duration"])) < 0.1
    capsys.readouterr()
    assert re_.main(cli_args(world)) == 0
    assert "up to date" in capsys.readouterr().out
