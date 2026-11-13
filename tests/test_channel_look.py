"""Landscape pictures, lettered cover, channel intro and camera variety (feedback round 1)."""

from __future__ import annotations

import pytest
from PIL import Image

from tests.helpers import ROOT
from video_pipeline import illustrate
from video_pipeline.cover_design import DEFAULT_PALETTE, compose_cover
from video_pipeline.episode import plan_episode
from video_pipeline.frames import page_turn_sound
from video_pipeline.render_episode import channel_intro_command
from video_pipeline.timeline import load_render_config


@pytest.fixture
def cfg():
    return load_render_config(ROOT / "config" / "render.json")


def test_landscape_picture_is_cropped_to_16_by_9(tmp_path):
    path = tmp_path / "p.png"
    Image.new("RGB", (1536, 1024), (10, 20, 30)).save(path)
    illustrate.crop_to_landscape(path)
    with Image.open(path) as im:
        assert im.size == (1536, 864)


def test_square_picture_is_refused(tmp_path):
    path = tmp_path / "p.png"
    Image.new("RGB", (1024, 1024)).save(path)
    with pytest.raises(illustrate.IllustrateError, match="not landscape"):
        illustrate.crop_to_landscape(path)


def test_prompt_asks_for_landscape():
    assert "landscape" in illustrate.STYLE_LINE and "Square" not in illustrate.STYLE_LINE


def test_cover_has_lettering_over_the_picture(tmp_path, cfg):
    src, out = tmp_path / "c.png", tmp_path / "out.png"
    Image.new("RGB", (1536, 864), (120, 160, 90)).save(src)
    compose_cover(src, "The Moon Guardian", "A Sleepy Tale", DEFAULT_PALETTE, None, cfg, out)
    with Image.open(out) as im:
        assert im.size == (cfg.width, cfg.height)
        top = im.crop((0, 0, cfg.width, cfg.height // 3))
        assert top.getextrema() != ((120, 120), (160, 160), (90, 90))  # lettering was drawn
        assert max(top.getdata(), key=sum)[0] > 200  # cream lettering


def test_page_turn_is_not_a_plain_noise_burst():
    sr = 24000
    a = page_turn_sound(sr)
    third = len(a) // 7
    assert abs(a[:third]).max() < abs(a).max()  # starts softly, builds to the swish


def test_channel_intro_is_conformed_to_the_episode_format(cfg):
    cmd = channel_intro_command(ROOT / "i.mp4", ROOT / "o.mp4", cfg)
    graph = cmd[cmd.index("-vf") + 1]
    assert f"{cfg.width}:{cfg.height}" in graph and "-an" not in cmd
    assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-ac") + 1] == "2"


def test_blueprint_books_move_the_camera_on_every_page():
    from tests.test_blueprint_consumers import book20  # noqa: F401
    from video_pipeline.blueprint import load_blueprint, load_context

    book = {"pages": [{} for _ in range(10)]}
    bp = load_blueprint(next((ROOT / "books").glob("*/blueprint.json")), load_context(ROOT))
    motions = plan_episode(book, 3, bp).motions
    assert "static" not in motions
    assert all(a != b for a, b in zip(motions, motions[1:], strict=False))
