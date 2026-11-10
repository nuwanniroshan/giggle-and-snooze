from pathlib import Path

import pytest
from PIL import Image

from video_pipeline.illustrate import (
    IllustrateError,
    build_image_command,
    check_image,
    illustrate,
    plan_images,
)

BOOK = {
    "metadata": {"title": "Plops", "blurb": "Mina keeps rain."},
    "pages": [
        {"page_number": 2, "prompt": "two", "illustration_file": "page_02.png"},
        {"page_number": 1, "prompt": "one", "illustration_file": "page_01.png"},
    ],
}


def test_plan_orders_pages_then_covers():
    assert [s for s, _ in plan_images(BOOK)] == ["page_01", "page_02", "cover_front", "cover_back"]


def test_command_attaches_reference_and_prompt():
    cmd = build_image_command(Path("codex"), "a prompt", Path("ref.png"))
    assert cmd[cmd.index("-i") + 1] == "ref.png"
    assert "a prompt" in cmd[-1] and "./out.png" in cmd[-1]
    assert "-i" not in build_image_command(Path("codex"), "p", None)


def test_illustrate_skips_existing_and_references_first_image(tmp_path):
    (tmp_path / "page_01.png").write_bytes(b"x")
    calls = []
    done = illustrate(
        BOOK,
        tmp_path,
        Path("codex"),
        paint=lambda b, p, r, t: calls.append((p[:3], r, t.stem)),
        log=lambda _: None,
    )
    assert done == ["page_02", "cover_front", "cover_back"]
    assert all(ref == tmp_path / "page_01.png" for _, ref, _ in calls)


def test_check_image_rejects_junk_and_small(tmp_path):
    (tmp_path / "bad.png").write_bytes(b"nope")
    with pytest.raises(IllustrateError):
        check_image(tmp_path / "bad.png")
    Image.new("RGB", (100, 100)).save(tmp_path / "small.png")
    with pytest.raises(IllustrateError, match="too small"):
        check_image(tmp_path / "small.png")
