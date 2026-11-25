import random

import pytest

from tests.blueprint_helpers import context, valid_blueprint
from video_pipeline.art_styles import RANDOM_STYLES, STYLES, apply_style, choose_style, style_art
from video_pipeline.blueprint import Blueprint, blueprint_errors, style_text
from video_pipeline.covers import load_cover_settings
from video_pipeline.illustrate import plan_images
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.write_story import blueprint_prompt


@pytest.mark.parametrize("style_id", STYLES)
def test_presets_fit_blueprint_and_preserve_book_identity(style_id):
    original = valid_blueprint()
    styled = apply_style(original, style_id)
    assert blueprint_errors(styled, context(), "test") == []
    assert styled["art"]["palette"] == original["art"]["palette"]
    for key in ("mood", "qa", "sound", "animation"):
        assert styled[key] == original[key]
    assert original["art"]["medium_key"] != style_id
    assert styled["art"]["medium_key"] == style_id


def test_style_reaches_every_page_and_untitled_cover():
    bp = Blueprint.model_validate(apply_style(valid_blueprint()))
    book = {
        "metadata": {"title": "Cloud Picnic"},
        "pages": [{"page_number": 1, "prompt": "A cloud shares its shade."}],
    }
    images = plan_images(book, style_text(bp))
    assert len(images) == 3
    assert all("Original Giggle & Snooze" in prompt for _, prompt in images)
    assert all("broken dry-brush edges" in prompt for _, prompt in images)


def test_design_brief_keeps_selected_style_within_book():
    prompt = blueprint_prompt({"title": "Cloud Picnic"}, {}, context(), style_id="paper-cut")
    assert '"medium_key": "paper-cut"' in prompt
    assert "Repeating a style is required" in prompt


def test_selection_always_uses_approved_house_style():
    rng = random.Random(91)
    picks = [choose_style(["pencil-and-brush"] * 5, rng=rng) for _ in range(100)]
    assert set(picks) == set(RANDOM_STYLES) == {"pencil-and-brush"}


def test_legacy_book_style_reaches_pages_and_covers():
    book = {
        "metadata": {"title": "Lighthouse"},
        "style_bible": style_art("paper-cut"),
        "pages": [{"page_number": 1, "prompt": "A lighthouse hops."}],
    }
    assert all("cut-paper" in prompt for _, prompt in plan_images(book))
    assert all(
        "Explicit blueprint style" in prompt
        for _, prompt in plan_images(book, "Explicit blueprint style")
    )


def test_titled_covers_use_the_same_preset_as_pages():
    bp = Blueprint.model_validate(apply_style(valid_blueprint()))
    book = {
        "metadata": {"title": "Cloud Picnic", "cover_style": "titled"},
        "pages": [{"page_number": 1, "prompt": "A cloud shares its shade."}],
    }
    images = dict(
        plan_images(
            book,
            style_text(bp),
            art=bp.art.model_dump(),
            settings=load_cover_settings(PROJECT_ROOT / "config"),
        )
    )
    assert set(images) == {"page_01", "cover_front", "cover_front_print", "cover_back"}
    assert all("broken dry-brush edges" in prompt for prompt in images.values())


def test_house_style_can_repeat_mood_without_rejecting_new_books():
    from tests.test_blueprint import CFG
    from video_pipeline.blueprint import similarity_problems

    original = Blueprint.model_validate(apply_style(valid_blueprint()))
    errors = similarity_problems(original, {"Earlier book": original}, CFG)
    assert not any("art.medium_key" in error for error in errors)
    assert any("palette" in error for error in errors)
