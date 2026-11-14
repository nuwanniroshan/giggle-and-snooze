"""Phase 0 of spec 025: contracts for the grounded camera target and caption band (pure)."""

import numpy as np
import pytest

from tests.helpers import MINI_BOOK, make_root
from video_pipeline import camera_ground as cg

CANVAS = (1920, 1080)
SETTINGS = cg.CameraSettings(
    zoom_min=1.0,
    zoom_max=2.0,
    zoom_margin=0.15,
    band_fraction=0.25,
    min_mask_share=0.002,
    max_subject_share=0.6,
)


@pytest.fixture
def folder_factory():
    """Build a BookFolder over `<tmp_path>/books/x` holding the mini book's book.json."""

    def make(tmp_path):
        return make_root(tmp_path, MINI_BOOK, slug="x")

    return make


def test_target_centres_on_box_and_keeps_it_in_frame():
    t = cg.target_for_box((400, 300, 600, 500), (1672, 941), CANVAS, SETTINGS)
    assert t["x"] == pytest.approx(500 / 1672, abs=1e-3)
    assert t["y"] == pytest.approx(400 / 941, abs=1e-3)
    assert 1.0 <= t["zoom"] <= 2.0
    # box width on canvas * (1 + margin) fits in the crop of width W / zoom
    box_w = 200 * 1920 / 1672
    assert box_w * 1.15 <= 1920 / t["zoom"] + 1e-6


def test_large_box_reduces_zoom_to_one():
    t = cg.target_for_box((0, 0, 1672, 941), (1672, 941), CANVAS, SETTINGS)
    assert t["zoom"] == 1.0


def test_square_picture_maps_through_contain():
    t = cg.target_for_box((0, 0, 100, 100), (1000, 1000), CANVAS, SETTINGS)
    assert t["x"] == pytest.approx(0.05, abs=1e-3) and t["y"] == pytest.approx(0.05, abs=1e-3)


def rows(top=0, bottom=0, h=941):
    r = np.zeros(h, np.int64)
    r[: int(h * 0.2)] = top
    r[-int(h * 0.2) :] = bottom
    return r


def test_band_prefers_less_covered_edge():
    assert cg.band_for_rows(rows(top=0, bottom=500), (1672, 941), CANVAS, SETTINGS) == "top"
    assert cg.band_for_rows(rows(top=500, bottom=0), (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_band_ties_and_empty_masks_are_bottom():
    assert cg.band_for_rows(rows(300, 300), (1672, 941), CANVAS, SETTINGS) == "bottom"
    assert cg.band_for_rows(np.zeros(941, np.int64), (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_tiny_masks_are_ignored():
    r = rows(top=0, bottom=1)  # far below min_mask_share of 1672*941 pixels
    assert cg.band_for_rows(r, (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_problems_rejects_bad_files():
    good = {
        "page_number": 2,
        "picture": {"sha256": "a", "width": 10, "height": 10},
        "status": "ok",
        "subject": {"name": "car", "source": "layer", "box": [1, 1, 5, 5]},
        "target": {"x": 0.5, "y": 0.5, "zoom": 1.3},
        "band": "top",
    }
    assert cg.problems(good) == []
    assert cg.problems({**good, "band": "left"})
    assert cg.problems({**good, "target": {"x": 1.5, "y": 0.5, "zoom": 1.3}})
    assert cg.problems({**good, "target": {"x": 0.5, "y": 0.5, "zoom": 2.5}})


def test_load_current_reasons(tmp_path, folder_factory):
    folder = folder_factory(tmp_path)
    data, reason = cg.load_current(folder, 2, "sha")
    assert data is None and "ground_camera" in reason
