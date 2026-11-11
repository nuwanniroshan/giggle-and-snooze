"""Pictures, Q&A and ambience follow the blueprint; no blueprint changes nothing (spec 011 P3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import (
    CAST_PATH,
    MINI_BOOK,
    PILOT_BOOK,
    ROOT,
    load_json,
    make_banter,
    make_root,
)
from video_pipeline import mcp_server
from video_pipeline.blueprint import (
    Blueprint,
    BlueprintError,
    assign_ambience,
    blueprint_sha256,
    load_context,
    read_use,
    rebuild_style_bible,
    style_text,
)
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode import assemble_script, plan_episode
from video_pipeline.episode_tools import CONFIG_DIR, EpisodeTools, write_atomic
from video_pipeline.file_hash import script_sha256
from video_pipeline.illustrate import plan_images
from video_pipeline.llm import build_brief

CAST = load_json(CAST_PATH)
TERMS = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
GOLDEN = load_json(ROOT / "tests" / "fixtures" / "legacy_golden.json")


def parsed_blueprint(**changes) -> Blueprint:
    return Blueprint.model_validate(valid_blueprint(**changes))


@pytest.fixture(scope="module")
def book8() -> dict:
    return load_json(MINI_BOOK)


@pytest.fixture(scope="module")
def book20() -> dict:
    return load_json(PILOT_BOOK)


def _script(book: dict, bp: Blueprint | None, seed: int = 7) -> dict:
    plan = plan_episode(book, seed, bp)
    amb = assign_ambience(bp.sound.ambience, len(book["pages"])) if bp else None
    return assemble_script(book, CAST, plan, make_banter(book, plan), amb)


def test_picture_request_has_blueprint_art_and_no_blueprint_is_unchanged(book8):  # AC-3.1, 3.2
    plain = plan_images(book8)
    styled = plan_images(book8, style=style_text(parsed_blueprint()))
    assert plain == plan_images(book8)
    assert all("Soft chalk-pastel" in prompt and "#1b2a49" in prompt for _, prompt in styled)
    assert [s for s, _ in styled] == [s for s, _ in plain]
    assert "cover_front" in [s for s, _ in styled]


def test_picture_request_has_colour_names_lighting_avoid_and_no_default_style(book20):  # AC-3.1
    from video_pipeline.illustrate import STYLE_LINE, build_image_command

    bp = parsed_blueprint()
    default_style = book20["style_bible"]["style_prompt_prefix"]
    for stem, prompt in plan_images(book20, style=style_text(bp)):
        request = build_image_command(Path("/fake/codex"), prompt, None)[-1]
        for swatch in bp.art.palette:
            assert swatch.name in request and swatch.hex in request, stem
        assert bp.art.lighting in request and bp.art.style_prompt_prefix in request
        assert "photographic realism" in request  # the avoid list
        assert default_style not in request, stem
        assert request.count(STYLE_LINE) == 1  # the unchanged line, nothing else of the old style
        styled_part = prompt.split("\n\n")[0]
        assert "watercolor" not in styled_part and "gouache" not in styled_part


def test_rebuild_style_bible_changes_only_that_field(tmp_path):  # AC-3.4
    folder = make_root(tmp_path)
    before = json.loads(folder.book_json.read_text(encoding="utf-8"))
    before.setdefault("style_bible", {})["style_prompt_prefix"] = "old"
    write_atomic(folder.book_json, before)
    write_atomic(folder.blueprint_path, valid_blueprint())
    rebuild_style_bible(folder, parsed_blueprint())
    after = json.loads(folder.book_json.read_text(encoding="utf-8"))
    assert after["style_bible"]["style_prompt_prefix"].startswith("Soft chalk-pastel")
    expected = copy.deepcopy(before)
    expected["style_bible"]["style_prompt_prefix"] = after["style_bible"]["style_prompt_prefix"]
    assert after == expected


def test_no_blueprint_plan_and_brief_are_unchanged(book8):  # AC-4.4
    assert plan_episode(book8, 7) == plan_episode(book8, 7, None)
    plan = plan_episode(book8, 7)
    assert plan.notes == ()
    assert build_brief(book8, CAST, plan) == build_brief(book8, CAST, plan, None)


def test_no_blueprint_reproduces_the_pilot_golden_values(book20):  # AC-7.1
    import hashlib

    plan = plan_episode(book20, GOLDEN["seed"], None)
    assert {str(k): list(v) for k, v in plan.qa_speakers.items()} == GOLDEN["plan"]["qa_speakers"]
    assert list(plan.motions) == GOLDEN["plan"]["motions"]
    brief = build_brief(book20, CAST, plan, None)
    assert hashlib.sha256(brief.encode("utf-8")).hexdigest() == GOLDEN["brief_sha256"]
    assert [list(x) for x in plan_images(book20)] == GOLDEN["image_plan"]
    script = _script(book20, None, GOLDEN["seed"])
    assert validate_script(script, book20, TERMS) == GOLDEN["script_validation_errors"]


def test_blueprint_brief_uses_its_format_and_drops_the_fixed_rule(book8):  # AC-4.1, AC-5.1
    plan = plan_episode(book8, 7)
    brief = build_brief(book8, CAST, plan, parsed_blueprint())
    assert "Bedtime wonderings" in brief
    assert "what if" in brief and "who is sleepy" in brief  # question types
    assert "Whispers guesses." in brief and "Counts stars." in brief
    assert "The kids yawn and settle in." in brief and "The kids say goodnight." in brief
    assert "Whispers the big moments." in brief and "hushed" in brief
    assert "kid_older asks curious questions about the plot" not in brief
    assert "crickets_night" in brief and "owl_hoot" in brief
    assert "only names allowed in `ambience` lists" in brief  # AC-5.1
    assert "assigned to the script automatically" in brief
    assert "Kid lines are at most 25 words." in brief  # RULES stay


def test_moment_count_stays_in_blueprint_range_inside_spec_001_bounds(book20):  # AC-4.2
    bp = parsed_blueprint(**{"qa.moments_min": 7, "qa.moments_max": 7})
    for seed in range(20):
        assert len(plan_episode(book20, seed, bp).qa_speakers) == 7


def test_out_of_bounds_range_is_clamped_and_noted(book20):  # AC-4.2
    bp = parsed_blueprint(**{"qa.moments_min": 2, "qa.moments_max": 3})
    plan = plan_episode(book20, 7, bp)
    assert len(plan.qa_speakers) == 7 and any("clamped" in n for n in plan.notes)


def test_generated_script_passes_every_spec_001_rule_with_a_blueprint(book20, tmp_path):  # AC-4.3
    bp = parsed_blueprint()
    tools = EpisodeTools(book20, CAST, TERMS, 7, tmp_path / "s.json", blueprint=bp)
    script, errors = tools.check(make_banter(book20, tools.plan).model_dump())
    assert errors == [] and script is not None
    assert tools.get_episode_plan()["brief"] == build_brief(book20, CAST, tools.plan, bp)


def test_script_ambience_is_assigned_from_the_blueprint_and_absent_without_one(book20):
    bp = parsed_blueprint()  # AC-5.5, AC-5.7
    script = _script(book20, bp)
    assert script["intro"]["ambience"] == ["crickets_night"]
    assert script["outro"]["ambience"] == ["owl_hoot"]
    assert all(isinstance(p["ambience"], list) and p["ambience"] for p in script["pages"])
    plain = _script(book20, None)
    assert "ambience" not in plain["intro"] and "ambience" not in plain["outro"]
    assert all("ambience" not in p for p in plain["pages"])


def test_ambience_outside_the_palette_is_refused_naming_sound_and_place(book20):  # AC-5.2
    script = _script(book20, parsed_blueprint())
    errors = validate_script(script, book20, TERMS, allowed_ambience={"crickets_night"})
    assert any("owl_hoot" in e and "page" in e for e in errors)
    assert any("owl_hoot" in e and "outro" in e for e in errors)
    ok = validate_script(script, book20, TERMS, allowed_ambience={"crickets_night", "owl_hoot"})
    assert ok == []


def test_hand_edited_ambience_inside_the_palette_keeps_the_voice_hash(book20, tmp_path):  # AC-5.6
    script = _script(book20, parsed_blueprint())
    before = tmp_path / "a.json"
    write_atomic(before, script)
    edited = copy.deepcopy(script)
    edited["pages"][0]["ambience"] = ["owl_hoot"]
    after = tmp_path / "b.json"
    write_atomic(after, edited)
    assert script_sha256(before) == script_sha256(after)


def test_ambience_is_checked_against_the_library_only_without_a_blueprint(book20):  # AC-5.3
    script = _script(book20, None)
    script["pages"][0]["ambience"] = ["rain_soft"]  # in no blueprint
    assert validate_script(script, book20, TERMS, None) == []
    errors = validate_script(script, book20, TERMS, {"crickets_night", "owl_hoot"})
    assert any("rain_soft" in e and "page 1" in e for e in errors)


def _root_with_blueprint(tmp_path, data=None):
    folder = make_root(tmp_path)
    (tmp_path / "assets").mkdir(exist_ok=True)
    link = tmp_path / "assets" / "sound_library"
    if not link.exists():
        link.symlink_to(ROOT / "assets" / "sound_library")
    write_atomic(folder.blueprint_path, data or valid_blueprint())
    return folder


def test_load_reads_the_books_blueprint_and_plans_with_it(tmp_path):
    folder = _root_with_blueprint(tmp_path)
    assert load_context(tmp_path).sounds  # library reachable
    tools = EpisodeTools.load(folder, 7, tmp_path / "config")
    assert tools.blueprint is not None and tools.blueprint.mood.key == "sleepy-drift"
    script, errors = tools.check(make_banter(tools.book, tools.plan).model_dump())
    assert errors == [] and script["intro"]["ambience"] == ["crickets_night"]
    assert EpisodeTools.load(make_root(tmp_path / "x"), 7, CONFIG_DIR).blueprint is None


def test_bad_blueprint_is_a_load_error_in_mcp_server_and_generate_script(
    tmp_path, capsys, monkeypatch
):
    from video_pipeline import generate_script

    folder = _root_with_blueprint(tmp_path, valid_blueprint(**{"art.medium_key": "Bad Key"}))
    with pytest.raises(BlueprintError):
        EpisodeTools.load(folder, 7, tmp_path / "config")
    assert mcp_server.main(["--book", folder.slug, "--root", str(tmp_path)]) == 1
    assert "medium_key" in capsys.readouterr().err
    assert generate_script.main(["--book", folder.slug, "--root", str(tmp_path)]) == 1
    assert "medium_key" in capsys.readouterr().err


def test_generate_script_prints_notes_and_records_use(tmp_path, capsys, monkeypatch):
    from video_pipeline import generate_script

    folder = _root_with_blueprint(tmp_path)

    def fake_generate(prompt, seed):
        tools = EpisodeTools.load(folder, 7, tmp_path / "config")
        return make_banter(tools.book, tools.plan).model_dump_json()

    monkeypatch.setattr(generate_script, "generate_cloud", lambda p, s, m: fake_generate(p, s))
    code = generate_script.main(
        ["--book", folder.slug, "--root", str(tmp_path), "--provider", "gemini-api"]
    )
    out = capsys.readouterr().out
    assert code == 0 and "clamped" in out  # 7-8 clamped to 3-3 for the 8-page mini book
    assert read_use(folder)["script"] == blueprint_sha256(folder.blueprint_path)


def test_studio_hand_edit_outside_the_palette_is_refused(tmp_path):  # AC-5.2
    from video_pipeline.studio import books as sb

    folder = _root_with_blueprint(tmp_path)
    tools = EpisodeTools.load(folder, 7, tmp_path / "config")
    script, _ = tools.check(make_banter(tools.book, tools.plan).model_dump())
    script["pages"][0]["ambience"] = ["rain_soft"]
    write_atomic(folder.script_path, script)
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, folder.slug)
    edits = {"intro-01": script["intro"]["lines"][0]["text"] + " Ok."}
    problems = sb.save_line_edits(paths, book, edits)
    assert any("rain_soft" in p for p in problems)


def _reviewed_script(tmp_path):
    from video_pipeline.studio import books as sb

    folder = _root_with_blueprint(tmp_path)
    tools = EpisodeTools.load(folder, 7, tmp_path / "config")
    script, _ = tools.check(make_banter(tools.book, tools.plan).model_dump())
    write_atomic(folder.script_path, script)
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, folder.slug)
    sb.mark_reviewed(paths, book)
    return sb, paths, book, folder, script


def test_hand_edit_inside_the_palette_keeps_edit_and_review_mark(tmp_path):  # AC-5.6
    sb, paths, book, folder, script = _reviewed_script(tmp_path)
    assert sb.reviewed_mark(paths, book) is not None
    script["pages"][0]["ambience"] = ["owl_hoot"]  # a hand edit inside the palette
    write_atomic(folder.script_path, script)
    assert sb.reviewed_mark(paths, book) is not None and sb.reviewed_path(paths, book).exists()
    same = {"intro-01": script["intro"]["lines"][0]["text"]}  # no line changes: a no-op check
    assert sb.save_line_edits(paths, book, same) == []
    assert json.loads(folder.script_path.read_text(encoding="utf-8"))["pages"][0]["ambience"] == [
        "owl_hoot"
    ]
    assert sb.reviewed_mark(paths, book) is not None


def test_studio_refuses_an_invalid_blueprint_naming_file_and_field(tmp_path):  # AC-6.7
    sb, paths, book, folder, script = _reviewed_script(tmp_path)
    write_atomic(folder.blueprint_path, valid_blueprint(**{"art.medium_key": "Bad Key"}))
    problems = sb.save_line_edits(
        paths, book, {"intro-01": script["intro"]["lines"][0]["text"] + " Ok."}
    )
    assert problems and "blueprint.json" in problems[0] and "art.medium_key" in problems[0]


def test_illustrate_refuses_an_invalid_blueprint_naming_file_and_field(
    tmp_path, capsys, monkeypatch
):  # AC-6.7
    from video_pipeline import illustrate

    folder = _root_with_blueprint(tmp_path, valid_blueprint(**{"art.medium_key": "Bad Key"}))
    monkeypatch.setattr(illustrate, "codex_status", lambda: Path("/fake/codex"))
    assert illustrate.main(["--book", folder.slug, "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "blueprint.json" in err and "art.medium_key" in err
