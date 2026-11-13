"""Spec 015 Story 1: the storybook video cover (title plate, palette, logo badge)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageOps

from tests.helpers import ROOT
from video_pipeline import render_episode
from video_pipeline.cover_design import (
    CREAM,
    DEFAULT_PALETTE,
    CoverPalette,
    compose_cover,
    contrast_ratio,
    cover_palette,
)
from video_pipeline.frames import PictureError
from video_pipeline.timeline import load_render_config

W, H = 960, 540


@pytest.fixture
def cfg():
    return dataclasses.replace(
        load_render_config(ROOT / "config" / "render.json"), width=W, height=H
    )


@pytest.fixture
def picture(tmp_path) -> Path:
    """A busy picture (gradient + noise) so untouched pixels are easy to tell apart."""
    rng = np.random.default_rng(1)
    y, x = np.mgrid[0:864, 0:1536]
    arr = np.stack([x % 256, y % 256, (x + y) % 256], axis=-1).astype(np.int16)
    arr = np.clip(arr + rng.integers(-20, 20, arr.shape), 0, 255).astype(np.uint8)
    path = tmp_path / "cover.png"
    Image.fromarray(arr, "RGB").save(path)
    return path


@pytest.fixture
def logo(tmp_path) -> Path:
    path = tmp_path / "logo.png"
    im = Image.new("RGBA", (1256, 733), (0, 0, 0, 0))
    im.paste((240, 140, 40, 255), (100, 100, 1156, 633))
    im.save(path)
    return path


def bp(*hexes: str) -> dict:
    return {"art": {"palette": [{"name": f"c{i}", "hex": h} for i, h in enumerate(hexes)]}}


def rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))


# --- palette ---------------------------------------------------------------------------------


def test_contrast_ratio_matches_wcag():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert contrast_ratio((10, 10, 10), (10, 10, 10)) == pytest.approx(1.0)
    assert contrast_ratio((255, 255, 255), (0, 0, 0)) == contrast_ratio((0, 0, 0), (255, 255, 255))


def test_palette_by_luminance():  # AC-1.1
    p = cover_palette(bp("#382E50", "#F3AE78", "#7DBBB0", "#F9E8B7", "#C85675"))
    assert p.fill == rgb("#F9E8B7")  # lightest
    assert p.outline == rgb("#382E50") and p.plate == rgb("#382E50")  # darkest
    assert p.accent == rgb("#C85675")  # most saturated of the rest
    assert contrast_ratio(p.fill, p.outline) >= 4.5


def test_cream_when_the_lightest_colour_is_too_dark_to_read():  # AC-1.1
    p = cover_palette(bp("#3A3A80", "#505090", "#6A5AA0", "#7070B0", "#4A4A70"))
    assert p.fill == CREAM
    assert contrast_ratio(p.fill, p.outline) >= 4.5


@pytest.mark.parametrize(
    "hexes",
    [
        ("#FFFFFF", "#F0F0F0", "#EEEEEE", "#DDDDDD", "#CCCCCC"),  # all light: outline darkened
        ("#808080", "#808080", "#808080", "#808080", "#808080"),
        ("#102A43", "#486FA8", "#B8A1D9", "#F2F6FF", "#DAA6BC"),
        ("#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#00FFFF"),
    ],
)
def test_contrast_at_least_4_5_for_any_palette(hexes):  # FR-1
    p = cover_palette(bp(*hexes))
    assert contrast_ratio(p.fill, p.outline) >= 4.5


def test_real_blueprints_give_readable_palettes():  # FR-1
    paths = sorted((ROOT / "books").glob("*/blueprint.json"))
    assert paths
    for path in paths:
        p = cover_palette(json.loads(path.read_text(encoding="utf-8")))
        assert contrast_ratio(p.fill, p.outline) >= 4.5, path


@pytest.mark.parametrize(
    "blueprint",
    [
        None,
        {},
        {"art": {}},
        {"art": {"palette": "red"}},
        {"art": {"palette": []}},
        {"art": {"palette": [{"name": "x", "hex": "nope"}]}},
        "garbage",
    ],
)
def test_default_palette_when_missing_or_malformed(blueprint):  # AC-1.2
    assert cover_palette(blueprint) == DEFAULT_PALETTE
    assert isinstance(DEFAULT_PALETTE, CoverPalette)
    assert contrast_ratio(DEFAULT_PALETTE.fill, DEFAULT_PALETTE.outline) >= 4.5


def test_accent_is_warm_gold_when_nothing_is_saturated():
    p = cover_palette(bp("#000000", "#FFFFFF", "#808080"))
    assert p.accent == DEFAULT_PALETTE.accent


# --- layout ----------------------------------------------------------------------------------


def _inside(box, outer) -> bool:
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


@pytest.mark.parametrize(
    "title",
    [
        "Pip Dinosaur",
        "The Snail Mail Express",
        "The Night the Lighthouse Hiccupped Very Loudly",
        "Captain Kind and the Great World Wobble of the Very Long Night",
    ],
)
def test_title_fits_the_plate_in_three_lines(cfg, picture, logo, tmp_path, title):  # AC-1.3/1.4
    out = tmp_path / "out.png"
    layout = compose_cover(picture, title, "A Little Story", DEFAULT_PALETTE, logo, cfg, out)
    assert 1 <= len(layout.title_lines) <= 3
    frame = (0, 0, W, H)
    assert _inside(layout.plate, frame)
    for box in layout.title_lines:
        assert _inside(box, layout.plate), (box, layout.plate)
    assert layout.subtitle is not None and _inside(layout.subtitle, layout.plate)
    assert layout.subtitle[1] >= max(b[3] for b in layout.title_lines) - 2  # under the title
    assert layout.plate[3] <= 0.6 * H  # top part: the picture stays visible below
    with Image.open(out) as im:
        assert im.size == (W, H) and im.mode == "RGB"


def test_badge_bottom_right_and_small(cfg, picture, logo, tmp_path):  # AC-1.6
    layout = compose_cover(picture, "A Title", "", DEFAULT_PALETTE, logo, cfg, tmp_path / "o.png")
    x0, y0, x1, y1 = layout.badge
    assert x0 >= 0.82 * W and x1 <= W and y1 <= H and y0 >= 0.6 * H
    assert x1 - x0 <= 0.18 * W
    assert layout.subtitle is None


def test_no_badge_without_logo(cfg, picture, tmp_path):  # AC-1.8
    layout = compose_cover(
        picture, "A Title", "Sub", DEFAULT_PALETTE, None, cfg, tmp_path / "o.png"
    )
    assert layout.badge is None


def test_bottom_band_is_the_plain_picture(cfg, picture, logo, tmp_path):  # FR-2
    out = tmp_path / "o.png"
    layout = compose_cover(
        picture, "The Snail Mail Express", "A Story", DEFAULT_PALETTE, logo, cfg, out
    )
    with Image.open(picture) as im:
        plain = np.asarray(ImageOps.fit(im.convert("RGB"), (W, H), Image.Resampling.LANCZOS))
    with Image.open(out) as im:
        got = np.asarray(im.convert("RGB"))
    band = slice(round(H * 0.75), H)
    mask = np.ones((H, W), dtype=bool)
    mask[: band.start] = False
    bx0, by0, bx1, by1 = layout.badge
    mask[by0:by1, bx0:bx1] = False
    assert np.array_equal(got[mask], plain[mask])


def test_cover_is_drawn_over_the_picture(cfg, picture, tmp_path):
    out = tmp_path / "o.png"
    layout = compose_cover(picture, "Moon", "Sub", DEFAULT_PALETTE, None, cfg, out)
    with Image.open(out) as im:
        x0, y0, x1, y1 = layout.title_lines[0]
        region = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1))).reshape(-1, 3)
    fill = np.array(DEFAULT_PALETTE.fill)
    assert (np.abs(region - fill).sum(axis=1) < 30).any()  # light lettering was drawn


def test_unreadable_picture(cfg, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("nope")
    with pytest.raises(PictureError):
        compose_cover(bad, "T", "", DEFAULT_PALETTE, None, cfg, tmp_path / "o.png")


# --- render wiring ---------------------------------------------------------------------------


def test_cover_key_changes_with_palette_and_logo():  # AC-1.7, FR-3
    other = dataclasses.replace(DEFAULT_PALETTE, accent=(1, 2, 3))
    base = render_episode.cover_key("sha", ["T", "S"], DEFAULT_PALETTE, "", (W, H))
    assert base == render_episode.cover_key("sha", ["T", "S"], DEFAULT_PALETTE, "", (W, H))
    assert base != render_episode.cover_key("sha", ["T", "S"], other, "", (W, H))
    assert base != render_episode.cover_key("sha", ["T", "S"], DEFAULT_PALETTE, "abc", (W, H))
    assert base != render_episode.cover_key("sha", ["T", "X"], DEFAULT_PALETTE, "", (W, H))


def test_render_config_has_the_channel_logo():  # FR-4
    cfg = load_render_config(ROOT / "config" / "render.json")
    assert cfg.channel_logo and (ROOT / cfg.channel_logo).is_file()
