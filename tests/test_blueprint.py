from __future__ import annotations

import pytest

from tests.blueprint_helpers import context, valid_blueprint
from video_pipeline import blueprint as bp

CFG = bp.BlueprintConfig(
    distance=20.0,
    medium_synonyms={"gouache": "painted-water-media", "watercolor": "painted-water-media"},
    mood_synonyms={},
    format_synonyms={},
)


def parsed(**changes) -> bp.Blueprint:
    return bp.Blueprint.model_validate(valid_blueprint(**changes))


def test_valid_blueprint_has_no_errors():
    assert bp.blueprint_errors(valid_blueprint(), context(), "blueprint.json") == []


@pytest.mark.parametrize(
    "changes, needle",
    [
        ({"art.palette": [{"name": "a", "hex": "#000000"}] * 4}, "art.palette"),  # FR-1: exactly 5
        ({"art.palette": [{"name": "a", "hex": "red"}] * 5}, "art.palette.0.hex"),  # #rrggbb
        ({"mood.key": "Bad Key"}, "mood.key"),  # slug rule
        ({"qa.moments_min": 9, "qa.moments_max": 8}, "moments_min"),  # min <= max
        ({"animation.intensity": 3.0}, "animation.intensity"),  # 0.25..2.0
        ({"sound.ambience": []}, "sound.ambience"),  # 1..6
    ],
)
def test_field_rules_name_file_and_field(changes, needle):  # AC-6.7, FR-3
    errors = bp.blueprint_errors(valid_blueprint(**changes), context(), "books/x/blueprint.json")
    assert errors and all(e.startswith("books/x/blueprint.json: ") for e in errors)
    assert any(needle in e for e in errors)


def test_unknown_field_is_an_error():
    data = valid_blueprint()
    data["extra"] = 1
    assert any("extra" in e for e in bp.blueprint_errors(data, context(), "f"))


def test_disallowed_and_gentle_terms_are_refused():
    errors = bp.blueprint_errors(
        valid_blueprint(**{"rationale": "a gun and a monster"}), context(), "f"
    )
    assert any("'gun'" in e for e in errors) and any("'monster'" in e for e in errors)


def test_unknown_sound_wrong_kind_and_unknown_effect_are_named():  # AC-5.4
    data = valid_blueprint(
        **{
            "sound.ambience": ["nope", "bell_chime"],
            "sound.sfx": ["rain_soft"],
            "animation.effects": ["lava"],
        }
    )
    text = "\n".join(bp.blueprint_errors(data, context(), "f"))
    assert (
        "'nope'" in text and "'bell_chime'" in text and "'rain_soft'" in text and "'lava'" in text
    )


def test_load_blueprint_refuses_bad_json_and_names_the_file(tmp_path):  # AC-6.7
    path = tmp_path / "blueprint.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(bp.BlueprintError, match="blueprint.json"):
        bp.load_blueprint(path, context())


# --- distinctness (FR-4) ---------------------------------------------------------------------


def test_same_medium_and_mood_is_rejected_naming_the_book():  # AC-2.1
    other = parsed()
    candidate = parsed(
        **{
            "qa.format_key": "other-format",
            "art.palette": [
                {"name": f"c{i}", "hex": h}
                for i, h in enumerate(["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff"])
            ],
        }
    )
    problems = bp.similarity_problems(candidate, {"Moon Book": other}, CFG)
    assert len(problems) == 1 and "Moon Book" in problems[0]
    assert "art.medium_key" in problems[0] and "mood.key" in problems[0]


def test_same_format_key_is_rejected():  # AC-2.2
    other = parsed(**{"art.medium_key": "ink", "mood.key": "spooky-fun"})
    problems = bp.similarity_problems(parsed(), {"B": other}, CFG)
    assert any("qa.format_key" in p for p in problems)


def test_three_close_palette_colours_are_rejected_naming_them():  # AC-2.3
    other = parsed(**{"art.medium_key": "ink", "mood.key": "x1", "qa.format_key": "f1"})
    problems = bp.similarity_problems(
        parsed(**{"art.medium_key": "oil", "mood.key": "x2", "qa.format_key": "f2"}),
        {"B": other},
        CFG,
    )
    assert any("palette" in p and "midnight" in p for p in problems)


def test_two_close_colours_pass():
    other = parsed(**{"art.medium_key": "ink", "mood.key": "x1", "qa.format_key": "f1"})
    cand = valid_blueprint(**{"art.medium_key": "oil", "mood.key": "x2", "qa.format_key": "f2"})
    for i in (2, 3, 4):
        cand["art"]["palette"][i]["hex"] = ["#ff0000", "#00ff00", "#0000ff"][i - 2]
    assert bp.similarity_problems(bp.Blueprint.model_validate(cand), {"B": other}, CFG) == []


def test_all_failed_checks_are_reported_together():
    problems = bp.similarity_problems(parsed(), {"B": parsed()}, CFG)
    assert len(problems) == 3


def test_synonym_keys_compare_as_their_canonical_key():  # AC-2.6
    a = parsed(**{"art.medium_key": "gouache", "qa.format_key": "f1"})
    b = parsed(**{"art.medium_key": "watercolor", "qa.format_key": "f2"})
    assert any("art.medium_key" in p for p in bp.similarity_problems(a, {"B": b}, CFG))


def test_unknown_but_valid_key_is_compared_as_itself():  # AC-2.7
    a = parsed(**{"art.medium_key": "felt-collage", "qa.format_key": "f1"})
    b = parsed(**{"art.medium_key": "felt-collage", "qa.format_key": "f2"})
    assert any("art.medium_key" in p for p in bp.similarity_problems(a, {"B": b}, CFG))


def test_delta_e76_known_values():
    assert bp.delta_e76("#336699", "#336699") == pytest.approx(0.0)
    assert bp.delta_e76("#000000", "#ffffff") == pytest.approx(100.0, abs=0.5)


# --- ambience placement (AC-5.5) and range clamp (AC-4.2) ------------------------------------


def test_assign_ambience_runs_in_palette_order_with_first_and_last_on_the_bookends():
    a = bp.assign_ambience(["rain", "wind", "owl"], 9)
    assert a.intro == ["rain"] and a.outro == ["owl"]
    assert [p[0] for p in a.pages] == ["rain"] * 3 + ["wind"] * 3 + ["owl"] * 3
    assert bp.assign_ambience(["rain", "wind", "owl"], 9) == a  # deterministic


def test_every_name_is_used_when_palette_is_not_longer_than_the_book():
    names = ["a", "b", "c", "d", "e", "f"]
    used = {p[0] for p in bp.assign_ambience(names, 8).pages}
    assert used == set(names)


@pytest.mark.parametrize(
    "lo, hi, low, high, expected",
    [
        (7, 8, 7, 8, (7, 8)),
        (1, 12, 7, 8, (7, 8)),
        (8, 8, 7, 8, (8, 8)),
        (2, 3, 7, 8, (7, 7)),
        (11, 12, 7, 8, (8, 8)),
    ],
)
def test_clamp_range(lo, hi, low, high, expected):
    assert bp.clamp_range(lo, hi, low, high) == expected


# --- others_for (AC-2.8, AC-2.9) -------------------------------------------------------------


def _write_book(root, slug, blueprint=True, text=None):
    import json

    folder = root / "books" / slug
    folder.mkdir(parents=True)
    (folder / "book.json").write_text("{}", encoding="utf-8")
    if text is not None:
        (folder / "blueprint.json").write_text(text, encoding="utf-8")
    elif blueprint:
        (folder / "blueprint.json").write_text(json.dumps(valid_blueprint()), encoding="utf-8")


def _write_draft(root, draft_id, title):
    import json

    folder = root / "drafts" / draft_id
    folder.mkdir(parents=True)
    (folder / "draft.json").write_text(
        json.dumps({"id": draft_id, "idea": {"title": title}}), encoding="utf-8"
    )
    (folder / "blueprint.json").write_text(json.dumps(valid_blueprint()), encoding="utf-8")


def test_others_for_lists_books_and_drafts_and_honours_exclude(tmp_path):
    _write_book(tmp_path, "a")
    _write_book(tmp_path, "b")
    _write_book(tmp_path, "plain", blueprint=False)
    _write_draft(tmp_path, "d1", "Draft Title")
    ctx = context()
    assert set(bp.others_for(tmp_path, ctx)) == {"a", "b", "Draft Title"}
    assert set(bp.others_for(tmp_path, ctx, ("book", "a"))) == {"b", "Draft Title"}
    assert set(bp.others_for(tmp_path, ctx, ("draft", "d1"))) == {"a", "b"}


def test_others_for_skips_unreadable_files(tmp_path):
    _write_book(tmp_path, "a")
    _write_book(tmp_path, "broken", text="{")
    assert set(bp.others_for(tmp_path, context())) == {"a"}
