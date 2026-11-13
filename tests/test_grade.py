"""Spec 013: the final grade's pure parts: settings, validation, filter text, fades, cache key and
the parsers of FFmpeg's loudness reports. No FFmpeg is run."""

import json
import math

import pytest

from tests.helpers import ROOT, load_json
from video_pipeline import grade as g
from video_pipeline.contract import ConfigError
from video_pipeline.episode import book_problems
from video_pipeline.timeline import GradeSettings, load_render_config, parse_grade

CFG = load_render_config(ROOT / "config" / "render.json")
DEFAULT = GradeSettings()


def test_shared_defaults_match_the_hand_enhanced_copy():  # FR-1
    assert CFG.grade == DEFAULT
    assert (DEFAULT.contrast, DEFAULT.saturation, DEFAULT.gamma) == (1.05, 1.12, 1.02)
    assert math.isclose(DEFAULT.vignette_angle, math.pi / 7, abs_tol=1e-6)
    assert (DEFAULT.picture_fade_in, DEFAULT.picture_fade_out) == (0.8, 2.0)
    assert (DEFAULT.sound_fade_in, DEFAULT.sound_fade_out) == (0.8, 2.0)
    assert (DEFAULT.loudness_lufs, DEFAULT.true_peak_dbfs, DEFAULT.loudness_range) == (-16, -1.5, 7)
    assert DEFAULT.enabled and DEFAULT.picture and DEFAULT.sound
    assert DEFAULT.brightness == 0 and not any(
        getattr(DEFAULT, k) for k in g.BALANCE
    )  # neutral balance


def test_shipped_config_fades_match_spec_012_fr5():  # spec 012 AC-5.5, AC-5.6
    shipped = load_json(ROOT / "config" / "render.json")
    assert shipped["channel_blend_seconds"] == 1.5 and CFG.channel_blend_seconds == 1.5
    for key, value in {
        "picture_fade_in": 0.8,
        "sound_fade_in": 0.8,
        "picture_fade_out": 2.0,
        "sound_fade_out": 2.0,
    }.items():
        assert shipped["grade"][key] == value and getattr(CFG.grade, key) == value
    frames = 30 * 600  # a 10-minute video
    duration = frames / 30
    text = g.picture_filter(CFG.grade, duration, 30)
    assert "fade=t=in:st=0:d=0.8" in text
    # a 2 s fade to black that ends on the last frame (t = duration - 1/30)
    assert f"fade=t=out:st={duration - 1 / 30 - 2:.6f}:d=2" in text
    sound = g.sound_filter(CFG.grade, duration, 48000, measuring=True)
    assert "afade=t=in:st=0:d=0.8" in sound
    assert f"afade=t=out:st={duration - 2:g}:d=2" in sound


def test_default_picture_filter():  # AC-1.1, 1.4, 2.1
    text = g.picture_filter(DEFAULT, 20.0, 30)
    assert text.startswith("eq=contrast=1.05:saturation=1.12:gamma=1.02,")
    assert "unsharp=5:5:0.5:5:5:0" in text
    assert "vignette=angle=0.448799" in text
    assert "fade=t=in:st=0:d=0.8" in text
    # the last frame (t = 20 - 1/30) is fully black: the fade out ends there
    assert "fade=t=out:st=17.966667:d=2" in text
    assert text.endswith(
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )
    assert "format=yuv420p" in text and "colorbalance" not in text


def test_default_sound_filter_in_two_passes():  # FR-1, AC-2.2, AC-3.1
    one = g.sound_filter(DEFAULT, 20.0, 48000, measuring=True)
    assert one.startswith("highpass=f=70,acompressor=threshold=-24dB:ratio=3:attack=15")
    assert "release=250:makeup=2" in one
    assert "afade=t=in:st=0:d=0.8" in one and "afade=t=out:st=18:d=2" in one
    assert one.endswith("loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json")
    measured = g.Loudness(-19.1, -3.2, 13.0, -29.5, 0.4)
    two = g.sound_filter(DEFAULT, 20.0, 48000, measured=measured)
    assert (
        "measured_I=-19.1:measured_TP=-3.2:measured_LRA=13:measured_thresh=-29.5:offset=0.4" in two
    )
    assert two.endswith(":linear=true,aresample=48000")


def test_overrides_change_only_their_value():  # AC-1.2
    one = parse_grade({"contrast": 1.2, "rs": 0.1}, "x: grade")
    assert one.contrast == 1.2 and one.saturation == 1.12 and one.gamma == 1.02
    text = g.picture_filter(one, 20.0, 30)
    assert "eq=contrast=1.2:saturation=1.12:gamma=1.02" in text
    assert "colorbalance=rs=0.1," in text
    bright = g.picture_filter(parse_grade({"brightness": 0.05}, "x"), 20.0, 30)
    assert "brightness=0.05" in bright


def test_neutral_values_add_no_filter():
    neutral = parse_grade(
        {"contrast": 1, "saturation": 1, "gamma": 1, "unsharp_amount": 0, "vignette_angle": 0}, "x"
    )
    text = g.picture_filter(neutral, 20.0, 30)
    assert "eq=" not in text and "unsharp" not in text and "vignette" not in text  # AC-1.3


def test_zero_sharpening_only_drops_sharpening():  # AC-1.3
    text = g.picture_filter(parse_grade({"unsharp_amount": 0}, "x"), 20.0, 30)
    assert "unsharp" not in text and "eq=contrast=1.05" in text and "vignette" in text


def test_zero_fades_apply_no_fade():  # AC-2.3
    off = parse_grade(
        {"picture_fade_in": 0, "picture_fade_out": 0, "sound_fade_in": 0, "sound_fade_out": 0}, "x"
    )
    assert "fade" not in g.picture_filter(off, 20.0, 30)
    assert "afade" not in g.sound_filter(off, 20.0, 48000, measuring=True)
    half = parse_grade({"picture_fade_in": 0}, "x")
    text = g.picture_filter(half, 20.0, 30)
    assert "t=in" not in text and "t=out" in text


def test_short_video_shortens_the_fades():  # AC-2.4
    assert g.fade_lengths(0.8, 2.0, 10.0) == (0.8, 2.0)
    fin, fout = g.fade_lengths(0.8, 2.0, 1.05)
    assert math.isclose(fin + fout, 1.05) and math.isclose(fout / fin, 2.5)
    assert g.fade_lengths(0.0, 1.5, 1.0) == (0.0, 1.0)
    text = g.picture_filter(DEFAULT, 1.0, 30)
    out = float(text.split("fade=t=out:st=")[1].split(":")[0])
    assert out >= 0.8 * (0.9667 / 2.8) - 1e-3  # starts when the fade in ends, not before
    sound = g.sound_filter(DEFAULT, 1.0, 48000, measuring=True)
    assert "afade=t=in:st=0:d=0.285714" in sound


def test_switches():  # AC-1.5, AC-3.4, FR-6
    assert g.is_active(DEFAULT)
    assert not g.is_active(parse_grade({"enabled": False}, "x"))
    assert not g.is_active(DEFAULT, no_grade=True)
    assert not g.is_active(parse_grade({"picture": False, "sound": False}, "x"))
    no_picture = parse_grade({"picture": False}, "x")
    assert g.picture_filter(no_picture, 20.0, 30) == ""
    cmd = g.grade_command("in.mp4", "out.mp4", no_picture, 20.0, CFG, None)
    assert cmd[cmd.index("-c:v") + 1] == "copy" and "-vf" not in cmd
    no_sound = parse_grade({"sound": False}, "x")
    assert g.sound_filter(no_sound, 20.0, 48000) == ""
    cmd = g.grade_command("in.mp4", "out.mp4", no_sound, 20.0, CFG, None)
    assert cmd[cmd.index("-c:a") + 1] == "copy" and "-af" not in cmd and "-b:a" not in cmd


def test_grade_command_keeps_format_and_tags():  # FR-5
    cmd = g.grade_command("in.mp4", "out.mp4", DEFAULT, 20.0, CFG, g.Loudness(-20, -5, 5, -30, 0))
    joined = " ".join(cmd)
    for part in ("-c:v libx264", f"-crf {CFG.video.crf}", "-pix_fmt yuv420p", "-colorspace bt709"):
        assert part in joined
    assert f"-r {CFG.fps}" in joined and f"-ar {CFG.video.audio_rate}" in joined
    assert "-movflags +faststart" in joined and "-c:a aac" in joined and cmd[-1] == "out.mp4"
    assert "-shell" not in joined


def test_silent_sound_skips_the_loudness_step():
    assert "loudnorm" not in g.sound_filter(DEFAULT, 20.0, 48000)


# --- validation (FR-3) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "words"),
    [
        ({"contrst": 1}, ["unknown setting 'contrst'", "x.json: grade"]),
        ({"contrast": 9}, ["grade.contrast", "between 0.1 and 3"]),
        ({"picture_fade_in": -1}, ["grade.picture_fade_in", "between 0 and 30"]),
        ({"sound_fade_out": -0.1}, ["grade.sound_fade_out"]),
        ({"saturation": "high"}, ["grade.saturation", "must be a number"]),
        ({"enabled": 1}, ["grade.enabled", "true or false"]),
        ({"unsharp_size": 4}, ["grade.unsharp_size", "odd"]),
        ({"loudness_lufs": 3}, ["grade.loudness_lufs", "between -70 and -5"]),
        ({"rs": float("nan")}, ["grade.rs"]),
    ],
)
def test_bad_settings_name_file_and_field(block, words):
    with pytest.raises(ConfigError) as exc:
        parse_grade(block, "x.json: grade")
    for word in words:
        assert word in str(exc.value)


def test_grade_must_be_an_object():
    with pytest.raises(ConfigError, match="must be an object"):
        parse_grade([1], "x.json: grade")


def test_render_config_validates_its_grade_block(tmp_path):
    data = load_json(ROOT / "config" / "render.json")
    data["grade"]["gamma"] = 7
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"render\.json: grade\.gamma"):
        load_render_config(path)
    data["grade"] = {"nope": 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown setting 'nope'"):
        load_render_config(path)


def test_book_grade_overrides_shared_and_omitted_keys_stay(tmp_path):  # FR-2, AC-4.1
    book = {"grade": {"contrast": 1.3, "picture_fade_out": 0}}
    mine = g.resolve_grade(CFG.grade, book, "books/a/book.json")
    assert mine.contrast == 1.3 and mine.picture_fade_out == 0
    assert mine.saturation == CFG.grade.saturation
    assert g.resolve_grade(CFG.grade, {}, "books/b/book.json") == CFG.grade
    custom = parse_grade({"saturation": 1.4}, "shared")
    assert g.resolve_grade(custom, book, "p").saturation == 1.4


def test_book_json_grade_is_validated_with_file_and_field(tmp_path):  # FR-3
    problems = book_problems(
        {"metadata": {"title": "T"}, "pages": [], "grade": {"gamma": 9}}, "b.json"
    )
    assert any("b.json: grade.gamma" in p for p in problems)
    ok = book_problems({"metadata": {"title": "T"}, "pages": [], "grade": {"gamma": 1.1}}, "b.json")
    assert not any("grade" in p for p in ok)


# --- key and reports ------------------------------------------------------------------------


def test_grade_key_depends_on_input_and_every_setting():
    key = g.grade_key("abc", DEFAULT, CFG)
    assert key == g.grade_key("abc", DEFAULT, CFG)
    assert key != g.grade_key("abd", DEFAULT, CFG)
    assert key != g.grade_key("abc", parse_grade({"contrast": 1.06}, "x"), CFG)
    assert key != g.grade_key("abc", parse_grade({"loudness_lufs": -14}, "x"), CFG)


LOUDNORM_REPORT = """
[Parsed_loudnorm_5 @ 0x1] 
{
\t"input_i" : "-19.12",
\t"input_tp" : "-3.01",
\t"input_lra" : "13.00",
\t"input_thresh" : "-29.64",
\t"output_i" : "-16.9",
\t"output_tp" : "-1.5",
\t"output_lra" : "9.7",
\t"output_thresh" : "-27.4",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.90"
}
"""


def test_parse_loudnorm_report():
    got = g.parse_loudnorm_report(LOUDNORM_REPORT)
    assert got == g.Loudness(-19.12, -3.01, 13.0, -29.64, 0.9)
    silent = LOUDNORM_REPORT.replace('"-19.12"', '"-inf"')
    assert g.parse_loudnorm_report(silent) is None
    with pytest.raises(g.LoudnessError):
        g.parse_loudnorm_report("nothing here")


def test_parse_ebur128_summary_takes_the_last_summary():
    text = """
[Parsed_ebur128_0 @ 0x1] t: 1 TARGET:-23 LUFS M: -20 S: -21 I: -22.0 LUFS LRA: 1.0 LU
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -16.3 LUFS
    Threshold: -26.4 LUFS

  Loudness range:
    LRA:         5.5 LU
    Threshold: -36.4 LUFS

  True peak:
    Peak:       -1.7 dBFS
"""
    assert g.parse_ebur128_summary(text) == g.Loudness(-16.3, -1.7, 5.5)
    with pytest.raises(g.LoudnessError):
        g.parse_ebur128_summary("no summary")
