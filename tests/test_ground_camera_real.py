"""Opt-in real-model test for spec 025: a phrase subject is segmented by the real SAM model."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from video_pipeline import cut_layers
from video_pipeline.camera_ground import load_settings, target_for_box
from video_pipeline.cut_layers import ModelsMissing, mask_bbox
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.motion_plan import Layer

PILOT = "the-snail-mail-express"
CANVAS = (1920, 1080)


@pytest.mark.models
def test_real_phrase_subject_mask_contains_box_centre_target() -> None:
    picture = PROJECT_ROOT / "books" / PILOT / "illustrations" / "page_01.png"
    if not picture.is_file():
        pytest.skip(f"{picture} is not available")
    try:
        segmenter = cut_layers.load_real_segmenter(
            PROJECT_ROOT, cut_layers.read_config(PROJECT_ROOT)["models"]
        )
    except ModelsMissing as exc:
        pytest.skip(f"real models not downloaded: {exc}")
    settings = load_settings(PROJECT_ROOT / "config")
    with Image.open(picture) as im:
        rgb = np.asarray(im.convert("RGB"))
    height, width = rgb.shape[:2]
    x0, y0, x1, y1 = 252, 215, 377, 386  # the hair box from the pilot's page 1 plan
    probe = Layer(
        name="subject",
        box=(x0, y0, x1, y1),
        z=1,
        depth=0.5,
        pivot=((x0 + x1) // 2, (y0 + y1) // 2),
    )
    mask = np.asarray(segmenter.masks(rgb, [probe])[0], bool)

    assert mask.any()
    assert mask.sum() / (width * height) < settings.max_subject_share
    box = mask_bbox(mask)
    assert box is not None
    target = target_for_box(box, (width, height), CANVAS, settings)
    cx, cy = target["x"] * width, target["y"] * height
    bx0, by0, bx1, by1 = box
    assert bx0 <= cx <= bx1
    assert by0 <= cy <= by1
