"""Spec 013 against the real FFmpeg: a short synthetic clip (test card + tone) is graded and the
result measured again (loudness, true peak, fades, length). Writes only to tmp_path."""

import subprocess
from dataclasses import replace

import numpy as np
import pytest

from tests.helpers import ROOT
from video_pipeline import grade as g
from video_pipeline.render_episode import grade_video, measure_ffmpeg, probe, run_ffmpeg
from video_pipeline.timeline import load_render_config, parse_grade

pytestmark = pytest.mark.ffmpeg

W, H, SECONDS = 320, 180, 12


def _cfg():
    return load_render_config(ROOT / "config" / "render.json")


def _synthetic(path, cfg):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={cfg.fps}:d={SECONDS}",
        "-f", "lavfi", "-i", f"sine=f=440:r={cfg.video.audio_rate}:d={SECONDS},volume=0.12",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(path),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _gray_frames(path):
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W)


def _duration(path):
    return float(probe(path)["format"]["duration"])


@pytest.mark.parametrize("target", [-16.0, -20.0])
def test_real_grade_meets_loudness_peak_fades_and_length(tmp_path, target, capsys):
    cfg = _cfg()
    settings = parse_grade({"loudness_lufs": target}, "test")
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _synthetic(src, cfg)
    duration = _duration(src)
    result = grade_video(src, out, settings, duration, cfg, run_ffmpeg, measure_ffmpeg)

    # measured again independently of the step's own measurement
    text = subprocess.run(
        g.loudness_command(str(out)), check=True, capture_output=True, text=True
    ).stderr
    again = g.parse_ebur128_summary(text)
    print(f"\nGRADE target={target} measured={again} step={result}")
    assert abs(again.integrated - target) <= 1.0
    assert again.true_peak <= settings.true_peak_dbfs
    assert abs(result.integrated - again.integrated) < 0.2

    frame = 1 / cfg.fps
    assert abs(_duration(out) - duration) <= frame + 1e-3
    frames = _gray_frames(out)
    assert frames[0].mean() < 3  # first frame black
    assert frames[-1].mean() < 3  # last frame black
    mid = frames[len(frames) // 2]
    assert mid.mean() > 40  # fully visible after the fade in
    assert (
        frames[int(0.6 * cfg.fps) + 1].mean()
        > 0.9 * _gray_frames(src)[int(0.6 * cfg.fps) + 1].mean() * 0.8
    )

    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"], video["pix_fmt"]) == (W, H, "yuv420p")
    assert video["color_space"] == "bt709" and video["color_primaries"] == "bt709"
    assert audio["codec_name"] == "aac" and int(audio["sample_rate"]) == cfg.video.audio_rate


def test_real_short_clip_with_overlapping_fades_still_renders(tmp_path):  # AC-2.4
    cfg = replace(_cfg())
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    _synthetic(src, cfg)
    short = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", "1.0", "-c", "copy", str(short)],
        check=True, capture_output=True,
    )  # fmt: skip
    grade_video(short, out, parse_grade({}, "t"), _duration(short), cfg, run_ffmpeg, measure_ffmpeg)
    assert out.exists() and _duration(out) > 0.9


def test_real_peak_limit_wins_over_the_loudness_target(tmp_path):  # spec Error Scenarios
    cfg = _cfg()
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    burst = "if(lt(mod(t,2),0.005),0.9,0.003)*sin(2*PI*440*t)"  # sparse loud bursts: a high crest
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={cfg.fps}:d={SECONDS}",
        "-f", "lavfi", "-i", f"aevalsrc='{burst}':s={cfg.video.audio_rate}:d={SECONDS}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", str(src),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    settings = parse_grade({"loudness_lufs": -5.0}, "test")
    result = grade_video(src, out, settings, _duration(src), cfg, run_ffmpeg, measure_ffmpeg)
    print(f"\nPEAK-LIMITED target=-5 got={result}")
    assert result.true_peak <= settings.true_peak_dbfs + 0.05
    assert result.integrated < -5.5  # the loudness gave way
