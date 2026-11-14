"""Phase 4 of spec 003: approval gate, pictures, caching and the render CLI.

FFmpeg is replaced by `FakeRunner` (except the one `ffmpeg`-marked end-to-end test). Everything is
written under tmp_path."""

import errno
import json

import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from tests.render_helpers import FakeFrameRunner, FakeRunner, cli_args, make_world, picture
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.episode import load_book
from video_pipeline.timeline import page_section_name

MINI_SECTIONS = ["intro", *[page_section_name(n) for n in range(1, 9)], "back-cover", "outro"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, MINI_BOOK)


def render(world, *extra, runner=None, frame_runner=None):
    runner = runner if runner is not None else FakeRunner()
    frame_runner = frame_runner if frame_runner is not None else FakeFrameRunner()
    return re_.main(cli_args(world, *extra), run=runner, frame_run=frame_runner), runner


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


def test_track_audio_edited_after_approval_is_refused(world, capsys):  # AC-2.2, review r1 #1
    wav = world["audio"] / "tracks" / "page-03.wav"
    samples, rate = sf.read(wav, dtype="float32")
    sf.write(wav, samples * 0.5, rate)
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1
    assert "audio track page-03 changed since approval; re-run synthesis and approve again" in err
    assert runner.calls == [] and not world["video"].exists()


def test_track_timing_text_edited_after_approval_is_refused(world, capsys):  # review r1 #1
    timing = world["audio"] / "tracks" / "page-03.json"
    data = load_json(timing)
    data["lines"][0]["text"] = "Unreviewed words"
    timing.write_text(json.dumps(data), encoding="utf-8")
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1
    assert "audio track page-03 changed since approval; re-run synthesis and approve again" in err
    assert runner.calls == []


def test_approval_without_track_hashes_is_refused(world, capsys):  # review r1 #1
    status_path = world["audio"] / "status.json"
    status = load_json(status_path)
    del status["track_sha256"]
    status_path.write_text(json.dumps(status), encoding="utf-8")
    code, _ = render(world)
    err = capsys.readouterr().err
    assert code == 1
    assert "approval predates track checks; re-run synthesis and approve again" in err


@pytest.mark.parametrize("name", ["status.json", "preview.json"])
def test_corrupt_audio_record_is_readable_error(world, capsys, name):  # review r1 #3
    path = world["audio"] / name
    path.write_text("{not json", encoding="utf-8")
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1
    assert f"error: {path} is unreadable; re-run synthesis" in err
    assert runner.calls == []


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


@pytest.mark.parametrize("value", [False, 1.5])
def test_page_animation_override_keeps_the_approval(world, value):  # spec 010 AC-3.6
    paths = sv.AudioPaths(world["audio"])
    settings = load_json(paths.status)["settings_sha256"]
    script = load_json(world["script_path"])
    script["pages"][0]["animation"] = value
    script["pages"][-1]["animation"] = False
    world["script_path"].write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    assert sv.approval_problems(paths, world["script_path"], settings) == []
    script["pages"][0]["lines"][0]["text"] = "A changed narration line."
    world["script_path"].write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    assert sv.approval_problems(paths, world["script_path"], settings) != []


# --- pictures --------------------------------------------------------------------------------


def test_missing_illustration_names_page_and_file(world, capsys):  # Error Scenarios
    missing = world["folder"].illustrations / "page_04.png"
    missing.unlink()
    code, runner = render(world)
    err = capsys.readouterr().err
    assert code == 1 and "page 4" in err and str(missing) in err
    assert runner.calls == []


def test_missing_cover_is_named(world, capsys):
    cover = world["folder"].illustrations / "cover_back.png"
    cover.unlink()
    code, _ = render(world)
    assert code == 1 and str(cover) in capsys.readouterr().err


def test_unreadable_nook_is_named_not_skipped(world, capsys):  # Error Scenarios
    nook = world["folder"].nooks / "intro_nook.png"
    nook.parent.mkdir(parents=True, exist_ok=True)
    nook.write_bytes(b"not a png")
    code, _ = render(world)
    err = capsys.readouterr().err
    assert code == 1 and str(nook) in err
    assert not world["final"].exists()


def test_nook_precedence(world):  # AC-6.1, AC-6.3
    book = load_book(world["book"])
    folder, assets = world["folder"], world["assets"]

    resolved = re_.resolve_pictures(book, folder, assets / "nook")
    assert resolved.by_section["intro"] == "cover_front"
    assert resolved.by_section["outro"] == "cover_back"
    assert resolved.by_section["back-cover"] == "cover_back"
    assert "intro_nook" not in resolved.files

    shared_intro = picture(assets / "nook" / "intro_nook.png")
    shared_outro = picture(assets / "nook" / "outro_nook.png")
    resolved = re_.resolve_pictures(book, folder, assets / "nook")
    assert resolved.by_section["intro"] == "intro_nook"
    assert resolved.files["intro_nook"] == shared_intro
    assert resolved.files["outro_nook"] == shared_outro
    assert resolved.by_section["back-cover"] == "cover_back"

    own_intro = picture(folder.nooks / "intro_nook.png")
    resolved = re_.resolve_pictures(book, folder, assets / "nook")
    assert resolved.files["intro_nook"] == own_intro
    assert resolved.files["outro_nook"] == shared_outro
    assert resolved.files["page_03"] == folder.illustrations / "page_03.png"


def test_per_book_nook_is_used_in_the_render(world):  # AC-6.1
    own = picture(world["folder"].nooks / "outro_nook.png")
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
    picture(world["folder"].illustrations / "page_03.png", (1, 2, 3))
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


def test_render_version_bump_rerenders_everything(world, capsys, monkeypatch):  # review r1 #6
    from video_pipeline import timeline

    assert render(world)[0] == 0
    monkeypatch.setattr(timeline, "RENDER_VERSION", timeline.RENDER_VERSION + 1)
    capsys.readouterr()
    code, runner = render(world)
    assert code == 0 and "up to date" not in capsys.readouterr().out
    assert sections_rendered(runner) == MINI_SECTIONS
    assert len(runner.whats("transition ")) == len(MINI_SECTIONS) - 1


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


# --- page eligibility and animated keys (spec 010 Phase 3) ------------------------------------


def _animate(world, n: int = 1, **layer):
    from tests import animation_helpers as ah

    ah.write_plan(world["folder"], n, layers=[ah.layer(**layer)])
    ah.cut(world["folder"], pages=[n])


def _inputs(world):
    return re_._load_inputs(world["folder"], world["root"])


def _named_keys(inputs) -> tuple[dict, dict]:
    names = [s.name for s in inputs.sections]
    pairs = [f"{a}__{b}" for a, b in zip(names, names[1:], strict=False)]
    return dict(zip(names, inputs.keys, strict=True)), dict(
        zip(pairs, inputs.trans_keys, strict=True)
    )


def _still_keys(inputs) -> list[str]:
    from video_pipeline.timeline import section_key

    sha = {pid: sv.sha256_file(p) for pid, p in inputs.pictures.files.items()}
    return [section_key(s, sha[s.picture], inputs.cfg) for s in inputs.sections]


def _edit_json(path, change) -> None:
    data = load_json(path)
    change(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def test_book_without_animation_keys_are_the_still_keys(world, capsys):  # AC-4.2, NFR-5
    inputs = _inputs(world)
    assert inputs.keys == _still_keys(inputs)
    assert not any(el.animated for el in inputs.eligibility.values())
    assert set(inputs.eligibility) == {page_section_name(n) for n in range(1, 9)}
    assert render(world)[0] == 0
    assert "still" not in capsys.readouterr().out


def test_animated_page_gets_its_own_key(world):  # FR-13
    _animate(world)
    inputs = _inputs(world)
    still = _still_keys(inputs)
    assert inputs.eligibility["page-01"].animated
    assert inputs.keys[1] != still[1]
    assert inputs.keys[:1] + inputs.keys[2:] == still[:1] + still[2:]


def test_override_false_keeps_the_still_key(world):  # AC-3.4
    _animate(world)
    _edit_json(world["script_path"], lambda s: s["pages"][0].update(animation=False))
    inputs = _inputs(world)
    assert inputs.keys == _still_keys(inputs)
    assert inputs.eligibility["page-01"].reason == "animation false in the script"


def test_motion_edit_changes_only_that_page(world):  # AC-5.2
    _animate(world)
    keys, trans = _named_keys(_inputs(world))
    _animate(world, motions=[{"type": "bob", "amplitude": 1.0, "period_s": 4.0}])
    new_keys, new_trans = _named_keys(_inputs(world))
    assert [n for n in keys if keys[n] != new_keys[n]] == ["page-01"]
    assert [n for n in trans if trans[n] != new_trans[n]] == ["intro__page-01", "page-01__page-02"]


def test_override_change_changes_only_that_page(world):  # AC-5.2
    _animate(world)
    keys, _ = _named_keys(_inputs(world))
    _edit_json(world["script_path"], lambda s: s["pages"][0].update(animation=1.5))
    new_keys, _ = _named_keys(_inputs(world))
    assert [n for n in keys if keys[n] != new_keys[n]] == ["page-01"]


def test_animation_settings_change_only_animated_keys(world):  # AC-5.3
    _animate(world)
    keys, _ = _named_keys(_inputs(world))
    _edit_json(world["config"] / "render.json", lambda c: c["animation"].update(ease_in_s=2.5))
    new_keys, _ = _named_keys(_inputs(world))
    assert [n for n in keys if keys[n] != new_keys[n]] == ["page-01"]


def test_plan_edit_on_animated_page_is_out_of_date(world):  # AC-5.5, FR-14
    _animate(world)
    assert render(world)[0] == 0
    assert re_.render_check_detail(world["folder"], world["root"]) == ("up_to_date", "")
    _animate(world, motions=[{"type": "bob", "amplitude": 1.0, "period_s": 4.0}])
    assert re_.render_check_detail(world["folder"], world["root"])[0] == "out_of_date"


def test_layer_png_edit_with_the_same_manifest_is_out_of_date(world):  # AC-5.5, FR-14
    from PIL import Image

    from video_pipeline.cut_layers import layers_dir

    _animate(world)
    assert render(world)[0] == 0
    assert re_.render_check(world["folder"], world["root"]) == "up_to_date"
    folder = layers_dir(world["folder"], 1)
    manifest = load_json(folder / "layers.json")
    name = next(e["file"] for e in manifest["layers"] if not e.get("empty"))
    with Image.open(folder / name) as im:
        changed = im.convert("RGBA")
    changed.putpixel((0, 0), (1, 2, 3, 255))
    changed.save(folder / name)
    assert load_json(folder / "layers.json") == manifest
    assert re_.render_check(world["folder"], world["root"]) == "out_of_date"


def test_override_outside_the_config_range_names_the_page(world, capsys):  # FR-9
    _edit_json(world["config"] / "render.json", lambda c: c["animation"].update(override_max=1.0))
    _edit_json(world["script_path"], lambda s: s["pages"][2].update(animation=1.5))
    with pytest.raises(re_.RenderError, match="page 3"):
        _inputs(world)
    code, _ = render(world)
    assert code == 1 and "page 3" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["high", [1], True, None])
def test_invalid_override_refuses_the_render(world, capsys, value):  # Error Scenarios, AC-3.5
    _animate(world)
    _edit_json(world["script_path"], lambda s: s["pages"][0].update(animation=value))
    with pytest.raises(re_.RenderError, match="page 1: animation must be false or a number"):
        _inputs(world)
    code, runner = render(world)
    assert code == 1 and "page 1: animation" in capsys.readouterr().err
    assert runner.calls == []
    state, why = re_.render_check_detail(world["folder"], world["root"])
    assert state == "blocked" and "page 1: animation" in why
    with pytest.raises(re_.RenderError, match="page 1: animation must be false or a number"):
        re_.render_preview(
            world["folder"], world["root"], 4, run=FakeRunner(), frame_run=FakeFrameRunner()
        )


@pytest.mark.parametrize("block", ["intro", "outro"])
def test_animation_on_a_bookend_refuses_the_render(world, capsys, block):  # AC-3.5
    _edit_json(world["script_path"], lambda s: s[block].update(animation=1.0))
    match = f"{block}: animation is allowed only on story pages"
    with pytest.raises(re_.RenderError, match=match):
        _inputs(world)
    code, runner = render(world)
    assert code == 1 and match in capsys.readouterr().err and runner.calls == []
    with pytest.raises(re_.RenderError, match=match):
        re_.render_preview(
            world["folder"], world["root"], 4, run=FakeRunner(), frame_run=FakeFrameRunner()
        )


def test_config_range_applies_only_when_animation_is_on(world):  # FR-9
    _edit_json(
        world["config"] / "render.json",
        lambda c: c["animation"].update(override_max=1.0, enabled=False),
    )
    _edit_json(world["script_path"], lambda s: s["pages"][2].update(animation=1.5))
    _inputs(world)  # the narrower config range is not checked while animation is off
    _edit_json(world["script_path"], lambda s: s["pages"][2].update(animation=3.0))
    with pytest.raises(re_.RenderError, match="page 3: animation must be false or a number"):
        _inputs(world)  # the script contract's own range always is


def test_still_pages_are_logged_once_with_the_reason(world, capsys):  # AC-4.1
    _animate(world)
    assert render(world)[0] == 0
    out = capsys.readouterr().out
    slug = world["slug"]
    line = f"page 02: still – no plan – run python -m video_pipeline.motion_plan --book {slug}"
    assert out.count(line) == 1
    assert "page 01: still" not in out


def test_animation_off_logs_nothing_and_keeps_still_keys(world, capsys):  # AC-2.4, NFR-5
    _animate(world)
    _edit_json(world["config"] / "render.json", lambda c: c["animation"].update(enabled=False))
    inputs = _inputs(world)
    assert inputs.keys == _still_keys(inputs)
    assert render(world)[0] == 0
    assert "still" not in capsys.readouterr().out


# --- animated sections (spec 010 Phase 4) -----------------------------------------------------


def _render_both(world, *extra):
    runner, frames = FakeRunner(), FakeFrameRunner()
    code = re_.main(cli_args(world, *extra), run=runner, frame_run=frames)
    return code, runner, frames


def _section_meta(world, name):
    return load_json(world["video"] / "sections" / f"{name}.json")


def test_animated_page_pipes_exactly_its_frames(world, capsys):  # AC-1.6, FR-12
    _animate(world)
    inputs = _inputs(world)
    page = next(s for s in inputs.sections if s.name == "page-01")
    code, runner, frames = _render_both(world)
    assert code == 0, capsys.readouterr().err
    assert frames.whats() == ["section page-01"]
    what, cmd, count, sizes = frames.calls[0]
    assert count == page.frames and sizes == {320 * 180 * 3}
    assert cmd[cmd.index("-i") + 1] == "pipe:0"
    assert cmd[cmd.index("-frames:v") + 1] == str(page.frames)
    assert sections_rendered(runner) == [n for n in MINI_SECTIONS if n != "page-01"]
    # transitions into and out of the animated page still grab its real frames (AC-2.2)
    clip = str(world["video"] / "sections" / "page-01.mp4")
    grabs = [w for w, c in runner.calls if w.startswith("grab ") and clip in c]
    assert grabs == ["grab first frame of page-01", "grab last frame of page-01"]
    assert runner.whats("transition ") == [
        f"transition {a}__{b}" for a, b in zip(MINI_SECTIONS, MINI_SECTIONS[1:], strict=False)
    ]
    assert _section_meta(world, "page-01")["key"] == inputs.keys[1]
    assert "page-01: rendered" in capsys.readouterr().out
    assert not list(world["video"].rglob("*.tmp*"))


def test_master_audio_is_identical_with_animation_on_and_off(world):  # AC-2.3
    _animate(world)
    assert _render_both(world)[0] == 0
    on = (world["video"] / "audio" / "master.wav").read_bytes()
    _edit_json(world["config"] / "render.json", lambda c: c["animation"].update(enabled=False))
    code, _runner, frames = _render_both(world)
    assert code == 0 and frames.calls == []
    assert (world["video"] / "audio" / "master.wav").read_bytes() == on


def test_animation_off_runs_exactly_todays_commands(world, tmp_path):  # AC-2.4, NFR-5
    import shutil

    code, today, _ = _render_both(world)
    assert code == 0
    metas = {p.name: p.read_bytes() for p in (world["video"] / "sections").glob("*.json")}
    shutil.rmtree(world["video"])
    _animate(world)
    _edit_json(world["config"] / "render.json", lambda c: c["animation"].update(enabled=False))
    code, off, frames = _render_both(world)
    assert code == 0 and frames.calls == []
    assert off.calls == today.calls
    assert {p.name: p.read_bytes() for p in (world["video"] / "sections").glob("*.json")} == metas


def test_second_animated_run_renders_nothing(world, capsys):  # AC-5.1
    _animate(world)
    assert _render_both(world)[0] == 0
    capsys.readouterr()
    code, runner, frames = _render_both(world)
    assert code == 0 and runner.calls == [] and frames.calls == []
    assert "up to date" in capsys.readouterr().out


def test_unloadable_layer_falls_back_to_still_and_logs(world, capsys):  # FR-11, plan Phase 4
    from video_pipeline.cut_layers import layers_dir

    _animate(world)
    folder = layers_dir(world["folder"], 1)
    manifest = load_json(folder / "layers.json")
    layer = next(e for e in manifest["layers"] if not e.get("empty"))
    (folder / layer["file"]).write_bytes(b"not a png")
    inputs = _inputs(world)
    assert inputs.eligibility["page-01"].animated  # the file is only read at render time
    code, runner, frames = _render_both(world)
    out = capsys.readouterr().out
    assert code == 0 and frames.calls == []
    assert "section page-01" in runner.whats("section ")
    assert out.count("page 01: still – ") == 1 and "can't read the layer" in out
    fix = f"re-run python -m video_pipeline.cut_layers --book {world['slug']} --page 1 --force"
    assert fix in out  # the fix command (AC-4.1)
    # written under the still key, so the next run tries the animation again
    assert _section_meta(world, "page-01")["key"] == _still_keys(inputs)[1]
    assert re_.render_check(world["folder"], world["root"]) == "out_of_date"
    code, runner, frames = _render_both(world)
    assert code == 0 and sections_rendered(runner) == ["page-01"]
    assert "page 01: still – " in capsys.readouterr().out


def test_fixed_layer_after_a_fallback_rerenders_both_transitions(world, capsys):  # AC-2.2, AC-5.2
    from video_pipeline.cut_layers import layers_dir

    _animate(world)
    folder = layers_dir(world["folder"], 1)
    manifest = load_json(folder / "layers.json")
    layer = next(e for e in manifest["layers"] if not e.get("empty"))
    good = (folder / layer["file"]).read_bytes()
    (folder / layer["file"]).write_bytes(b"not a png")
    assert _render_both(world)[0] == 0
    record = load_json(world["video"] / f"{world['slug']}.json")
    still = _still_keys(_inputs(world))
    assert [s["key"] for s in record["sections"]][1] == still[1]  # what the clip was made under
    assert record["final_key"] is None
    (folder / layer["file"]).write_bytes(good)  # fixed; layers.json is unchanged
    code, runner, frames = _render_both(world)
    assert code == 0
    assert frames.whats() == ["section page-01"]
    assert runner.whats("transition ") == [
        "transition intro__page-01",
        "transition page-01__page-02",
    ]
    assert re_.render_check(world["folder"], world["root"]) == "up_to_date"


def test_transitions_follow_the_key_a_fallback_was_recorded_under(world, monkeypatch):  # AC-2.2
    from video_pipeline.animate import AnimationError

    _animate(world)
    real = re_.load_page_animation

    def unreadable(*args, **kwargs):
        raise AnimationError("can't read the layer (a passing glitch)")

    monkeypatch.setattr(re_, "load_page_animation", unreadable)
    assert _render_both(world)[0] == 0
    monkeypatch.setattr(re_, "load_page_animation", real)  # same inputs, now readable
    code, runner, frames = _render_both(world)
    assert code == 0 and frames.whats() == ["section page-01"]
    assert runner.whats("transition ") == [
        "transition intro__page-01",
        "transition page-01__page-02",
    ]


def test_main_disk_full_outside_render_names_the_video_folder(world, capsys, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(re_, "render_video", full)
    code, _ = render(world)
    assert code == 1
    assert f"not enough disk space to write video in {world['video']}" in capsys.readouterr().err


# --- page preview (spec 010 Phase 5: FR-15, AC-6.1, AC-6.3) -----------------------------------


def _preview(world, page="4", runner=None, frames=None):
    runner = runner if runner is not None else FakeRunner()
    frames = frames if frames is not None else FakeFrameRunner()
    code = re_.main(cli_args(world, "--preview-page", page), run=runner, frame_run=frames)
    return code, runner, frames


def test_preview_writes_the_page_video(world, capsys):  # AC-6.1, FR-15
    code, runner, frames = _preview(world)
    out = capsys.readouterr().out
    assert code == 0
    preview = world["video"] / "preview" / "page_04.mp4"
    assert preview.exists()
    assert runner.whats() == ["section page-04", "mux"]
    mux = runner.calls[-1][1]
    assert mux[mux.index("-i") + 1] == str(world["video"] / "sections" / "page-04.mp4")
    assert str(preview) in out
    slug = world["slug"]
    assert f"still – no plan – run python -m video_pipeline.motion_plan --book {slug}" in out
    assert not world["final"].exists()
    assert not list(world["video"].rglob("*.tmp*"))
    assert not list((world["video"] / "preview").glob("*.wav"))


def test_preview_works_without_approval(tmp_path, monkeypatch, capsys):  # AC-6.1
    world = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=False)
    code, _runner, _frames = _preview(world)
    assert code == 0, capsys.readouterr().err
    assert (world["video"] / "preview" / "page_04.mp4").exists()


def test_preview_without_the_page_track_names_the_voices_command(world, capsys):  # AC-6.3
    (world["audio"] / "tracks" / "page-04.wav").unlink()
    code, runner, frames = _preview(world)
    err = capsys.readouterr().err
    assert code == 1
    assert "no voice track for page 4" in err
    assert f"python -m video_pipeline.synthesize_voices --book {world['slug']} --reviewed" in err
    assert runner.calls == [] and frames.calls == []


def test_preview_of_a_page_that_is_not_a_story_page(world, capsys):
    code, runner, _frames = _preview(world, page="99")
    err = capsys.readouterr().err
    assert code == 1 and "page 99" in err and "not a story page" in err
    assert runner.calls == []


def test_preview_audio_spans_the_section(world):  # the voice sits after the lead-in
    seen = {}

    class Grab(FakeRunner):
        def __call__(self, cmd, what):
            if what == "mux":
                wav = cmd[cmd.index("-i", cmd.index("-i") + 1) + 1]
                seen["info"] = sf.info(wav)
            super().__call__(cmd, what)

    assert _preview(world, runner=Grab())[0] == 0
    inputs = _inputs(world)
    page = next(s for s in inputs.sections if s.name == "page-04")
    info = seen["info"]
    assert abs(info.frames / info.samplerate - page.frames / inputs.cfg.fps) < 0.001


def test_full_render_reuses_the_previewed_section(world, capsys):  # FR-15
    assert _preview(world)[0] == 0
    meta = _section_meta(world, "page-04")
    capsys.readouterr()
    code, runner = render(world)
    out = capsys.readouterr().out
    assert code == 0
    assert "page-04" not in sections_rendered(runner)
    assert "page-04: reused" in out
    assert meta["key"] == _inputs(world).keys[MINI_SECTIONS.index("page-04")]


def test_preview_of_an_animated_page(world, capsys):  # AC-6.1
    _animate(world, 4)
    code, runner, frames = _preview(world)
    out = capsys.readouterr().out
    assert code == 0
    assert frames.whats() == ["section page-04"] and runner.whats() == ["mux"]
    assert "animated" in out and "still" not in out
    assert _section_meta(world, "page-04")["key"] == _inputs(world).keys[4]


def test_render_preview_result(world):
    result = re_.render_preview(
        world["folder"], world["root"], 4, run=FakeRunner(), frame_run=FakeFrameRunner()
    )
    assert result.path == world["video"] / "preview" / "page_04.mp4"
    assert result.animated is False and result.reason.startswith("no plan")


# --- spec 025: grounded camera and caption band wiring --------------------------------------


def _qa_page(world) -> int:
    script = load_json(world["script_path"])
    return next(
        int(p["page_number"])
        for p in script["pages"]
        if p["has_qa"] and p["ffmpeg_camera"].get("qa_zoom_target")
    )


def _write_camera(world, page: int, **extra) -> None:
    from video_pipeline import camera_ground
    from video_pipeline.file_hash import sha256_file

    folder = world["folder"]
    pic = folder.illustrations / f"page_{page:02d}.png"
    data = {
        "picture": {"sha256": sha256_file(pic), "width": 64, "height": 64},
        "band": "top",
        "status": "ok",
        "subject": {"name": "car", "source": "layer", "box": [1, 1, 9, 9]},
        "target": {"x": 0.1, "y": 0.2, "zoom": 1.5},
        **extra,
    }
    path = camera_ground.camera_path(folder, page)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _page_sections(world):
    inputs = re_._load_inputs(world["folder"], world["root"])
    return {s.name: s for s in inputs.sections if s.kind == "page"}, inputs


def test_current_camera_file_sets_target_and_band(world):  # AC-3.1
    page = _qa_page(world)
    name = page_section_name(page)
    before, _ = _page_sections(world)
    _write_camera(world, page)
    after, inputs = _page_sections(world)
    assert before[name].caption_band == "bottom"
    assert after[name].caption_band == "top"
    assert after[name].camera.qa_target != before[name].camera.qa_target
    assert after[name].camera.qa_target[1:] == pytest.approx((0.1, 0.2), abs=0.2)
    others = [n for n in after if n != name]
    assert all(after[n] == before[n] for n in others)  # AC-3.5: other pages unchanged
    assert f"page {page:02d}" not in dict(inputs.still_notes)


def test_invalid_camera_file_is_ignored_with_a_note(world):  # AC-4.4
    page = _qa_page(world)
    name = page_section_name(page)
    before, _ = _page_sections(world)
    _write_camera(world, page, subject={"name": "car", "box": [9, 9, 1, 1]})
    after, inputs = _page_sections(world)
    assert after[name] == before[name]
    notes = dict(inputs.still_notes)
    assert "invalid camera file" in notes[f"page {page:02d}"]
    assert "subject.box" in notes[f"page {page:02d}"]


def test_failed_camera_file_keeps_guess_and_bottom_band(world):  # AC-3.2
    page = _qa_page(world)
    name = page_section_name(page)
    before, _ = _page_sections(world)
    _write_camera(world, page, status="failed", reason="empty mask", target=None, subject=None)
    after, inputs = _page_sections(world)
    assert after[name] == before[name]
    assert "grounding failed" in dict(inputs.still_notes)[f"page {page:02d}"]


def test_every_caption_card_uses_the_section_band(world, monkeypatch, tmp_path):  # AC-2.3
    page = _qa_page(world)
    _write_camera(world, page)
    sections, inputs = _page_sections(world)
    section = sections[page_section_name(page)]
    assert section.caption_band == "top" and len(section.captions) >= 1
    bands: list[str] = []
    monkeypatch.setattr(
        re_, "render_caption", lambda text, cfg, path, band="bottom": bands.append(band)
    )
    paths = re_._render_captions(section, tmp_path, inputs.cfg)
    assert bands == ["top"] * len(section.captions) == ["top"] * len(paths)
