"""Cover settings and the titled-cover marker (spec 022, phase 0)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import ROOT
from video_pipeline import covers


def test_settings_load_from_config():
    settings = covers.load_cover_settings(ROOT / "config")
    assert settings.logo == Path("assets/brand/giggle_and_snooze_logo.png")
    assert (ROOT / settings.logo).is_file()
    assert settings.logo_name == "Giggle & Snooze"
    assert (settings.logo_width_pct, settings.safe_side_pct, settings.safe_top_bottom_pct) == (
        14,
        12,
        5,
    )
    assert settings.tries == 3
    assert tuple(settings.shapes) == covers.TITLED_STEMS
    video = settings.shapes["cover_front"]
    assert video.ratio == (4, 3) and video.accept == (1.0, None)
    print_shape = settings.shapes["cover_front_print"]
    assert print_shape.ratio == (1, 1) and print_shape.accept == (0.77, 1.3)
    assert print_shape.label == "print cover" and "1024x1024" in print_shape.frame
    assert len(settings.layout_rules) == 4


def _copy_config(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(ROOT / "config" / "covers.json", config / "covers.json")
    return config


def test_missing_or_bad_settings_name_the_field(tmp_path):
    with pytest.raises(covers.CoverError, match="covers.json"):
        covers.load_cover_settings(tmp_path)  # no file at all

    config = _copy_config(tmp_path)
    path = config / "covers.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    del data["tries"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(covers.CoverError, match="tries"):
        covers.load_cover_settings(config)

    data = json.loads((ROOT / "config" / "covers.json").read_text(encoding="utf-8"))
    data["shapes"]["cover_front"]["ratio"] = [4, 0]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(covers.CoverError, match="cover_front.*ratio"):
        covers.load_cover_settings(config)


def test_titled_covers_only_with_marker():
    assert covers.titled_covers({"metadata": {"cover_style": "titled"}})
    assert not covers.titled_covers({"metadata": {}})
    assert not covers.titled_covers({"metadata": {"cover_style": "plain"}})
    assert not covers.titled_covers({})
    assert not covers.titled_covers({"metadata": None})


def test_book_defaults_mark_new_books_titled():
    defaults = json.loads((ROOT / "config" / "book_defaults.json").read_text(encoding="utf-8"))
    assert defaults["metadata"]["cover_style"] == "titled"


# --- Phase 1: cover requests, trim and read-back -------------------------------------------

ART = {
    "style_prompt_prefix": "Soft gouache on cold-press paper, gentle grain.",
    "medium_label": "gouache",
    "line": "Loose brown pencil outlines.",
    "lighting": "Warm late-afternoon light.",
    "palette": [{"name": "moss", "hex": "#6B8F3A"}, {"name": "plum", "hex": "#6A3D6E"}],
    "avoid": ["neon colours", "photo realism"],
}


def _book(title: str = "Mira and the Moon Kite", subtitle: str = "A Windy Hill Story") -> dict:
    return {
        "metadata": {
            "title": title,
            "subtitle": subtitle,
            "blurb": "Mira builds a kite that wants to reach the moon.",
        },
        "style_bible": {"style_prompt_prefix": "Bright crayon drawing on brown paper."},
        "pages": [{"page_number": 1, "prompt": "one"}],
    }


def _settings() -> covers.CoverSettings:
    return covers.load_cover_settings(ROOT / "config")


def test_request_has_exact_words_art_layout_and_safe_area():  # AC-1.2, AC-1.3
    settings = _settings()
    request = covers.cover_request(_book(), ART, "cover_front", settings)
    assert request.startswith(
        "Create the finished FRONT COVER of a children's picture book, "
        + settings.shapes["cover_front"].frame
    )
    assert 'Book: "Mira and the Moon Kite" — subtitle "A Windy Hill Story".' in request
    assert "Story: Mira builds a kite that wants to reach the moon." in request
    assert "Soft gouache on cold-press paper, gentle grain." in request
    assert "Medium: gouache. Line: Loose brown pencil outlines." in request
    assert "Lighting: Warm late-afternoon light." in request
    assert "Palette: moss #6B8F3A, plum #6A3D6E. Avoid: neon colours, photo realism." in request
    assert 'Reference image 1 is the channel logo "Giggle & Snooze".' in request
    assert "Reference image 2 is a page from this book" in request
    for rule in settings.layout_rules:
        assert f"- {rule}" in request
    assert "about 14% of the cover width" in request
    assert (
        "at least 12% of the finished cover's width away from the left and right edges" in request
    )
    assert "at least 5% from the top and bottom edges" in request
    assert "Bright crayon" not in request  # the blueprint wins over the style bible
    order = [
        request.index("Book:"),
        request.index("Art style"),
        request.index("Reference image 1"),
        request.index("Cover layout"),
    ]
    assert order == sorted(order)


def test_request_without_blueprint_uses_style_bible():  # AC-1.4
    request = covers.cover_request(_book(), None, "cover_front", _settings())
    assert "Art style (use it for the picture AND the lettering):\n" in request
    assert "Bright crayon drawing on brown paper." in request
    assert "Medium:" not in request and "Palette:" not in request
    bare = _book()
    del bare["style_bible"]
    assert "Art style" not in covers.cover_request(bare, None, "cover_front", _settings())


def test_requests_differ_per_book():  # AC-1.5: nothing fixed is shared between books
    settings = _settings()
    one = covers.cover_request(_book(), ART, "cover_front", settings)
    other_art = dict(ART, style_prompt_prefix="Cut-paper collage.", medium_label="collage")
    pip = _book("Pip's Big Splash", "A Puddle Tale")
    pip["metadata"]["blurb"] = "Pip the duck finds the biggest puddle in town."
    two = covers.cover_request(pip, other_art, "cover_front", settings)
    assert one != two
    assert "Pip's Big Splash" in two and "Mira" not in two
    assert "Cut-paper collage." in two and "gouache" not in two


def test_print_request_is_square():  # AC-2.1
    settings = _settings()
    request = covers.cover_request(_book(), ART, "cover_front_print", settings)
    assert settings.shapes["cover_front_print"].frame in request
    assert "square 1:1" in request and "landscape 4:3" not in request
    with pytest.raises(covers.CoverError):
        covers.cover_request(_book(), ART, "cover_back", settings)


def test_request_adds_character_looks():  # spec 018
    from video_pipeline import characters

    book = _book()
    book["style_bible"]["characters"] = [
        {"name": "Mira", "look": "A small girl with a yellow scarf and two bunches."}
    ]
    request = covers.cover_request(book, ART, "cover_front", _settings())
    looks = characters.cover_characters_text(book)
    assert looks and request.endswith("\n\n" + looks)
    assert "Mira: A small girl with a yellow scarf" in request
    assert characters.COVER_HEADING not in covers.cover_request(
        _book(), ART, "cover_front", _settings()
    )


def test_cover_command_attaches_logo_then_page_and_no_style_line(tmp_path):  # AC-1.3
    from video_pipeline import illustrate

    binary, logo, page = tmp_path / "codex", tmp_path / "logo.png", tmp_path / "page_01.png"
    command = covers.build_cover_command(binary, "REQUEST", logo, page)
    assert command[:10] == illustrate.build_image_command(binary, "x", None)[:10]
    images = [command[i + 1] for i, arg in enumerate(command) if arg == "-i"]
    assert images == [str(logo), str(page)]
    assert command[-2] == "--"
    assert command[-1].startswith("Use the image generation tool to create this picture")
    assert command[-1].endswith("\n\nREQUEST")
    assert illustrate.STYLE_LINE not in command[-1]
    no_page = covers.build_cover_command(binary, "REQUEST", logo, None)
    assert [a for a in no_page if a == "-i"] == ["-i"]


def _png(path, size):
    from PIL import Image

    Image.new("RGBA", size, (200, 100, 50, 255)).save(path)
    return path


def test_trim_to_4_3_and_square_exactly(tmp_path):  # AC-1.1, AC-2.1
    from PIL import Image

    settings = _settings()
    wide = _png(tmp_path / "cover_front.png", (1536, 1024))
    covers.trim_to_ratio(wide, settings.shapes["cover_front"])
    with Image.open(wide) as im:
        width, height = im.size
        assert im.mode == "RGB"
    assert width * 3 == height * 4 and height >= 1000
    square = _png(tmp_path / "cover_front_print.png", (1024, 1024))
    covers.trim_to_ratio(square, settings.shapes["cover_front_print"])
    with Image.open(square) as im:
        assert im.size == (1024, 1024)
    tall_ok = _png(tmp_path / "tall.png", (1000, 1200))  # 0.83, within the print range
    covers.trim_to_ratio(tall_ok, settings.shapes["cover_front_print"])
    with Image.open(tall_ok) as im:
        assert im.size == (1000, 1000)


def test_trim_refuses_portrait_video_and_wide_print(tmp_path):
    settings = _settings()
    portrait = _png(tmp_path / "cover_front.png", (1024, 1536))
    with pytest.raises(
        covers.CoverError,
        match=r"cover_front.png: came back 1024x1536, outside "
        r"the accepted shape for the video cover",
    ):
        covers.trim_to_ratio(portrait, settings.shapes["cover_front"])
    wide = _png(tmp_path / "cover_front_print.png", (1536, 1024))
    with pytest.raises(covers.CoverError, match="print cover"):
        covers.trim_to_ratio(wide, settings.shapes["cover_front_print"])


def test_check_command_sends_only_the_cover_and_schema(tmp_path):  # FR-4
    binary, cover = tmp_path / "codex", tmp_path / "cover_front.png"
    schema, reply = tmp_path / "schema.json", tmp_path / "reply.json"
    command = covers.build_check_command(binary, cover, schema, reply, "Giggle & Snooze")
    assert command[0] == str(binary) and "exec" in command and "--ephemeral" in command
    images = [command[i + 1] for i, arg in enumerate(command) if arg == "-i"]
    assert images == [str(cover)]
    assert command[command.index("--output-schema") + 1] == str(schema)
    assert command[command.index("-o") + 1] == str(reply)
    assert command[-2] == "--"
    task = command[-1]
    assert '"title"' in task and '"subtitle"' in task and '"logo_whole"' in task
    assert '"Giggle & Snooze"' in task
    assert "Mira and the Moon Kite" not in " ".join(command)
    assert set(covers.CHECK_SCHEMA["required"]) == {"title", "subtitle", "logo_whole"}
    assert covers.CHECK_SCHEMA["additionalProperties"] is False


def test_parse_reading_ok_and_bad():
    reading = covers.parse_reading(
        '\n {"title": "Mira and the Moon Kite", "subtitle": "", "logo_whole": true}\n'
    )
    assert reading == covers.CoverReading("Mira and the Moon Kite", "", True)
    for bad in (
        "",
        "not json",
        "[]",
        '{"title": "A", "subtitle": "B"}',
        '{"title": "A", "subtitle": "B", "logo_whole": "yes"}',
        '{"title": 3, "subtitle": "B", "logo_whole": true}',
        '{"title": "A", "subtitle": "B", "logo_whole": true, "extra": 1}',
    ):
        with pytest.raises(covers.CoverError, match="cover check gave no readable answer"):
            covers.parse_reading(bad)


def test_words_match_ignores_case_breaks_spaces_quotes():  # AC-3.2
    title = "Mira's Moon Kite"
    assert covers.words_match(title, "MIRA’S\nMOON   kite")
    assert covers.words_match(' "Hello" ', "“Hello”")
    assert not covers.words_match(title, "Mira's Mon Kite")
    assert not covers.words_match(title, "Mira's Moon Kite Race")
    assert not covers.words_match(title, "Miras Moon Kite")
    assert not covers.words_match(title, "Mira's Moon Kite!")


def test_check_problem_reasons():
    ok = covers.CoverReading("Mira and the Moon Kite", "A Windy Hill Story", True)
    assert covers.check_problem(ok, "Mira and the Moon Kite", "A Windy Hill Story") is None
    bad_title = covers.CoverReading("Mira and the Mon Kite", "A Windy Hill Story", True)
    assert covers.check_problem(bad_title, "Mira and the Moon Kite", "A Windy Hill Story") == (
        "title read as 'Mira and the Mon Kite'"
    )
    bad_sub = covers.CoverReading("Mira and the Moon Kite", "", False)
    assert covers.check_problem(bad_sub, "Mira and the Moon Kite", "A Windy Hill Story") == (
        "subtitle read as ''"
    )
    no_logo = covers.CoverReading("Mira and the Moon Kite", "A Windy Hill Story", False)
    assert covers.check_problem(no_logo, "Mira and the Moon Kite", "A Windy Hill Story") == (
        "logo not whole"
    )
