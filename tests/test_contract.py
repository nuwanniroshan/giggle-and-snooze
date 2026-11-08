import pytest

from video_pipeline.contract import (
    BanterParseError,
    ConfigError,
    ends_sentence,
    find_disallowed,
    load_disallowed_terms,
    normalize_ws,
    parse_banter,
    qa_schedule_errors,
    slugify,
    split_sentences,
    strip_tags,
    word_count,
)


def test_normalize_ws_collapses_line_breaks():
    assert normalize_ws("  a\n\nb \t c ") == "a b c"


def test_strip_tags_and_word_count_ignore_tags():
    assert strip_tags("<giggle> Look at   that!") == "Look at that!"
    assert word_count("<gasp> One two <sigh> three") == 3


def test_split_sentences_handles_quotes_abbreviations_and_paragraphs():
    text = (
        '"Tonight is the Lantern Bug Parade!" buzzed Mr. Pebble the beetle. '
        '"Every bug who glows gets to march."\n\nTilly looked at her lantern and sighed.'
    )
    assert split_sentences(text) == [
        '"Tonight is the Lantern Bug Parade!" buzzed Mr. Pebble the beetle.',
        '"Every bug who glows gets to march."',
        "Tilly looked at her lantern and sighed.",
    ]
    assert split_sentences("   ") == []


def test_ends_sentence():
    assert ends_sentence('He waved. "Bye!"')
    assert not ends_sentence("and then")


def test_slugify():
    assert slugify("The Snail Mail Express") == "the-snail-mail-express"
    assert slugify("Tilly's Big-Night, Out!") == "tillys-big-night-out"
    assert slugify("!!!") == ""


def test_find_disallowed_is_whole_word_and_case_insensitive():
    terms = ["hell", "shut up"]
    assert find_disallowed("Hello there", terms) == []
    assert find_disallowed("Oh HELL no, shut up!", terms) == ["hell", "shut up"]


def test_load_disallowed_terms(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("# comment\n\n kill \nshut up\n", encoding="utf-8")
    assert load_disallowed_terms(path) == ["kill", "shut up"]
    with pytest.raises(ConfigError, match="not found"):
        load_disallowed_terms(tmp_path / "missing.txt")


def test_parse_banter_reports_field_errors():
    with pytest.raises(BanterParseError) as excinfo:
        parse_banter('{"intro": [], "moments": [], "outro": []}')
    assert any("intro" in e for e in excinfo.value.errors)
    with pytest.raises(BanterParseError):
        parse_banter("not json")


@pytest.mark.parametrize(
    ("pages", "count", "expected"),
    [
        ([1, 4, 6, 9, 11, 14, 17, 19], 20, []),
        ([2, 4, 7], 8, []),
        ([4, 6, 9, 11, 14, 17, 19], 20, ["first Q&A must be on page 3 or earlier"]),
        ([1, 4, 6, 9, 11, 14, 16], 20, ["last Q&A must be within 3 pages of page 20"]),
        ([1, 5, 7], 8, ["gap between Q&A pages 1 and 5 must be 2 or 3 pages"]),
        ([1, 3, 5, 7], 8, ["needs 3-3 Q&A pages for an 8-page book (has 4)"]),
    ],
)
def test_qa_schedule_errors(pages, count, expected):
    assert qa_schedule_errors(pages, count) == expected


def test_split_sentences_keeps_name_verb_dialogue_tag_with_quote():
    text = '"Three lost treasures, Barnaby!" Clara declared, jotting clues. Barnaby nodded.'
    assert split_sentences(text) == [
        '"Three lost treasures, Barnaby!" Clara declared, jotting clues.',
        "Barnaby nodded.",
    ]


def test_split_sentences_keeps_multi_word_name_dialogue_tag():
    assert split_sentences('"Hurry up!" Mr. Pebble buzzed. "We march soon."') == [
        '"Hurry up!" Mr. Pebble buzzed.',
        '"We march soon."',
    ]
    assert split_sentences('"Why?" Granny Dusk asked softly.') == [
        '"Why?" Granny Dusk asked softly.'
    ]


def test_split_sentences_keeps_verb_first_dialogue_tag():
    assert split_sentences('"Look out!" said Clara. Then she ran.') == [
        '"Look out!" said Clara.',
        "Then she ran.",
    ]


def test_split_sentences_still_splits_new_sentence_after_quote():
    assert split_sentences('"Wait!" Tilly flew off.') == ['"Wait!"', "Tilly flew off."]
    assert split_sentences('"Thank you." Clara thanked Fergus.') == [
        '"Thank you."',
        "Clara thanked Fergus.",
    ]
