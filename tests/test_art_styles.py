import pytest

from tests.blueprint_helpers import context, valid_blueprint
from video_pipeline.art_styles import DEFAULT_STYLE, STYLES, apply_style
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
    assert all("restrained soft shading" in prompt for _, prompt in images)


def test_design_brief_defaults_to_brand_preset():
    prompt = blueprint_prompt({"title": "Cloud Picnic"}, {}, context())
    assert DEFAULT_STYLE in prompt
    assert "not by\n   inventing another art medium" in prompt


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
    assert all("restrained soft shading" in prompt for prompt in images.values())
