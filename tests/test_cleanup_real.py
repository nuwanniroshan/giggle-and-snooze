"""Opt-in real-model test for spec 026: the real SAM and LaMa remove a painted blob (no Codex)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from video_pipeline import cleanup, cut_layers
from video_pipeline.cut_layers import ModelsMissing
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.motion_plan import Layer

PILOT = "the-snail-mail-express"
MAGENTA = np.array([255, 0, 255])
CENTRE = (300, 300)
RADIUS = 10


@pytest.mark.models
def test_real_models_remove_a_painted_blob_and_leave_the_rest_alone(tmp_path: Path) -> None:
    source = PROJECT_ROOT / "books" / PILOT / "illustrations" / "page_01.png"
    if not source.is_file():
        pytest.skip(f"{source} is not available")
    try:
        segmenter, fill = cut_layers.load_real_models(
            PROJECT_ROOT, cut_layers.read_config(PROJECT_ROOT)["models"]
        )
    except ModelsMissing as exc:
        pytest.skip(f"real models not downloaded: {exc}")
    picture = tmp_path / "page_01.png"
    shutil.copy(source, picture)
    cx, cy = CENTRE
    with Image.open(picture) as im:
        painted = im.convert("RGB")
    ImageDraw.Draw(painted).ellipse(
        (cx - RADIUS, cy - RADIUS, cx + RADIUS, cy + RADIUS), fill=tuple(int(v) for v in MAGENTA)
    )
    rgb = np.asarray(painted)
    box = (cx - 15, cy - 15, cx + 15, cy + 15)
    region = {"box": list(box), "include_points": [[cx, cy]], "reason": "magenta blob"}
    probe = Layer(
        name="artifact_0",
        box=box,
        include_points=[(cx, cy)],
        z=1,
        depth=0.5,
        pivot=((box[0] + box[2]) // 2, (box[1] + box[3]) // 2),
    )
    settings = cleanup.load_settings(PROJECT_ROOT / "config")

    result = cleanup.apply_regions(rgb, [region], segmenter.masks(rgb, [probe]), fill, settings)

    assert [a["index"] for a in result.applied] == [0], result.skipped
    assert result.hole[cy, cx]
    assert (result.image[~result.hole] == rgb[~result.hole]).all()
    centre = result.image[cy, cx]
    assert not (centre == rgb[cy, cx]).all()
    assert int(np.abs(centre.astype(int) - MAGENTA).sum()) > 150
