"""The motion plan save tool and its MCP server (spec 009, Phase 2: FR-4, FR-5, AC-3.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tests.test_motion_plan import LIMITS, make_layer
from video_pipeline import plan_mcp
from video_pipeline.motion_plan import MAX_SAVES, PlanTools, picture_identity


@pytest.fixture
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "page_03.png"
    Image.new("RGB", (1024, 1024), "red").save(path)
    return path


@pytest.fixture
def tools(tmp_path: Path, picture: Path) -> PlanTools:
    return PlanTools(LIMITS, picture_identity(picture), 3, tmp_path / "out" / "plan.json")


def body(**over) -> dict:
    plan = {"layers": [make_layer()], "effects": [{"type": "snow", "intensity": 0.5}]}
    plan.update(over)
    return plan


def test_valid_plan_is_saved_with_the_server_picture(tools, picture):
    assert tools.save_motion_plan(body()) == {"saved": True, "errors": []}
    saved = json.loads(tools.out.read_text(encoding="utf-8"))
    assert saved["picture"]["sha256"] == picture_identity(picture).sha256
    assert saved["page_number"] == 3 and saved["provider"] == "codex" and saved["written_at"]


def test_payload_cannot_override_server_fields(tools, picture):
    forged = body(
        picture={"file": "x.png", "sha256": "00" * 32, "width": 9999, "height": 9999},
        page_number=7,
        provider="me",
        written_at="1999",
    )
    assert tools.save_motion_plan(forged)["saved"] is True
    saved = json.loads(tools.out.read_text(encoding="utf-8"))
    assert saved["picture"] == picture_identity(picture).model_dump()
    assert saved["page_number"] == 3 and saved["provider"] == "codex"
    assert saved["written_at"] != "1999"


def test_invalid_plan_is_refused_and_nothing_written(tools):
    result = tools.save_motion_plan(body(layers=[]))
    assert result["saved"] is False and result["errors"]
    assert not tools.out.exists()


def test_non_dict_plan_is_refused(tools):
    result = tools.save_motion_plan(["not", "a", "plan"])
    assert result["saved"] is False and result["errors"]
    assert not tools.out.exists()


def test_amplitude_over_limit_names_the_limit_and_saves_nothing(tools):  # AC-3.3
    layer = make_layer(motions=[{"type": "rotate", "amplitude": 9.0, "period_s": 6.0}])
    result = tools.save_motion_plan(body(layers=[layer]))
    assert result["saved"] is False
    assert any("amplitude" in e and "max 4.0" in e for e in result["errors"])
    assert not tools.out.exists()


def test_save_limit(tools):
    assert MAX_SAVES == 3
    for _ in range(MAX_SAVES):
        tools.save_motion_plan(body(layers=[]))
    result = tools.save_motion_plan(body())
    assert result["saved"] is False and "save limit" in result["errors"][0]
    assert not tools.out.exists()


def test_server_args_and_tools_come_from_argv(tmp_path, picture):
    out = tmp_path / "o.json"
    args = plan_mcp.parse_args(
        ["--picture", str(picture), "--page", "3", "--out", str(out), "--root", str(tmp_path)]
    )
    assert args.picture == picture and args.page == 3 and args.out == out
    with pytest.raises(SystemExit):
        plan_mcp.parse_args(["--page", "3", "--out", str(out)])  # picture is required


def test_server_exposes_only_the_save_tool(tools):
    import asyncio

    server = plan_mcp.create_server(tools)
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert names == ["save_motion_plan"]
