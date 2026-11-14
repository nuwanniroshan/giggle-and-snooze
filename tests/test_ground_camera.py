"""The ground_camera stage and CLI (spec 025, Phase 4): fakes for Codex and the segmenter."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import ROOT, make_root
from video_pipeline import camera_ground as cg
from video_pipeline import camera_mcp, cut_layers, ground_camera
from video_pipeline.agents import AgentEvent, AgentResult, AgentUnavailable
from video_pipeline.cut_layers import MANIFEST, ModelsMissing, layers_dir
from video_pipeline.library import BookFolder
from video_pipeline.motion_plan import MotionPlan, picture_identity, plan_path, save_plan

W, H = 160, 90
BINARY = Path("/fake/codex")


def _server_args(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    return json.loads(next(c for c in command if c.startswith(prefix))[len(prefix) :])


class FakeCodex:
    """Plays the agent: for a page, saves the next payload through the real subject tool.

    `payloads` maps a page number (read from the picture name in the command) to the payloads
    its attempts save in turn; `None` saves nothing. A page with no entry saves nothing.
    """

    def __init__(self, payloads: dict[int, list]):
        self.payloads = {k: list(v) for k, v in payloads.items()}
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=600, on_event=None, max_refused_saves=5):
        self.commands.append(command)
        args = camera_mcp.parse_args(_server_args(command)[2:])  # drop "-m video_pipeline.…"
        tools = camera_mcp.build_tools(args)
        picture = Path(command[command.index("-i") + 1])
        queue = self.payloads.get(int(picture.stem.split("_")[1]), [])
        payload = queue.pop(0) if queue else None
        events = []
        if payload is not None:
            result = tools.save_camera_subject(payload)
            events.append(AgentEvent("tool-result", tool="save_camera_subject", data=result))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        reason = "" if payload is not None else "agent exited with status 1"
        return AgentResult(ok=not reason, events=events, log_path=log_path, reason=reason)


class FakeSeg:
    """A mask that is the layer's box filled; `modes` maps a box to "empty" or "full"."""

    def __init__(self, modes: dict[tuple, str] | None = None):
        self.modes = modes or {}
        self.calls = 0

    def masks(self, image, layers):
        self.calls += 1
        out = []
        for layer in layers:
            mask = np.zeros(image.shape[:2], bool)
            mode = self.modes.get(tuple(layer.box), "box")
            if mode == "full":
                mask[:] = True
            elif mode == "box":
                x0, y0, x1, y1 = layer.box
                mask[y0:y1, x0:x1] = True
            out.append(mask)
        return out


def add_layer_cut(folder: BookFolder, page: int, box: tuple[int, int, int, int]) -> None:
    """A plan with layer `car` over `box` and its cut: manifest plus an opaque RGBA PNG."""
    picture = folder.illustrations / f"page_{page:02d}.png"
    x0, y0, x1, y1 = box
    plan = MotionPlan.model_validate(
        {
            "page_number": page,
            "picture": picture_identity(picture).model_dump(),
            "layers": [
                {
                    "name": "car",
                    "description": "a red toy car",
                    "box": list(box),
                    "z": 1,
                    "depth": 0.5,
                    "pivot": [x0, y1],
                    "motions": [],
                }
            ],
        }
    )
    save_plan(plan_path(folder, page), plan)
    out = layers_dir(folder, page)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((y1 - y0, x1 - x0, 4), 255, np.uint8), "RGBA").save(out / "car.png")
    (out / MANIFEST).write_text(
        json.dumps(
            {
                "background": "background.png",
                "layers": [
                    {
                        "name": "car",
                        "file": "car.png",
                        "offset": [x0, y0],
                        "size": [x1 - x0, y1 - y0],
                        "empty": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def script_page(number: int, qa: bool) -> dict:
    lines = [{"speaker": "storyteller", "role": "narration", "text": "A car drove by."}]
    page = {"page_number": number, "has_qa": qa, "lines": lines}
    if qa:
        lines.append({"speaker": "kid_older", "role": "question", "text": "What is that car?"})
        page["qa_grounding"] = "that car"
    return page


@pytest.fixture
def book(tmp_path: Path) -> BookFolder:
    """Pages 1 (no Q&A, car low), 2 (Q&A, car in the middle), 3-5 (Q&A, no layers)."""
    folder = make_root(tmp_path)
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    for n in range(1, 9):
        Image.new("RGB", (W, H), (n * 20, 90, 160)).save(folder.illustrations / f"page_{n:02d}.png")
    add_layer_cut(folder, 1, (40, 60, 100, 90))
    add_layer_cut(folder, 2, (40, 30, 80, 60))
    script = {"pages": [script_page(1, False)] + [script_page(n, True) for n in range(2, 6)]}
    folder.episode_dir.mkdir(parents=True, exist_ok=True)
    folder.script_path.write_text(json.dumps(script, indent=2), encoding="utf-8")
    return folder


def run(folder: BookFolder, codex: FakeCodex, seg: FakeSeg | None = None, loads=None, **kw):
    def load(root, cfg):
        if loads is not None:
            loads.append(1)
        return seg or FakeSeg()

    kw.setdefault("load_segmenter", load)
    return cg.ground_book(
        folder,
        root=folder.root,
        provider=codex,
        binary=BINARY,
        log=lambda line: None,
        **kw,
    )


def read(folder: BookFolder, page: int) -> dict:
    return json.loads(cg.camera_path(folder, page).read_text(encoding="utf-8"))


def test_layer_subject_uses_layer_mask_without_loading_segmenter(book):
    loads: list[int] = []
    codex = FakeCodex({2: [{"layer": "car"}]})
    out = run(book, codex, loads=loads, pages=[2])
    data = read(book, 2)
    assert out[0].status == "written" and data["subject"]["source"] == "layer"
    assert data["subject"]["name"] == "car" and data["status"] == "ok"
    assert not loads
    x0, y0, x1, y1 = data["subject"]["box"]
    assert (x0, y0, x1, y1) == (40, 30, 80, 60)
    assert x0 <= data["target"]["x"] * W <= x1 and y0 <= data["target"]["y"] * H <= y1
    assert data["band"] == "bottom"  # tie: the car sits in neither band
    picture = book.illustrations / "page_02.png"
    assert data["picture"] == picture_identity(picture).model_dump()
    assert data["provider"] and data["written_at"]
    command = codex.commands[0]
    assert command[command.index("-i") + 1] == str(picture)
    assert "car: a red toy car" in command[-1]
    assert _server_args(command)[:2] == ["-m", "video_pipeline.camera_mcp"]


def test_phrase_subject_runs_segmenter_once(book):
    loads: list[int] = []
    box = [100, 60, 140, 85]
    codex = FakeCodex(
        {3: [{"phrase": "red kite", "box": box}], 4: [{"phrase": "red kite", "box": box}]}
    )
    out = run(book, codex, loads=loads, pages=[3, 4])
    assert [o.status for o in out] == ["written", "written"]
    assert loads == [1]
    data = read(book, 3)
    assert data["subject"] == {"name": "red kite", "source": "sam", "box": box}
    assert box[0] <= data["target"]["x"] * W <= box[2]
    assert data["band"] == "top"  # the subject sits low, so the words go on top


def test_no_qa_page_records_band_only(book):
    codex = FakeCodex({})
    out = run(book, codex, pages=[1])
    data = read(book, 1)
    assert out[0].status == "no_qa" and not codex.commands
    assert data["status"] == "no_qa" and data["subject"] is None and data["target"] is None
    assert data["band"] == "top"  # the cut car covers the bottom rows


def test_empty_and_oversized_masks_fail_page_but_not_book(book):
    seg = FakeSeg({(1, 1, 5, 5): "empty", (0, 0, 160, 90): "full"})
    codex = FakeCodex(
        {
            3: [{"phrase": "nothing", "box": [1, 1, 5, 5]}],
            4: [{"phrase": "everything", "box": [0, 0, 160, 90]}],
            5: [{"phrase": "red kite", "box": [10, 10, 50, 40]}],
        }
    )
    out = run(book, codex, seg, pages=[3, 4, 5])
    assert [o.status for o in out] == ["failed", "failed", "written"]
    assert "empty" in out[0].reason and "share" in out[1].reason
    for page in (3, 4):
        data = read(book, page)
        assert data["status"] == "failed" and data["target"] is None
        assert data["reason"] and data["band"] in cg.BANDS
    assert read(book, 5)["status"] == "ok"


def test_codex_refusals_exhaust_attempts(book):
    codex = FakeCodex({3: [{"layer": "dog"}, {"layer": "cat"}, {"layer": "cow"}]})
    out = run(book, codex, pages=[3])
    assert out[0].status == "failed" and "not one of" in out[0].reason
    assert len(codex.commands) == 3
    assert "refused" in codex.commands[1][-1] and "dog" in codex.commands[1][-1]
    data = read(book, 3)
    assert data["status"] == "failed" and data["target"] is None and data["subject"] is None
    assert data["band"] == "bottom"  # no layers cut for page 3


def test_codex_that_never_saves_fails_after_three_calls(book):
    codex = FakeCodex({})
    out = run(book, codex, pages=[3])
    assert out[0].status == "failed" and len(codex.commands) == 3


def test_layer_subject_without_cut_layers_fails(book):
    (layers_dir(book, 2) / MANIFEST).unlink()
    out = run(book, FakeCodex({2: [{"layer": "car"}]}), pages=[2])
    assert out[0].status == "failed" and "cut layers missing" in out[0].reason
    assert read(book, 2)["target"] is None


def test_script_untouched(book):
    before = book.script_path.read_bytes()
    run(book, FakeCodex({2: [{"layer": "car"}]}))
    assert book.script_path.read_bytes() == before


def test_current_file_skipped_unless_forced(book):
    codex = FakeCodex({2: [{"layer": "car"}, {"layer": "car"}]})
    assert run(book, codex, pages=[2])[0].status == "written"
    before = cg.camera_path(book, 2).read_bytes()
    assert run(book, codex, pages=[2])[0].status == "kept"
    assert cg.camera_path(book, 2).read_bytes() == before and len(codex.commands) == 1
    assert run(book, codex, pages=[2], force=True)[0].status == "written"
    assert len(codex.commands) == 2


def test_stale_file_is_regrounded(book):
    codex = FakeCodex({3: [{"phrase": "kite", "box": [10, 10, 50, 40]}] * 2})
    run(book, codex, pages=[3])
    Image.new("RGB", (W, H), (1, 2, 3)).save(book.illustrations / "page_03.png")
    assert run(book, codex, pages=[3])[0].status == "written"
    assert (
        read(book, 3)["picture"]["sha256"]
        == picture_identity(book.illustrations / "page_03.png").model_dump()["sha256"]
    )


def test_failed_file_is_retried(book):
    codex = FakeCodex({3: [None, None, None, {"phrase": "kite", "box": [10, 10, 50, 40]}]})
    assert run(book, codex, pages=[3])[0].status == "failed"
    assert run(book, codex, pages=[3])[0].status == "written"


def test_agent_unavailable_is_raised_before_any_page(book, monkeypatch):
    def unavailable():
        raise AgentUnavailable("no codex")

    monkeypatch.setattr(cg, "codex_status", unavailable)
    with pytest.raises(AgentUnavailable):
        cg.ground_book(book, root=book.root, provider=FakeCodex({}), log=lambda line: None)
    assert not (book.folder / "camera").exists()


def test_load_real_segmenter_needs_downloaded_files(tmp_path, monkeypatch):
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    models = cut_layers.read_config(ROOT)["models"]
    with pytest.raises(ModelsMissing) as err:
        cut_layers.load_real_segmenter(ROOT, models)
    assert cut_layers.DOWNLOAD_HINT in str(err.value)


def _outcomes(*statuses: str) -> list[cg.GroundOutcome]:
    return [
        cg.GroundOutcome(n, s, "why" if s == "failed" else "") for n, s in enumerate(statuses, 1)
    ]


def test_cli_prints_counts_and_exits_1_on_failure(book, monkeypatch, capsys):
    seen = {}

    def fake_ground(folder, **kw):
        seen.update(kw)
        return _outcomes("written", "kept", "no_qa", "failed")

    monkeypatch.setattr(ground_camera, "ground_book", fake_ground)
    argv = ["--book", book.slug, "--root", str(book.root), "--page", "2", "--page", "3", "--force"]
    assert ground_camera.main(argv) == 1
    assert "1 written, 1 kept, 1 no Q&A, 1 failed" in capsys.readouterr().out
    assert seen["pages"] == [2, 3] and seen["force"] is True


def test_cli_exits_0_when_nothing_failed(book, monkeypatch, capsys):
    monkeypatch.setattr(ground_camera, "ground_book", lambda folder, **kw: _outcomes("written"))
    assert ground_camera.main(["--book", book.slug, "--root", str(book.root)]) == 0
    assert "1 written, 0 kept, 0 no Q&A, 0 failed" in capsys.readouterr().out


@pytest.mark.parametrize("exc", [AgentUnavailable("no codex"), ModelsMissing("no models")])
def test_cli_reports_errors(book, monkeypatch, capsys, exc):
    def boom(folder, **kw):
        raise exc

    monkeypatch.setattr(ground_camera, "ground_book", boom)
    assert ground_camera.main(["--book", book.slug, "--root", str(book.root)]) == 1
    assert f"error: {exc}" in capsys.readouterr().err


def test_cli_unknown_book_is_an_error(tmp_path, capsys):
    assert ground_camera.main(["--book", "nope", "--root", str(tmp_path)]) == 1
    assert "error:" in capsys.readouterr().err
