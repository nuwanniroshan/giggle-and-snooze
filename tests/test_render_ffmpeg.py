"""Phase 3 of spec 003: FFmpeg command and filtergraph builders (pure; no real FFmpeg)."""

import subprocess
from pathlib import Path

import pytest

from tests.helpers import ROOT
from video_pipeline import render_episode
from video_pipeline.render_episode import (
    ENCODE_ARGS,
    RenderError,
    concat_command,
    frame_grab_command,
    mux_command,
    probe,
    run_ffmpeg,
    section_command,
    section_filtergraph,
    transition_command,
    write_concat_list,
)
from video_pipeline.timeline import (
    CENTRE,
    Camera,
    CaptionCard,
    Section,
    load_render_config,
    zoompan_expr,
)


@pytest.fixture
def cfg():
    return load_render_config(ROOT / "config" / "render.json")


def _page(frames=90, captions=None):
    cam = Camera("slow_push_in", frames, CENTRE, (1.12, 0.5, 0.5))
    return Section("page-01", "page", "page_01", frames, "page-01", captions or [], cam)


def _outro(frames=120, fade=45):
    cam = Camera("outro", frames, (1.15, 0.5, 0.5), CENTRE, fade_out_frames=fade)
    return Section("outro", "outro", "cover_back", frames, "outro", [], cam)


# FFmpeg 8 encodes frame colour properties over -color_* options, so frames are tagged in-graph.
BT709_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"

CARDS = [CaptionCard("Hello there", 0.6, 2.25), CaptionCard("Second card", 2.25, 3.5)]


# --- ENCODE_ARGS ----------------------------------------------------------------------------


def test_encode_args_carry_codec_pixfmt_rate_and_bt709(cfg):
    args = ENCODE_ARGS(cfg)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-preset") + 1] == cfg.video.preset
    assert args[args.index("-crf") + 1] == str(cfg.video.crf)
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-r") + 1] == "30"
    for flag in ("-colorspace", "-color_primaries", "-color_trc"):
        assert args[args.index(flag) + 1] == "bt709"
    assert args[args.index("-color_range") + 1] == "tv"
    assert args[args.index("-video_track_timescale") + 1] == "15360"
    assert "-an" in args


# --- section filtergraph --------------------------------------------------------------------


def test_section_filtergraph_zoompan_uses_camera_and_exact_size(cfg):
    section = _page()
    graph = section_filtergraph(section, 0, cfg)
    z, x, y = zoompan_expr(section.camera)
    assert graph.startswith("[0:v]scale=7680:4320")
    assert f"zoompan=z='{z}':x='{x}':y='{y}':d=90:s=1920x1080:fps=30" in graph
    assert "[base]" in graph
    assert "overlay" not in graph
    assert "fade" not in graph
    assert graph.endswith(f"{BT709_TAGS},format=yuv420p[vout]")


def test_section_filtergraph_one_overlay_per_caption_with_times(cfg):
    section = _page(captions=CARDS)
    graph = section_filtergraph(section, len(CARDS), cfg)
    assert graph.count("overlay=") == 2
    assert "[base][1:v]overlay=" in graph
    assert graph.count("eof_action=repeat") == 2
    assert "[v1][2:v]overlay=" in graph
    assert "enable='between(t,0.600,2.250)'" in graph
    assert "enable='between(t,2.250,3.500)'" in graph
    assert "[v2]" in graph
    assert graph.endswith("format=yuv420p[vout]")


def test_section_filtergraph_caption_count_must_match(cfg):
    with pytest.raises(ValueError, match="caption"):
        section_filtergraph(_page(captions=CARDS), 1, cfg)


def test_section_filtergraph_fades_out_only_the_outro(cfg):
    graph = section_filtergraph(_outro(frames=120, fade=45), 0, cfg)
    assert "fade=t=out:st=2.500:d=1.500" in graph
    assert graph.endswith("format=yuv420p[vout]")
    assert "fade" not in section_filtergraph(_page(), 0, cfg)


def test_section_filtergraph_no_fade_when_fade_frames_zero(cfg):
    assert "fade" not in section_filtergraph(_outro(fade=0), 0, cfg)


# --- commands -------------------------------------------------------------------------------


def test_section_command_inputs_script_map_and_frames(cfg, tmp_path):
    section = _page(captions=CARDS)
    caps = [tmp_path / "c0.png", tmp_path / "c1.png"]
    cmd = section_command(
        section, tmp_path / "canvas.png", caps, tmp_path / "g.txt", tmp_path / "o.mp4", cfg
    )
    assert isinstance(cmd, list)
    assert all(isinstance(a, str) for a in cmd)
    assert cmd[0] == "ffmpeg"
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    assert inputs == [str(tmp_path / "canvas.png"), *map(str, caps)]
    # Every still is one frame: zoompan emits d frames from the canvas and overlay repeats each
    # caption's single frame (decoding looped full-frame captions every frame was ~3x slower).
    assert "-loop" not in cmd
    assert cmd[cmd.index("-filter_complex_script") + 1] == str(tmp_path / "g.txt")
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert cmd[cmd.index("-frames:v") + 1] == "90"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "bt709" in cmd
    assert cmd[-1] == str(tmp_path / "o.mp4")


def test_transition_command_xfade_with_exact_frames(cfg, tmp_path):
    cmd = transition_command(tmp_path / "a.png", tmp_path / "b.png", tmp_path / "t.mp4", cfg)
    assert cmd[0] == "ffmpeg"
    assert cmd.count("-loop") == 2
    assert cmd.count("-t") == 2
    assert cmd[cmd.index("-t") + 1] == "0.800"
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade=transition=slideleft:duration=0.800:offset=0" in graph
    assert graph.endswith(f"{BT709_TAGS},format=yuv420p[vout]")
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert cmd[cmd.index("-frames:v") + 1] == "24"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[-1] == str(tmp_path / "t.mp4")


def test_frame_grab_first_and_last(tmp_path):
    first = frame_grab_command(tmp_path / "v.mp4", tmp_path / "f.png", last=False)
    assert first[0] == "ffmpeg"
    assert "-sseof" not in first
    assert first[first.index("-frames:v") + 1] == "1"
    assert first[-1] == str(tmp_path / "f.png")
    last = frame_grab_command(tmp_path / "v.mp4", tmp_path / "l.png", last=True)
    assert last.index("-sseof") < last.index("-i")
    assert last[last.index("-update") + 1] == "1"
    assert last[-1] == str(tmp_path / "l.png")


def test_concat_command_copies(tmp_path):
    cmd = concat_command(tmp_path / "list.txt", tmp_path / "joined.mp4")
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert cmd[cmd.index("-safe") + 1] == "0"
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[-1] == str(tmp_path / "joined.mp4")


def test_write_concat_list_quotes_paths(tmp_path):
    clips = [tmp_path / "a.mp4", tmp_path / "it's.mp4"]
    out = tmp_path / "list.txt"
    write_concat_list(clips, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"file '{tmp_path / 'a.mp4'}'"
    assert lines[1] == f"file '{tmp_path}/it'\\''s.mp4'"
    assert not list(tmp_path.glob("*.tmp"))


def test_mux_command_maps_both_and_faststart(cfg, tmp_path):
    cmd = mux_command(tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "out.mp4", cfg)
    assert cmd[0] == "ffmpeg"
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["0:v", "1:a"]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[-1] == str(tmp_path / "out.mp4")


# --- runner ---------------------------------------------------------------------------------


def test_run_ffmpeg_uses_safe_subprocess_call(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(render_episode.subprocess, "run", fake_run)
    run_ffmpeg(["ffmpeg", "-version"], "probe")
    assert seen["cmd"] == ["ffmpeg", "-version"]
    assert seen["kwargs"] == {"check": True, "capture_output": True, "text": True}


def test_run_ffmpeg_missing_binary_is_render_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(render_episode.subprocess, "run", fake_run)
    with pytest.raises(RenderError, match="ffmpeg not found"):
        run_ffmpeg(["ffmpeg"], "section page-01")


def test_run_ffmpeg_failure_names_what_and_stderr_tail(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, "", "x" * 2000 + "Invalid argument")

    monkeypatch.setattr(render_episode.subprocess, "run", fake_run)
    with pytest.raises(RenderError) as info:
        run_ffmpeg(["ffmpeg"], "section page-01")
    message = str(info.value)
    assert message.startswith("section page-01: ")
    assert message.endswith("Invalid argument")
    assert len(message) < 1000


def test_probe_parses_ffprobe_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "ffprobe"
        assert "-show_streams" in cmd and "-show_format" in cmd
        assert cmd[cmd.index("-of") + 1] == "json"
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        return subprocess.CompletedProcess(cmd, 0, '{"streams": [], "format": {}}', "")

    monkeypatch.setattr(render_episode.subprocess, "run", fake_run)
    assert probe(Path("x.mp4")) == {"streams": [], "format": {}}


def test_probe_missing_binary_is_render_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(render_episode.subprocess, "run", fake_run)
    with pytest.raises(RenderError, match="ffprobe not found"):
        probe(Path("x.mp4"))
