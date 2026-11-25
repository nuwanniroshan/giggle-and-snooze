"""Subject naming save tool and its MCP server (spec 025, Phase 3: FR-2)."""

from __future__ import annotations

import json

from video_pipeline import camera_ground as cg
from video_pipeline import camera_mcp

SIZE = (1672, 941)


def test_layer_subject_must_exist():
    assert cg.subject_problems({"layer": "car"}, ["car", "boy"], SIZE) == []
    assert cg.subject_problems({"layer": "dog"}, ["car", "boy"], SIZE)


def test_phrase_needs_box_inside_picture():
    ok = {"phrase": "red toy car", "box": [10, 10, 200, 150]}
    assert cg.subject_problems(ok, [], SIZE) == []
    assert cg.subject_problems({**ok, "box": [10, 10, 2000, 150]}, [], SIZE)
    assert cg.subject_problems({**ok, "box": [200, 10, 10, 150]}, [], SIZE)
    assert cg.subject_problems({"phrase": "x" * 200, "box": [1, 1, 5, 5]}, [], SIZE)
    assert cg.subject_problems({"phrase": "a", "layer": "car", "box": [1, 1, 5, 5]}, ["car"], SIZE)


def test_non_dict_subject_is_refused():
    assert cg.subject_problems(["car"], ["car"], SIZE)


def test_save_tool_writes_only_valid(tmp_path):
    out = tmp_path / "s.json"
    tools = cg.SubjectTools(["car"], SIZE, out)
    assert tools.save_camera_subject({"layer": "dog"})["saved"] is False and not out.exists()
    assert tools.save_camera_subject({"layer": "car"})["saved"] is True and out.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == {"layer": "car"}


def test_fourth_save_is_refused(tmp_path):
    out = tmp_path / "s.json"
    tools = cg.SubjectTools(["car"], SIZE, out)
    for _ in range(cg.MAX_SAVES):
        tools.save_camera_subject({"layer": "dog"})
    result = tools.save_camera_subject({"layer": "car"})
    assert result["saved"] is False and not out.exists()


def test_subject_prompt_has_questions_layers_and_size():
    page = {
        "page_number": 3,
        "lines": [
            {"role": "narration", "speaker": "Storyteller", "text": "Secret narration."},
            {"role": "kid_older", "speaker": "Zac", "text": "Why is the car red?"},
        ],
        "qa_grounding": "the toy car",
    }
    text = cg.subject_prompt(page, [("car", "a red toy car"), ("boy", "a boy")], (1672, 941))
    assert "Why is the car red?" in text and "the toy car" in text
    assert "car" in text and "boy" in text and "1672" in text and "941" in text
    assert "0: Secret narration." in text
    assert "save_camera_subject" in text


def test_server_args_and_build(tmp_path):
    args = camera_mcp.parse_args(
        ["--layers", "car,boy", "--size", "1672x941", "--out", str(tmp_path / "s.json")]
    )
    tools = camera_mcp.build_tools(args)
    assert tools.layer_names == ["car", "boy"] and tools.picture_size == (1672, 941)
    empty = camera_mcp.build_tools(
        camera_mcp.parse_args(["--layers", "", "--size", "10x10", "--out", str(tmp_path / "t")])
    )
    assert empty.layer_names == []
    assert camera_mcp.create_server(tools) is not None
