"""The Codex region save tool and prompt for artifact cleanup (spec 026, FR-2, AC-1.5)."""

from __future__ import annotations

import asyncio
import json

from tests.helpers import ROOT
from tests.test_cleanup import SETTINGS
from video_pipeline import cleanup as cl
from video_pipeline import cleanup_mcp

SIZE = (160, 90)
GOOD = {"regions": [{"box": [5, 5, 20, 20], "include_points": [], "reason": "blob"}]}
BAD = {"regions": [{"box": [5, 5, 1, 9], "reason": "x"}]}


def test_save_tool_writes_only_valid(tmp_path):
    out = tmp_path / "r.json"
    tools = cl.RegionTools(SIZE, SETTINGS, out)
    refused = tools.save_artifact_regions(BAD)
    assert refused["saved"] is False and refused["errors"]
    assert not out.exists()
    ok = tools.save_artifact_regions(GOOD)
    assert ok["saved"] is True and ok["errors"] == [] and out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == GOOD


def test_refusal_lists_every_problem(tmp_path):
    out = tmp_path / "r.json"
    tools = cl.RegionTools(SIZE, SETTINGS, out)
    two_bad = {
        "regions": [
            {"box": [5, 5, 1, 9], "include_points": [], "reason": "x"},
            {"box": [5, 5, 20, 20], "include_points": [[999, 1]], "reason": ""},
        ]
    }
    result = tools.save_artifact_regions(two_bad)
    assert result["saved"] is False and len(result["errors"]) >= 3
    assert not out.exists()


def test_fourth_save_refused(tmp_path):
    out = tmp_path / "r.json"
    tools = cl.RegionTools(SIZE, SETTINGS, out)
    for _ in range(3):
        assert tools.save_artifact_regions(BAD)["saved"] is False
    fourth = tools.save_artifact_regions(GOOD)
    assert fourth["saved"] is False and fourth["errors"]
    assert not out.exists()


def test_empty_list_saves(tmp_path):  # AC-1.1: "nothing to clean" is a valid answer
    out = tmp_path / "r.json"
    tools = cl.RegionTools(SIZE, SETTINGS, out)
    assert tools.save_artifact_regions({"regions": []})["saved"] is True
    assert json.loads(out.read_text(encoding="utf-8")) == {"regions": []}


def test_cleanup_prompt_mentions_size_and_tool():
    page = {"page_number": 3, "text": "Tilly flickered her lantern."}
    prompt = cl.cleanup_prompt(page, SIZE, SETTINGS)
    assert "160x90" in prompt
    assert "save_artifact_regions" in prompt
    assert '{"regions": []}' in prompt
    assert "Tilly flickered her lantern." in prompt
    assert str(SETTINGS.max_regions) in prompt and str(SETTINGS.max_points) in prompt


def test_server_tools_come_from_argv(tmp_path):
    out = tmp_path / "o.json"
    args = cleanup_mcp.parse_args(["--size", "160x90", "--out", str(out), "--root", str(ROOT)])
    tools = cleanup_mcp.build_tools(args)
    assert isinstance(tools, cl.RegionTools)
    assert tuple(tools.size) == SIZE and tools.out == out
    assert tools.settings == cl.load_settings(ROOT / "config")


def test_server_exposes_only_the_save_tool(tmp_path):
    server = cleanup_mcp.create_server(cl.RegionTools(SIZE, SETTINGS, tmp_path / "o.json"))
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert names == ["save_artifact_regions"]
