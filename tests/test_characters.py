"""Character sheet helpers (spec 018: AC-1.4, AC-2.1..2.3, FR-3)."""

from __future__ import annotations

import copy

import pytest

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from video_pipeline import characters as ch

JACK = {"name": "Jack", "look": "A small boy with curly red hair, a green raincoat and boots."}
ZIB = {"name": "Zib", "look": "A round friend with three eyes and a lilac scarf around its neck."}


def sheet_book(**page_names) -> dict:
    book = copy.deepcopy(load_json(MINI_BOOK))
    book.setdefault("style_bible", {})["characters"] = [dict(JACK), dict(ZIB)]
    for page in book["pages"]:
        page["characters_on_page"] = list(page_names.get(str(page["page_number"]), ["Jack"]))
    return book


def test_sheet_is_empty_for_the_pilot_and_books_without_a_style_bible():  # AC-2.3
    assert ch.sheet(load_json(PILOT_BOOK)) == []
    assert ch.sheet({"metadata": {}, "pages": []}) == []
    assert ch.sheet({"style_bible": {"characters": "nope"}}) == []


def test_sheet_returns_the_entries():
    assert ch.sheet(sheet_book()) == [JACK, ZIB]


def test_absent_fields_have_no_problems():  # FR-3: pilot and older books still load
    assert ch.sheet_problems(load_json(PILOT_BOOK), "b") == []
    assert ch.sheet_problems(load_json(MINI_BOOK), "b") == []


def test_valid_sheet_has_no_problems():
    assert ch.sheet_problems(sheet_book(), "b") == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b["style_bible"].update(characters="Jack"), "style_bible.characters"),
        (lambda b: b["style_bible"]["characters"][1].pop("look"), "characters[2].look"),
        (lambda b: b["style_bible"]["characters"][1].update(name="Jack"), "duplicate"),
        (lambda b: b["pages"][2].update(characters_on_page="Jack"), "page 3: characters_on_page"),
        (lambda b: b["pages"][6].update(characters_on_page=["Zob"]), "page 7"),
    ],
)
def test_malformed_sheet_is_reported(mutate, message):
    book = sheet_book()
    mutate(book)
    problems = ch.sheet_problems(book, "b")
    assert problems and any(message in p for p in problems), problems
    assert all(p.startswith("b: ") for p in problems)


def test_unknown_name_names_the_page_and_the_name():
    book = sheet_book(**{"7": ["Zob"]})
    problems = ch.sheet_problems(book, "b")
    assert any("page 7" in p and "'Zob'" in p for p in problems)


def test_page_text_lists_the_page_characters_in_sheet_order():  # AC-2.1
    book = sheet_book(**{"2": ["Zib", "Jack"]})
    text = ch.page_characters_text(book, book["pages"][1])
    assert f"Jack: {JACK['look']}" in text and f"Zib: {ZIB['look']}" in text
    assert text.index("Jack:") < text.index("Zib:")
    only_jack = ch.page_characters_text(book, book["pages"][0])
    assert "Jack:" in only_jack and "Zib" not in only_jack


def test_page_text_is_empty_without_a_sheet_or_names():  # AC-2.3
    pilot = load_json(PILOT_BOOK)
    assert ch.page_characters_text(pilot, pilot["pages"][0]) == ""
    book = sheet_book(**{"3": []})
    assert ch.page_characters_text(book, book["pages"][2]) == ""


def test_cover_text_holds_every_look():  # AC-2.2
    text = ch.cover_characters_text(sheet_book())
    assert f"Jack: {JACK['look']}" in text and f"Zib: {ZIB['look']}" in text
    assert ch.cover_characters_text(load_json(PILOT_BOOK)) == ""
