"""Phase 1 of spec 003: render config, captions, sections and camera math (pure)."""

import dataclasses
import json
import math
import re

import pytest

from tests.helpers import MINI_BOOK, ROOT, load_json
from tests.voice_helpers import make_script
from video_pipeline.contract import ConfigError
from video_pipeline.timeline import (
    Camera,
    CaptionCard,
    build_sections,
    camera_at,
    caption_cards,
    load_render_config,
    narration_camera,
    qa_target_on_canvas,
    section_key,
    section_times,
    smoothstep,
    strip_tags,
    total_frames,
    transition_key,
    wrap_lines,
    zoompan_expr,
)

RENDER_CONFIG = ROOT / "config" / "render.json"
MOTIONS = ("slow_push_in", "slow_pull_out", "pan_left", "pan_right", "static")


@pytest.fixture
def cfg():
    return load_render_config(RENDER_CONFIG)


def ceil_frames(seconds, fps):
    return math.ceil(round(seconds * fps, 6))


# --- config ---------------------------------------------------------------------------------


def test_config_loads(cfg):
    assert (cfg.width, cfg.height, cfg.fps, cfg.upscale) == (1920, 1080, 30, 4)
    assert cfg.motion.push_zoom == pytest.approx(1.12)
    assert cfg.captions.max_chars == 44
    assert cfg.captions.max_lines == 2
    assert cfg.video.audio_rate == 48000
    assert cfg.transition_style == "slideleft"


@pytest.mark.parametrize("dotted", ["fps", "motion.qa_ease", "captions.font_size", "video.crf"])
def test_config_missing_key_named(tmp_path, dotted):
    data = load_json(RENDER_CONFIG)
    parts = dotted.split(".")
    target = data
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=re.escape(dotted)):
        load_render_config(path)


def test_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="render configuration"):
        load_render_config(tmp_path / "nope.json")


# --- captions -------------------------------------------------------------------------------


def test_strip_tags():
    assert strip_tags("<giggle> Hi <b>there</b>!") == "Hi there!"
    assert strip_tags("Wow <gasp>, look") == "Wow, look"
    assert strip_tags("  plain   text ") == "plain text"


def test_wrap_lines():
    assert wrap_lines("one two three", 44) == ["one two three"]
    lines = wrap_lines("aaaa bbbb cccc dddd", 9)
    assert lines == ["aaaa bbbb", "cccc dddd"]
    assert wrap_lines("tiny Supercalifragilisticexpialidocious end", 10) == [
        "tiny",
        "Supercalifragilisticexpialidocious",
        "end",
    ]
    assert wrap_lines("", 10) == []


def test_caption_short_text_one_card():
    cards = caption_cards("Hello there!", 1.0, 2.5, 44, 2)
    assert cards == [CaptionCard("Hello there!", 1.0, 2.5)]


def test_caption_empty():
    assert caption_cards("", 1.0, 2.0, 44, 2) == []
    assert caption_cards("   ", 1.0, 2.0, 44, 2) == []


def test_caption_long_text_split_proportionally():
    words = [f"word{i:02d}" + ("x" * (i % 5)) for i in range(60)]
    text = " ".join(words)
    start, end = 3.0, 27.0
    cards = caption_cards(text, start, end, 44, 2)
    assert len(cards) > 1
    assert cards[0].start == start
    assert cards[-1].end == end
    total_words = len(words)
    for prev, card in zip(cards, cards[1:], strict=False):
        assert card.start == pytest.approx(prev.end, abs=1e-9)
    for card in cards:
        lines = wrap_lines(card.text, 44)
        assert len(lines) <= 2
        assert all(len(line) <= 44 for line in lines)
        share = len(card.text.split()) / total_words * (end - start)
        assert card.end - card.start == pytest.approx(share, abs=1e-6)
    assert " ".join(card.text for card in cards) == text


# --- camera ---------------------------------------------------------------------------------


def test_narration_camera_values(cfg):
    m = cfg.motion
    assert narration_camera("static", cfg) == ((1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    assert narration_camera("slow_push_in", cfg) == ((1.0, 0.5, 0.5), (m.push_zoom, 0.5, 0.5))
    assert narration_camera("slow_pull_out", cfg) == ((m.push_zoom, 0.5, 0.5), (1.0, 0.5, 0.5))
    left = narration_camera("pan_left", cfg)
    assert left == (
        (m.pan_zoom, 0.5 + m.pan_travel, 0.5),
        (m.pan_zoom, 0.5 - m.pan_travel, 0.5),
    )
    right = narration_camera("pan_right", cfg)
    assert right == (left[1], left[0])
    with pytest.raises(ValueError, match="wobble"):
        narration_camera("wobble", cfg)


def test_smoothstep():
    assert smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0
    assert smoothstep(0.5) == pytest.approx(0.5)
    assert smoothstep(-1.0) == 0.0
    assert smoothstep(2.0) == 1.0


def in_bounds(z, cx, cy):
    lo, hi = 1 / (2 * z), 1 - 1 / (2 * z)
    return lo - 1e-12 <= cx <= hi + 1e-12 and lo - 1e-12 <= cy <= hi + 1e-12


def clamp_point(point):
    z, cx, cy = point
    lo, hi = 1 / (2 * z), 1 - 1 / (2 * z)
    return (z, min(max(cx, lo), hi), min(max(cy, lo), hi))


def monotonic(values):
    diffs = [b - a for a, b in zip(values, values[1:], strict=False)]
    return all(d >= -1e-12 for d in diffs) or all(d <= 1e-12 for d in diffs)


def plain_camera(start, end, frames=90):
    return Camera("test", frames, start, end, None, None, 0, 0)


@pytest.mark.parametrize("motion", MOTIONS)
def test_camera_narration_motion(cfg, motion):
    start, end = narration_camera(motion, cfg)
    cam = plain_camera(start, end)
    values = [camera_at(cam, f) for f in range(cam.frames)]
    assert values[0] == pytest.approx(clamp_point(start))
    assert values[-1] == pytest.approx(clamp_point(end))
    for i in range(3):
        assert monotonic([v[i] for v in values])
    assert all(in_bounds(*v) for v in values)
    if motion == "static":
        assert all(v == (1.0, 0.5, 0.5) for v in values)


def qa_camera(cfg, target=(1.3, 0.6, 0.4), motion="slow_push_in"):
    start, end = narration_camera(motion, cfg)
    return Camera("page", 150, start, end, 60, target, 36, 0)


def test_camera_qa_before_matches_narration(cfg):
    cam = qa_camera(cfg)
    start, end = narration_camera("slow_push_in", cfg)
    narration_only = Camera("page", 60, start, end, None, None, 0, 0)
    for f in range(60):
        assert camera_at(cam, f) == pytest.approx(camera_at(narration_only, f))
    assert camera_at(cam, 59) == pytest.approx(clamp_point(end))


def test_camera_qa_ease_then_hold(cfg):
    cam = qa_camera(cfg)
    values = [camera_at(cam, f) for f in range(60, 60 + 36 + 1)]
    for i in range(3):
        assert monotonic([v[i] for v in values])
    for f in range(60 + 36, cam.frames):
        assert camera_at(cam, f) == pytest.approx((1.3, 0.6, 0.4))
    assert camera_at(cam, 60) == pytest.approx(camera_at(cam, 59))


def test_camera_qa_target_clamped(cfg):
    cam = qa_camera(cfg, target=(1.3, 0.0, 1.0))
    z, cx, cy = camera_at(cam, cam.frames - 1)
    assert z == pytest.approx(1.3)
    assert cx == pytest.approx(1 / (2 * 1.3))
    assert cy == pytest.approx(1 - 1 / (2 * 1.3))
    values = [camera_at(cam, f) for f in range(cam.frames)]
    assert all(in_bounds(*v) for v in values)
    for i in range(3):
        assert monotonic([v[i] for v in values[60:]])


def test_qa_target_on_canvas(cfg):
    assert qa_target_on_canvas({"x": 0.0, "y": 0.25, "zoom": 1.3}, cfg) == pytest.approx(
        (1.3, 420 / 1920, 0.25)
    )
    assert qa_target_on_canvas({"x": 1.0, "y": 0.5, "zoom": 1.2}, cfg) == pytest.approx(
        (1.2, 1500 / 1920, 0.5)
    )


# --- zoompan expressions --------------------------------------------------------------------

_FUNCS = {
    "if": lambda c, a, b: a if c else b,
    "lt": lambda a, b: 1.0 if a < b else 0.0,
    "gte": lambda a, b: 1.0 if a >= b else 0.0,
    "clip": lambda x, lo, hi: min(max(x, lo), hi),
    "min": min,
    "max": max,
}


def evaluate(expr, **variables):
    assert "'" not in expr and '"' not in expr
    assert re.fullmatch(r"[A-Za-z0-9_.+\-*/(), ]+", expr), expr
    names = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expr))
    assert names <= set(_FUNCS) | set(variables), names - set(_FUNCS) - set(variables)
    py = re.sub(r"\b(if|lt|gte|clip|min|max)\(", r"_f_\1(", expr)
    namespace = {f"_f_{k}": v for k, v in _FUNCS.items()}
    namespace.update(variables)
    return eval(py, {"__builtins__": {}}, namespace)  # noqa: S307 - test-only, checked charset


def check_expressions(cam, iw=7680.0, ih=4320.0):
    z_expr, x_expr, y_expr = zoompan_expr(cam)
    for f in sorted({0, 1, 2, cam.frames // 3, cam.frames // 2, cam.frames - 2, cam.frames - 1}
                    | ({cam.qa_frame - 1, cam.qa_frame, cam.qa_frame + 1,
                        cam.qa_frame + cam.ease_frames // 2, cam.qa_frame + cam.ease_frames}
                       if cam.qa_frame is not None else set())):  # fmt: skip
        z, cx, cy = camera_at(cam, f)
        zoom = evaluate(z_expr, on=float(f), iw=iw, ih=ih)
        assert zoom == pytest.approx(z, abs=1e-6), f
        x = evaluate(x_expr, on=float(f), iw=iw, ih=ih, zoom=zoom)
        y = evaluate(y_expr, on=float(f), iw=iw, ih=ih, zoom=zoom)
        assert x == pytest.approx(cx * iw - iw / z / 2, abs=1e-6), f
        assert y == pytest.approx(cy * ih - ih / z / 2, abs=1e-6), f


@pytest.mark.parametrize("motion", MOTIONS)
def test_zoompan_expr_matches_camera(cfg, motion):
    start, end = narration_camera(motion, cfg)
    check_expressions(plain_camera(start, end, frames=97))


@pytest.mark.parametrize("motion", MOTIONS)
def test_zoompan_expr_matches_camera_with_qa(cfg, motion):
    check_expressions(qa_camera(cfg, motion=motion))
    check_expressions(qa_camera(cfg, target=(1.3, 0.0, 1.0), motion=motion))


def test_zoompan_expr_single_frame_and_qa_at_zero(cfg):
    check_expressions(plain_camera((1.0, 0.5, 0.5), (1.15, 0.5, 0.5), frames=1))
    check_expressions(
        Camera("page", 40, (1.0, 0.5, 0.5), (1.1, 0.5, 0.5), 0, (1.3, 0.6, 0.5), 10, 0)
    )


# --- sections -------------------------------------------------------------------------------


def synth_timing(name, lines, gap=0.4):
    """Each line lasts 1.5 s + 0.25 s per word; lines are separated by `gap`."""
    t, out = 0.0, []
    for index, line in enumerate(lines, start=1):
        dur = 1.5 + 0.25 * len(line["text"].split())
        out.append(
            {
                "line_id": f"{name}-{index:02d}",
                "speaker": line["speaker"],
                "role": line.get("role", name.split("-")[0]),
                "text": line["text"],
                "start": round(t, 2),
                "end": round(t + dur, 2),
            }
        )
        t += dur + gap
    return {"track": name, "duration": round(t - gap + 0.13, 2), "lines": out}


@pytest.fixture
def script():
    return make_script(MINI_BOOK)


@pytest.fixture
def timings(script):
    result = {"intro": synth_timing("intro", script["intro"]["lines"])}
    for page in script["pages"]:
        name = f"page-{page['page_number']:02d}"
        result[name] = synth_timing(name, page["lines"])
    result["outro"] = synth_timing("outro", script["outro"]["lines"])
    return result


def test_build_sections_order_and_frames(cfg, script, timings):
    sections = build_sections(script, timings, cfg)
    names = [s.name for s in sections]
    pages = sorted(p["page_number"] for p in script["pages"])
    assert names == ["intro", *[f"page-{n:02d}" for n in pages], "back-cover", "outro"]
    assert [s.kind for s in sections] == ["intro"] + ["page"] * len(pages) + ["beat", "outro"]
    for section in sections:
        if section.kind == "beat":
            assert section.frames == round(cfg.back_cover_seconds * cfg.fps)
            assert section.track is None
            assert section.captions == []
            assert section.picture == "cover_back"
            continue
        assert section.track == section.name
        seconds = cfg.lead_in + timings[section.name]["duration"] + cfg.hold
        assert section.frames == ceil_frames(seconds, cfg.fps)
        assert section.camera.frames == section.frames
    pictures = {s.name: s.picture for s in sections}
    assert pictures["intro"] == "cover_front"
    assert pictures["outro"] == "cover_back"
    assert pictures["page-01"] == "page_01"


def test_build_sections_picture_override(cfg, script, timings):
    sections = build_sections(script, timings, cfg, {"intro": "intro_nook", "outro": "outro_nook"})
    pictures = {s.name: s.picture for s in sections}
    assert pictures["intro"] == "intro_nook"
    assert pictures["outro"] == "outro_nook"
    assert pictures["page-02"] == "page_02"


def test_build_sections_captions_shifted(cfg, script, timings):
    sections = {s.name: s for s in build_sections(script, timings, cfg)}
    for name in ["intro", "page-01", "outro"]:
        section = sections[name]
        lines = timings[name]["lines"]
        for line in lines:
            cards = [
                c
                for c in section.captions
                if cfg.lead_in + line["start"] - 1e-9 <= c.start < cfg.lead_in + line["end"]
            ]
            assert cards, line["text"]
            assert cards[0].start == pytest.approx(cfg.lead_in + line["start"])
            assert cards[-1].end == pytest.approx(cfg.lead_in + line["end"])
            assert "<" not in " ".join(c.text for c in cards)
            assert " ".join(c.text for c in cards) == strip_tags(line["text"])
        # no caption outside the spoken spans (AC-4.1)
        assert section.captions[0].start == pytest.approx(cfg.lead_in + lines[0]["start"])
        assert section.captions[-1].end == pytest.approx(cfg.lead_in + lines[-1]["end"])


def test_build_sections_qa_cameras(cfg, script, timings):
    sections = {s.name: s for s in build_sections(script, timings, cfg)}
    qa_pages = [p for p in script["pages"] if p["has_qa"]]
    assert qa_pages
    for page in script["pages"]:
        name = f"page-{page['page_number']:02d}"
        cam = sections[name].camera
        start, end = narration_camera(page["ffmpeg_camera"]["narration_motion"], cfg)
        assert (cam.start, cam.end) == (start, end)
        if page["has_qa"]:
            first = next(ln for ln in timings[name]["lines"] if ln["role"] != "narration")
            assert cam.qa_frame == round((cfg.lead_in + first["start"]) * cfg.fps)
            assert cam.qa_target == qa_target_on_canvas(
                page["ffmpeg_camera"]["qa_zoom_target"], cfg
            )
            assert cam.ease_frames == round(cfg.motion.qa_ease * cfg.fps)
        else:
            assert cam.qa_frame is None
            assert cam.qa_target is None


def test_build_sections_intro_outro_cameras(cfg, script, timings):
    sections = {s.name: s for s in build_sections(script, timings, cfg)}
    intro, outro, beat = sections["intro"], sections["outro"], sections["back-cover"]
    assert intro.camera.start == (1.0, 0.5, 0.5)
    assert intro.camera.end == (cfg.motion.intro_zoom, 0.5, 0.5)
    assert intro.camera.fade_out_frames == 0
    assert outro.camera.start == (cfg.motion.outro_zoom, 0.5, 0.5)
    assert outro.camera.end == (1.0, 0.5, 0.5)
    assert outro.camera.fade_out_frames == round(cfg.motion.outro_fade * cfg.fps)
    assert beat.camera.start == beat.camera.end == (1.0, 0.5, 0.5)


def test_build_sections_missing_timing(cfg, script, timings):
    del timings["page-02"]
    with pytest.raises(ValueError, match="page-02"):
        build_sections(script, timings, cfg)


def test_section_times_and_total(cfg, script, timings):
    sections = build_sections(script, timings, cfg)
    times = section_times(sections, cfg)
    tf = round(cfg.transition * cfg.fps)
    assert total_frames(sections, cfg) == sum(s.frames for s in sections) + (len(sections) - 1) * tf
    assert times[0][0] == 0.0
    for (start, end), section in zip(times, sections, strict=True):
        assert end - start == pytest.approx(section.frames / cfg.fps)
    for (_, prev_end), (next_start, _) in zip(times, times[1:], strict=False):
        # the gap between sections is exactly one transition: no section starts inside it
        assert next_start - prev_end == pytest.approx(tf / cfg.fps)
    assert times[-1][1] == pytest.approx(total_frames(sections, cfg) / cfg.fps)


# --- keys -----------------------------------------------------------------------------------


def test_section_key_changes(cfg, script, timings):
    sections = {s.name: s for s in build_sections(script, timings, cfg)}
    page = sections["page-01"]
    base = section_key(page, "abc", cfg)
    assert base == section_key(page, "abc", cfg)
    assert len(base) == 32
    assert section_key(page, "abd", cfg) != base
    changed_caption = dataclasses.replace(
        page, captions=[CaptionCard("other", 0.6, 1.0), *page.captions[1:]]
    )
    assert section_key(changed_caption, "abc", cfg) != base
    changed_motion = dataclasses.replace(
        page, camera=dataclasses.replace(page.camera, end=(1.2, 0.5, 0.5))
    )
    assert section_key(changed_motion, "abc", cfg) != base
    timings["page-01"]["duration"] += 1.0
    retimed = {s.name: s for s in build_sections(script, timings, cfg)}["page-01"]
    assert section_key(retimed, "abc", cfg) != base
    other_cfg = dataclasses.replace(cfg, captions=dataclasses.replace(cfg.captions, font_size=52))
    assert section_key(page, "abc", other_cfg) != base
    assert section_key(page, "abc", dataclasses.replace(cfg, upscale=2)) != base


def test_transition_key(cfg):
    key = transition_key("a" * 32, "b" * 32, cfg)
    assert key == transition_key("a" * 32, "b" * 32, cfg)
    assert key != transition_key("a" * 32, "c" * 32, cfg)
    assert key != transition_key("a" * 32, "b" * 32, dataclasses.replace(cfg, transition=1.0))
    assert key != transition_key(
        "a" * 32, "b" * 32, dataclasses.replace(cfg, transition_style="fade")
    )
