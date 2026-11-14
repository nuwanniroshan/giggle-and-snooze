import json

import pytest

from tests.helpers import load_json, make_banter, make_root
from video_pipeline import episode_tools
from video_pipeline.contract import ConfigError, validate_script
from video_pipeline.episode import BookError, plan_episode
from video_pipeline.episode_tools import (
    CONFIG_DIR,
    MAX_SAVES,
    EpisodeTools,
    write_atomic,
)
from video_pipeline.library import BookFolder


def make_tools(tmp_path, force=False, seed=7) -> EpisodeTools:
    folder = make_root(tmp_path)
    return EpisodeTools.load(folder, seed, CONFIG_DIR, force)


def valid_banter(tools: EpisodeTools) -> dict:
    return make_banter(tools.book, tools.plan).model_dump()


def test_script_is_written_in_the_book_episode_folder(tmp_path):  # AC-2.1
    tools = make_tools(tmp_path)
    assert (
        tools.out
        == tmp_path / "books" / "the-lantern-bug-parade" / "episode" / "episode_script.json"
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
    folder = make_root(tmp_path / "book-root")
    (tmp_path / "book-root" / "config" / "disallowed_terms.txt").unlink()
    with pytest.raises(ConfigError, match="disallowed_terms.txt"):
        EpisodeTools.load(folder, 7, tmp_path / "book-root" / "config")


def test_load_rejects_bad_book(tmp_path):
    with pytest.raises(BookError, match="not found"):
        EpisodeTools.load(BookFolder("nope", tmp_path), 7, CONFIG_DIR)


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


# --- spec 028: narration tags in generated banter -----------------------------------------------


def _tag(page, sentence, position="start", name="gasp"):
    return {"page_number": page, "sentence": sentence, "position": position, "tag": name}


def _tagged_banter(tools, *tags) -> dict:
    return {**valid_banter(tools), "narration_tags": list(tags)}


def _narration(script, page) -> list[str]:
    entry = next(p for p in script["pages"] if p["page_number"] == page)
    return [line["text"] for line in entry["lines"] if line["role"] == "narration"]


def test_check_places_a_valid_narration_tag(tmp_path):  # AC-1.1, AC-1.3
    from video_pipeline.contract import strip_tags

    tools = make_tools(tmp_path)
    script, errors = tools.check(_tagged_banter(tools, _tag(1, 2, "start", "gasp")))
    assert errors == [] and script is not None
    lines = _narration(script, 1)
    assert any("<gasp>" in line for line in lines)
    text = tools.book["pages"][0]["text"]
    assert strip_tags(" ".join(lines)).split() == text.split()


def test_check_refuses_two_tags_on_a_page(tmp_path):  # AC-2.1, AC-2.4
    tools = make_tools(tmp_path)
    script, errors = tools.check(_tagged_banter(tools, _tag(1, 1, "end"), _tag(1, 2, "end")))
    assert script is None
    assert any("page 1" in e and "narration tags" in e for e in errors)


def test_check_refuses_more_tags_than_the_book_allows(tmp_path):  # AC-2.2
    tools = make_tools(tmp_path)
    tags = [_tag(n, 1, "end") for n in range(1, 6)]  # 8 pages: at most 4
    script, errors = tools.check(_tagged_banter(tools, *tags))
    assert script is None
    assert any("5 narration tags" in e and "at most 4" in e for e in errors)


def test_check_lists_narration_and_other_problems_together(tmp_path):  # AC-2.4
    tools = make_tools(tmp_path)
    banter = _tagged_banter(tools, _tag(1, 1, "end"), _tag(1, 2, "end"))
    banter["moments"] = banter["moments"][:-1]
    script, errors = tools.check(banter)
    assert script is None
    assert any("narration tags" in e for e in errors)
    assert any("narration tags" not in e for e in errors)


def test_validate_script_does_not_enforce_the_generation_limits(tmp_path):  # AC-3.2
    tools = make_tools(tmp_path)
    script, errors = tools.check(valid_banter(tools))
    assert errors == []
    page = next(p for p in script["pages"] if p["page_number"] == 3)
    for line in page["lines"]:
        if line["role"] == "narration":
            line["text"] = "<gasp> " + line["text"] + " <sigh> <yawn>"
    terms = tools.terms
    assert validate_script(script, tools.book, terms, None, tools.feeling_names) == []


def test_without_narration_limits_tags_add_no_limit_errors(tmp_path):  # FR-6
    tools = make_tools(tmp_path)
    tools.emotions = tools.emotions.model_copy(update={"narration": None})
    tags = [_tag(1, 1, "end"), _tag(1, 2, "end"), _tag(99, 1), _tag(2, 50)]
    script, errors = tools.check(_tagged_banter(tools, *tags))
    assert errors == [] and script is not None
    # the two valid page-1 entries are still placed; the invalid ones are skipped
    assert sum("<" in line for line in _narration(script, 1)) >= 1
    assert all("<" not in line for line in _narration(script, 2))


def test_get_episode_plan_schema_lists_narration_tags(tmp_path):  # AC-4.1
    plan = make_tools(tmp_path).get_episode_plan()
    assert "narration_tags" in plan["banter_schema"]["properties"]


def test_save_refuses_over_limit_narration_tags_and_accepts_valid(tmp_path):  # AC-4.1
    tools = make_tools(tmp_path)
    refused = tools.save_episode_script(_tagged_banter(tools, _tag(1, 1, "end"), _tag(1, 2, "end")))
    assert refused["saved"] is False and any("narration tags" in e for e in refused["errors"])
    assert not tools.out.exists()
    saved = tools.save_episode_script(_tagged_banter(tools, _tag(1, 2, "start", "gasp")))
    assert saved["saved"] is True
    assert any("<gasp>" in line for line in _narration(load_json(tools.out), 1))
