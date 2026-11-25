"""Pure page animator (spec 010 Phase 2): ease, seeds, layer matrices, compositor, effects,
camera crop and canvas frames. Tiny pictures only; no FFmpeg."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from video_pipeline import animate
from video_pipeline.animate import (
    EFFECT_TYPES,
    AnimationError,
    PageRenderer,
    camera_matrix,
    canvas_frames,
    ease,
    layer_matrix,
    load_page_animation,
    picture_frames,
    render_frame,
    seed_for,
    zoompan_crop,
)
from video_pipeline.motion_plan import EFFECTS, Layer, MotionPlan
from video_pipeline.timeline import AnimationSettings, Camera, EffectDefaults, camera_at

W, H = 64, 48
SHORT = min(W, H)
FPS = 10
FRAMES = 40
RED = (220, 20, 20)
BLUE = (20, 40, 220)

# effect defaults sized for a 48 px picture (sizes are px at 1024 and scale by side/1024)
TEST_EFFECTS = {
    name: EffectDefaults(count=60, speed=0.5, size=300.0, strength=0.9, colour="#ffffff")
    for name in EFFECTS
}
TEST_EFFECTS["water_ripple"] = EffectDefaults(count=0, speed=0.5, size=300.0, strength=60.0)
SETTINGS = AnimationSettings(
    ease_in_s=1.0, parallax_max_shift=0.05, parallax_period_s=4.0, effects=TEST_EFFECTS
)


# --- fixtures -----------------------------------------------------------------------------


def _noise(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)


def _layer(name: str, box, colour, z: int, **over) -> dict:
    x0, y0, x1, y1 = box
    data = {
        "name": name,
        "box": list(box),
        "z": z,
        "depth": 0.0,
        "pivot": [(x0 + x1) // 2, (y0 + y1) // 2],
        "motions": [],
        "_colour": colour,
    }
    data.update(over)
    return data


def make_page(
    tmp_path: Path, layers: list[dict], effects: list[dict] | None = None, empty=()
) -> tuple[MotionPlan, Path, Path]:
    """Picture = noise with solid squares painted in z order; background = the noise (so it equals
    the picture outside the layer boxes); one solid RGBA PNG per layer."""
    folder = tmp_path / "layers"
    folder.mkdir(exist_ok=True)
    noise = _noise()
    picture = noise.copy()
    texture = _noise(1)  # a layer with colour None is cut from this texture
    for spec in sorted(layers, key=lambda s: s["z"]):
        x0, y0, x1, y1 = spec["box"]
        fill = spec["_colour"]
        picture[y0:y1, x0:x1] = texture[y0:y1, x0:x1] if fill is None else fill
    pic_path = tmp_path / "page_01.png"
    Image.fromarray(picture).save(pic_path)
    Image.fromarray(noise).save(folder / "background.png")
    entries = []
    for spec in layers:
        x0, y0, x1, y1 = spec["box"]
        if spec["name"] in empty:
            entries.append(
                {"name": spec["name"], "file": None, "offset": None, "size": None, "empty": True}
            )
            continue
        rgba = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        fill = spec["_colour"]
        rgba[..., :3] = texture[y0:y1, x0:x1] if fill is None else fill
        rgba[..., 3] = 255
        Image.fromarray(rgba, "RGBA").save(folder / f"{spec['name']}.png")
        entries.append(
            {
                "name": spec["name"],
                "file": f"{spec['name']}.png",
                "offset": [x0, y0],
                "size": [x1 - x0, y1 - y0],
                "empty": False,
            }
        )
    (folder / "layers.json").write_text(
        json.dumps({"background": "background.png", "layers": entries}), encoding="utf-8"
    )
    plan = MotionPlan.model_validate(
        {
            "page_number": 1,
            "picture": {"file": "page_01.png", "sha256": "0" * 64, "width": W, "height": H},
            "layers": [{k: v for k, v in s.items() if k != "_colour"} for s in layers],
            "effects": effects or [],
        }
    )
    return plan, folder, pic_path


def load(tmp_path, layers, effects=None, *, settings=SETTINGS, scale=1.0, frames=FRAMES, **kw):
    plan, folder, pic = make_page(tmp_path, layers, effects, **kw)
    return load_page_animation(plan, folder, pic, settings, scale, "book", 1, FPS, frames)


def picture_of(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def plan_layer(**over) -> Layer:
    data = {"name": "a", "box": [10, 10, 30, 30], "z": 1, "depth": 0.0, "pivot": [20, 20]}
    data.update(over)
    return Layer.model_validate(data)


def apply(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    out = m @ np.array([x, y, 1.0])
    return float(out[0]), float(out[1])


PARALLAX = (0.05, 4.0)
NO_PARALLAX = (0.0, 4.0)


# --- ease and seeds -----------------------------------------------------------------------


def test_ease_bounds():
    assert ease(0.0, 1.0) == 0.0
    assert ease(1.0, 1.0) == 1.0
    assert ease(5.0, 1.0) == 1.0
    assert 0.0 < ease(0.3, 1.0) < ease(0.6, 1.0) < 1.0
    assert ease(0.0, 0.0) == 1.0  # no ease-in: full motion at once


def test_seed_is_stable_and_specific():
    digest = hashlib.sha256(b"book:3:1:rain").digest()
    assert seed_for("book", 3, 1, "rain") == int.from_bytes(digest[:8], "big")
    assert seed_for("book", 3, 1, "rain") != seed_for("book", 3, 2, "rain")
    assert seed_for("book", 3, 1, "rain") != seed_for("book", 3, 1, "snow")


# --- layer matrices -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "motion",
    [
        {"type": "sway", "amplitude": 4.0, "period_s": 4.0},
        {"type": "rotate", "amplitude": 4.0, "period_s": 4.0, "phase": 0.3},
        {"type": "bob", "amplitude": 0.05, "period_s": 4.0},
        {"type": "shift", "amplitude": 0.05, "period_s": 4.0, "direction": [1, 1]},
        {"type": "breathe", "amplitude": 0.05, "period_s": 4.0},
    ],
)
def test_every_motion_is_identity_at_rest(motion):
    lay = plan_layer(depth=1.0, motions=[motion])
    m = layer_matrix(lay, 1.0, SHORT, 0.0, 1.0, PARALLAX)
    np.testing.assert_allclose(m, [[1, 0, 0], [0, 1, 0]], atol=1e-12)


def _angle_of(m, pivot, point) -> float:
    px, py = pivot
    x, y = apply(m, *point)
    return math.degrees(math.atan2(y - py, x - px) - math.atan2(point[1] - py, point[0] - px))


def test_sway_rotates_around_the_pivot_by_amplitude_at_quarter_period():
    lay = plan_layer(motions=[{"type": "sway", "amplitude": 4.0, "period_s": 4.0}])
    m = layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX)  # t/P = 0.25 → sin = 1
    assert apply(m, 20, 20) == pytest.approx((20, 20))
    assert abs(_angle_of(m, (20, 20), (30, 20))) == pytest.approx(4.0)
    back = layer_matrix(lay, 3.0, SHORT, 1.0, 1.0, NO_PARALLAX)  # sin = −1
    assert _angle_of(back, (20, 20), (30, 20)) == pytest.approx(-_angle_of(m, (20, 20), (30, 20)))


def test_rotate_is_one_sided():
    lay = plan_layer(motions=[{"type": "rotate", "amplitude": 3.0, "period_s": 4.0}])
    sweep = layer_matrix(lay, 2.0, SHORT, 1.0, 1.0, NO_PARALLAX)  # peak of the rocking
    sign = math.copysign(1.0, _angle_of(sweep, (20, 20), (30, 20)))
    angles = [
        sign * _angle_of(layer_matrix(lay, t, SHORT, 1.0, 1.0, NO_PARALLAX), (20, 20), (30, 20))
        for t in np.linspace(0, 8, 33)
    ]
    assert min(angles) >= -1e-9
    assert max(angles) == pytest.approx(3.0)


def test_bob_moves_vertically_by_amplitude_times_short_side():
    lay = plan_layer(motions=[{"type": "bob", "amplitude": 0.1, "period_s": 4.0}])
    m = layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX)
    assert apply(m, 5, 5) == pytest.approx((5, 5 + 0.1 * SHORT))


def test_shift_moves_along_the_normalised_direction():
    lay = plan_layer(
        motions=[{"type": "shift", "amplitude": 0.1, "period_s": 4.0, "direction": [1, -1]}]
    )
    m = layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX)
    d = 0.1 * SHORT / math.sqrt(2)
    assert apply(m, 5, 5) == pytest.approx((5 + d, 5 - d))


def test_breathe_scales_around_the_pivot():
    lay = plan_layer(motions=[{"type": "breathe", "amplitude": 0.1, "period_s": 4.0}])
    m = layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX)
    assert apply(m, 20, 20) == pytest.approx((20, 20))
    assert apply(m, 30, 20) == pytest.approx((20 + 10 * 1.1, 20))


def test_motions_combine():
    lay = plan_layer(
        motions=[
            {"type": "bob", "amplitude": 0.1, "period_s": 4.0},
            {"type": "shift", "amplitude": 0.1, "period_s": 4.0, "direction": [1, 0]},
        ]
    )
    m = layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX)
    assert apply(m, 0, 0) == pytest.approx((0.1 * SHORT, 0.1 * SHORT))


def test_scale_and_ease_multiply_the_offsets():
    lay = plan_layer(motions=[{"type": "bob", "amplitude": 0.1, "period_s": 4.0}])
    one = apply(layer_matrix(lay, 1.0, SHORT, 1.0, 1.0, NO_PARALLAX), 0, 0)[1]
    two = apply(layer_matrix(lay, 1.0, SHORT, 1.0, 2.0, NO_PARALLAX), 0, 0)[1]
    half = apply(layer_matrix(lay, 1.0, SHORT, 0.5, 1.0, NO_PARALLAX), 0, 0)[1]
    assert two == pytest.approx(2 * one)
    assert half == pytest.approx(0.5 * one)


def test_parallax_is_proportional_to_depth():
    still = plan_layer(depth=0.0)
    deep = plan_layer(depth=1.0)
    t = PARALLAX[1] / 4  # peak of the periodic drift
    assert apply(layer_matrix(still, t, SHORT, 1.0, 1.0, PARALLAX), 0, 0) == pytest.approx((0, 0))
    x, y = apply(layer_matrix(deep, t, SHORT, 1.0, 1.0, PARALLAX), 0, 0)
    assert (abs(x), y) == pytest.approx((PARALLAX[0] * SHORT, 0))
    x2, _ = apply(layer_matrix(deep, t, SHORT, 1.0, 2.0, PARALLAX), 0, 0)
    assert x2 == pytest.approx(2 * x)


# --- compositor ---------------------------------------------------------------------------


def _moving(name, box, colour, z, amplitude=0.1, **over):
    return _layer(
        name,
        box,
        colour,
        z,
        motions=[{"type": "shift", "amplitude": amplitude, "period_s": 4.0, "direction": [1, 0]}],
        **over,
    )


def test_frame_zero_is_the_original_picture(tmp_path):
    layers = [
        _moving("a", (10, 10, 30, 30), RED, 1, depth=0.5),
        _layer(
            "b",
            (40, 5, 60, 20),
            BLUE,
            2,
            motions=[{"type": "sway", "amplitude": 4.0, "period_s": 3.0}],
        ),
    ]
    effects = [
        {"type": "rain", "intensity": 1.0},
        {"type": "water_ripple", "region": {"box": [0, 30, 64, 48]}, "intensity": 1.0},
    ]
    anim = load(tmp_path, layers, effects)
    np.testing.assert_array_equal(render_frame(anim, 0), picture_of(tmp_path / "page_01.png"))


def test_frames_keep_shape_and_type_when_a_layer_leaves_the_picture(tmp_path):
    anim = load(tmp_path, [_moving("a", (44, 10, 64, 30), RED, 1, amplitude=0.5)])
    for n in (10, 15, 25, 39):
        frame = render_frame(anim, n)
        assert frame.shape == (H, W, 3)
        assert frame.dtype == np.uint8


def test_layer_moves_and_background_shows_behind_it(tmp_path):
    anim = load(tmp_path, [_moving("a", (10, 10, 30, 30), RED, 1, amplitude=0.2)])
    frame = render_frame(anim, 10)  # t = 1 s: e = 1, sin = 1 → +0.2·48 ≈ 9.6 px right
    noise = _noise()
    assert tuple(frame[20, 38]) == RED  # moved into new ground
    np.testing.assert_array_equal(frame[20, 12], noise[20, 12])  # uncovered background


def test_higher_z_wins_where_layers_overlap(tmp_path):
    # red is drawn above blue (z 2 over z 1) even though the picture painted them the same way
    layers = [
        _moving("blue", (10, 10, 30, 30), BLUE, 1, amplitude=0.0),
        _moving("red", (20, 20, 40, 40), RED, 2, amplitude=0.0),
    ]
    anim = load(tmp_path, layers)
    frame = render_frame(anim, 20)
    assert tuple(frame[25, 25]) == RED
    flipped = load(tmp_path, [{**layers[0], "z": 3}, layers[1]])
    assert tuple(render_frame(flipped, 20)[25, 25]) == BLUE


def test_equal_z_keeps_plan_order(tmp_path):
    layers = [
        _moving("blue", (10, 10, 30, 30), BLUE, 1, amplitude=0.0),
        _moving("red", (20, 20, 40, 40), RED, 1, amplitude=0.0),
    ]
    assert tuple(render_frame(load(tmp_path, layers), 20)[25, 25]) == RED


def test_stationary_foreground_stays_above_moving_background(tmp_path):
    layers = [
        _moving("back", (10, 10, 30, 30), BLUE, 1, amplitude=0.15),
        _layer("front", (20, 20, 40, 40), RED, 2),
    ]
    anim = load(tmp_path, layers)
    for frame in (10, 20, 30, 10):
        assert tuple(render_frame(anim, frame)[25, 25]) == RED


def test_empty_layers_are_skipped(tmp_path):
    layers = [_moving("a", (10, 10, 30, 30), RED, 1), _moving("gone", (40, 5, 60, 20), BLUE, 2)]
    anim = load(tmp_path, layers, empty=("gone",))
    assert [ls.layer.name for ls in anim.layers] == ["a"]
    assert render_frame(anim, 15).shape == (H, W, 3)


def test_missing_layer_file_raises(tmp_path):
    plan, folder, pic = make_page(tmp_path, [_moving("a", (10, 10, 30, 30), RED, 1)])
    (folder / "a.png").unlink()
    with pytest.raises(AnimationError, match="a.png"):
        load_page_animation(plan, folder, pic, SETTINGS, 1.0, "book", 1, FPS, FRAMES)


def test_sequential_frames_equal_frames_built_from_scratch(tmp_path):
    layers = [_moving("a", (10, 10, 30, 30), RED, 1, depth=1.0)]
    anim = load(tmp_path, layers, [{"type": "snow", "intensity": 0.7}])
    seq = [f.copy() for f in picture_frames(anim, FRAMES)]
    assert len(seq) == FRAMES
    for n in (0, 7, 23, 39):
        np.testing.assert_array_equal(seq[n], render_frame(anim, n))


def test_scale_zero_like_intensity_keeps_the_original(tmp_path):
    """A page scaled to (almost) nothing barely moves: scale multiplies every amplitude."""
    layers = [_moving("a", (10, 10, 30, 30), RED, 1)]
    calm = load(tmp_path, layers, scale=1e-9)
    np.testing.assert_array_equal(render_frame(calm, 10)[20, 31:40], _noise()[20, 31:40])


def test_two_runs_are_identical(tmp_path):
    layers = [_moving("a", (10, 10, 30, 30), RED, 1, depth=0.5)]
    effects = [{"type": t, "intensity": 0.8} for t in ("rain", "fireflies", "smoke")]
    one = render_frame(load(tmp_path, layers, effects), 37)
    two = render_frame(load(tmp_path, layers, effects), 37)
    np.testing.assert_array_equal(one, two)


def test_background_differing_outside_layers_falls_back_to_full_frame(tmp_path):
    plan, folder, pic = make_page(tmp_path, [_moving("a", (10, 10, 30, 30), RED, 1)])
    bg = _noise()
    bg[40:44, 50:54] = 0  # differs from the picture far from any layer
    Image.fromarray(bg).save(folder / "background.png")
    anim = load_page_animation(plan, folder, pic, SETTINGS, 1.0, "book", 1, FPS, FRAMES)
    assert anim.rects == [(0, 0, W, H)]
    np.testing.assert_array_equal(render_frame(anim, 0), picture_of(pic))
    assert tuple(render_frame(anim, 20)[41, 51]) == (0, 0, 0)


# --- effects ------------------------------------------------------------------------------


def test_registry_covers_exactly_the_effects():
    assert set(EFFECT_TYPES) == set(EFFECTS)


def test_real_config_defaults_cover_every_effect():
    from video_pipeline.library import PROJECT_ROOT
    from video_pipeline.timeline import load_render_config

    cfg = load_render_config(PROJECT_ROOT / "config" / "render.json")
    assert set(cfg.animation.effects) == set(EFFECTS)
    assert cfg.animation.effects["water_ripple"].strength == pytest.approx(4.0)
    assert cfg.animation.effects["rain"].count == 260


BASE_LAYERS = [_moving("a", (6, 6, 20, 20), RED, 1, amplitude=0.0)]
BOX = (30, 10, 60, 40)


def _changed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.any(a != b, axis=2)


@pytest.mark.parametrize("kind", EFFECTS)
def test_box_effect_changes_only_its_box(tmp_path, kind):
    plain = load(tmp_path, BASE_LAYERS)
    eff = load(
        tmp_path, BASE_LAYERS, [{"type": kind, "region": {"box": list(BOX)}, "intensity": 1.0}]
    )
    x0, y0, x1, y1 = BOX
    changed_any = False
    for n in (12, 17, 25, 33):
        diff = _changed(render_frame(plain, n), render_frame(eff, n))
        outside = diff.copy()
        outside[y0:y1, x0:x1] = False
        assert not outside.any(), f"{kind} drew outside its box at frame {n}"
        changed_any |= bool(diff.any())
    assert changed_any, f"{kind} changed nothing"


@pytest.mark.parametrize("kind", EFFECTS)
def test_effect_at_intensity_zero_does_nothing(tmp_path, kind):
    plain = load(tmp_path, BASE_LAYERS)
    eff = load(tmp_path, BASE_LAYERS, [{"type": kind, "intensity": 0.0}])
    for n in (15, 30):
        np.testing.assert_array_equal(render_frame(plain, n), render_frame(eff, n))


@pytest.mark.parametrize("kind", EFFECTS)
def test_whole_picture_effect_changes_pixels(tmp_path, kind):
    plain = load(tmp_path, BASE_LAYERS)
    eff = load(tmp_path, BASE_LAYERS, [{"type": kind, "intensity": 1.0}])
    assert any(
        _changed(render_frame(plain, n), render_frame(eff, n)).any() for n in (12, 20, 28, 36)
    )


@pytest.mark.parametrize("kind", EFFECTS)
def test_layer_effect_follows_the_moved_layer(tmp_path, kind):
    box = (8, 14, 28, 34)
    layers = [_moving("a", box, None, 1, amplitude=0.4)]  # 0.4·48 ≈ 19 px right at t = 1 s
    plain = load(tmp_path, layers)
    eff = load(tmp_path, layers, [{"type": kind, "region": {"layer": "a"}, "intensity": 1.0}])
    lay = plain.layers[0]
    seen = False
    for n in (10, 11, 12):
        diff = _changed(render_frame(plain, n), render_frame(eff, n))
        m = layer_matrix(lay.layer, n / FPS, SHORT, 1.0, 1.0, (0.05, 4.0))
        x0, y0 = apply(m, box[0], box[1])
        x1, y1 = apply(m, box[2], box[3])
        allowed = np.zeros((H, W), bool)
        allowed[max(0, math.floor(y0)) : math.ceil(y1), max(0, math.floor(x0)) : math.ceil(x1)] = (
            True
        )
        assert not (diff & ~allowed).any(), f"{kind} left its layer at frame {n}"
        seen |= bool((diff[:, box[2] :]).any())  # beyond the layer's resting box
    assert seen, f"{kind} did not follow the layer"


def test_effect_on_an_empty_layer_draws_nothing(tmp_path):
    layers = [*BASE_LAYERS, _moving("gone", (40, 5, 60, 20), BLUE, 2)]
    plain = load(tmp_path, layers, empty=("gone",))
    eff = load(
        tmp_path,
        layers,
        [{"type": "glow", "region": {"layer": "gone"}, "intensity": 1.0}],
        empty=("gone",),
    )
    np.testing.assert_array_equal(render_frame(plain, 20), render_frame(eff, 20))


def test_effect_intensity_is_scaled_and_capped(tmp_path):
    anim = load(tmp_path, BASE_LAYERS, [{"type": "rain", "intensity": 0.4}], scale=2.0)
    assert anim.effects[0].intensity == pytest.approx(0.8)
    anim = load(tmp_path, BASE_LAYERS, [{"type": "rain", "intensity": 0.8}], scale=2.0)
    assert anim.effects[0].intensity == pytest.approx(1.0)


@pytest.mark.parametrize("kind", ["rain", "snow", "falling_leaves"])
def test_particle_speed_follows_the_intensity(tmp_path, kind):  # AC-1.3
    slow = load(tmp_path, BASE_LAYERS, [{"type": kind, "intensity": 0.1}]).effects[0]
    fast = load(tmp_path, BASE_LAYERS, [{"type": kind, "intensity": 1.0}]).effects[0]
    # speed × (0.5 + 0.5 × intensity): every slow particle moves less per frame than any fast one
    assert slow.speed.max() < fast.speed.min()


def test_smoke_rise_follows_the_intensity(tmp_path):  # AC-1.3
    slow = load(tmp_path, BASE_LAYERS, [{"type": "smoke", "intensity": 0.1}]).effects[0]
    fast = load(tmp_path, BASE_LAYERS, [{"type": "smoke", "intensity": 1.0}]).effects[0]
    assert slow.rate == pytest.approx(fast.rate * 0.55)


def test_colour_hint_tints_the_effect(tmp_path):
    plain = load(tmp_path, BASE_LAYERS)
    red = load(tmp_path, BASE_LAYERS, [{"type": "glow", "intensity": 1.0, "colour": "#ff0000"}])
    a = render_frame(plain, 20).astype(int)
    b = render_frame(red, 20).astype(int)
    delta = (b - a).reshape(-1, 3).sum(axis=0)
    assert delta[0] > 0 and delta[1] == 0 and delta[2] == 0


def test_settings_without_an_effects_map_use_the_code_defaults(tmp_path):  # FR-8
    anim = load(
        tmp_path,
        BASE_LAYERS,
        [{"type": "rain", "intensity": 1.0}],
        settings=replace(SETTINGS, effects=AnimationSettings().effects),
    )
    assert len(anim.effects) == 1


def test_missing_effect_defaults_raise(tmp_path):
    with pytest.raises(AnimationError, match="rain"):
        load(
            tmp_path,
            BASE_LAYERS,
            [{"type": "rain", "intensity": 1.0}],
            settings=replace(SETTINGS, effects={}),
        )


# --- camera -------------------------------------------------------------------------------


CW, CH, UP = 192, 108, 4


def test_static_camera_matrix_is_identity():
    cam = Camera("static", 30, (1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    assert zoompan_crop(cam, 0, CW, CH, UP) == (0.0, 0.0, CW, CH)
    np.testing.assert_allclose(camera_matrix(cam, 7, CW, CH, UP), [[1, 0, 0], [0, 1, 0]])


def test_push_camera_crop_matches_camera_at():
    cam = Camera("page", 90, (1.0, 0.5, 0.5), (1.2, 0.45, 0.55))
    for frame in (0, 30, 60, 89):
        z, cx, cy = camera_at(cam, frame)
        x, y, w, h = zoompan_crop(cam, frame, CW, CH, UP)
        assert w == pytest.approx(CW / z, abs=1 / UP)
        assert h == pytest.approx(CH / z, abs=1 / UP)
        assert x == pytest.approx(cx * CW - CW / z / 2, abs=2 / UP)
        assert y == pytest.approx(cy * CH - CH / z / 2, abs=2 / UP)
        assert (x * UP) % 2 == 0 and (y * UP) % 2 == 0  # zoompan's even crop origin
        m = camera_matrix(cam, frame, CW, CH, UP)
        # output pixel edges map to the crop's edges (pixel-centre convention)
        assert apply(m, -0.5, -0.5) == pytest.approx((x - 0.5, y - 0.5))
        assert apply(m, CW - 0.5, CH - 0.5) == pytest.approx((x + w - 0.5, y + h - 0.5))


def test_canvas_frames_yield_exact_buffers():
    backdrop = np.full((CH, CW, 3), 7, np.uint8)
    pics = (np.full((H, W, 3), v, np.uint8) for v in range(10, 200))
    cam = Camera("static", 5, (1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    out = list(canvas_frames(pics, backdrop, (144, 108), (24, 0), cam, 5, CW, CH, UP))
    assert len(out) == 5
    assert all(isinstance(b, bytes) and len(b) == CW * CH * 3 for b in out)
    first = np.frombuffer(out[0], np.uint8).reshape(CH, CW, 3)
    assert (first[:, :24] == 7).all() and (first[:, 168:] == 7).all()
    assert (first[:, 24:168] == 10).all()
    assert (np.frombuffer(out[4], np.uint8).reshape(CH, CW, 3)[:, 24:168] == 14).all()


def test_canvas_frames_with_a_moving_camera():
    backdrop = np.zeros((CH, CW, 3), np.uint8)
    pics = (np.full((H, W, 3), 200, np.uint8) for _ in range(3))
    cam = Camera("page", 3, (1.0, 0.5, 0.5), (1.5, 0.5, 0.5))
    out = list(canvas_frames(pics, backdrop, (144, 108), (24, 0), cam, 3, CW, CH, UP))
    first = np.frombuffer(out[0], np.uint8).reshape(CH, CW, 3)
    last = np.frombuffer(out[2], np.uint8).reshape(CH, CW, 3)
    assert (first[:, :24] == 0).all()
    assert (last == 200).all()  # zoomed ×1.5 on the centre: the picture fills the frame


def test_canvas_frames_need_enough_pictures():
    backdrop = np.zeros((CH, CW, 3), np.uint8)
    pics = (np.zeros((H, W, 3), np.uint8) for _ in range(2))
    cam = Camera("static", 5, (1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    with pytest.raises(ValueError, match="frames"):
        list(canvas_frames(pics, backdrop, (144, 108), (24, 0), cam, 5, CW, CH, UP))


def test_renderer_refuses_frames_past_the_section(tmp_path):
    anim = load(tmp_path, BASE_LAYERS)
    with pytest.raises(ValueError):
        PageRenderer(anim).frame(FRAMES)


def test_module_exports_version():
    # 3: stationary foreground layers keep their z order over moving layers.
    assert animate.ANIMATION_VERSION == 3


def test_canvas_frames_paste_the_rest_picture_while_at_rest():
    backdrop = np.zeros((CH, CW, 3), np.uint8)
    pics = (np.full((H, W, 3), 50, np.uint8) for _ in range(3))
    rest = np.full((108, 144, 3), 200, np.uint8)
    cam = Camera("static", 3, (1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    out = list(canvas_frames(pics, backdrop, (144, 108), (24, 0), cam, 3, CW, CH, UP, rest, 1))
    first, second = (np.frombuffer(b, np.uint8).reshape(CH, CW, 3) for b in out[:2])
    assert (first[:, 24:168] == 200).all() and (second[:, 24:168] == 50).all()
    with pytest.raises(ValueError, match="rest picture"):
        list(canvas_frames(iter([]), backdrop, (144, 108), (24, 0), cam, 3, CW, CH, UP, None, 1))


# --- eligibility and animated keys (spec 010 Phase 3: FR-11, FR-13, AC-4.1) ---------------------


def _eligible_world(tmp_path):
    from tests import animation_helpers as ah
    from tests.helpers import make_root

    folder = make_root(tmp_path)
    for n in (1, 2):
        ah.write_picture(folder, n)
    ah.write_plan(folder, 1)
    ah.cut(folder, pages=[1])
    return folder, ah


def _eligibility(folder, n=1, script_page=None, settings=None):
    from video_pipeline.animate import page_eligibility

    return page_eligibility(
        folder,
        {"page_number": n},
        script_page if script_page is not None else {"page_number": n},
        settings or AnimationSettings(),
        folder.root,
    )


def test_eligible_page_is_animated_with_hashes(tmp_path):  # FR-11
    from video_pipeline.cut_layers import MANIFEST, layers_dir
    from video_pipeline.motion_plan import plan_path

    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder, settings=AnimationSettings(intensity=0.8))
    assert el.animated and el.reason == ""
    assert el.plan is not None and el.plan.page_number == 1
    assert el.scale == pytest.approx(0.8)
    assert el.plan_sha == hashlib.sha256(plan_path(folder, 1).read_bytes()).hexdigest()
    manifest = layers_dir(folder, 1) / MANIFEST
    assert el.manifest_sha == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_override_number_scales_the_intensity(tmp_path):  # FR-9
    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(
        folder,
        script_page={"page_number": 1, "animation": 1.5},
        settings=AnimationSettings(intensity=0.5),
    )
    assert el.animated and el.scale == pytest.approx(0.75)


@pytest.mark.parametrize("amplitude", [None, 0.0])
def test_motionless_plan_uses_camera_only(tmp_path, amplitude):
    folder, ah = _eligible_world(tmp_path)
    motions = (
        [] if amplitude is None else [{"type": "sway", "amplitude": amplitude, "period_s": 6.0}]
    )
    ah.write_plan(folder, layers=[ah.layer(depth=0, motions=motions)])
    el = _eligibility(folder)
    assert not el.animated
    assert "camera only" in el.reason


def test_stationary_layer_with_glow_is_animated(tmp_path):
    folder, ah = _eligible_world(tmp_path)
    ah.write_plan(
        folder, layers=[ah.layer(depth=0, motions=[])], effects=[{"type": "glow", "intensity": 0.5}]
    )
    assert _eligibility(folder).animated


def test_parallax_counts_only_when_enabled(tmp_path):
    folder, ah = _eligible_world(tmp_path)
    ah.write_plan(folder, layers=[ah.layer(depth=0.5, motions=[])])
    assert _eligibility(folder).animated
    assert not _eligibility(folder, settings=AnimationSettings(parallax_max_shift=0)).animated


def test_empty_cut_cannot_supply_motion_or_layer_effect(tmp_path):
    folder, ah = _eligible_world(tmp_path)
    ah.write_plan(
        folder,
        layers=[ah.layer(name="nothing")],
        effects=[{"type": "glow", "intensity": 0.5, "region": {"layer": "nothing"}}],
    )
    ah.cut(folder, pages=[1])
    assert not _eligibility(folder).animated


def test_zero_intensity_is_camera_only(tmp_path):
    folder, _ah = _eligible_world(tmp_path)
    assert not _eligibility(folder, settings=AnimationSettings(intensity=0)).animated


def test_animation_off_is_still(tmp_path):
    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder, settings=AnimationSettings(enabled=False))
    assert not el.animated and el.reason == "animation off"


def test_override_false_is_still(tmp_path):  # AC-3.4
    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder, script_page={"page_number": 1, "animation": False})
    assert not el.animated and el.reason == "animation false in the script"


@pytest.mark.parametrize("value", ["high", [1], True, None, {"a": 1}])
def test_invalid_override_is_still_never_raises(tmp_path, value):  # AC-3.5 (studio must not crash)
    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder, script_page={"page_number": 1, "animation": value})
    assert not el.animated
    assert el.reason.startswith("invalid animation value in the script – ")
    assert repr(value) in el.reason


def test_no_plan_names_the_planning_command(tmp_path):  # AC-4.1
    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder, 2)
    assert not el.animated
    assert el.reason.startswith("no plan")
    assert f"python -m video_pipeline.motion_plan --book {folder.slug} --page 2" in el.reason


def test_stale_plan_names_the_planning_command(tmp_path):  # AC-4.1
    folder, ah = _eligible_world(tmp_path)
    ah.write_picture(folder, 1, colour=(0, 200, 0))
    el = _eligibility(folder)
    assert not el.animated and el.reason.startswith("stale plan")
    assert f"python -m video_pipeline.motion_plan --book {folder.slug} --page 1" in el.reason


def test_invalid_plan_names_the_planning_command(tmp_path):  # AC-4.1
    from video_pipeline.motion_plan import plan_path

    folder, _ah = _eligible_world(tmp_path)
    plan_path(folder, 1).write_text('{"page_number": 1}', encoding="utf-8")
    el = _eligibility(folder)
    assert not el.animated and el.reason.startswith("invalid plan")
    assert f"python -m video_pipeline.motion_plan --book {folder.slug} --page 1" in el.reason


def test_no_picture_is_still(tmp_path):
    folder, _ah = _eligible_world(tmp_path)
    (folder.illustrations / "page_01.png").unlink()
    el = _eligibility(folder)
    assert not el.animated and "page_01.png" in el.reason


def test_uncut_page_names_the_cut_command(tmp_path):  # AC-4.1
    folder, ah = _eligible_world(tmp_path)
    ah.write_plan(folder, 2)
    el = _eligibility(folder, 2)
    assert not el.animated and el.reason.startswith("needs cut")
    assert f"python -m video_pipeline.cut_layers --book {folder.slug}" in el.reason


def test_mismatched_cut_names_the_cut_command(tmp_path):  # AC-4.1
    folder, ah = _eligible_world(tmp_path)
    ah.write_plan(folder, 1, layers=[ah.layer(box=[10, 10, 30, 30])])
    el = _eligibility(folder)
    assert not el.animated and el.reason.startswith("needs cut")
    assert "cut_layers" in el.reason


def test_missing_layer_file_names_the_cut_command(tmp_path):  # AC-4.1
    from video_pipeline.cut_layers import layers_dir

    folder, _ah = _eligible_world(tmp_path)
    (layers_dir(folder, 1) / "background.png").unlink()
    el = _eligibility(folder)
    assert not el.animated and "background.png" in el.reason
    assert "cut_layers" in el.reason


def test_unreadable_animation_config_is_still(tmp_path):
    folder, _ah = _eligible_world(tmp_path)
    (folder.root / "config" / "animation.json").write_text("{", encoding="utf-8")
    el = _eligibility(folder)
    assert not el.animated and "animation.json" in el.reason


def test_animated_key_formula(tmp_path):  # Global constraints: animated key
    from dataclasses import asdict

    from video_pipeline.animate import ANIMATION_VERSION, animated_key
    from video_pipeline.timeline import _hash

    folder, _ah = _eligible_world(tmp_path)
    settings = AnimationSettings()
    el = _eligibility(folder, settings=settings)
    expected = _hash(
        {
            "still": "abc",
            "plan": el.plan_sha,
            "manifest": el.manifest_sha,
            "files": [list(f) for f in el.files_sha],
            "settings": asdict(settings),
            "scale": el.scale,
            "animation_version": ANIMATION_VERSION,
        }
    )
    assert animated_key("abc", el, settings) == expected
    assert animated_key("abd", el, settings) != expected
    assert animated_key("abc", replace(el, scale=0.5), settings) != expected
    assert animated_key("abc", el, AnimationSettings(ease_in_s=2.0)) != expected


def test_eligibility_hashes_every_cut_file(tmp_path):  # FR-13 (layer and background contents)
    from video_pipeline.cut_layers import MANIFEST, layers_dir, manifest_files

    folder, _ah = _eligible_world(tmp_path)
    el = _eligibility(folder)
    out = layers_dir(folder, 1)
    manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    names = manifest_files(manifest)
    assert manifest["background"] in names
    assert dict(el.files_sha) == {
        n: hashlib.sha256((out / n).read_bytes()).hexdigest() for n in names
    }


@pytest.mark.parametrize("which", ["layer", "background"])
def test_changed_cut_file_bytes_change_the_animated_key(tmp_path, which):  # FR-13, AC-5.2
    from video_pipeline.animate import animated_key
    from video_pipeline.cut_layers import MANIFEST, layers_dir

    folder, _ah = _eligible_world(tmp_path)
    settings = AnimationSettings()
    before = animated_key("abc", _eligibility(folder, settings=settings), settings)
    out = layers_dir(folder, 1)
    manifest_bytes = (out / MANIFEST).read_bytes()
    manifest = json.loads(manifest_bytes)
    name = (
        manifest["background"]
        if which == "background"
        else next(e["file"] for e in manifest["layers"] if not e.get("empty"))
    )
    with Image.open(out / name) as im:
        changed = im.copy()
    changed.putpixel((0, 0), (1, 2, 3, 255) if changed.mode == "RGBA" else (1, 2, 3))
    changed.save(out / name)
    assert (out / MANIFEST).read_bytes() == manifest_bytes
    after = animated_key("abc", _eligibility(folder, settings=settings), settings)
    assert after != before


def test_animated_canvas_frame_zero_is_the_still_canvas(tmp_path):  # AC-1.4 (review S2)
    from types import SimpleNamespace

    from tests.helpers import ROOT
    from video_pipeline.frames import compose_canvas
    from video_pipeline.render_episode import animated_canvas_frames
    from video_pipeline.timeline import load_render_config

    cfg = replace(load_render_config(ROOT / "config" / "render.json"), width=320, height=180)
    layers = [_moving("a", (6, 6, 20, 20), RED, 1, amplitude=0.2)]
    anim = load(tmp_path, layers, [{"type": "snow", "intensity": 1.0}], frames=3)
    picture = tmp_path / "page_01.png"
    cam = Camera("static", 3, (1.0, 0.5, 0.5), (1.0, 0.5, 0.5))
    section = SimpleNamespace(camera=cam, frames=3)
    frames = list(animated_canvas_frames(anim, picture, section, cfg))
    compose_canvas(picture, cfg, tmp_path / "canvas.png")
    still = picture_of(tmp_path / "canvas.png")
    assert frames[0] == still.tobytes()
    assert frames[1] != frames[0]  # later frames still animate
