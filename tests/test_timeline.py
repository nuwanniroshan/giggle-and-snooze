"""Phase 1 of spec 003: render config, captions, sections and camera math (pure)."""

import dataclasses
import functools
import json
import math
import re

import pytest

from tests.helpers import MINI_BOOK, ROOT, load_json
from tests.voice_helpers import make_script
from video_pipeline import timeline
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
    assert cfg.motion.push_zoom == pytest.approx(1.22)
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


def test_page_section_is_the_build_sections_entry(cfg, script, timings):  # spec 010 FR-15
    sections = {s.name: s for s in build_sections(script, timings, cfg)}
    for page in script["pages"]:
        name = timeline.page_section_name(int(page["page_number"]))
        picture = f"page_{int(page['page_number']):02d}"
        assert timeline.page_section(page, timings[name], cfg, picture) == sections[name]


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
    assert 0 < outro.camera.fade_out_frames <= round(cfg.motion.outro_fade * cfg.fps)
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


def test_section_key_hashes_the_caption_font_file(cfg, script, timings, tmp_path):  # review r1 #6
    page = {s.name: s for s in build_sections(script, timings, cfg)}["page-01"]
    font = tmp_path / "Font.ttf"
    font.write_bytes(b"font v1")
    with_font = dataclasses.replace(cfg, captions=dataclasses.replace(cfg.captions, font=str(font)))
    before = section_key(page, "abc", with_font)
    font.write_bytes(b"font v2")  # same path, new glyphs
    assert section_key(page, "abc", with_font) != before


def test_render_version_invalidates_every_key(cfg, script, timings, monkeypatch):  # review r1 #6
    assert timeline.RENDER_VERSION == 3
    page = {s.name: s for s in build_sections(script, timings, cfg)}["page-01"]
    section, trans = section_key(page, "abc", cfg), transition_key("a" * 32, "b" * 32, cfg)
    monkeypatch.setattr(timeline, "RENDER_VERSION", 4)
    assert section_key(page, "abc", cfg) != section
    assert transition_key("a" * 32, "b" * 32, cfg) != trans


def test_outro_fade_starts_after_the_last_line(cfg, script, timings):  # review r1 #7
    long_fade = dataclasses.replace(cfg, motion=dataclasses.replace(cfg.motion, outro_fade=5.0))
    for config in (cfg, long_fade):
        outro = build_sections(script, timings, config)[-1]
        last_end = config.lead_in + max(line["end"] for line in timings["outro"]["lines"])
        fade = outro.camera.fade_out_frames
        assert 0 < fade <= round(config.motion.outro_fade * config.fps)
        assert (outro.frames - fade) / config.fps >= last_end - 1e-9


def test_outro_fade_is_never_negative(cfg, script, timings):  # review r1 #7
    timings["outro"]["lines"][-1]["end"] = timings["outro"]["duration"] + 5.0
    assert build_sections(script, timings, cfg)[-1].camera.fade_out_frames == 0


def test_shipped_outro_fade_fits_in_the_hold(cfg):  # review r1 #7
    assert cfg.motion.outro_fade <= cfg.hold


# --- readable caption breaks (spec 003 v1.1.0: AC-4.5, AC-4.6) -----------------------------

PILOT_SCRIPT = ROOT / "books" / "the-snail-mail-express" / "episode" / "episode_script.json"
SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)\]]*$")
CLAUSE_END_RE = re.compile(r"[,;:—–-][\"'”’)\]]*$")
WORDS_PER_SECOND = 2.5

NARRATION = [
    "Ding-a-ling! As Clara rang the brass sorting bell, a gust of wind ruffled her chestnut "
    "hair and lifted the pages of her logbook.",
    "The first parcel of the day was wrapped in brown paper and tied with green twine. "
    "It was addressed to Mrs. Hedgehog, who lived at the very top of Bramble Hill.",
    "Barnaby stretched out his neck, wiggled his antennae, and began the long climb; "
    "Clara walked beside him, humming softly, counting the ferns as they passed.",
    "Wow! Look at that! The whole meadow was full of fireflies, blinking like tiny lanterns "
    "in the dusk.",
]


def synthetic_cards(text, cfg, start=0.0):
    seconds = len(text.split()) / WORDS_PER_SECOND
    return caption_cards(
        text,
        start,
        start + seconds,
        cfg.captions.max_chars,
        cfg.captions.max_lines,
        min_seconds=cfg.captions.min_card_seconds,
    )


def break_kind(card_text):
    last = card_text.split()[-1]
    if SENTENCE_END_RE.search(last):
        return "sentence"
    if CLAUSE_END_RE.search(last):
        return "clause"
    return "word"


def fits_card(text, cfg):
    lines = wrap_lines(text, cfg.captions.max_chars)
    return len(lines) <= cfg.captions.max_lines and all(
        len(line) <= cfg.captions.max_chars for line in lines
    )


def test_config_min_card_seconds(cfg, tmp_path):
    assert cfg.captions.min_card_seconds == pytest.approx(1.2)
    data = load_json(RENDER_CONFIG)
    data["captions"]["min_card_seconds"] = -1
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=re.escape("captions.min_card_seconds")):
        load_render_config(path)
    del data["captions"]["min_card_seconds"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_render_config(path).captions.min_card_seconds == pytest.approx(1.2)


@pytest.mark.parametrize("text", NARRATION)
def test_caption_cards_readable_breaks(cfg, text):
    cards = synthetic_cards(text, cfg, start=2.0)
    assert len(cards) > 1
    assert " ".join(card.text for card in cards) == text
    assert cards[0].start == 2.0
    assert cards[-1].end == pytest.approx(2.0 + len(text.split()) / WORDS_PER_SECOND)
    for prev, card in zip(cards, cards[1:], strict=False):
        assert card.start == pytest.approx(prev.end, abs=1e-9)
    for card in cards:
        assert fits_card(card.text, cfg), card.text
        assert len(card.text.split()) > 1, card.text
        assert card.end - card.start >= cfg.captions.min_card_seconds - 1e-9, card.text
    for card in cards[:-1]:
        assert break_kind(card.text) != "word", [c.text for c in cards]


def test_caption_cards_prefer_sentence_end(cfg):
    text = NARRATION[1]
    cards = synthetic_cards(text, cfg)
    # The sentence end after "twine." fits a card, so the first card must stop there.
    assert [card.text for card in cards] == [
        "The first parcel of the day was wrapped in brown paper and tied with green twine.",
        "It was addressed to Mrs. Hedgehog, who lived at the very top of Bramble Hill.",
    ]


def test_caption_cards_never_break_after_title_abbreviation(cfg):
    text = (
        "It was addressed to Mrs. Hedgehog, who lived at the very top of Bramble Hill, "
        "past the old mill and the blackberry patch"
    )
    cards = synthetic_cards(text, cfg)
    assert not any(card.text.endswith("Mrs.") for card in cards[:-1])


def test_render_version_bumped_for_readable_captions():
    assert timeline.RENDER_VERSION >= 2


def test_caption_cards_sentence_end_beats_clause_and_balance(cfg):
    cards = synthetic_cards(NARRATION[3], cfg)
    assert cards[0].text == "Wow! Look at that!"


def test_caption_cards_skip_single_word_sentence(cfg):
    cards = synthetic_cards(NARRATION[0], cfg)
    assert cards[0].text == "Ding-a-ling! As Clara rang the brass sorting bell,"


def test_caption_cards_clause_before_word(cfg):
    text = (
        "Barnaby stretched out his long neck and wiggled his antennae slowly, then began the "
        "long climb up the hill with Clara walking beside him"
    )
    cards = synthetic_cards(text, cfg)
    assert len(cards) == 2
    assert cards[0].text.endswith("slowly,")


def test_caption_cards_fewest_cards_and_balanced(cfg):
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    cards = caption_cards(text, 0.0, 10.0, 22, 2)
    # 80 chars need two 2-line cards of ≤ 22, split into even halves.
    assert len(cards) == 2
    lengths = [len(card.text) for card in cards]
    assert abs(lengths[0] - lengths[1]) <= 12


def test_caption_cards_no_single_word_tail(cfg):
    # Greedy 2-line packing would strand "home." on its own card.
    text = "The little snail carried the heavy letter all the way across the meadow to home."
    cards = caption_cards(text, 0.0, 6.0, 20, 2)
    assert len(cards) > 1
    assert all(len(card.text.split()) > 1 for card in cards)
    assert all(card.end - card.start >= 1.2 - 1e-9 for card in cards)


def test_caption_cards_short_line_may_be_short(cfg):
    assert caption_cards("Wow!", 0.0, 0.4, 44, 2) == [CaptionCard("Wow!", 0.0, 0.4)]


def test_caption_cards_fall_back_when_min_duration_impossible():
    text = "one two three four five six seven eight nine ten eleven twelve"
    cards = caption_cards(text, 0.0, 1.0, 10, 2, min_seconds=1.2)
    assert len(cards) > 1
    for card in cards:
        lines = wrap_lines(card.text, 10)
        assert len(lines) <= 2
    assert " ".join(card.text for card in cards) == text
    assert cards[-1].end == 1.0


def _pilot_lines():
    if not PILOT_SCRIPT.exists():
        pytest.skip("pilot episode script not present")
    script = json.loads(PILOT_SCRIPT.read_text(encoding="utf-8"))
    sections = [script["intro"], *script["pages"], script["outro"]]
    return [strip_tags(line["text"]) for section in sections for line in section["lines"]]


def test_caption_cards_over_pilot_episode(cfg):
    single = 0
    breaks = []
    shortest = math.inf
    for text in _pilot_lines():
        if not text:
            continue
        cards = synthetic_cards(text, cfg)
        for card in cards:
            assert fits_card(card.text, cfg), card.text
            if len(cards) > 1:
                single += len(card.text.split()) == 1
                shortest = min(shortest, card.end - card.start)
        if len(cards) > 1:
            breaks += [break_kind(card.text) for card in cards[:-1]]
    punctuated = sum(kind != "word" for kind in breaks) / len(breaks) if breaks else 1.0
    print(
        f"\npilot captions: {len(breaks)} breaks, single-word cards={single}, "
        f"punctuation breaks={punctuated:.1%}, "
        f"sentence={breaks.count('sentence')}, clause={breaks.count('clause')}, "
        f"word={breaks.count('word')}, shortest multi-card card={shortest:.2f}s"
    )
    assert single == 0
    # The pilot's narration sentences are often longer than one 2-line card (88 chars) with few
    # commas, so at the fewest cards some breaks must fall between words: 61.8% is the best a
    # fewest-cards split reaches on this script (greedy packing managed 5.9%).
    assert punctuated >= 0.6
    assert shortest >= cfg.captions.min_card_seconds - 1e-9


# --- sentence ends always win; breaks after dashes (spec 003 v1.1.0 AC-4.5, review r2) -------

PAGE_14_03 = (
    "Clara peered up through the golden willow fronds. Huddled on a crooked branch, with his "
    "slate-blue wings drooped low and his leather mailbag empty, sat Fleet the Falcon—the very "
    "bird who had zoomed past them that morning!"
)


def rejoin(cards):
    """Card texts joined back into the line: no space after a card that ends in a dash."""
    text = ""
    for card in cards:
        sep = "" if not text or text.endswith(("—", "–")) else " "
        text += sep + card.text
    return text


def test_render_version_bumped_for_sentence_first_breaks():
    assert timeline.RENDER_VERSION >= 3


def test_caption_cards_sentence_end_beats_two_clause_breaks(cfg):
    cards = synthetic_cards(PAGE_14_03, cfg)
    assert cards[0].text == "Clara peered up through the golden willow fronds."
    assert rejoin(cards) == PAGE_14_03
    for card in cards:
        assert fits_card(card.text, cfg), card.text
        assert len(card.text.split()) > 1, card.text


def test_wrap_lines_breaks_after_a_dash_without_adding_a_space():
    assert wrap_lines("sat Fleet the Falcon—the very bird", 21) == [
        "sat Fleet the Falcon—",
        "the very bird",
    ]
    assert wrap_lines("a Falcon—the b", 44) == ["a Falcon—the b"]
    assert wrap_lines("pages 3–4 and Barnaby—a snail", 44) == ["pages 3–4 and Barnaby—a snail"]


def test_caption_cards_break_after_a_dash(cfg):
    text = (
        "Fleet the falcon sat alone on the crooked branch—the very bird who had zoomed past "
        "them that morning"
    )
    cards = synthetic_cards(text, cfg)
    assert [card.text for card in cards] == [
        "Fleet the falcon sat alone on the crooked branch—",
        "the very bird who had zoomed past them that morning",
    ]
    assert rejoin(cards) == text
    # Word-share timing counts "branch—the" as one word: each half gets half a word.
    words = len(text.split())
    assert cards[0].end == pytest.approx(words / WORDS_PER_SECOND * 8.5 / words)


def _fewest_mid_sentence_breaks(words, span, count, cfg):
    """Oracle: fewest breaks not after a sentence end over every readable split into `count`
    cards (whitespace words only, so the real splitter, which may also break after dashes, can
    only do as well or better)."""
    n = len(words)
    max_chars, max_lines = cfg.captions.max_chars, cfg.captions.max_lines

    def ok(i, j):
        return (
            j - i > 1
            and span * (j - i) / n >= cfg.captions.min_card_seconds - 1e-9
            and len(wrap_lines(" ".join(words[i:j]), max_chars)) <= max_lines
        )

    @functools.cache
    def best(i, cards):
        if cards == 1:
            return 0 if ok(i, n) else math.inf
        result = math.inf
        for j in range(i + 2, n):
            if ok(i, j):
                cost = break_kind(words[j - 1]) != "sentence"
                result = min(result, cost + best(j, cards - 1))
        return result

    return best(0, count)


def test_caption_cards_pilot_never_ends_mid_sentence_needlessly(cfg):
    needless = 0
    for text in _pilot_lines():
        cards = synthetic_cards(text, cfg) if text else []
        if len(cards) < 2:
            continue
        chosen = sum(break_kind(card.text) != "sentence" for card in cards[:-1])
        words = text.split()
        oracle = _fewest_mid_sentence_breaks(words, len(words) / WORDS_PER_SECOND, len(cards), cfg)
        needless += chosen > oracle
    print(f"\npilot captions: needless mid-sentence card ends={needless}")
    assert needless == 0


def test_config_ambience_settings_read_and_default(cfg, tmp_path):
    """Spec 007 FR-3: level, fade and loop crossfade come from `audio`; all three are optional."""
    assert cfg.audio.ambience_gain_db == pytest.approx(-20.0)  # spec 012 FR-7 (was -14)
    assert cfg.audio.voice_gain_db == pytest.approx(6.0)  # spec 012 FR-6
    assert cfg.audio.ambience_fade == pytest.approx(1.5)
    assert cfg.audio.loop_crossfade == pytest.approx(1.0)
    data = load_json(RENDER_CONFIG)
    data["audio"].update(ambience_gain_db=-30, ambience_fade=2, loop_crossfade=0.5)
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    audio = load_render_config(path).audio
    assert (audio.ambience_gain_db, audio.ambience_fade, audio.loop_crossfade) == (-30, 2, 0.5)
    for key in ("ambience_gain_db", "ambience_fade", "loop_crossfade"):
        del data["audio"][key]
    path.write_text(json.dumps(data), encoding="utf-8")
    audio = load_render_config(path).audio
    assert (audio.ambience_gain_db, audio.ambience_fade, audio.loop_crossfade) == (-20.0, 1.5, 1.0)


# --- animation settings (spec 010 FR-8) -------------------------------------------------------

ALL_EFFECTS = (
    "rain",
    "snow",
    "fireflies",
    "falling_leaves",
    "water_ripple",
    "smoke",
    "mist",
    "glow",
)


def _write_config(tmp_path, data):
    path = tmp_path / "render.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_config_animation_block_read(cfg):
    anim = cfg.animation
    assert anim.enabled is True
    assert anim.intensity == pytest.approx(1.0)
    assert anim.ease_in_s == pytest.approx(1.0)
    assert anim.parallax_max_shift == pytest.approx(0.006)
    assert anim.parallax_period_s == pytest.approx(9.0)
    assert (anim.override_min, anim.override_max) == (0.25, 2.0)
    assert set(anim.effects) == set(ALL_EFFECTS)
    for effect in anim.effects.values():
        assert isinstance(effect, timeline.EffectDefaults)
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", effect.colour)


def test_config_without_animation_block_gives_defaults(tmp_path):
    data = load_json(RENDER_CONFIG)
    del data["animation"]
    anim = load_render_config(_write_config(tmp_path, data)).animation
    assert anim == timeline.AnimationSettings()
    assert anim.enabled is True
    assert anim.effects == timeline.DEFAULT_EFFECTS


def test_config_partial_animation_block_uses_field_defaults(tmp_path):
    data = load_json(RENDER_CONFIG)
    data["animation"] = {"intensity": 0.5}
    anim = load_render_config(_write_config(tmp_path, data)).animation
    assert anim.intensity == pytest.approx(0.5)
    assert anim.override_max == pytest.approx(2.0)
    assert anim.effects == timeline.DEFAULT_EFFECTS  # no effects map: the code defaults


def test_default_effects_are_the_recommended_config_values():  # FR-8
    data = load_json(RENDER_CONFIG)["animation"]["effects"]
    assert set(timeline.DEFAULT_EFFECTS) == set(ALL_EFFECTS)
    assert {n: timeline.EffectDefaults(**v) for n, v in data.items()} == timeline.DEFAULT_EFFECTS
    assert timeline.AnimationSettings().effects == timeline.DEFAULT_EFFECTS
    assert timeline.AnimationSettings().effects is not timeline.DEFAULT_EFFECTS


def test_config_effect_defaults_partial_fields_default(tmp_path):
    data = load_json(RENDER_CONFIG)
    data["animation"]["effects"]["rain"] = {"count": 10}
    rain = load_render_config(_write_config(tmp_path, data)).animation.effects["rain"]
    assert rain == timeline.EffectDefaults(count=10)


def _bad_animation(data, dotted, value):
    parts = dotted.split(".")
    target = data
    for part in parts[:-1]:
        target = target[part]
    if value is _DELETE:
        del target[parts[-1]]
    else:
        target[parts[-1]] = value


_DELETE = object()


@pytest.mark.parametrize(
    ("dotted", "value", "named"),
    [
        ("animation.effects.smog", {"count": 1}, "animation.effects.smog"),
        ("animation.effects.mist", _DELETE, "animation.effects.mist"),
        ("animation.intensity", 0, "animation.intensity"),
        ("animation.intensity", -1, "animation.intensity"),
        ("animation.override_min", 0, "animation.override_min"),
        ("animation.override_min", 3.0, "animation.override_min"),
        ("animation.ease_in_s", -0.5, "animation.ease_in_s"),
        ("animation.effects.glow.colour", "white", "animation.effects.glow.colour"),
        ("animation.effects.glow.colour", "#ffff", "animation.effects.glow.colour"),
        ("animation.effects", ["rain"], "animation.effects"),
        ("animation.effects.rain", 3, "animation.effects.rain"),
        ("animation.enabled", "yes", "animation.enabled"),
    ],
)
def test_config_bad_animation_names_the_field(tmp_path, dotted, value, named):
    data = load_json(RENDER_CONFIG)
    _bad_animation(data, dotted, value)
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_render_config(_write_config(tmp_path, data))


def test_config_animation_not_an_object(tmp_path):
    data = load_json(RENDER_CONFIG)
    data["animation"] = [1]
    with pytest.raises(ConfigError, match="animation"):
        load_render_config(_write_config(tmp_path, data))


def test_animation_block_does_not_change_cache_keys(cfg, script, timings, tmp_path):
    """NFR-5: adding (or editing) the animation block changes no section or transition key."""
    assert timeline.RENDER_VERSION == 3
    data = load_json(RENDER_CONFIG)
    data["animation"] = {"enabled": False, "intensity": 0.5}  # differs from the real block
    plain = load_render_config(_write_config(tmp_path, data))
    assert plain.animation != cfg.animation
    for a, b in zip(
        build_sections(script, timings, cfg), build_sections(script, timings, plain), strict=True
    ):
        assert a == b
        assert section_key(a, "abc", cfg) == section_key(b, "abc", plain)
    assert transition_key("a", "b", cfg) == transition_key("a", "b", plain)
