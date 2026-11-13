"""Story rules for add-a-book-from-an-idea (spec 006: AC-1.4, AC-2.4, AC-3.2, AC-3.3, FR-1..FR-6)."""

from __future__ import annotations

import pytest

from video_pipeline import story_tools as st
from video_pipeline.contract import load_disallowed_terms
from video_pipeline.library import PROJECT_ROOT

TERMS = load_disallowed_terms(PROJECT_ROOT / "config" / "disallowed_terms.txt")
GENTLE = st.load_gentle_terms(PROJECT_ROOT / "config" / "gentle_terms.txt")


def idea(title: str = "The Brave Little Teapot", **changes) -> dict:
    data = {
        "title": title,
        "premise": "A teapot is afraid of the dark. Her friends light a path home.",
        "age_range": "Ages 3-6",
        "lesson": "Friends help us be brave.",
    }
    data.update(changes)
    return data


TEAPOT = {
    "name": "Tilly",
    "look": "A small round teapot, sky blue with white polka dots and a gold lid.",
}
OWL = {"name": "Hoot", "look": "A fluffy grey owl with big amber eyes and a striped red scarf."}


def book(pages: int = 8, **changes) -> dict:
    data = {
        "metadata": {
            "title": "The Brave Little Teapot",
            "subtitle": "A Story",
            "author": "Test Author",
            "age_range": "Ages 3-6",
            "blurb": "A teapot finds courage with friends.",
            "dedication": "For every little explorer.",
        },
        "style_bible": {"note": "set in spec 008"},
        "characters": [dict(TEAPOT), dict(OWL)],
        "pages": [
            {
                "page_number": n,
                "title": f"Page {n}",
                "text": "The teapot walked on. She was brave.",
                "layout": "image_top_text_bottom",
                "prompt": "A small teapot on a hill at dusk.",
                "illustration_file": f"page_{n:02d}.png",
                "characters_on_page": ["Tilly"] if n % 2 else ["Tilly", "Hoot"],
            }
            for n in range(1, pages + 1)
        ],
    }
    data.update(changes)
    return data


# --- ideas (FR-1) ----------------------------------------------------------------------------


def test_three_ideas_with_all_fields_pass():
    payload = {"ideas": [idea(f"Story Number {n}") for n in range(3)]}
    assert st.ideas_errors(payload, TERMS, GENTLE) == []


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_idea_count_must_be_three_to_five(count):
    payload = {"ideas": [idea(f"Story Number {n}") for n in range(count)]}
    assert any("3 to 5 ideas" in e for e in st.ideas_errors(payload, TERMS, GENTLE))


def test_idea_fields_are_checked():
    bad = idea("ab", premise="One. Two. Three.", age_range="Ages 9-12", lesson="One. Two.")
    errors = st.idea_errors(bad, "idea 1", TERMS, GENTLE)
    assert any("title must be 3 to 60 characters" in e for e in errors)
    assert any("premise must be 1 to 2 sentences" in e for e in errors)
    assert any("age_range must be one of" in e for e in errors)
    assert any("lesson must be one sentence" in e for e in errors)


def test_idea_with_a_disallowed_term_is_refused():  # FR-5
    errors = st.idea_errors(
        idea(premise="The old teapot dies in the story."), "idea", TERMS, GENTLE
    )
    assert any("'dies'" in e for e in errors)


# --- candidate (FR-2) ------------------------------------------------------------------------


def test_candidate_is_one_idea():
    assert st.candidate_errors(idea(), TERMS, GENTLE) == []


def test_candidate_gentle_rule_applies():
    errors = st.candidate_errors(idea(premise="A monster hides under the bed."), TERMS, GENTLE)
    assert any("'monster'" in e for e in errors)


# --- book (FR-3, FR-5) -----------------------------------------------------------------------


def test_eight_page_book_passes_with_default_max():
    assert st.book_errors(book(8), 20, TERMS, GENTLE) == []


def test_book_may_be_shorter_than_the_maximum():  # AC-3.2: fits the maximum, may be shorter
    assert st.book_errors(book(12), 20, TERMS, GENTLE) == []


def test_book_longer_than_the_maximum_is_refused():  # AC-3.2
    assert any("more than the maximum" in e for e in st.book_errors(book(12), 10, TERMS, GENTLE))


def test_book_shorter_than_five_pages_is_refused():  # AC-3.2
    errors = st.book_errors(book(4), 20, TERMS, GENTLE)
    assert any("at least 5 pages" in e for e in errors)


def test_page_numbers_must_be_consecutive():  # AC-3.2
    data = book(8)
    data["pages"][3]["page_number"] = 9
    assert any("page 4" in e for e in st.book_errors(data, 20, TERMS, GENTLE))


def test_every_page_needs_title_text_layout_prompt_and_file():  # AC-3.2
    data = book(8)
    del data["pages"][0]["layout"]
    data["pages"][1]["prompt"] = ""
    data["pages"][2].pop("illustration_file")
    errors = st.book_errors(data, 20, TERMS, GENTLE)
    assert any("page 1" in e and "layout" in e for e in errors)
    assert any("page 2" in e and "prompt" in e for e in errors)
    assert any("page 3" in e and "illustration_file" in e for e in errors)


def test_page_text_is_one_to_four_sentences():
    data = book(8)
    data["pages"][0]["text"] = " ".join(["She ran."] * 5)
    assert any(
        "page 1" in e and "1 to 4 sentences" in e for e in st.book_errors(data, 20, TERMS, GENTLE)
    )


def test_book_with_a_gentle_term_is_refused_with_the_page():  # AC-3.3
    data = book(8)
    data["pages"][4]["text"] = "The little teapot was scared of the monster."
    errors = st.book_errors(data, 20, TERMS, GENTLE)
    assert any("page 5" in e and "'monster'" in e for e in errors)


def test_book_metadata_title_must_be_present():
    data = book(8)
    data["metadata"]["title"] = ""
    assert any("title" in e for e in st.book_errors(data, 20, TERMS, GENTLE))


# --- the character sheet (spec 018: AC-1.1..1.3) --------------------------------------------


def test_book_without_characters_is_refused_naming_the_field():  # AC-1.2
    data = book(8)
    del data["characters"]
    assert any(e.startswith("characters:") for e in st.book_errors(data, 20, TERMS, GENTLE))


@pytest.mark.parametrize("count", [0, 7])
def test_book_needs_one_to_six_characters(count):  # AC-1.1
    sheet = [{"name": f"Friend {n}", "look": OWL["look"]} for n in range(count)]
    errors = st.book_errors(book(8, characters=sheet), 20, TERMS, GENTLE)
    assert any("characters: must hold 1 to 6 characters" in e for e in errors), errors


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"name": "N" * 41, "look": OWL["look"]}, "characters[2].name: must be 1 to 40"),
        ({"name": "Hoot", "look": "L" * 19}, "characters[2].look: must be 20 to 300"),
        ({"name": "Hoot", "look": "L" * 301}, "characters[2].look: must be 20 to 300"),
        (
            {"name": "Hoot", "look": "A grey owl here. Amber eyes shine. A red scarf too."},
            "characters[2].look: must be 1 to 2 sentences",
        ),
        (
            {"name": "Hoot", "look": "A grey owl with a monster mask and amber eyes."},
            "characters[2].look: 'monster'",
        ),
        ({"name": "Tilly", "look": OWL["look"]}, "characters[2].name: duplicate name 'Tilly'"),
        ({"name": "Hoot"}, "characters[2].look"),
    ],
)
def test_character_entries_are_checked(entry, message):  # AC-1.1, AC-1.2
    errors = st.book_errors(book(8, characters=[dict(TEAPOT), entry]), 20, TERMS, GENTLE)
    assert any(message in e for e in errors), errors


def test_page_without_characters_on_page_is_refused_naming_the_page():  # AC-1.3
    data = book(8)
    del data["pages"][3]["characters_on_page"]
    errors = st.book_errors(data, 20, TERMS, GENTLE)
    assert any(e.startswith("page 4: characters_on_page") for e in errors), errors


def test_unknown_character_on_a_page_is_refused_naming_page_and_name():  # AC-1.3
    data = book(8)
    data["pages"][6]["characters_on_page"] = ["Tilly", "Zib"]
    errors = st.book_errors(data, 20, TERMS, GENTLE)
    assert "page 7: characters_on_page names unknown character 'Zib'" in errors, errors


def test_scenery_page_may_list_no_characters():
    data = book(8)
    data["pages"][0]["characters_on_page"] = []
    assert st.book_errors(data, 20, TERMS, GENTLE) == []


def test_characters_are_optional_only_when_not_required():  # pre-018 drafts stay editable
    data = book(8)
    del data["characters"]
    for page in data["pages"]:
        del page["characters_on_page"]
    assert st.book_errors(data, 20, TERMS, GENTLE, require_characters=False) == []


def test_submit_book_saves_a_valid_book_and_refuses_one_without_a_sheet(tmp_path):  # AC-1.2
    tools = st.StoryTools(TERMS, GENTLE, tmp_path / "book.json", pages=20)
    bad = book(8)
    del bad["characters"]
    assert tools.submit_book(bad)["saved"] is False
    assert not (tmp_path / "book.json").exists()
    assert tools.submit_book(book(8)) == {"saved": True, "errors": []}
    assert (tmp_path / "book.json").exists()


# --- the submit tools and the attempt limit (FR-7) -------------------------------------------


def test_valid_candidate_is_saved_atomically(tmp_path):
    tools = st.StoryTools(TERMS, GENTLE, tmp_path / "candidate.json", pages=20)
    result = tools.submit_candidate(idea())
    assert result["saved"] is True and result["errors"] == []
    assert (tmp_path / "candidate.json").exists()


def test_invalid_submission_is_refused_and_not_saved(tmp_path):
    tools = st.StoryTools(TERMS, GENTLE, tmp_path / "candidate.json", pages=20)
    result = tools.submit_candidate(idea(premise="A monster hides."))
    assert result["saved"] is False and result["errors"]
    assert not (tmp_path / "candidate.json").exists()


def test_save_limit_stops_further_submissions(tmp_path):  # FR-7
    tools = st.StoryTools(TERMS, GENTLE, tmp_path / "x.json", pages=20, max_saves=2)
    tools.submit_candidate(idea(premise="A monster hides."))
    tools.submit_candidate(idea(premise="A monster hides."))
    result = tools.submit_candidate(idea())
    assert result["saved"] is False
    assert any("save limit" in e for e in result["errors"])
