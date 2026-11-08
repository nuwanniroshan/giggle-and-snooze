import json

import pytest

from tests.helpers import MINI_BOOK, load_json, make_banter
from video_pipeline import episode_tools
from video_pipeline.contract import ConfigError, validate_script
from video_pipeline.episode import BookError, plan_episode
from video_pipeline.episode_tools import (
    CONFIG_DIR,
    MAX_SAVES,
    EpisodeTools,
    output_path,
    write_atomic,
)


def make_tools(tmp_path, force=False, seed=7) -> EpisodeTools:
    return EpisodeTools.load(MINI_BOOK, seed, tmp_path / "episodes", CONFIG_DIR, force)


def valid_banter(tools: EpisodeTools) -> dict:
    return make_banter(tools.book, tools.plan).model_dump()


def test_output_path_uses_slug(tmp_path):
    book = load_json(MINI_BOOK)
    assert (
        output_path(book, tmp_path) == tmp_path / "the-lantern-bug-parade" / "episode_script.json"
    )


def test_plan_comes_from_seed(tmp_path):
    tools = make_tools(tmp_path, seed=11)
    assert tools.plan == plan_episode(tools.book, 11)


def test_get_episode_plan_has_everything_the_agent_needs(tmp_path):  # FR-8
    tools = make_tools(tmp_path)
    plan = tools.get_episode_plan()
    assert plan["title"] == "The Lantern Bug Parade"
    assert plan["age_range"] == "Ages 3-6"
    assert [p["page_number"] for p in plan["pages"]] == list(range(1, 9))
    assert plan["pages"][1]["illustration"].startswith("Mr. Pebble the beetle")
    assert set(plan["cast"]) == {"storyteller", "kid_older", "kid_younger"}
    expected = [
        {"page_number": n, "kids": list(kids)} for n, kids in sorted(tools.plan.qa_speakers.items())
    ]
    assert plan["qa_pages"] == expected
    assert "grounding" in plan["brief"] and "PAGE 8" in plan["brief"]
    assert "Reply with JSON only" not in plan["brief"]
    assert plan["banter_schema"]["title"] == "Banter"


def test_validate_banter_accepts_valid_and_lists_errors(tmp_path):
    tools = make_tools(tmp_path)
    assert tools.validate_banter(valid_banter(tools)) == {"valid": True, "errors": []}
    result = tools.validate_banter({"intro": [], "moments": [], "outro": []})
    assert result["valid"] is False
    assert any("intro" in error for error in result["errors"])


def test_validate_banter_accepts_a_json_string(tmp_path):
    tools = make_tools(tmp_path)
    assert tools.validate_banter(json.dumps(valid_banter(tools)))["valid"] is True


def test_save_writes_valid_script(tmp_path):  # AC-1.2
    tools = make_tools(tmp_path)
    result = tools.save_episode_script(valid_banter(tools))
    assert result == {"saved": True, "path": str(tools.out), "errors": []}
    assert validate_script(load_json(tools.out), tools.book, tools.terms) == []


def test_save_refuses_invalid_banter(tmp_path):  # AC-6.7
    tools = make_tools(tmp_path)
    result = tools.save_episode_script({"intro": []})
    assert result["saved"] is False and result["errors"]
    assert not tools.out.exists()


def test_save_respects_force(tmp_path):  # AC-6.1, AC-6.2
    tools = make_tools(tmp_path)
    tools.out.parent.mkdir(parents=True)
    tools.out.write_bytes(b"hand edited")
    result = tools.save_episode_script(valid_banter(tools))
    assert result["saved"] is False and "--force" in result["errors"][0]
    assert tools.out.read_bytes() == b"hand edited"
    forced = make_tools(tmp_path, force=True)
    assert forced.save_episode_script(valid_banter(forced))["saved"] is True


def test_save_limit(tmp_path):  # FR-7: at most 5 saves
    tools = make_tools(tmp_path)
    for _ in range(MAX_SAVES):
        assert tools.save_episode_script({})["saved"] is False
    result = tools.save_episode_script(valid_banter(tools))
    assert result["saved"] is False and "save limit reached" in result["errors"][0]
    assert not tools.out.exists()


def test_load_needs_terms_file(tmp_path):  # AC-7.2
    config = tmp_path / "config"
    config.mkdir()
    (config / "cast.json").write_text((CONFIG_DIR / "cast.json").read_text(), encoding="utf-8")
    with pytest.raises(ConfigError, match="disallowed_terms.txt"):
        EpisodeTools.load(MINI_BOOK, 7, tmp_path, config)


def test_load_rejects_bad_book(tmp_path):
    with pytest.raises(BookError, match="not found"):
        EpisodeTools.load(tmp_path / "nope.json", 7, tmp_path, CONFIG_DIR)


def test_interrupted_write_keeps_previous_file(monkeypatch, tmp_path):  # Reliability NFR
    target = tmp_path / "episode_script.json"
    target.write_text("previous", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(episode_tools.os, "replace", boom)
    with pytest.raises(OSError):
        write_atomic(target, {"new": True})
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [target]
