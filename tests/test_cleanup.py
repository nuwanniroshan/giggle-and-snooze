"""Artifact cleanup (spec 026): pure region, mask and picture logic, and the cleanup_page stage
with fakes for Codex, the segmenter and the fill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import ROOT, make_root
from video_pipeline import cleanup as cl
from video_pipeline import cleanup_mcp
from video_pipeline.agents import AgentEvent, AgentResult
from video_pipeline.library import BookFolder

SIZE = (160, 90)
W, H = SIZE
BINARY = Path("/fake/codex")
GREEN = (0, 255, 0)
SETTINGS = cl.CleanupSettings(
    max_regions=6,
    max_points=4,
    max_reason_chars=120,
    margin_px=2,
    max_region_share=0.05,
    attempts=3,
    time_limit_s=600.0,
)


def region(box=(10, 10, 30, 30), points=(), reason="extra finger"):
    return {"box": list(box), "include_points": [list(p) for p in points], "reason": reason}


def _box_mask(box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    mask = np.zeros((H, W), bool)
    mask[y0:y1, x0:x1] = True
    return mask


def gradient() -> np.ndarray:
    """A 160x90 picture where every pixel differs from its neighbours (no pure green)."""
    x = np.arange(W)[None, :].repeat(H, 0)
    y = np.arange(H)[:, None].repeat(W, 1)
    return np.stack([x, y * 2, x // 2 + y], -1).astype(np.uint8)


class FakeFill:
    """An inpainter that paints the whole picture solid green."""

    def __init__(self):
        self.calls = 0

    def fill(self, image, mask):
        self.calls += 1
        return np.full_like(np.asarray(image), GREEN, dtype=np.uint8)


class FakeSeg:
    """A mask that is each probe layer's box filled."""

    def __init__(self):
        self.layers: list = []

    def masks(self, image, layers):
        self.layers.extend(layers)
        return [_box_mask(tuple(layer.box)) for layer in layers]


def _server_args(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    return json.loads(next(c for c in command if c.startswith(prefix))[len(prefix) :])


class FakeCodex:
    """Plays the agent: each call saves the next payload through the real region tool, which
    writes the JSON to the `--out` path in the command; `None` (or no payload left) saves
    nothing."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=600, on_event=None, max_refused_saves=5):
        self.commands.append(command)
        tools = cleanup_mcp.build_tools(cleanup_mcp.parse_args(_server_args(command)[2:]))
        payload = self.payloads.pop(0) if self.payloads else None
        events = []
        saved = False
        if payload is not None:
            result = tools.save_artifact_regions(payload)
            saved = bool(result["saved"])
            events.append(AgentEvent("tool-result", tool="save_artifact_regions", data=result))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        reason = "" if saved else "agent exited with status 1"
        return AgentResult(ok=not reason, events=events, log_path=log_path, reason=reason)


@pytest.fixture
def book(tmp_path: Path) -> BookFolder:
    """A tmp book: a 160x90 gradient page_01.png, an episode script, its review mark and the
    voice approval (so the script-untouched checks can compare bytes)."""
    folder = make_root(tmp_path)
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gradient(), "RGB").save(folder.illustrations / "page_01.png")
    folder.episode_dir.mkdir(parents=True, exist_ok=True)
    folder.script_path.write_text(json.dumps({"pages": []}, indent=2), encoding="utf-8")
    folder.reviewed_path.write_bytes(b'{"reviewed": true}')
    status = folder.folder / "build" / "audio" / "status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_bytes(b'{"status": "approved"}')
    return folder


def book_data(folder: BookFolder) -> dict:
    return json.loads(folder.book_json.read_text(encoding="utf-8"))


def picture_of(folder: BookFolder) -> Path:
    return folder.illustrations / "page_01.png"


def script_marks(folder: BookFolder) -> dict[Path, bytes]:
    paths = [
        folder.script_path,
        folder.reviewed_path,
        folder.folder / "build" / "audio" / "status.json",
    ]
    return {path: path.read_bytes() for path in paths}


def pixels(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(folder: BookFolder, codex: FakeCodex, *, loads=None, yes=False, data=None, lines=None):
    def load(root, cfg):
        if loads is not None:
            loads.append(1)
        return FakeSeg(), FakeFill()

    return cl.cleanup_page(
        folder,
        data or book_data(folder),
        1,
        root=ROOT,
        binary=BINARY,
        load_models=load,
        provider=codex,
        yes=yes,
        log=(lines.append if lines is not None else lambda line: None),
    )


def real_settings() -> cl.CleanupSettings:
    return cl.load_settings(ROOT / "config")


# --- pure logic -----------------------------------------------------------------------------


def test_load_settings_reads_the_project_config():
    settings = real_settings()
    assert settings.attempts == 3
    assert settings.max_points == 4
    assert 0 < settings.max_region_share < 1
    assert settings.margin_px > 0 and settings.max_regions > 0


def test_region_problems_accepts_good_and_empty():
    assert cl.region_problems({"regions": []}, SIZE, SETTINGS) == []
    assert cl.region_problems({"regions": [region()]}, SIZE, SETTINGS) == []
    assert cl.region_problems({"regions": [region(points=[(15, 15)] * 4)]}, SIZE, SETTINGS) == []


@pytest.mark.parametrize(
    "bad",
    [
        region(box=(10, 10, 5, 30)),
        region(box=(0, 0, 500, 30)),
        region(reason=""),
        region(reason="x" * 500),
        region(points=[(1, 1)] * 9),
        region(points=[(999, 1)]),
    ],
)
def test_region_problems_lists_every_problem(bad):
    assert cl.region_problems({"regions": [bad]}, SIZE, SETTINGS)


def test_region_problems_lists_all_problems_at_once():
    bad = [region(box=(10, 10, 5, 30)), region(reason="")]
    assert len(cl.region_problems({"regions": bad}, SIZE, SETTINGS)) >= 2


@pytest.mark.parametrize("data", [None, [], {"regions": "x"}, {"boxes": []}])
def test_region_problems_refuses_wrong_shape(data):
    assert cl.region_problems(data, SIZE, SETTINGS)


def test_too_many_regions_refused():
    assert cl.region_problems({"regions": [region()] * 7}, SIZE, SETTINGS)


def test_grow_mask_grows_by_the_margin():
    mask = np.zeros((H, W), bool)
    mask[50, 40] = True
    grown = cl.grow_mask(mask, 2)
    assert grown[mask].all()
    assert (grown == _box_mask((38, 48, 43, 53))).all()


def test_composite_changes_only_inside_hole():
    rgb = np.full((90, 160, 3), 100, np.uint8)
    filled = np.full_like(rgb, 200)
    hole = np.zeros((90, 160), bool)
    hole[10:20, 10:20] = True
    out = cl.composite(rgb, filled, hole)
    assert (out[hole] == 200).all()
    assert (out[~hole] == 100).all()
    assert out.dtype == np.uint8 and out.shape == rgb.shape


def test_apply_regions_skips_oversized_and_empty_but_applies_rest():
    rgb = np.full((90, 160, 3), 100, np.uint8)
    regions = [region((10, 10, 30, 30)), region((0, 0, 160, 90)), region((50, 50, 60, 60))]
    masks = [_box_mask((10, 10, 30, 30)), _box_mask((0, 0, 160, 90)), np.zeros((90, 160), bool)]
    result = cl.apply_regions(rgb, regions, masks, FakeFill(), SETTINGS)
    assert [a["index"] for a in result.applied] == [0]
    assert {s["index"]: s["why"] for s in result.skipped}.keys() == {1, 2}
    assert all(s["why"] for s in result.skipped)
    assert (result.image[~result.hole] == 100).all()
    assert (result.image[result.hole] == GREEN).all()
    assert (result.hole == cl.grow_mask(_box_mask((10, 10, 30, 30)), SETTINGS.margin_px)).all()


def test_apply_regions_with_nothing_kept_never_calls_fill():
    rgb = np.full((90, 160, 3), 100, np.uint8)
    fill = FakeFill()
    result = cl.apply_regions(rgb, [region()], [np.zeros((90, 160), bool)], fill, SETTINGS)
    assert result.applied == [] and len(result.skipped) == 1
    assert fill.calls == 0 and (result.image == rgb).all()


def test_apply_regions_failed_fill_skips_every_region():
    class Broken:
        def fill(self, image, mask):
            raise RuntimeError("onnx exploded")

    rgb = np.full((90, 160, 3), 100, np.uint8)
    result = cl.apply_regions(rgb, [region()], [_box_mask((10, 10, 30, 30))], Broken(), SETTINGS)
    assert result.applied == []
    assert len(result.skipped) == 1 and "onnx exploded" in result.skipped[0]["why"]
    assert (result.image == rgb).all()


def test_bak_and_record_paths_sit_beside_the_picture(tmp_path):
    picture = tmp_path / "page_03.png"
    assert cl.bak_path(picture) == tmp_path / "page_03.png.bak"
    assert cl.record_path(picture) == tmp_path / "page_03.cleanup.json"


# --- cleanup_page stage ---------------------------------------------------------------------


def test_cleanup_page_writes_bak_then_picture_and_record(book):  # AC-1.2, AC-1.3, AC-2.1
    picture = picture_of(book)
    old = picture.read_bytes()
    marks = script_marks(book)
    codex = FakeCodex({"regions": [region(points=[(20, 20)])]})
    out = run(book, codex)

    assert out.status == "cleaned"
    assert [a["index"] for a in out.applied] == [0] and out.skipped == []
    assert cl.bak_path(picture).read_bytes() == old
    grown = cl.grow_mask(_box_mask((10, 10, 30, 30)), real_settings().margin_px)
    after = pixels(picture)
    assert after.shape == (H, W, 3)
    with Image.open(picture) as im:
        assert im.format == "PNG" and im.size == SIZE
    assert (after[~grown] == gradient()[~grown]).all()  # bit-identical outside the grown mask
    assert (after[grown] == GREEN).all()

    record = json.loads(cl.record_path(picture).read_text(encoding="utf-8"))
    assert record["page_number"] == 1
    assert [a["index"] for a in record["applied"]] == [0] and record["skipped"] == []
    assert record["picture_sha256_before"] == sha(old)
    assert record["picture_sha256_after"] == sha(picture.read_bytes())
    assert record["reviewed"] is False
    assert record["written_at"] and record["provider"]

    assert script_marks(book) == marks  # AC-3.2

    command = codex.commands[0]
    assert command[command.index("-i") + 1] == str(picture)
    assert _server_args(command)[:2] == ["-m", "video_pipeline.cleanup_mcp"]
    assert "160x90" in _server_args(command)
    assert "save_artifact_regions" in command[-1]


def test_cleanup_page_probes_each_region_with_its_box_and_points(book):  # AC-1.2
    seen: list = []

    def load(root, cfg):
        seg = FakeSeg()
        seen.append(seg)
        return seg, FakeFill()

    payload = {"regions": [region(points=[(20, 20)]), region((50, 50, 60, 60), reason="blob")]}
    out = cl.cleanup_page(
        book,
        book_data(book),
        1,
        root=ROOT,
        binary=BINARY,
        load_models=load,
        provider=FakeCodex(payload),
        yes=False,
        log=lambda line: None,
    )
    assert out.status == "cleaned" and len(out.applied) == 2
    assert len(seen) == 1  # models loaded once
    layers = seen[0].layers
    assert [tuple(layer.box) for layer in layers] == [(10, 10, 30, 30), (50, 50, 60, 60)]
    assert [list(map(list, layer.include_points)) for layer in layers] == [[[20, 20]], []]


def test_cleanup_page_skips_oversized_region_but_applies_the_rest(book):  # AC-1.4
    picture = picture_of(book)
    lines: list[str] = []
    payload = {"regions": [region(), region((0, 0, 160, 90), reason="whole sky")]}
    out = run(book, FakeCodex(payload), lines=lines)
    assert out.status == "cleaned"
    assert [a["index"] for a in out.applied] == [0]
    assert [s["index"] for s in out.skipped] == [1]
    assert out.skipped[0]["why"] in "\n".join(lines)  # every skip is logged with its reason
    record = json.loads(cl.record_path(picture).read_text(encoding="utf-8"))
    assert [s["index"] for s in record["skipped"]] == [1] and record["skipped"][0]["why"]
    grown = cl.grow_mask(_box_mask((10, 10, 30, 30)), real_settings().margin_px)
    assert (pixels(picture)[~grown] == gradient()[~grown]).all()


def test_cleanup_page_nothing_to_clean_changes_nothing(book):  # AC-1.1
    picture = picture_of(book)
    old = picture.read_bytes()
    marks = script_marks(book)
    loads: list[int] = []
    out = run(book, FakeCodex({"regions": []}), loads=loads)
    assert out.status == "nothing"
    assert picture.read_bytes() == old
    assert not cl.bak_path(picture).exists()
    assert not cl.record_path(picture).exists()
    assert loads == []  # no models loaded for a clean picture
    assert script_marks(book) == marks


def test_cleanup_page_all_skipped_fails_without_bak(book):  # AC-1.6
    picture = picture_of(book)
    old = picture.read_bytes()
    lines: list[str] = []
    out = run(book, FakeCodex({"regions": [region((0, 0, 160, 90))]}), lines=lines)
    assert out.status == "failed"
    assert out.applied == [] and len(out.skipped) == 1
    assert out.reason and out.reason == out.skipped[0]["why"]
    assert out.skipped[0]["why"] in "\n".join(lines)
    assert picture.read_bytes() == old
    assert not cl.bak_path(picture).exists()
    assert not cl.record_path(picture).exists()


def test_cleanup_refuses_covers(book):  # AC-3.3
    data = book_data(book)
    data["pages"][0]["illustration_file"] = "cover_front.png"
    cover = book.illustrations / "cover_front.png"
    cover.write_bytes(picture_of(book).read_bytes())
    codex = FakeCodex({"regions": [region()]})
    with pytest.raises(cl.CleanupError, match="only story pages"):
        run(book, codex, data=data)
    assert codex.commands == []
    assert not cl.bak_path(cover).exists()


def test_cleanup_refuses_missing_picture(book):
    picture_of(book).unlink()
    codex = FakeCodex({"regions": [region()]})
    with pytest.raises(cl.CleanupError):
        run(book, codex)
    assert codex.commands == []


def test_existing_bak_needs_yes(book):  # AC-2.2
    picture = picture_of(book)
    old = picture.read_bytes()
    bak = cl.bak_path(picture)
    bak.write_bytes(b"an older repaint")
    with pytest.raises(cl.CleanupError):
        run(book, FakeCodex({"regions": [region()]}), yes=False)
    assert picture.read_bytes() == old
    assert bak.read_bytes() == b"an older repaint"
    assert not cl.record_path(picture).exists()

    out = run(book, FakeCodex({"regions": [region()]}), yes=True)
    assert out.status == "cleaned"
    assert bak.read_bytes() == old
    assert picture.read_bytes() != old


def test_accept_keeps_bak_and_marks_reviewed(book):  # AC-2.4
    picture = picture_of(book)
    old = picture.read_bytes()
    run(book, FakeCodex({"regions": [region()]}))
    cleaned = picture.read_bytes()
    cl.accept_page(picture)
    record = json.loads(cl.record_path(picture).read_text(encoding="utf-8"))
    assert record["reviewed"] is True
    assert cl.bak_path(picture).read_bytes() == old
    assert picture.read_bytes() == cleaned


def test_accept_without_record_is_refused(book):
    with pytest.raises(cl.CleanupError):
        cl.accept_page(picture_of(book))


def test_revert_restores_bytes_and_removes_bak_and_record(book):  # AC-2.5
    picture = picture_of(book)
    old = picture.read_bytes()
    marks = script_marks(book)
    run(book, FakeCodex({"regions": [region()]}))
    assert picture.read_bytes() != old
    cl.revert_page(picture)
    assert picture.read_bytes() == old
    assert not cl.bak_path(picture).exists()
    assert not cl.record_path(picture).exists()
    assert script_marks(book) == marks


def test_revert_without_bak_is_refused(book):
    picture = picture_of(book)
    old = picture.read_bytes()
    with pytest.raises(cl.CleanupError):
        cl.revert_page(picture)
    assert picture.read_bytes() == old


def test_codex_refusals_exhaust_attempts(book):  # AC-1.5
    picture = picture_of(book)
    old = picture.read_bytes()
    loads: list[int] = []
    bad = {"regions": [region(box=(10, 10, 5, 30))]}
    codex = FakeCodex(bad, bad, bad)
    out = run(book, codex, loads=loads)
    assert out.status == "failed" and out.reason
    assert len(codex.commands) == 3
    # the refusal is fed back to the next attempt
    assert "region 0" not in codex.commands[0][-1] and "region 0" in codex.commands[1][-1]
    assert loads == []
    assert picture.read_bytes() == old
    assert not cl.bak_path(picture).exists()
    assert not cl.record_path(picture).exists()


def test_codex_that_never_saves_fails_after_three_calls(book):
    picture = picture_of(book)
    old = picture.read_bytes()
    codex = FakeCodex()
    out = run(book, codex)
    assert out.status == "failed" and len(codex.commands) == 3
    assert picture.read_bytes() == old
    assert not cl.bak_path(picture).exists()
