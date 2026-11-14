"""Spec 028: story-aware sound tags in the narration (model, limits, refusals, insertion)."""

import json

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, load_json, make_banter
from video_pipeline import episode
from video_pipeline.contract import (
    Banter,
    NarrationTag,
    normalize_ws,
    strip_tags,
    validate_script,
)
from video_pipeline.emotions import NarrationLimits

LIMITS = NarrationLimits(per_page_max=1, per_book_ratio=0.5)
PAGE = {
    "page_number": 1,
    "text": 'Pip sat down. "I need help," said Pip. The moon rose.',
    "prompt": "p",
}
BOOK = {
    "metadata": {"title": "T"},
    "pages": [PAGE, *({**PAGE, "page_number": n} for n in (2, 3, 4))],
}


def tag(page=1, sentence=1, position="end", name="sigh"):
    return {"page_number": page, "sentence": sentence, "position": position, "tag": name}


def errors(*tags, book=BOOK):
    return episode.narration_tag_errors(tags_list=list(tags), book=book, limits=LIMITS)


def with_page_one(text):
    return {**BOOK, "pages": [{**PAGE, "text": text}, *BOOK["pages"][1:]]}


# --- refusals (AC-2.1-2.6, FR-3, FR-5) ----------------------------------------------------------


def test_valid_tags_have_no_errors():  # AC-1.1
    assert errors(tag(1, 3, "start", "gasp"), tag(3, 1, "end", "yawn")) == []


def test_model_entries_are_checked_like_dicts():
    assert errors(NarrationTag(**tag(1, 3, "start", "gasp"))) == []
    assert errors(NarrationTag(**tag(name="wink")))


def test_empty_is_valid():
    assert errors() == []


def test_per_page_limit():  # AC-2.1
    problems = errors(tag(1, 1), tag(1, 3))
    assert any("page 1" in p and "at most 1" in p for p in problems)


def test_book_average_limit():  # AC-2.2: 3 > ceil(0.5 * 4) = 2
    problems = errors(tag(1), tag(2), tag(3))
    assert any("3 narration tags" in p and "at most 2" in p for p in problems)


def test_book_average_limit_rounds_up():
    five = {**BOOK, "pages": [*BOOK["pages"], {**PAGE, "page_number": 5}]}
    assert errors(tag(1), tag(2), tag(3), book=five) == []  # ceil(0.5 * 5) = 3


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (tag(name="wink"), "wink"),
        (tag(page=9), "page 9"),
        (tag(sentence=0), "sentence"),
        (tag(sentence=99), "sentence"),
        (tag(position="middle"), "position"),
    ],
)
def test_unknown_values_refused_with_entry(bad, reason):  # AC-2.3
    problems = errors(bad)
    assert problems
    assert any(p.startswith("narration tag 1") and reason in p for p in problems)


def test_duplicates_refused():  # AC-2.5
    problems = errors(tag(2, 1, "end", "sigh"), tag(2, 1, "end", "yawn"))
    assert any("duplicate" in p for p in problems)


def test_inside_open_quotation_refused():  # AC-2.6
    book = with_page_one('"Hello there. How are you today?" said Pip. The end.')
    assert any("quotation" in p for p in errors(tag(1, 2, "start"), book=book))
    assert any("quotation" in p for p in errors(tag(1, 1, "end"), book=book))
    assert errors(tag(1, 1, "start"), book=book) == []  # before the opening quote
    assert errors(tag(1, 2, "end"), book=book) == []  # after the closing quote
    assert errors(tag(1, 3, "start"), book=book) == []


def test_curly_quotes_open_and_close():  # AC-2.6
    book = with_page_one("“Hello there. How are you today?” said Pip. The end.")
    assert any("quotation" in p for p in errors(tag(1, 2, "start"), book=book))
    assert any("quotation" in p for p in errors(tag(1, 1, "end"), book=book))
    assert errors(tag(1, 2, "end"), book=book) == []
    assert errors(tag(1, 3, "start"), book=book) == []


@pytest.mark.parametrize(
    "text",
    ['"Hi." "Bye." The end.', "“Hi.” “Bye.” The end."],
)
def test_closed_quotes_leave_sentence_edges_free(text):  # each quote opens and closes
    book = with_page_one(text)
    for position in (tag(1, 1, "end"), tag(1, 2, "start"), tag(1, 2, "end"), tag(1, 3, "start")):
        assert errors(position, book=book) == [], position


def test_position_that_already_carries_a_tag_is_refused():  # AC-2.6
    book = with_page_one("<sigh> Pip sat down. The moon rose <yawn>")
    assert any("already" in p for p in errors(tag(1, 1, "start", "gasp"), book=book))
    assert any("already" in p for p in errors(tag(1, 2, "end", "gasp"), book=book))
    assert errors(tag(1, 1, "end", "gasp"), book=book) == []
    assert errors(tag(1, 2, "start", "gasp"), book=book) == []


def test_all_problems_listed_together():  # AC-2.4, FR-5
    assert len(errors(tag(name="wink"), tag(page=9), tag(2, 1), tag(2, 1))) >= 3


# --- insertion (AC-1.2-1.5, FR-2) ---------------------------------------------------------------


def test_decorations_insert_at_sentence_edges_and_strip_to_book_text():  # AC-1.2, AC-1.3
    text = PAGE["text"]
    lines = episode.split_narration(text, {(1, "start"): "<gasp>", (3, "end"): "<yawn>"})
    joined = " ".join(lines)
    assert joined.startswith("<gasp> Pip sat down.")
    assert joined.endswith("The moon rose. <yawn>")
    assert strip_tags(joined) == strip_tags(text)


def test_decorations_do_not_change_line_breaks():  # word_count strips tags
    text = load_json(MINI_BOOK)["pages"][2]["text"]
    plain = episode.split_narration(text)
    tagged = episode.split_narration(text, {(1, "end"): "<sigh>", (2, "start"): "<gasp>"})
    assert [strip_tags(line) for line in tagged] == plain


def test_no_decorations_equal_today():  # AC-1.5
    assert episode.split_narration(PAGE["text"], None) == episode.split_narration(PAGE["text"])
    assert episode.split_narration(PAGE["text"], {}) == episode.split_narration(PAGE["text"])


def _assemble(narration_tags):
    book = load_json(MINI_BOOK)
    plan = episode.plan_episode(book, 7)
    data = make_banter(book, plan).model_dump(exclude_none=True)
    if narration_tags is not None:
        data["narration_tags"] = narration_tags
    script = episode.assemble_script(book, load_json(CAST_PATH), plan, Banter.model_validate(data))
    return book, script


def _narration(page):
    return [line["text"] for line in page["lines"] if line["role"] == "narration"]


def test_assemble_applies_only_valid_entries():  # AC-1.2, AC-1.3; bad entries never crash
    book, script = _assemble(
        [tag(1, 2, "end", "yawn"), tag(1, 99, "start", "gasp"), tag(2, 1, "start", "wink")]
    )
    narration = {p["page_number"]: _narration(p) for p in script["pages"]}
    every_line = " ".join(text for lines in narration.values() for text in lines)
    assert every_line.count("<") == 1
    assert narration[1][-1].endswith("sleepy star. <yawn>")
    for page in book["pages"]:
        joined = strip_tags(" ".join(narration[page["page_number"]]))
        assert joined == normalize_ws(page["text"])
    assert validate_script(script, book, ["stupid"]) == []  # AC-1.3 / AC-3.2


def test_assemble_without_tags_is_byte_identical():  # AC-1.5
    _, today = _assemble(None)
    _, empty = _assemble([])
    assert json.dumps(empty) == json.dumps(today)


def test_saved_script_with_many_narration_tags_still_validates():  # AC-3.1, AC-3.2
    book, script = _assemble(None)
    for page in script["pages"]:
        for line in page["lines"]:
            if line["role"] == "narration":
                line["text"] = f"<sigh> {line['text']} <yawn>"
    assert validate_script(script, book, ["stupid"]) == []


# --- model (FR-1, NFR-3) ------------------------------------------------------------------------

_MINIMAL = {
    "intro": [
        {"speaker": "kid_older", "text": "Hi!"},
        {"speaker": "storyteller", "text": "Hello."},
    ],
    "moments": [],
    "outro": [{"speaker": "storyteller", "text": "Bye."}] * 3,
}


def test_banter_parses_optional_list():  # FR-1
    assert Banter.model_fields["narration_tags"].default_factory() == []
    raw = json.dumps({**_MINIMAL, "narration_tags": [tag(2, 1, "start", "gasp")]})
    parsed = Banter.model_validate_json(raw).narration_tags
    assert parsed == [NarrationTag(page_number=2, sentence=1, position="start", tag="gasp")]


def test_banter_without_narration_tags_still_parses():  # NFR-3
    assert Banter.model_validate_json(json.dumps(_MINIMAL)).narration_tags == []
    assert (
        Banter.model_validate_json(json.dumps({**_MINIMAL, "narration_tags": []})).narration_tags
        == []
    )
