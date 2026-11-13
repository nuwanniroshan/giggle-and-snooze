import json
import random
import re

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline.contract import (
    KIDS,
    NARRATION_MOTIONS,
    SPEECH_VERBS,
    normalize_ws,
    qa_schedule_errors,
    split_sentences,
    word_count,
)
from video_pipeline.episode import (
    DEFAULT_ZOOM,
    BookError,
    assemble_script,
    assign_speakers,
    banter_plan_errors,
    choose_qa_pages,
    load_book,
    plan_episode,
    split_narration,
    zoom_target,
)


def write_book(tmp_path, book):
    path = tmp_path / "book.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    return path


def test_load_book_accepts_both_books():
    assert len(load_book(MINI_BOOK)["pages"]) == 8
    assert len(load_book(PILOT_BOOK)["pages"]) == 20


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b.update(pages=b["pages"][:4]), "supported range is 5-32"),
        (lambda b: b["pages"][0].update(text="  "), "page 1: 'text' is missing or empty"),
        (lambda b: b["pages"][1].update(page_number=3), "page 2: page_number must be 2"),
        (lambda b: b["metadata"].update(title="!!!"), "title has no letters or digits"),
        (  # spec 018: a malformed character sheet
            lambda b: b.setdefault("style_bible", {}).update(characters=[{"name": "Dot"}]),
            r"characters\[1\]\.look",
        ),
    ],
)
def test_load_book_rejects_invalid_books(tmp_path, mutate, message):
    book = load_json(MINI_BOOK)
    mutate(book)
    with pytest.raises(BookError, match=message):
        load_book(write_book(tmp_path, book))


def test_load_book_accepts_a_valid_character_sheet(tmp_path):  # spec 018: AC-1.4, AC-2.3
    book = load_json(MINI_BOOK)
    look = "A tiny ladybird with seven black spots and a yellow satchel."
    book.setdefault("style_bible", {})["characters"] = [{"name": "Dot", "look": look}]
    for page in book["pages"]:
        page["characters_on_page"] = ["Dot"]
    book["print_extras"] = {"note": "spec 020 may add this top-level key"}
    assert load_book(write_book(tmp_path, book))["style_bible"]["characters"][0]["name"] == "Dot"


def test_load_book_reports_bad_json(tmp_path):
    path = tmp_path / "book.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(BookError, match="not valid JSON"):
        load_book(path)


@pytest.mark.parametrize("book_path", [MINI_BOOK, PILOT_BOOK])
def test_split_narration_is_verbatim_and_within_limits(book_path):
    for page in load_json(book_path)["pages"]:
        lines = split_narration(page["text"])
        assert normalize_ws(" ".join(lines)) == normalize_ws(page["text"])
        for line in lines:
            sentences = split_sentences(line)
            assert len(sentences) <= 2
            assert len(sentences) == 1 or word_count(line) <= 40


def test_split_narration_keeps_long_sentence_whole():
    text = load_json(MINI_BOOK)["pages"][2]["text"]
    lines = split_narration(text)
    assert len(lines) == 2
    assert word_count(lines[0]) > 40
    assert lines[1] == "Granny Dusk smiled."


def test_split_narration_keeps_dialogue_tag_on_fixture():  # AC-2.4, FR-3
    text = load_json(MINI_BOOK)["pages"][4]["text"]
    assert split_narration(text)[-1] == '"I can\'t find my way home!" Little Dot cried.'


_TAG_START_RE = re.compile(r"^((?:[A-Z][\w.'’-]*\s+){1,3})([a-z]+)\b")


def _starts_with_dialogue_tag(text: str) -> bool:
    match = _TAG_START_RE.match(text)
    return bool(match) and match.group(2) in SPEECH_VERBS


def test_pilot_narration_never_breaks_before_a_dialogue_tag():  # AC-2.4, FR-3
    for page in load_json(PILOT_BOOK)["pages"]:
        for pieces in (split_narration(page["text"]), split_sentences(page["text"])):
            for before, after in zip(pieces, pieces[1:], strict=False):
                broken = re.search(r"[\"”’']$", before) and _starts_with_dialogue_tag(after)
                assert not broken, f"page {page['page_number']}: {before!r} | {after!r}"


@pytest.mark.parametrize("page_count", range(8, 33))
def test_choose_qa_pages_always_valid(page_count):
    for seed in range(10):
        pages = choose_qa_pages(page_count, random.Random(seed))
        assert qa_schedule_errors(pages, page_count) == []


def test_assign_speakers_covers_all_three_kinds():
    picks = assign_speakers(7, random.Random(1))
    assert len(picks) == 7
    assert {("kid_older",), ("kid_younger",), ("kid_older", "kid_younger")} <= set(picks)


def test_plan_episode_is_deterministic_and_seed_sensitive():
    book = load_json(PILOT_BOOK)
    assert plan_episode(book, 7) == plan_episode(book, 7)
    assert len({repr(plan_episode(book, seed)) for seed in range(5)}) > 1
    plan = plan_episode(book, 7)
    assert len(plan.motions) == 20 and set(plan.motions) <= set(NARRATION_MOTIONS)


def test_zoom_target_uses_position_words_near_grounding():
    prompt = "Tilly the firefly sitting on the left of a mossy log, soft sky in the background."
    assert zoom_target(prompt, "Tilly the firefly") == {"x": 0.3, "y": 0.5, "zoom": 1.3}
    assert zoom_target(prompt, "soft sky") == {"x": 0.5, "y": 0.3, "zoom": 1.3}  # "sky" first


def test_zoom_target_defaults_without_position_cue():
    assert zoom_target("Granny Dusk beside her books.", "Granny Dusk") == DEFAULT_ZOOM
    assert zoom_target("A log on the left.", "not in the prompt") == DEFAULT_ZOOM


def test_assemble_script_structure():
    book = load_json(MINI_BOOK)
    cast = load_json(CAST_PATH)
    plan = plan_episode(book, 7)
    script = assemble_script(book, cast, plan, make_banter(book, plan))
    assert script["book_slug"] == "the-lantern-bug-parade"
    assert [p["page_number"] for p in script["pages"]] == list(range(1, 9))
    assert script["intro"]["ffmpeg_effect"] == "zoom_in_center"
    assert script["outro"]["ffmpeg_effect"] == "zoom_out_warm_fade"
    for page in script["pages"]:
        roles = [line["role"] for line in page["lines"]]
        assert roles[0] == "narration"
        assert roles == sorted(roles, key=lambda r: r != "narration")  # narration first
        assert page["has_qa"] == (page["page_number"] in plan.qa_speakers)
        assert ("qa_zoom_target" in page["ffmpeg_camera"]) == page["has_qa"]
        if page["has_qa"]:
            tag = "" if "kid_younger" in plan.qa_speakers[page["page_number"]] else "<chuckle> "
            assert page["lines"][-1] == {
                "speaker": "storyteller",
                "role": "answer",
                "text": f"{tag}Let's keep reading and see!",
                "emotion": "happy",  # spec 017: make_banter gives each moment a feeling
            }


def test_banter_plan_errors_detects_mismatch():
    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    banter = make_banter(book, plan)
    assert banter_plan_errors(banter, plan) == []
    first = banter.moments[0]
    wrong = [k for k in KIDS if (k,) != plan.qa_speakers[first.page_number]][0]
    first.lines = [
        line.model_copy(update={"speaker": wrong}) if line.speaker in KIDS else line
        for line in first.lines
    ]
    banter.moments.append(first.model_copy(update={"page_number": 99}))
    errors = banter_plan_errors(banter, plan)
    assert any(f"page {first.page_number}: kids speaking must be exactly" in e for e in errors)
    assert "unexpected Q&A moment for page 99" in errors
