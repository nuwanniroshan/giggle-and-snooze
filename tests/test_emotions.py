"""Spec 017: feelings in the episode script (config, contract, generation rules, brief)."""

import copy
import json
import shutil

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline.contract import Banter, ConfigError, validate_script
from video_pipeline.emotions import NEUTRAL, load_emotions
from video_pipeline.episode import assemble_script, plan_episode
from video_pipeline.episode_tools import CONFIG_DIR

TERMS = ["stupid"]
DEFAULT_FEELINGS = (
    "neutral",
    "excited",
    "curious",
    "happy",
    "calm",
    "sleepy",
    "whisper",
    "surprised",
)


def _config(tmp_path, mutate=None):
    data = load_json(CONFIG_DIR / "emotions.json")
    if mutate:
        mutate(data)
    (tmp_path / "emotions.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


# --- Phase 1: config (AC-1.5, AC-2.1, AC-3.4) ---------------------------------------------------


def test_default_config_lists_the_spec_feelings_and_counts():  # AC-2.1, AC-1.5
    settings = load_emotions(CONFIG_DIR)
    assert set(settings.names) == set(DEFAULT_FEELINGS)
    assert settings.feeling(NEUTRAL).is_neutral
    gen = settings.generation
    assert (gen.tags_per_moment_min, gen.tags_per_moment_max, gen.feelings_per_moment_min) == (
        1,
        2,
        1,
    )


def test_missing_feeling_means_neutral():  # AC-2.3
    settings = load_emotions(CONFIG_DIR)
    assert settings.feeling(None) == settings.feeling("neutral")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speed", 0.79),
        ("speed", 1.26),
        ("semitones", -2.1),
        ("semitones", 2.5),
        ("gain_db", -9.5),
        ("gain_db", 3.1),
    ],
)
def test_out_of_range_settings_name_feeling_and_field(tmp_path, field, value):  # AC-3.4
    config = _config(tmp_path, lambda d: d["feelings"]["sleepy"].__setitem__(field, value))
    with pytest.raises(ConfigError) as exc:
        load_emotions(config)
    assert "sleepy" in str(exc.value) and field in str(exc.value)


@pytest.mark.parametrize("value", [0.8, 1.25])
def test_limits_are_inclusive(tmp_path, value):
    config = _config(tmp_path, lambda d: d["feelings"]["sleepy"].__setitem__("speed", value))
    assert load_emotions(config).feeling("sleepy").speed == value


def test_neutral_must_change_nothing(tmp_path):
    config = _config(tmp_path, lambda d: d["feelings"]["neutral"].__setitem__("gain_db", 1))
    with pytest.raises(ConfigError, match="neutral"):
        load_emotions(config)
    config = _config(tmp_path, lambda d: d["feelings"].pop("neutral"))
    with pytest.raises(ConfigError, match="neutral"):
        load_emotions(config)


def test_missing_or_broken_config_file(tmp_path):
    with pytest.raises(ConfigError, match="emotions.json"):
        load_emotions(tmp_path)
    (tmp_path / "emotions.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="emotions.json"):
        load_emotions(tmp_path)


def test_generation_min_above_max_is_refused(tmp_path):
    config = _config(
        tmp_path, lambda d: d["generation"].update(tags_per_moment_min=3, tags_per_moment_max=2)
    )
    with pytest.raises(ConfigError, match="tags_per_moment"):
        load_emotions(config)


# --- Phase 1: contract (FR-1, AC-2.2) -----------------------------------------------------------


def _script(book_path=MINI_BOOK, seed=7, banter=None):
    book = load_json(book_path)
    plan = plan_episode(book, seed)
    banter = banter or make_banter(book, plan)
    return book, plan, assemble_script(book, load_json(CAST_PATH), plan, banter)


def _qa_line(script):
    page = next(p for p in script["pages"] if p["has_qa"])
    return page, next(line for line in page["lines"] if line["role"] != "narration")


def test_banter_line_takes_an_optional_emotion():
    line = Banter.model_validate(
        {
            "intro": [
                {"speaker": "kid_older", "text": "Hi!", "emotion": "excited"},
                {"speaker": "storyteller", "text": "Hello."},
            ],
            "moments": [],
            "outro": [{"speaker": "storyteller", "text": "Bye."}] * 3,
        }
    ).intro
    assert line[0].emotion == "excited" and line[1].emotion is None


def test_assembled_script_carries_emotion_only_when_set():  # FR-1
    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    data = make_banter(book, plan).model_dump()
    for line in [*data["intro"], *data["outro"], *(x for m in data["moments"] for x in m["lines"])]:
        line.pop("emotion", None)
    data["intro"][0]["emotion"] = "excited"
    data["moments"][0]["lines"][0]["emotion"] = "curious"
    _, _, script = _script(banter=Banter.model_validate(data))
    assert script["intro"]["lines"][0]["emotion"] == "excited"
    assert "emotion" not in script["intro"]["lines"][1]
    _, first = _qa_line(script)
    assert first["emotion"] == "curious"
    assert all("emotion" not in line for line in script["outro"]["lines"])
    narration = [
        line for p in script["pages"] for line in p["lines"] if line["role"] == "narration"
    ]
    assert all("emotion" not in line for line in narration)


def test_unknown_emotion_is_refused_naming_line_and_allowed_list():  # AC-2.2
    book, _, script = _script()
    script["intro"]["lines"][0]["emotion"] = "angry"
    page, line = _qa_line(script)
    line["emotion"] = "grumpy"
    errors = validate_script(script, book, TERMS, emotions=DEFAULT_FEELINGS)
    intro = [e for e in errors if e.startswith("intro line 1")]
    assert intro and "'angry'" in intro[0] and "sleepy" in intro[0] and "neutral" in intro[0]
    assert any(f"page {page['page_number']} Q&A line 1" in e and "grumpy" in e for e in errors)


def test_known_emotion_is_accepted_and_none_list_allows_any():
    book, _, script = _script()
    script["outro"]["lines"][0]["emotion"] = "sleepy"
    assert validate_script(script, book, TERMS, emotions=DEFAULT_FEELINGS) == []
    script["outro"]["lines"][0]["emotion"] = "angry"
    assert validate_script(script, book, TERMS) == []


def test_emotion_must_be_a_string():
    book, _, script = _script()
    script["outro"]["lines"][0]["emotion"] = 3
    errors = validate_script(script, book, TERMS, emotions=DEFAULT_FEELINGS)
    assert any("outro line 1" in e and "emotion" in e for e in errors)


def test_narration_may_not_carry_an_emotion():  # FR-1, non-goal: page text is read as written
    book, _, script = _script()
    script["pages"][0]["lines"][0]["emotion"] = "happy"
    for emotions in (None, DEFAULT_FEELINGS):
        errors = validate_script(script, book, TERMS, emotions=emotions)
        assert any("page 1 narration line 1" in e and "emotion" in e for e in errors)


def test_existing_script_without_emotions_is_byte_identical():  # AC-2.3: old scripts unchanged
    book = load_json(PILOT_BOOK)
    plan = plan_episode(book, 7)
    data = make_banter(book, plan).model_dump(exclude_none=True)
    for line in [*data["intro"], *data["outro"], *(x for m in data["moments"] for x in m["lines"])]:
        line.pop("emotion", None)
    script = assemble_script(book, load_json(CAST_PATH), plan, Banter.model_validate(data))
    assert "emotion" not in json.dumps(script)


def test_narration_tag_limits_from_config(tmp_path):  # spec 028 FR-3
    from video_pipeline.emotions import NarrationLimits

    assert load_emotions(CONFIG_DIR).narration == NarrationLimits(
        per_page_max=1, per_book_ratio=0.5
    )
    config = _config(
        tmp_path, lambda d: d.update(narration_tags={"per_page_max": 2, "per_book_ratio": 1})
    )
    limits = load_emotions(config).narration
    assert (limits.per_page_max, limits.per_book_ratio, limits.book_max(5)) == (2, 1.0, 5)
    assert NarrationLimits(per_page_max=1, per_book_ratio=0.5).book_max(5) == 3  # rounded up


def test_narration_tag_limits_are_optional(tmp_path):  # spec 028: older configs keep working
    config = _config(tmp_path, lambda d: d.pop("narration_tags", None))
    assert load_emotions(config).narration is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("per_page_max", 0), ("per_book_ratio", 2), ("per_book_ratio", 0)],
)
def test_bad_narration_tag_limits_name_the_field(tmp_path, field, value):  # spec 028
    def mutate(data):
        data["narration_tags"] = {"per_page_max": 1, "per_book_ratio": 0.5, field: value}

    with pytest.raises(ConfigError) as exc:
        load_emotions(_config(tmp_path, mutate))
    assert f"narration_tags.{field}" in str(exc.value)


def test_unknown_narration_tag_key_is_refused(tmp_path):
    config = _config(
        tmp_path,
        lambda d: d.update(narration_tags={"per_page_max": 1, "per_book_ratio": 0.5, "x": 1}),
    )
    with pytest.raises(ConfigError, match="narration_tags"):
        load_emotions(config)


def test_config_copy_used_by_test_roots_has_emotions(tmp_path):
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    assert load_emotions(tmp_path / "config").names == load_emotions(CONFIG_DIR).names


# --- Phase 2: generation rules + brief (FR-2; AC-1.1-1.4, AC-2.1, AC-2.4) -----------------------


def _tools(tmp_path):
    from tests.helpers import make_root
    from video_pipeline.episode_tools import EpisodeTools

    return EpisodeTools.load(make_root(tmp_path), 7, CONFIG_DIR)


def _banter(tools) -> dict:
    return make_banter(tools.book, tools.plan).model_dump(exclude_none=True)


def _all_lines(data):
    return [*data["intro"], *(x for m in data["moments"] for x in m["lines"]), *data["outro"]]


def _strip_tags(data):
    import re

    for line in _all_lines(data):
        line["text"] = re.sub(r"<[^<>]*>\s*", "", line["text"])


def test_helper_banter_meets_the_generation_rules(tmp_path):
    tools = _tools(tmp_path)
    assert tools.emotions is not None
    assert tools.check(_banter(tools))[1] == []


def test_too_few_tags_are_refused_with_count_and_minimum(tmp_path):  # AC-1.2
    tools = _tools(tmp_path)
    data = _banter(tools)
    _strip_tags(data)
    m = len(tools.plan.qa_speakers)
    script, errors = tools.check(data)
    assert script is None
    assert any("only 0 sound tags" in e and f"at least {m}" in e for e in errors)


def test_too_many_tags_are_refused_with_count_and_maximum(tmp_path):  # AC-1.3
    tools = _tools(tmp_path)
    data = _banter(tools)
    _strip_tags(data)
    m = len(tools.plan.qa_speakers)
    lines = _all_lines(data)
    for line in lines[: 2 * m + 1]:
        line["text"] = "<giggle> " + line["text"]
    _, errors = tools.check(data)
    assert any(f"{2 * m + 1} sound tags" in e and f"at most {2 * m}" in e for e in errors)


def test_two_tags_in_one_line_are_refused_naming_the_line(tmp_path):  # AC-1.3
    tools = _tools(tmp_path)
    data = _banter(tools)
    data["outro"][1]["text"] = "<laugh> That was so much fun! <giggle>"
    _, errors = tools.check(data)
    assert any(e.startswith("outro line 2") and "more than one sound tag" in e for e in errors)


def test_all_neutral_moment_is_refused(tmp_path):  # AC-2.4
    tools = _tools(tmp_path)
    data = _banter(tools)
    for line in _all_lines(data):
        line.pop("emotion", None)
    data["moments"][1]["lines"][0]["emotion"] = "neutral"
    _, errors = tools.check(data)
    pages = sorted(tools.plan.qa_speakers)
    for number in pages:
        assert any(f"page {number}" in e and "other than neutral" in e for e in errors)


def test_unknown_emotion_in_generated_banter_is_refused(tmp_path):  # AC-2.2
    tools = _tools(tmp_path)
    data = _banter(tools)
    data["intro"][1]["emotion"] = "angry"
    _, errors = tools.check(data)
    assert any(e.startswith("intro line 2") and "'angry'" in e for e in errors)


def test_counts_come_from_config(tmp_path):  # AC-1.5
    tools = _tools(tmp_path)
    data = _banter(tools)
    _strip_tags(data)
    for line in _all_lines(data):
        line.pop("emotion", None)
    gen = tools.emotions.generation.model_copy(
        update={"tags_per_moment_min": 0, "feelings_per_moment_min": 0}
    )
    tools.emotions = tools.emotions.model_copy(update={"generation": gen})
    assert tools.check(data)[1] == []


def test_hand_edited_script_with_no_tags_or_feelings_still_validates(tmp_path):  # AC-1.4
    tools = _tools(tmp_path)
    script, errors = tools.check(_banter(tools))
    assert errors == []
    for line in [*script["intro"]["lines"], *script["outro"]["lines"]]:
        line["text"] = line["text"].replace("<gasp> ", "")
        line.pop("emotion", None)
    for page in script["pages"]:
        for line in page["lines"]:
            line.pop("emotion", None)
            if line["role"] != "narration":
                line["text"] = line["text"].replace("<giggle> ", "").replace("<chuckle> ", "")
    names = tools.emotions.names
    assert "<" not in json.dumps(script)
    assert validate_script(script, tools.book, tools.terms, emotions=names) == []


def test_brief_asks_for_tags_and_feelings_with_counts(tmp_path):  # AC-1.1, AC-2.1
    tools = _tools(tmp_path)
    brief = tools.get_episode_plan()["brief"]
    m = len(tools.plan.qa_speakers)
    assert "FEELINGS" in brief
    assert f"at least {m} and at most {2 * m}" in brief
    assert "one tag per line" in brief or "more than one in a line" in brief
    for name in DEFAULT_FEELINGS:
        assert name in brief
    assert "other than neutral" in brief
    assert "where they fit" in brief


def test_brief_without_feelings_is_unchanged():  # legacy golden brief stays valid
    from video_pipeline.llm import build_brief

    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    cast = load_json(CAST_PATH)
    brief = build_brief(book, cast, plan)
    assert brief == build_brief(book, cast, plan, None, feelings=None)
    assert "FEELINGS" not in brief and "where they fit" not in brief


def test_gemini_prompt_carries_feelings_and_retries_on_tag_problems(tmp_path):  # AC-1.2 retry
    from video_pipeline.generate_script import generate_episode_script

    tools = _tools(tmp_path)
    good = _banter(tools)
    bad = copy.deepcopy(good)
    _strip_tags(bad)
    prompts = []

    def generate(prompt, seed):
        prompts.append(prompt)
        return json.dumps(bad if len(prompts) == 1 else good)

    script = generate_episode_script(tools, generate)
    assert script is not None and len(prompts) == 2
    assert "FEELINGS" in prompts[0]
    assert "only 0 sound tags" in prompts[1]


def test_saved_script_is_revalidated_with_the_feelings_list(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from video_pipeline import generate_script as gs
    from video_pipeline.episode_tools import write_atomic

    tools = _tools(tmp_path)
    script, _ = tools.check(_banter(tools))
    script["intro"]["lines"][0]["emotion"] = "angry"
    write_atomic(tools.out, script)
    result = SimpleNamespace(save_results=[{"saved": True}], reason=None)
    monkeypatch.setattr(gs, "codex_status", lambda: "codex")
    monkeypatch.setattr(gs, "build_codex_command", lambda *a, **k: ["codex"])
    monkeypatch.setattr(gs, "run_codex", lambda *a, **k: result)
    args = SimpleNamespace(book="b", root=tmp_path, seed=7, force=False, model=None)
    folder = SimpleNamespace(build_dir=tmp_path / "build")
    with pytest.raises(gs.GenerationFailed) as exc:
        gs.run_agent(args, tools, folder)
    assert any("'angry'" in e for e in exc.value.errors)
