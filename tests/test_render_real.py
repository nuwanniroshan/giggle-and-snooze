"""Phase 3 of spec 003 against the real FFmpeg: a tiny episode rendered section by section,
joined, muxed and probed (AC-1.2, AC-1.4, AC-5.1, AC-6.3). Writes only to tmp_path."""

import dataclasses
import subprocess

import numpy as np
import pytest
from PIL import Image

from tests.helpers import ROOT
from video_pipeline.frames import compose_canvas, mix_master, page_turn_sound, render_caption
from video_pipeline.render_episode import (
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
from video_pipeline.synthesize_voices import write_wav_atomic
from video_pipeline.timeline import (
    build_sections,
    load_render_config,
    total_frames,
    transition_frames,
)

pytestmark = pytest.mark.ffmpeg

SR = 24000
W, H, FPS = 320, 180, 10
COLOURS = {"intro": (10, 10, 90), "page-01": (90, 10, 10), "back-cover": (10, 70, 10)}


def _cfg():
    base = load_render_config(ROOT / "config" / "render.json")
    captions = dataclasses.replace(base.captions, font_size=16, margin=8, padding=6)
    video = dataclasses.replace(base.video, preset="ultrafast")
    return dataclasses.replace(
        base, width=W, height=H, fps=FPS, upscale=2, captions=captions, video=video
    )


def _timing(duration, lines):
    return {
        "duration": duration,
        "lines": [
            {"start": s, "end": e, "text": t, "role": r, "speaker": "storyteller"}
            for s, e, t, r in lines
        ],
    }


def _tone(seconds, freq):
    t = np.arange(round(seconds * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _decode_rgb(video):
    """Every frame of `video` as an (n, H, W, 3) uint8 array."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout  # binary: no text=True
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W, 3).astype(np.int16)


def _render(tmp_path, cfg):
    script = {
        "pages": [
            {
                "page_number": 1,
                "ffmpeg_camera": {
                    "narration_motion": "slow_push_in",
                    "qa_zoom_target": {"x": 0.3, "y": 0.4, "zoom": 1.4},
                },
            }
        ]
    }
    timings = {
        "intro": _timing(1.0, [(0.0, 1.0, "Welcome to <giggle> story time!", "intro")]),
        "page-01": _timing(
            1.5,
            [(0.0, 0.8, "Once upon a time.", "narration"), (0.8, 1.5, "Look!", "kid_question")],
        ),
        "outro": _timing(1.0, [(0.0, 1.0, "Goodnight!", "outro")]),
    }
    sections = build_sections(script, timings, cfg)
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    clips = []
    for index, section in enumerate(sections):
        work = tmp_path / section.name
        picture = pictures / f"{section.name}.png"
        colour = COLOURS.get(section.name, (60, 60, 10))
        im = Image.new("RGB", (256, 256), colour)
        im.paste((200, 200, 40), (16, 16, 80, 80))  # some detail for the camera to move over
        im.save(picture)
        canvas = work / "canvas.png"
        compose_canvas(picture, cfg, canvas)
        captions = []
        for number, card in enumerate(section.captions):
            path = work / f"caption_{number}.png"
            render_caption(card.text, cfg, path)
            captions.append(path)
        graph = work / "graph.txt"
        graph.write_text(section_filtergraph(section, len(captions), cfg), encoding="utf-8")
        clip = tmp_path / f"{section.name}.mp4"
        run_ffmpeg(
            section_command(section, canvas, captions, graph, clip, cfg), f"section {section.name}"
        )
        if index:
            previous = clips[-1]
            last_png, first_png = work / "prev_last.png", work / "first.png"
            run_ffmpeg(frame_grab_command(previous, last_png, last=True), "grab last")
            run_ffmpeg(frame_grab_command(clip, first_png, last=False), "grab first")
            trans = tmp_path / f"trans_{index}.mp4"
            run_ffmpeg(transition_command(last_png, first_png, trans, cfg), "transition")
            clips.append(trans)
        clips.append(clip)
    list_file = tmp_path / "list.txt"
    write_concat_list(clips, list_file)
    joined = tmp_path / "joined.mp4"
    run_ffmpeg(concat_command(list_file, joined), "join")
    tracks = {
        name: _tone(t["duration"], 330 + 110 * i) for i, (name, t) in enumerate(timings.items())
    }
    master = mix_master(sections, tracks, page_turn_sound(SR), cfg, SR)
    wav = tmp_path / "master.wav"
    write_wav_atomic(wav, master, SR)
    out = tmp_path / "episode.mp4"
    run_ffmpeg(mux_command(joined, wav, out, cfg), "mux")
    return sections, clips, out


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    cfg = _cfg()
    tmp = tmp_path_factory.mktemp("render")
    sections, clips, out = _render(tmp, cfg)
    return cfg, sections, clips, out, _decode_rgb(out)


def test_streams_are_youtube_ready(rendered):
    cfg, sections, _clips, out, frames = rendered
    info = probe(out)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(video) == 1 and len(audio) == 1
    v, a = video[0], audio[0]
    assert (v["codec_name"], v["pix_fmt"], v["width"], v["height"]) == ("h264", "yuv420p", W, H)
    assert v["r_frame_rate"] == f"{FPS}/1"
    assert v["color_space"] == "bt709"
    assert v["color_primaries"] == "bt709"
    assert v["color_transfer"] == "bt709"
    assert (a["codec_name"], a["channels"], a["sample_rate"]) == ("aac", 2, "48000")
    expected = total_frames(sections, cfg)
    assert int(v["nb_frames"]) == expected
    assert len(frames) == expected
    assert abs(float(v["duration"]) - expected / FPS) < 0.1
    assert abs(float(a["duration"]) - float(v["duration"])) < 0.1


def test_moov_atom_is_at_the_start(rendered):
    out = rendered[3]
    data = out.read_bytes()
    assert 0 <= data.find(b"moov") < data.find(b"mdat")


def test_last_frame_grab_matches_the_clips_final_frame(rendered, tmp_path):
    _cfg_, _sections, clips, _out, _frames = rendered
    clip = clips[0]
    png = tmp_path / "last.png"
    run_ffmpeg(frame_grab_command(clip, png, last=True), "grab last")
    decoded = _decode_rgb(clip)
    with Image.open(png) as im:
        grabbed = np.asarray(im.convert("RGB"), dtype=np.int16)
    assert np.abs(grabbed - decoded[-1]).mean() < 2
    assert np.abs(grabbed - decoded[-2]).mean() >= np.abs(grabbed - decoded[-1]).mean()


def test_mid_transition_differs_from_both_neighbours(rendered):
    cfg, sections, _clips, _out, frames = rendered
    a_end = sections[0].frames  # first frame of the intro→page transition
    t = transition_frames(cfg)
    last_a, mid, first_b = frames[a_end - 1], frames[a_end + t // 2], frames[a_end + t]
    assert np.abs(mid - last_a).mean() > 5
    assert np.abs(mid - first_b).mean() > 5


def _caption_band(frame, cfg):
    """Brightest pixel of the caption band area (white text when a caption shows)."""
    margin = cfg.captions.margin
    return frame[H - margin - 30 : H - margin, W // 4 : 3 * W // 4].max()


def test_caption_shows_at_its_time_and_not_before_or_after(rendered):
    cfg, sections, _clips, _out, frames = rendered
    card = sections[0].captions[0]
    assert _caption_band(frames[round(card.start * FPS) - 2], cfg) < 120
    assert _caption_band(frames[round(card.start * FPS) + 2], cfg) > 200
    assert _caption_band(frames[round(card.end * FPS) + 3], cfg) < 120


def test_every_caption_of_a_section_shows(rendered):
    cfg, sections, _clips, _out, frames = rendered
    page = sections[1]
    offset = sections[0].frames + transition_frames(cfg)  # page start in the final video
    assert len(page.captions) == 2
    for card in page.captions:
        middle = offset + round((card.start + card.end) / 2 * FPS)
        assert _caption_band(frames[middle], cfg) > 200, card


def test_outro_fades_to_black(rendered):
    frames = rendered[4]
    assert frames[-1].mean() < 8
    assert frames[-1].mean() < frames[-10].mean()


# --- animated page sections (spec 010 Phase 4) ------------------------------------------------


@pytest.fixture(scope="module")
def animated(tmp_path_factory):
    """The mini book with page 1 animated (fake cut): its page-01 section rendered animated (twice,
    from a clean cache) and still, with its own camera and with a static camera."""
    from tests import animation_helpers as ah
    from tests.helpers import MINI_BOOK
    from tests.render_helpers import make_world
    from video_pipeline import render_episode as re_
    from video_pipeline.timeline import CENTRE, Camera

    tmp = tmp_path_factory.mktemp("animated")
    with pytest.MonkeyPatch.context() as mp:
        world = make_world(tmp, mp, MINI_BOOK)
    ah.write_plan(world["folder"], 1, layers=[ah.layer()])
    ah.cut(world["folder"], pages=[1])
    inputs = re_._load_inputs(world["folder"], world["root"])
    assert inputs.eligibility["page-01"].animated
    section = next(s for s in inputs.sections if s.name == "page-01")
    static = dataclasses.replace(section, camera=Camera("static", section.frames, CENTRE, CENTRE))
    picture = inputs.pictures.files[section.picture]
    cfg = inputs.cfg
    out = {"cfg": cfg, "section": section}

    work = re_._SectionWork(
        re_.VideoPaths(tmp), cfg, world["folder"], world["slug"], run_ffmpeg, None, False, None
    )
    el = inputs.eligibility["page-01"]

    def clip(name, sec, animate):
        path = tmp / f"{name}.mp4"
        anim = re_._load_animation(work, sec, el, picture)[0] if animate else None
        assert (anim is not None) == animate
        re_._render_section(
            sec, picture, path, tmp / "work" / name, cfg, run_ffmpeg, re_.run_ffmpeg_frames, anim
        )
        return path

    for name, sec, animate in [
        ("animated", section, True),
        ("animated_again", section, True),
        ("still", section, False),
        ("static_animated", static, True),
        ("static_still", static, False),
    ]:
        path = clip(name, sec, animate)
        out[name] = _decode_rgb(path)
        out[f"{name}_probe"] = probe(path)
    return out


def test_animated_clip_is_tagged_like_the_still(animated):  # FR-12
    for name in ("animated", "still"):
        v = animated[f"{name}_probe"]["streams"][0]
        assert (v["pix_fmt"], v["color_range"], v["color_space"]) == ("yuv420p", "tv", "bt709")
        assert (v["width"], v["height"]) == (W, H)


def test_animated_frame_count_equals_the_still(animated):  # AC-1.6
    frames = animated["section"].frames
    assert len(animated["animated"]) == len(animated["still"]) == frames
    assert int(animated["animated_probe"]["streams"][0]["nb_frames"]) == frames


def test_animated_first_frame_matches_the_still(animated):  # AC-1.4
    mad = np.abs(animated["animated"][0] - animated["still"][0]).mean()
    assert mad <= 3, mad


def test_animated_page_actually_moves(animated):  # AC-1.1
    frames = animated["static_animated"]
    assert np.abs(frames[-1] - frames[0]).max() > 30


def test_animated_render_is_repeatable(animated):  # AC-5.4
    assert np.array_equal(animated["animated"], animated["animated_again"])


def test_backdrop_outside_the_picture_matches_the_still(animated):  # AC-1.5
    side = H  # the 64×64 test picture is contained at full height
    x0 = (W - side) // 2
    margin = 2  # 4:2:0 chroma bleeds across the picture edge
    keep = np.ones((H, W), bool)
    keep[:, x0 - margin : x0 + side + margin] = False
    a, s = animated["static_animated"], animated["static_still"]
    assert len(a) == len(s)
    for n in range(len(a)):
        mad = np.abs(a[n][keep] - s[n][keep]).mean()
        assert mad <= 2, (n, mad)


def test_zero_motion_animated_section_matches_the_still_under_a_push(tmp_path):  # FR-12, AC-2.1
    """A plan with no motion, parallax or effect goes through the animated path (OpenCV canvas
    and camera warp) and must look like the still section (FFmpeg zoompan) frame by frame."""
    from tests import animation_helpers as ah
    from tests.helpers import MINI_BOOK
    from tests.render_helpers import make_world
    from video_pipeline import render_episode as re_
    from video_pipeline.timeline import CENTRE, Camera

    with pytest.MonkeyPatch.context() as mp:
        world = make_world(tmp_path, mp, MINI_BOOK)
    ah.write_plan(world["folder"], 2, layers=[ah.layer(motions=[], depth=0.0)])
    ah.cut(world["folder"], pages=[2])
    inputs = re_._load_inputs(world["folder"], world["root"])
    el = inputs.eligibility["page-02"]
    assert el.animated
    section = next(s for s in inputs.sections if s.name == "page-02")
    push = Camera("narration", section.frames, CENTRE, (1.3, 0.45, 0.55))
    section = dataclasses.replace(section, camera=push)
    picture = inputs.pictures.files[section.picture]
    cfg = inputs.cfg
    work = re_._SectionWork(
        re_.VideoPaths(tmp_path), cfg, world["folder"], world["slug"], run_ffmpeg, None, False, None
    )
    anim = re_._load_animation(work, section, el, picture)[0]
    assert anim is not None
    clips = {}
    for name, a in (("animated", anim), ("still", None)):
        clips[name] = tmp_path / f"{name}.mp4"
        re_._render_section(
            section, picture, clips[name], tmp_path / "work" / name, cfg, run_ffmpeg,
            re_.run_ffmpeg_frames, a,
        )  # fmt: skip
    moving, still = _decode_rgb(clips["animated"]), _decode_rgb(clips["still"])
    assert len(moving) == len(still) == section.frames
    assert np.abs(still[-1] - still[0]).mean() > 1  # the push really moves the picture
    for n in range(0, section.frames, max(1, section.frames // 12)):
        mad = np.abs(moving[n] - still[n]).mean()
        assert mad <= 3, (n, mad)
