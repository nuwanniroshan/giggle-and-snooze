"""Spec 024 Phase 1: pure YouTube text, chapters and records."""

import json

import pytest

from tests.helpers import ROOT, make_root
from video_pipeline.contract import ConfigError, load_disallowed_terms
from video_pipeline.youtube import (
    Chapter,
    YouTubeError,
    YouTubePaths,
    append_upload,
    category_for,
    chapter_problems,
    chapters_from_record,
    compose_description,
    format_timestamp,
    language_for,
    last_upload,
    load_settings,
    mark_not_found,
    read_text,
    read_uploads,
    tags_length,
    text_problems,
    text_sha256,
    upload_state,
    write_text,
)

BOOK_TITLE = "The Lantern Bug Parade"


@pytest.fixture
def settings(tmp_path):
    folder = make_root(tmp_path)
    return load_settings(folder.root / "config")


@pytest.fixture
def terms():
    return load_disallowed_terms(ROOT / "config" / "disallowed_terms.txt")


def make_book(**metadata) -> dict:
    return {
        "metadata": {"title": BOOK_TITLE, **metadata},
        "pages": [
            {"page_number": 1, "title": "Lanterns Wake", "text": "x"},
            {"page_number": 2, "title": "The Parade Starts", "text": "x"},
            {"page_number": 3, "title": "", "text": "x"},
            {"page_number": 4, "title": "Home Again", "text": "x"},
        ],
    }


def good_chapters() -> list[Chapter]:
    return [Chapter(0.0, "Welcome"), Chapter(30.0, "Lanterns Wake"), Chapter(80.0, "Goodnight")]


def good_text() -> dict:
    return {
        "title": f"{BOOK_TITLE} | Bedtime Story for Kids",
        "description": "A gentle story about glowing bugs on parade.",
        "tags": ["bedtime story", "kids", "lanterns"],
    }


def problems(settings, terms, text=None, book=None, chapters=None, duration=120.0):
    return text_problems(
        text if text is not None else good_text(),
        book or make_book(),
        chapters if chapters is not None else good_chapters(),
        duration,
        settings,
        terms,
    )


def record(chapters, duration=300.0) -> dict:
    return {
        "final_key": "k1",
        "duration": duration,
        "chapters": [{"name": n, "start": s} for n, s in chapters],
    }


# --- settings -------------------------------------------------------------------------------


def test_load_settings_reads_real_config(settings):
    assert settings.series == "Giggle & Snooze"
    assert settings.categories["storybook"] == "27"
    assert isinstance(settings.cta_terms, tuple)
    assert settings.thumbnail_size == (1280, 720)


def test_load_settings_missing_field_names_it(tmp_path):
    folder = make_root(tmp_path)
    path = folder.root / "config" / "youtube.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["min_chapters"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_settings(folder.root / "config")
    assert "youtube.json" in str(exc.value)
    assert "min_chapters" in str(exc.value)


# --- text problems --------------------------------------------------------------------------


def test_good_text_has_no_problems(settings, terms):
    assert problems(settings, terms) == []


def test_title_over_100_refused(settings, terms):
    text = good_text() | {"title": BOOK_TITLE + " " + "x" * 100}
    found = problems(settings, terms, text=text)
    assert any(p.startswith("title:") and "100" in p for p in found)


def test_title_must_contain_book_title(settings, terms):
    text = good_text() | {"title": "A different title"}
    found = problems(settings, terms, text=text)
    assert any(p.startswith("title:") and BOOK_TITLE in p for p in found)
    ok = good_text() | {"title": "the lantern bug parade, read aloud"}
    assert problems(settings, terms, text=ok) == []


def test_description_over_5000_bytes_counts_utf8(settings, terms):
    # 3600 + 2000 = 5600 UTF-8 bytes, but only 2200 characters
    body = "☃" * 1200 + "é" * 1000
    assert len(body) < 5000
    found = problems(settings, terms, text=good_text() | {"description": body})
    assert any(p.startswith("description:") and "5000" in p for p in found)


def test_tags_length_quotes_tags_with_spaces():
    assert tags_length(["a", "b"]) == 3
    assert tags_length(["bedtime story"]) == len("bedtime story") + 2
    assert tags_length(["bedtime story", "kids"]) == 13 + 2 + 4 + 1
    assert tags_length([]) == 0


def test_tags_over_limit_refused(settings, terms):
    tags = [f"tag{i:03d}" + "x" * 20 for i in range(30)]
    found = problems(settings, terms, text=good_text() | {"tags": tags})
    assert any(p.startswith("tags:") and "500" in p for p in found)


def test_angle_brackets_refused_in_any_field(settings, terms):
    text = {
        "title": f"{BOOK_TITLE} <b>",
        "description": "a > b",
        "tags": ["<tag>"],
    }
    found = problems(settings, terms, text=text)
    for field in ("title:", "description:", "tags:"):
        assert any(p.startswith(field) and ("<" in p or ">" in p) for p in found), field


def test_disallowed_term_refused(settings, terms):
    assert terms
    bad = terms[0]
    text = good_text() | {"description": f"A story about {bad}."}
    found = problems(settings, terms, text=text)
    assert any(p.startswith("description:") and bad in p for p in found)


def test_email_refused(settings, terms):
    text = good_text() | {"description": "Write to me at someone@example.com please."}
    found = problems(settings, terms, text=text)
    assert any(p.startswith("description:") and "email" in p for p in found)


def test_link_refused_except_site_line(settings, terms):
    for body in (
        "See https://example.org/x",
        "Visit www.example",
        "Go to example.com today",
        "try foo.io now",
        "bar.ly/abc",
    ):
        found = problems(settings, terms, text=good_text() | {"description": body})
        assert any(p.startswith("description:") and "link" in p for p in found), body
    # the site line is added by compose and is never part of the checked body
    assert problems(settings, terms) == []
    assert "http" in compose_description("body", good_chapters(), settings)
    # links in title and tags are refused too
    found = problems(
        settings, terms, text=good_text() | {"title": f"{BOOK_TITLE} foo.com", "tags": ["a.net"]}
    )
    assert any(p.startswith("title:") and "link" in p for p in found)
    assert any(p.startswith("tags:") and "link" in p for p in found)


def test_cta_term_refused(settings, terms):
    text = good_text() | {"description": "Please Like and Subscribe!"}
    found = problems(settings, terms, text=text)
    assert any(p.startswith("description:") and "subscribe" in p.lower() for p in found)
    # whole-word only: "buying" is not "buy", "subscribed" is not "subscribe"
    ok = good_text() | {"description": "Buying lanterns is not needed."}
    assert problems(settings, terms, text=ok) == []


def test_all_problems_reported_at_once(settings, terms):
    text = {
        "title": "x" * 150,
        "description": "mail a@b.com or visit foo.com <b>",
        "tags": ["<t>", ""],
    }
    found = problems(settings, terms, text=text, chapters=[Chapter(5.0, "A")])
    prefixes = {p.split(":")[0] for p in found}
    assert {"title", "description", "tags", "chapters"} <= prefixes
    assert len(found) >= 6


def test_wrong_typed_fields_are_problems_not_exceptions(settings, terms):
    found = problems(settings, terms, text={"title": 5, "description": None, "tags": "kids"})
    prefixes = {p.split(":")[0] for p in found}
    assert {"title", "description", "tags"} <= prefixes
    assert problems(settings, terms, text={})


# --- chapters -------------------------------------------------------------------------------


def test_chapters_start_at_zero_with_page_titles(settings):
    rec = record(
        [("intro", 0.0), ("page-01", 40.0), ("page-02", 90.0), ("page-03", 140.0), ("outro", 200.0)]
    )
    chapters = chapters_from_record(rec, make_book(), settings)
    assert chapters[0] == Chapter(0.0, "Welcome")
    assert [c.label for c in chapters] == [
        "Welcome",
        "Lanterns Wake",
        "The Parade Starts",
        "Page 3",  # no title: fallback
        "Goodnight",
    ]
    assert chapters[1].start == 40.0


def test_first_chapter_starts_at_zero_when_channel_clip_precedes(settings):
    rec = record([("intro", 6.5), ("page-01", 40.0), ("page-02", 90.0), ("outro", 150.0)])
    chapters = chapters_from_record(rec, make_book(), settings)
    assert chapters[0] == Chapter(0.0, "Welcome")


def test_page_matched_by_page_number_not_position(settings):
    book = make_book()
    book["pages"].reverse()
    rec = record([("intro", 0.0), ("page-01", 40.0), ("page-02", 90.0), ("outro", 150.0)])
    chapters = chapters_from_record(rec, book, settings)
    assert chapters[1].label == "Lanterns Wake"


def test_short_page_joins_previous_chapter(settings):
    rec = record(
        [("intro", 0.0), ("page-01", 40.0), ("page-02", 90.0), ("page-04", 95.0), ("outro", 200.0)]
    )
    chapters = chapters_from_record(rec, make_book(), settings)
    # page-02 is only 5 s long, so page-01 absorbs it
    assert [c.label for c in chapters] == ["Welcome", "Lanterns Wake", "Home Again", "Goodnight"]
    assert [c.start for c in chapters] == [0.0, 40.0, 95.0, 200.0]


def test_short_last_chapter_uses_duration(settings):
    rec = record([("intro", 0.0), ("page-01", 40.0), ("page-02", 90.0), ("outro", 295.0)], 300.0)
    chapters = chapters_from_record(rec, make_book(), settings)
    assert [c.label for c in chapters] == ["Welcome", "Lanterns Wake", "The Parade Starts"]


def test_short_first_chapter_absorbs_next(settings):
    rec = record([("intro", 0.0), ("page-01", 5.0), ("page-02", 60.0), ("outro", 120.0)])
    chapters = chapters_from_record(rec, make_book(), settings)
    assert [c.label for c in chapters] == ["Welcome", "The Parade Starts", "Goodnight"]
    assert chapters[0].start == 0.0


def test_back_cover_joins_outro(settings):
    rec = record(
        [
            ("intro", 0.0),
            ("page-01", 40.0),
            ("page-02", 90.0),
            ("back-cover", 150.0),
            ("outro", 160.0),
        ]
    )
    chapters = chapters_from_record(rec, make_book(), settings)
    assert [c.label for c in chapters] == [
        "Welcome",
        "Lanterns Wake",
        "The Parade Starts",
        "Goodnight",
    ]
    assert chapters[-1].start == 150.0


def test_fewer_than_3_chapters_refused(settings):
    chapters = [Chapter(0.0, "Welcome"), Chapter(60.0, "Goodnight")]
    found = chapter_problems(chapters, 120.0, settings)
    assert any("at least 3" in p for p in found)


def test_chapter_problems_first_order_and_short(settings):
    found = chapter_problems(
        [Chapter(1.0, "A"), Chapter(50.0, "B"), Chapter(40.0, "C"), Chapter(45.0, "D")],
        100.0,
        settings,
    )
    text = " ".join(found)
    assert "0:00" in text or "start at 0" in text
    assert "increasing" in text
    assert "10" in text
    assert chapter_problems(good_chapters(), 120.0, settings) == []


def test_record_without_chapters_names_rerender(settings):
    with pytest.raises(YouTubeError) as exc:
        chapters_from_record({"final_key": "k", "duration": 100.0}, make_book(), settings)
    assert "render_episode" in str(exc.value)
    assert "--force" in str(exc.value)


# --- description, timestamps, category, language --------------------------------------------


def test_format_timestamp():
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(65.9) == "1:05"
    assert format_timestamp(750) == "12:30"
    assert format_timestamp(3723) == "1:02:03"


def test_compose_ends_with_site_line(settings):
    out = compose_description("  Body text.  ", good_chapters(), settings)
    assert out == (
        "Body text.\n\n0:00 Welcome\n0:30 Lanterns Wake\n1:20 Goodnight\n\n" + settings.site_line
    )
    assert out.endswith(settings.site_line)


def test_category_storybook_is_27_and_book_override_wins(settings):
    assert category_for(make_book(), None, settings) == "27"
    assert category_for(make_book(content_kind="nursery_rhyme"), {}, settings) == "10"
    assert category_for(make_book(), {"category_id": "24"}, settings) == "24"
    with pytest.raises(YouTubeError):
        category_for(make_book(content_kind="opera"), None, settings)


def test_language_for(settings):
    assert language_for(make_book(), settings) == "en"
    assert language_for(make_book(language="si"), settings) == "si"


# --- records --------------------------------------------------------------------------------


@pytest.fixture
def paths(tmp_path):
    return YouTubePaths(make_root(tmp_path))


def test_paths(paths):
    folder = paths.folder
    assert paths.dir == folder.folder / "youtube"
    assert paths.text == paths.dir / "text.json"
    assert paths.backup == paths.dir / "text.json.bak"
    assert paths.uploads == paths.dir / "uploads.json"
    assert paths.thumbnail == folder.build_dir / "youtube" / "thumbnail.jpg"


def test_read_text_missing_is_none(paths):
    assert read_text(paths.text) is None


def test_write_text_keeps_bak(paths):
    write_text(paths, {"title": "one"}, keep_backup=False)
    assert not paths.backup.exists()
    first_hash = text_sha256(paths.text)
    write_text(paths, {"title": "two"}, keep_backup=True)
    assert read_text(paths.text) == {"title": "two"}
    assert json.loads(paths.backup.read_text(encoding="utf-8")) == {"title": "one"}
    assert text_sha256(paths.text) != first_hash
    assert len(text_sha256(paths.text)) == 64


def test_append_upload_keeps_earlier(paths):
    assert read_uploads(paths) == []
    assert last_upload(paths) is None
    append_upload(paths, {"video_id": "a", "final_key": "k1", "state": "uploaded"})
    append_upload(paths, {"video_id": "b", "final_key": "k2", "state": "uploaded"})
    assert [u["video_id"] for u in read_uploads(paths)] == ["a", "b"]
    assert last_upload(paths)["video_id"] == "b"
    assert not list(paths.dir.glob("*.tmp"))


def test_mark_not_found_keeps_record(paths):
    append_upload(paths, {"video_id": "a", "final_key": "k1", "state": "uploaded"})
    append_upload(paths, {"video_id": "b", "final_key": "k2", "state": "uploaded"})
    mark_not_found(paths, "a", "2026-11-14T10:00:00Z")
    uploads = read_uploads(paths)
    assert len(uploads) == 2
    assert uploads[0]["state"] == "not_found"
    assert uploads[0]["checked_at"] == "2026-11-14T10:00:00Z"
    assert uploads[0]["final_key"] == "k1"
    assert uploads[1]["state"] == "uploaded"


def test_upload_state_none_current_older_cut_not_found(paths):
    rec = {"final_key": "k1", "duration": 10.0}
    assert upload_state(paths, rec) == "none"
    append_upload(paths, {"video_id": "a", "final_key": "k1", "state": "uploaded"})
    assert upload_state(paths, rec) == "current"
    assert upload_state(paths, {"final_key": "k2"}) == "older_cut"
    assert upload_state(paths, None) == "older_cut"
    mark_not_found(paths, "a", "2026-11-14T10:00:00Z")
    assert upload_state(paths, rec) == "not_found"
