import copy

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline.contract import validate_script
from video_pipeline.episode import assemble_script, plan_episode

TERMS = ["stupid", "shut up"]


def build(book_path=MINI_BOOK, seed=7):
    book = load_json(book_path)
    plan = plan_episode(book, seed)
    return book, assemble_script(book, load_json(CAST_PATH), plan, make_banter(book, plan))


def qa_page(script):
    return next(p for p in script["pages"] if p["has_qa"])


def plain_page(script):
    return next(p for p in script["pages"] if not p["has_qa"])


@pytest.mark.parametrize("book_path", [MINI_BOOK, PILOT_BOOK])
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_assembled_script_is_valid(book_path, seed):  # AC-1.2
    book, script = build(book_path, seed)
    assert validate_script(script, book, TERMS) == []


def test_narration_may_carry_tags_without_breaking_verbatim():  # AC-2.2, FR-3
    book, script = build()
    line = script["pages"][0]["lines"][0]
    line["text"] = "<sigh> " + line["text"]
    assert validate_script(script, book, TERMS) == []


def test_book_text_is_not_term_checked():  # Security: only generated lines
    book, script = build()
    book["pages"][0]["text"] = book["pages"][0]["text"].replace("tiny", "stupid", 1)
    script["pages"][0]["lines"][0]["text"] = script["pages"][0]["lines"][0]["text"].replace(
        "tiny", "stupid", 1
    )
    assert validate_script(script, book, TERMS) == []


def mutate_missing_page(script):
    script["pages"].pop()


def mutate_word(script):
    line = script["pages"][0]["lines"][0]
    line["text"] = line["text"].replace("tiny", "small", 1)


def mutate_qa_before_narration(script):
    page = qa_page(script)
    page["lines"].insert(0, page["lines"].pop())


def mutate_three_sentences(script):
    page = script["pages"][3]  # mini page 4 has 4 sentences in 2 lines
    lines = [x for x in page["lines"] if x["role"] == "narration"]
    lines[0]["text"] = lines[0]["text"] + " " + lines[1]["text"].split(". ")[0] + "."
    lines[1]["text"] = lines[1]["text"].split(". ", 1)[1]


def mutate_all_both(script):
    for page in script["pages"]:
        if page["has_qa"]:
            kids = [x for x in page["lines"] if x["speaker"] != "storyteller"]
            kids[0]["speaker"] = "kid_older"
            page["lines"].insert(-1, {**kids[0], "speaker": "kid_younger"})


def mutate_moment_ends_with_kid(script):
    page = qa_page(script)
    page["lines"].append({"speaker": "kid_younger", "role": "reaction", "text": "Wow!"})


def mutate_moment_too_long(script):
    page = qa_page(script)
    extra = {"speaker": "kid_older", "role": "question", "text": "Why?"}
    for _ in range(5):
        page["lines"].insert(-1, dict(extra))


def mutate_grounding(script):
    qa_page(script)["qa_grounding"] = "a purple elephant"


def mutate_long_kid_line(script):
    page = qa_page(script)
    kid = next(x for x in page["lines"] if x["speaker"] != "storyteller")
    kid["text"] = " ".join(["word"] * 26)


def mutate_bad_tag(script):
    script["intro"]["lines"][0]["text"] = "<whisper> Hello there!"


def mutate_intro_no_title(script):
    script["intro"]["lines"][0]["text"] = "<gasp> Look at that book!"


def mutate_outro_ends_with_kid(script):
    script["outro"]["lines"].append({"speaker": "kid_older", "text": "Bye!"})


def mutate_outro_no_come_back(script):
    script["outro"]["lines"][-1]["text"] = "Please subscribe!"


def mutate_motion(script):
    script["pages"][0]["ffmpeg_camera"]["narration_motion"] = "spin"


def mutate_zoom_range(script):
    qa_page(script)["ffmpeg_camera"]["qa_zoom_target"]["zoom"] = 2.5


def mutate_zoom_on_plain_page(script):
    plain_page(script)["ffmpeg_camera"]["qa_zoom_target"] = {"x": 0.5, "y": 0.5, "zoom": 1.3}


def mutate_effects(script):
    script["intro"]["ffmpeg_effect"] = "zoom_out_warm_fade"


def mutate_term(script):
    page = qa_page(script)
    kid = next(x for x in page["lines"] if x["speaker"] != "storyteller")
    kid["text"] = "That is so stupid!"


def mutate_cast(script):
    del script["cast"]["kid_younger"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (mutate_missing_page, "pages must be numbered 1..8 in book order"),  # AC-2.1
        (mutate_word, "page 1: narration does not match the page text word for word"),  # AC-2.2
        (mutate_qa_before_narration, "narration lines must come before Q&A lines"),  # AC-2.3
        (mutate_three_sentences, "page 4 narration line 1: has 3 sentences (max 2)"),  # AC-2.4
        (mutate_all_both, "needs at least one Q&A moment with only Leo (kid_older)"),  # AC-3.3
        (mutate_moment_ends_with_kid, "Q&A moment must end with a storyteller answer"),  # AC-3.4
        (mutate_moment_too_long, "Q&A moment must have 2-5 lines"),  # AC-3.4
        (mutate_grounding, "qa_grounding must be a phrase copied word for word"),  # AC-3.5
        (mutate_long_kid_line, "kid line has 26 words (max 25)"),  # AC-3.6
        (mutate_bad_tag, "intro line 1: tag <whisper> is not allowed"),  # FR-5
        (mutate_intro_no_title, "intro must mention the title"),  # AC-4.1
        (mutate_outro_ends_with_kid, "outro must end with a storyteller line inviting"),  # AC-4.2
        (mutate_outro_no_come_back, "and come back for the next story"),  # AC-4.2
        (mutate_motion, "page 1: narration_motion must be one of"),  # AC-5.1
        (mutate_zoom_range, "qa_zoom_target needs x, y in [0, 1] and zoom in [1, 2]"),  # AC-5.2
        (mutate_zoom_on_plain_page, "has_qa is false but the page has Q&A content"),  # AC-5.3
        (mutate_effects, "intro.ffmpeg_effect must be 'zoom_in_center'"),  # AC-5.4
        (mutate_term, "disallowed term 'stupid'"),  # AC-7.1
        (mutate_cast, "cast must define exactly: storyteller, kid_older, kid_younger"),  # FR-2
    ],
)
def test_validator_rejects(mutate, expected):
    book, script = build()
    broken = copy.deepcopy(script)
    mutate(broken)
    errors = validate_script(broken, book, TERMS)
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize(
    ("where", "text", "markup"),
    [
        ("intro", "*giggles* Look, a book!", "*giggles*"),
        ("intro", "[laughs] Look, a book!", "[laughs]"),
        ("intro", "Look, a book! (whispering)", "(whispering)"),
        ("intro", "_softly_ Look, a book!", "_softly_"),
        ("qa", "*points* Who sent it?", "*points*"),
    ],
)
def test_validator_rejects_stage_directions_in_generated_lines(where, text, markup):  # FR-5
    book, script = build()
    if where == "intro":
        line, label = script["intro"]["lines"][1], "intro line 2"
    else:
        page = qa_page(script)
        line = next(x for x in page["lines"] if x["speaker"] != "storyteller")
        label = f"page {page['page_number']} Q&A line 1"
    line["text"] = text
    errors = validate_script(script, book, TERMS)
    assert f"{label}: stage direction or markup {markup!r} is not allowed" in errors, errors


def test_book_narration_may_contain_brackets():  # FR-5 checks generated lines only
    book, script = build()
    book["pages"][0]["text"] = book["pages"][0]["text"].replace("tiny", "tiny (very tiny)", 1)
    line = script["pages"][0]["lines"][0]
    line["text"] = line["text"].replace("tiny", "tiny (very tiny)", 1)
    assert validate_script(script, book, TERMS) == []


def test_missing_top_level_key():
    book, script = build()
    del script["outro"]
    assert validate_script(script, book, TERMS) == ["missing top-level key: outro"]
