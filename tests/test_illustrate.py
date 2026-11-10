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


# -- plans written by illustrate (spec 009, Phase 3: FR-13) ---------------------------------------


def _paint_real(record: list | None = None, colour: str = "green"):
    def paint(binary, prompt, reference, target):
        if record is not None:
            record.append(("paint", target.stem))
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 1024), colour).save(target)

    return paint


def test_planner_runs_after_each_painted_page_not_covers(tmp_path):  # AC-2.1, AC-2.2
    from video_pipeline.motion_plan import PlanOutcome

    events: list = []

    def plan(page):
        assert (tmp_path / f"page_{page['page_number']:02d}.png").is_file()
        events.append(("plan", page["page_number"]))
        return PlanOutcome(page["page_number"], "written")

    done = illustrate(
        BOOK, tmp_path, Path("codex"), paint=_paint_real(events), log=lambda _: None, plan=plan
    )
    assert done == ["page_01", "page_02", "cover_front", "cover_back"]
    assert events == [
        ("paint", "page_01"),
        ("plan", 1),
        ("paint", "page_02"),
        ("plan", 2),
        ("paint", "cover_front"),
        ("paint", "cover_back"),
    ]


def test_failed_plan_is_logged_and_painting_continues(tmp_path):  # AC-2.3
    from video_pipeline.motion_plan import PlanOutcome

    lines: list[str] = []
    done = illustrate(
        BOOK,
        tmp_path,
        Path("codex"),
        paint=_paint_real(),
        log=lines.append,
        plan=lambda page: PlanOutcome(page["page_number"], "failed", "agent saved nothing"),
    )
    assert done == ["page_01", "page_02", "cover_front", "cover_back"]
    assert (tmp_path / "page_01.png").is_file()
    assert any(line.strip() == "page_01: plan failed – agent saved nothing" for line in lines)
    assert any(line.strip() == "page_02: plan failed – agent saved nothing" for line in lines)


def test_kept_picture_gets_no_plan(tmp_path):  # AC-2.5
    from video_pipeline.motion_plan import PlanOutcome

    (tmp_path / "page_01.png").write_bytes(b"x")
    planned: list[int] = []

    def plan(page):
        planned.append(page["page_number"])
        return PlanOutcome(page["page_number"], "written")

    illustrate(BOOK, tmp_path, Path("codex"), paint=_paint_real(), log=lambda _: None, plan=plan)
    assert planned == [2]


def test_force_repaint_replans_and_keeps_old_plan_as_bak(tmp_path):  # AC-2.4
    import json

    from tests.helpers import make_root
    from tests.test_motion_plan import LIMITS, FakeCodex, _plan_body
    from video_pipeline.motion_plan import load_plan, plan_page, plan_path

    folder = make_root(tmp_path)
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    book["pages"] = [p for p in book["pages"] if p["page_number"] == 1]
    first = FakeCodex()

    def planner(provider):
        return lambda page: plan_page(
            folder,
            page,
            LIMITS,
            binary=Path("/fake/codex"),
            book=book,
            root=folder.root,
            force=True,
            provider=provider,
        )

    illustrate(
        book,
        folder.illustrations,
        Path("codex"),
        paint=_paint_real(),
        log=lambda _: None,
        plan=planner(first),
    )
    before = plan_path(folder, 1).read_bytes()
    other = _plan_body()
    other["layers"][0]["name"] = "bush"
    second = FakeCodex({1: [other]})
    illustrate(
        book,
        folder.illustrations,
        Path("codex"),
        force=True,
        paint=_paint_real(colour="red"),
        log=lambda _: None,
        plan=planner(second),
    )
    assert len(second.commands) == 1
    assert plan_path(folder, 1).with_name("page_01.json.bak").read_bytes() == before
    assert load_plan(plan_path(folder, 1), LIMITS).layers[0].name == "bush"


def test_main_plans_with_force_and_exits_1_naming_failed_pages(tmp_path, monkeypatch, capsys):
    from tests.helpers import make_root
    from video_pipeline import illustrate as module
    from video_pipeline.motion_plan import PlanOutcome

    folder = make_root(tmp_path)
    calls: list[tuple[int, bool]] = []

    def fake_plan_page(folder_, page, limits, *, binary, book, root, force=False, provider=None):
        calls.append((page["page_number"], force))
        status = "failed" if page["page_number"] == 2 else "written"
        return PlanOutcome(page["page_number"], status, "no plan" if status == "failed" else "")

    monkeypatch.setattr(module, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(module, "paint_one", _paint_real())
    monkeypatch.setattr(module, "plan_page", fake_plan_page)
    code = module.main(["--book", folder.slug, "--root", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 1
    assert calls and all(force for _, force in calls)
    assert "painted" in out.out
    assert "page_02" in out.err
