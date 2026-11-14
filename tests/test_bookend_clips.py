"""Spec 012: the bedtime opening and ending clips joined around the episode.

FFmpeg is replaced by `FakeRunner`; `probe` is stubbed because the runner writes placeholder files.
The one real-FFmpeg test is in `test_bookend_clips_real` (marker `ffmpeg`). Writes only to tmp_path.
"""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import MINI_BOOK, ROOT, load_json
from tests.render_helpers import FakeFrameRunner, FakeRunner, make_world
from video_pipeline import render_episode as re_
from video_pipeline.timeline import load_render_config, section_key

OPEN_LEN, CLOSE_LEN, LOGO_LEN = 10.0, 20.0, 5.0


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, MINI_BOOK)


def _set_config(world, **values):
    path = world["config"] / "render.json"
    cfg = load_json(path)
    cfg.update(values)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _clip_file(world, name, content=b"clip"):
    path = world["root"] / "assets" / "brand" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _bookends(world):
    _clip_file(world, "open.mp4", b"opening")
    _clip_file(world, "close.mp4", b"ending")
    _set_config(
        world,
        channel_open_clip="assets/brand/open.mp4",
        channel_close_clip="assets/brand/close.mp4",
    )


def _all_three(world):
    _bookends(world)
    _clip_file(world, "logo.mp4", b"logo")
    _set_config(world, channel_intro="assets/brand/logo.mp4")


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe stand-in: the length follows the conformed clip's name (`lengths` can be changed);
    `no_audio` lists sources (by file name) without an audio track. Keyframes fall every 5 s."""
    state = {
        "no_audio": set(),
        "calls": [],
        "lengths": {
            "channel_intro.mp4": LOGO_LEN,
            "channel_open.mp4": OPEN_LEN,
            "channel_close.mp4": CLOSE_LEN,
        },
    }

    def seconds(path):
        return state["lengths"].get(Path(path).name, 30.0)  # 30 s: the body

    def fake(path):
        path = Path(path)
        state["calls"].append(path.name)
        streams = [{"codec_type": "video", "duration": str(seconds(path))}]
        if path.name not in state["no_audio"]:
            streams.append({"codec_type": "audio"})
        return {"streams": streams, "format": {"duration": str(seconds(path))}}

    monkeypatch.setattr(re_, "probe", fake)
    monkeypatch.setattr(
        re_,
        "keyframe_frames",
        lambda path, fps: list(range(0, round(seconds(path) * fps), 5 * fps)),
    )
    return state


def _render(world, force=False):
    runner = FakeRunner()
    result = re_.render_video(
        world["folder"], world["root"], force=force, run=runner, frame_run=FakeFrameRunner()
    )
    return result, runner


def _join_cmd(runner):
    return dict(runner.calls)["join clips"]


def _audio_order(runner):
    """Names of the files whose sound is blended, in order (the concat listing is input 0)."""
    cmd = _join_cmd(runner)
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    return [Path(x).name for x in inputs[1:]]


def _record(world):
    return load_json(world["video"] / f"{world['slug']}.json")


# --- configuration -----------------------------------------------------------------------------


def test_shipped_config_names_the_two_clips_and_they_exist():  # FR-1, AC-3.1
    cfg = load_render_config(ROOT / "config" / "render.json")
    assert cfg.channel_open_clip == "assets/brand/bedtime_open.mp4"
    assert cfg.channel_close_clip == "assets/brand/bedtime_close.mp4"
    assert (ROOT / cfg.channel_open_clip).is_file() and (ROOT / cfg.channel_close_clip).is_file()


def test_clip_settings_are_optional_and_default_to_none(tmp_path):
    data = load_json(ROOT / "config" / "render.json")
    data.pop("channel_open_clip")
    data.pop("channel_close_clip")
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = load_render_config(path)
    assert cfg.channel_open_clip == "" and cfg.channel_close_clip == ""


def test_section_key_ignores_the_clips(world):
    cfg = load_render_config(world["config"] / "render.json")
    with_clips = dataclasses.replace(cfg, channel_open_clip="a.mp4", channel_close_clip="b.mp4")
    inputs = re_._load_inputs(world["folder"], world["root"])
    section = inputs.sections[0]
    assert section_key(section, "x", cfg) == section_key(section, "x", with_clips)


# --- join order --------------------------------------------------------------------------------


def test_join_order_is_logo_open_body_close(world, fake_probe):  # AC-1.1, AC-2.1, FR-2
    _all_three(world)
    _, runner = _render(world)
    assert _audio_order(runner) == [
        "channel_intro.mp4",
        "channel_open.mp4",
        "master.wav",  # the body's sound comes straight from the master mix
        "channel_close.mp4",
    ]


def test_logo_absent_still_joins_open_then_body_then_close(world, fake_probe):  # AC-5.4
    _bookends(world)
    _, runner = _render(world)
    assert _audio_order(runner) == ["channel_open.mp4", "master.wav", "channel_close.mp4"]
    assert len(runner.whats("blend ")) == 2


def test_only_the_ending_clip_is_appended_after_the_body(world, fake_probe):  # AC-5.4
    _clip_file(world, "close.mp4", b"ending")
    _set_config(world, channel_close_clip="assets/brand/close.mp4")
    _, runner = _render(world)
    assert _audio_order(runner) == ["master.wav", "channel_close.mp4"]
    assert len(runner.whats("blend ")) == 1  # one join, no join for the absent parts


def test_each_clip_is_conformed_with_the_episode_format(world, fake_probe):  # AC-1.2, AC-2.2
    _all_three(world)
    _, runner = _render(world)
    assert runner.whats("clip ") == ["clip intro", "clip open", "clip close"]
    for what, cmd in runner.calls:
        if what.startswith("clip "):
            graph = cmd[cmd.index("-vf") + 1]
            assert "320:180" in graph and cmd[cmd.index("-ac") + 1] == "2"


def test_no_clips_configured_renders_exactly_as_before(world, fake_probe):  # AC-1.3, AC-2.3
    _, runner = _render(world)
    assert [w for w, _ in runner.calls[-2:]] == ["join", "mux"]
    assert not runner.whats("clip ") and not runner.whats("join clips")
    assert "bookend_clips" not in _record(world)
    assert fake_probe["calls"] == []
    assert not (world["video"] / "channel_join.txt").exists()


def test_logo_only_dissolves_into_the_cover(world, fake_probe):  # AC-5.4
    _clip_file(world, "logo.mp4", b"logo")
    _set_config(world, channel_intro="assets/brand/logo.mp4")
    _, runner = _render(world)
    assert _audio_order(runner) == ["channel_intro.mp4", "master.wav"]
    assert len(runner.whats("blend ")) == 1
    assert "bookend_clips" not in _record(world)


# --- record and duration -----------------------------------------------------------------------


def test_duration_and_record_include_the_clips(world, fake_probe):  # FR-4, AC-3.3
    _all_three(world)
    result, _ = _render(world)
    record = _record(world)
    body = record["frames"] / load_render_config(world["config"] / "render.json").fps
    blend = load_render_config(world["config"] / "render.json").channel_blend_seconds
    # AC-5.2: the sum of the parts minus one dissolve per join (three joins here)
    assert result.duration == pytest.approx(body + LOGO_LEN + OPEN_LEN + CLOSE_LEN - 3 * blend)
    assert record["duration"] == pytest.approx(result.duration, abs=0.001)
    clips = record["bookend_clips"]
    assert set(clips) == {"open", "close"}
    assert clips["open"]["seconds"] == OPEN_LEN and clips["close"]["seconds"] == CLOSE_LEN
    assert clips["open"]["sha256"] == re_.sha256_file(world["root"] / "assets/brand/open.mp4")
    assert clips["close"]["sha256"] == re_.sha256_file(world["root"] / "assets/brand/close.mp4")


def test_duration_with_only_some_parts_subtracts_only_present_joins(world, fake_probe):  # AC-5.4
    _clip_file(world, "close.mp4", b"ending")
    _set_config(world, channel_close_clip="assets/brand/close.mp4")
    result, _ = _render(world)
    cfg = load_render_config(world["config"] / "render.json")
    body = _record(world)["frames"] / cfg.fps
    assert result.duration == pytest.approx(body + CLOSE_LEN - cfg.channel_blend_seconds)


def test_a_clip_shorter_than_two_blends_is_blended_on_both_sides(world, fake_probe):  # AC-5.3
    _all_three(world)
    short = 2.0  # at least one 1.5 s blend, less than two
    fake_probe["lengths"]["channel_open.mp4"] = short
    result, runner = _render(world)
    cfg = load_render_config(world["config"] / "render.json")
    body = _record(world)["frames"] / cfg.fps
    assert result.duration == pytest.approx(
        body + LOGO_LEN + short + CLOSE_LEN - 3 * cfg.channel_blend_seconds
    )
    chains = [cmd for what, cmd in runner.calls if what.startswith("blend ")]
    graph = chains[0][chains[0].index("-filter_complex") + 1]
    assert graph.count("xfade") == 2  # logo tail, the short clip whole, body head, in one chain
    assert _record(world)["bookend_clips"]["open"]["seconds"] == pytest.approx(short)


def test_only_small_windows_are_reencoded_the_rest_is_copied(world, fake_probe):
    _all_three(world)
    _, runner = _render(world)
    for what, cmd in runner.calls:
        if what.startswith("cut "):
            assert cmd[cmd.index("-c") + 1] == "copy"
        if what.startswith("blend "):
            assert "-c:v" in cmd and "libx264" in cmd and "-t" in cmd  # windows have a length
    assert runner.whats("cut ")  # the long parts are cut, not re-encoded whole


def test_blend_length_comes_from_the_config_and_is_in_the_final_key(world, fake_probe):
    _bookends(world)
    _render(world)
    first = _record(world)["final_key"]
    _set_config(world, channel_blend_seconds=0.5)
    result, runner = _render(world)
    assert not result.up_to_date and runner.whats("join clips")
    assert _record(world)["final_key"] != first
    assert runner.whats("clip ") == []  # the cached conformed clips do not depend on the blend
    cmd = _join_cmd(runner)
    script = cmd[cmd.index("-filter_complex_script") + 1]
    assert script  # the graph file is passed by path


def test_blend_setting_is_optional_and_defaults_to_one_point_five(tmp_path):
    data = load_json(ROOT / "config" / "render.json")
    assert data["channel_blend_seconds"] == 1.5
    data.pop("channel_blend_seconds")
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_render_config(path).channel_blend_seconds == 1.5


def test_section_key_ignores_the_blend(world):
    cfg = load_render_config(world["config"] / "render.json")
    other = dataclasses.replace(cfg, channel_blend_seconds=0.4)
    section = re_._load_inputs(world["folder"], world["root"]).sections[0]
    assert section_key(section, "x", cfg) == section_key(section, "x", other)


def test_audio_and_video_settings_change_the_final_key_too(world, fake_probe):  # spec 012 FR-6/7
    _bookends(world)
    _render(world)
    first = _record(world)["final_key"]
    cfg = load_json(world["config"] / "render.json")
    _set_config(world, audio={**cfg["audio"], "voice_gain_db": 3.0})
    _render(world)
    assert _record(world)["final_key"] != first


# --- missing and unreadable files ----------------------------------------------------------------


@pytest.mark.parametrize("field", ["channel_open_clip", "channel_close_clip"])
def test_missing_clip_stops_before_any_encoding(world, fake_probe, field):  # AC-3.4
    _set_config(world, **{field: "assets/brand/nope.mp4"})
    runner = FakeRunner()
    with pytest.raises(re_.RenderError, match="nope.mp4"):
        re_.render_video(world["folder"], world["root"], run=runner, frame_run=FakeFrameRunner())
    assert runner.calls == []
    assert not world["final"].exists()


def test_missing_clip_message_names_the_full_path(world, fake_probe):
    _set_config(world, channel_open_clip="assets/brand/nope.mp4")
    with pytest.raises(re_.RenderError) as err:
        _render(world)
    assert str(world["root"] / "assets" / "brand" / "nope.mp4") in str(err.value)


# --- silence for a clip with no audio ------------------------------------------------------------


def test_command_adds_silence_when_the_source_has_no_audio(tmp_path):  # AC-3.5
    cfg = load_render_config(ROOT / "config" / "render.json")
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    plain = re_.channel_intro_command(src, out, cfg)
    assert "anullsrc" not in " ".join(plain) and "0:a:0?" in plain
    silent = re_.channel_intro_command(src, out, cfg, has_audio=False)
    text = " ".join(silent)
    assert "anullsrc" in text and "-shortest" in silent
    assert f"sample_rate={cfg.video.audio_rate}" in text
    assert silent[silent.index("-ac") + 1] == "2" and "aac" in silent
    assert "0:a:0?" not in silent and "-an" not in silent


def test_silence_is_used_only_for_a_clip_without_an_audio_track(world, fake_probe):
    _bookends(world)
    fake_probe["no_audio"] = {"open.mp4"}
    _, runner = _render(world)
    cmds = dict(runner.calls)
    assert "anullsrc" in " ".join(cmds["clip open"])
    assert "anullsrc" not in " ".join(cmds["clip close"])


# --- caching -----------------------------------------------------------------------------------


def test_unchanged_rerender_does_not_reencode_the_clips(world, fake_probe):  # AC-3.2
    _bookends(world)
    _render(world)
    result, runner = _render(world)
    assert result.up_to_date and runner.calls == []


def test_clips_are_reused_when_only_the_body_changes(world, fake_probe):  # AC-3.2
    _bookends(world)
    _render(world)
    _, runner = _render(world, force=True)
    assert runner.whats("clip ") == []  # force re-renders sections, not cached clip conversions
    assert runner.whats("join clips") == ["join clips"]


def test_changed_clip_is_reencoded_and_invalidates_the_video(world, fake_probe):  # AC-3.3
    _bookends(world)
    _render(world)
    first = _record(world)
    _clip_file(world, "close.mp4", b"ending, new cut")
    result, runner = _render(world)
    assert not result.up_to_date
    assert runner.whats("clip ") == ["clip close"]  # only the changed clip
    second = _record(world)
    assert second["final_key"] != first["final_key"]
    assert second["bookend_clips"]["close"]["sha256"] != first["bookend_clips"]["close"]["sha256"]
    assert second["bookend_clips"]["open"] == first["bookend_clips"]["open"]


def test_changed_video_settings_reencode_the_clips(world, fake_probe):
    _bookends(world)
    _render(world)
    cfg = load_json(world["config"] / "render.json")
    _set_config(world, video={**cfg["video"], "crf": cfg["video"]["crf"] + 1})
    _, runner = _render(world)
    assert runner.whats("clip ") == ["clip open", "clip close"]


# --- preview -------------------------------------------------------------------------------------


def test_preview_includes_no_clips(world, fake_probe):  # AC-3.6
    _bookends(world)
    runner = FakeRunner()
    re_.render_preview(world["folder"], world["root"], 4, run=runner, frame_run=FakeFrameRunner())
    assert not runner.whats("clip ") and not runner.whats("join")
    assert fake_probe["calls"] == []


def test_preview_does_not_need_the_clip_files(world):
    _set_config(world, channel_open_clip="assets/brand/nope.mp4")
    re_.render_preview(
        world["folder"], world["root"], 4, run=FakeRunner(), frame_run=FakeFrameRunner()
    )


# --- real FFmpeg ---------------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_real_clips_are_conformed_to_the_episode_frame(tmp_path):  # AC-1.2, AC-2.2, AC-3.5
    base = load_render_config(ROOT / "config" / "render.json")
    video = dataclasses.replace(base.video, preset="ultrafast")
    cfg = dataclasses.replace(base, video=video)  # real 1920x1080 at 30 fps
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=640x360:rate=24:duration=1", "-pix_fmt", "yuv420p", str(silent)],
        check=True,
    )  # fmt: skip
    sources = {
        "open": (ROOT / cfg.channel_open_clip, 1.2),
        "close": (ROOT / cfg.channel_close_clip, 17.2),
        "silent": (silent, 1.0),
    }
    for role, (src, seconds) in sources.items():
        out = tmp_path / f"conformed_{role}.mp4"
        has_audio = any(s["codec_type"] == "audio" for s in re_.probe(src)["streams"])
        re_.run_ffmpeg(
            re_.channel_intro_command(src, out, cfg, has_audio=has_audio), f"clip {role}"
        )
        info = re_.probe(out)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio_stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
        assert (video_stream["width"], video_stream["height"]) == (1920, 1080)
        assert video_stream["r_frame_rate"] == "30/1"
        assert audio_stream["codec_name"] == "aac" and audio_stream["channels"] == 2
        assert int(audio_stream["sample_rate"]) == cfg.video.audio_rate
        assert float(info["format"]["duration"]) == pytest.approx(seconds, abs=0.2)


@pytest.mark.ffmpeg
def test_real_blended_join_keeps_length_frames_and_sound_in_step(tmp_path):  # AC-5.1 to AC-5.3
    """Real FFmpeg, the real logo and bedtime clips around a short synthetic body: the video is the
    sum of the parts minus 0.8 s per join, every frame is on the 1/30 s grid, and the sound and the
    picture end together."""
    base = load_render_config(ROOT / "config" / "render.json")
    cfg = dataclasses.replace(base, width=320, height=180)  # the shipped preset: B-frames
    body, wav = tmp_path / "body.mp4", tmp_path / "master.wav"
    seconds = 12.0
    raw = tmp_path / "raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=30:duration={seconds}", "-g", "300", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "medium", str(raw)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c", "copy", "-an", str(body)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-ac", "1", str(wav)],
        check=True,
    )  # fmt: skip
    logo, opening, closing = (
        ROOT / cfg.channel_intro,
        ROOT / cfg.channel_open_clip,
        ROOT / cfg.channel_close_clip,
    )
    out = tmp_path / "out.mp4"
    lengths = re_.join_channel_clips(
        body, [("intro", logo), ("open", opening)], [("close", closing)], out, cfg,
        tmp_path / "work", re_.run_ffmpeg, body_audio=wav,
    )  # fmt: skip
    fps, blend = cfg.fps, cfg.channel_blend_seconds
    assert lengths["open"] == pytest.approx(1.2, abs=0.1)  # shorter than 2 s: AC-5.3
    parts = lengths["intro"] + lengths["open"] + lengths["close"] + seconds
    expected = parts - 3 * blend  # four parts, three joins
    info = re_.probe(out)
    vid = next(s for s in info["streams"] if s["codec_type"] == "video")
    aud = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert int(vid["nb_frames"]) == round(expected * fps)
    assert float(aud["duration"]) == pytest.approx(expected, abs=0.05)
    assert float(info["format"]["duration"]) == pytest.approx(expected, abs=0.05)
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(out)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    times = sorted(float(t) for t in probed.stdout.split())
    steps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(abs(step - 1 / fps) < 0.001 for step in steps), "gap or overlap in the picture"
    assert times[-1] == pytest.approx(expected - 1 / fps, abs=0.01)  # no drift over the join
    assert len(times) == round(expected * fps)


# --- chapter times (spec 024 AC-2.3) -----------------------------------------------------------


def test_body_offset_is_opening_clips_minus_one_blend_each():
    cfg = load_render_config(ROOT / "config" / "render.json")
    blend = re_.blend_size(cfg) / cfg.fps
    seconds = {"intro": LOGO_LEN, "open": OPEN_LEN, "close": CLOSE_LEN}
    assert re_.body_offset([], seconds, cfg) == 0.0
    assert re_.body_offset(["open"], seconds, cfg) == pytest.approx(OPEN_LEN - blend)
    assert re_.body_offset(["intro", "open"], seconds, cfg) == pytest.approx(
        LOGO_LEN + OPEN_LEN - 2 * blend
    )


def test_record_has_chapters_in_section_order(world, fake_probe):
    _render(world)
    record = _record(world)
    chapters = record["chapters"]
    assert [c["name"] for c in chapters] == [s["name"] for s in record["sections"]]
    assert chapters[0]["start"] == 0.0
    starts = [c["start"] for c in chapters]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_record_chapters_shift_with_open_bookend(world, fake_probe):
    _render(world)
    plain = _record(world)["chapters"]
    _bookends(world)
    _render(world)
    cfg = load_render_config(world["config"] / "render.json")
    offset = OPEN_LEN - re_.blend_size(cfg) / cfg.fps
    shifted = _record(world)["chapters"]
    assert [c["name"] for c in shifted] == [c["name"] for c in plain]
    for a, b in zip(plain, shifted, strict=True):
        assert b["start"] == pytest.approx(a["start"] + offset, abs=0.002)


def test_chapters_do_not_change_final_key(world, fake_probe):
    _render(world)
    path = world["video"] / f"{world['slug']}.json"
    record = _record(world)
    assert re_.render_check(world["folder"], world["root"]) == "up_to_date"
    old = {k: v for k, v in record.items() if k != "chapters"}  # a record from before spec 024
    path.write_text(json.dumps(old), encoding="utf-8")
    assert re_.render_check(world["folder"], world["root"]) == "up_to_date"
    edited = {**record, "chapters": [{"name": "intro", "start": 99.0}]}
    path.write_text(json.dumps(edited), encoding="utf-8")
    assert re_.render_check(world["folder"], world["root"]) == "up_to_date"


def test_record_chapters_round_trip_to_youtube(world, fake_probe):
    from video_pipeline.youtube import chapters_from_record, load_settings

    _render(world)
    book = load_json(world["book"])
    chapters = chapters_from_record(_record(world), book, load_settings(world["config"]))
    assert chapters and chapters[0].start == 0.0
