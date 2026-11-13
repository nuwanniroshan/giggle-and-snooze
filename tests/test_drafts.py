"""Draft store for add-a-book-from-an-idea (spec 006: AC-3.4..3.8, FR-4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_story_tools import book, idea
from video_pipeline import drafts as dr
from video_pipeline.library import BookFolder


@pytest.fixture
def root(tmp_path: Path) -> Path:
    from tests.helpers import make_root

    make_root(tmp_path)  # shared config, including config/book_defaults.json
    return tmp_path


def ready_draft(store: dr.DraftStore, title: str = "The Brave Little Teapot") -> str:
    record = store.new_draft(idea(title), pages=8, author="Test Author")
    store.update(record["id"], state="ready", book=book(8, **{}), attempts=1, problems=[])
    return record["id"]


def test_new_draft_is_written_and_listed(root):  # FR-4
    store = dr.DraftStore(root)
    record = store.new_draft(idea(), pages=12, author="Test Author")
    assert record["state"] == "designing" and record["pages"] == 12
    assert (root / "drafts" / record["id"] / "draft.json").is_file()
    assert [r["id"] for r in store.list()] == [record["id"]]


def test_draft_survives_a_new_store(root):  # FR-4: kept across restarts
    first = dr.DraftStore(root)
    record = first.new_draft(idea(), pages=8, author="Test Author")
    assert dr.DraftStore(root).get(record["id"])["idea"]["title"] == "The Brave Little Teapot"


def test_valid_page_edit_is_saved(root):  # AC-3.4
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    assert store.update_page_text(draft_id, 2, "The teapot sang a happy song.") == []
    assert store.get(draft_id)["book"]["pages"][1]["text"] == "The teapot sang a happy song."


def test_invalid_page_edit_changes_nothing(root):  # AC-3.4
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    before = store.get(draft_id)["book"]["pages"][1]["text"]
    errors = store.update_page_text(draft_id, 2, "A monster came to visit.")
    assert errors and any("'monster'" in e for e in errors)
    assert store.get(draft_id)["book"]["pages"][1]["text"] == before


def test_create_book_writes_the_book_folder_and_removes_the_draft(root):  # AC-3.5
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    slug = store.create_book(draft_id)
    folder = BookFolder(slug, root)
    assert slug == "the-brave-little-teapot"
    data = json.loads(folder.book_json.read_text(encoding="utf-8"))
    assert data["metadata"]["author"] == "Test Author"
    assert data["metadata"]["publisher"] == "Giggle & Snooze"  # from defaults
    assert data["metadata"]["digital_page_count"] == 8
    assert len(data["pages"]) == 8
    assert folder.illustrations.is_dir()
    assert store.get(draft_id) is None


def test_create_book_marks_titled_covers(root):  # spec 022
    store = dr.DraftStore(root)
    slug = store.create_book(ready_draft(store))
    data = json.loads(BookFolder(slug, root).book_json.read_text(encoding="utf-8"))
    assert data["metadata"]["cover_style"] == "titled"


def test_create_book_refuses_an_existing_slug_and_keeps_the_draft(root):  # AC-3.6
    store = dr.DraftStore(root)
    first = ready_draft(store)
    store.create_book(first)
    second = ready_draft(store)
    with pytest.raises(dr.DraftError, match="already exists"):
        store.create_book(second)
    assert store.get(second) is not None


def test_create_book_needs_a_ready_draft(root):
    store = dr.DraftStore(root)
    record = store.new_draft(idea(), pages=8, author="Test Author")
    with pytest.raises(dr.DraftError, match="not ready"):
        store.create_book(record["id"])


def test_discard_deletes_the_draft_folder(root):  # AC-3.8
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    store.discard(draft_id)
    assert not (root / "drafts" / draft_id).exists()


def test_running_drafts_are_interrupted_on_startup(root):  # AC-3.7
    store = dr.DraftStore(root)
    draft_id = store.new_draft(idea(), pages=8, author="Test Author")["id"]
    assert store.mark_interrupted_all() == 1
    assert store.get(draft_id)["state"] == "interrupted"


def test_idea_runs_keep_their_result(root):  # FR-4: suggestions and candidates
    store = dr.DraftStore(root)
    run_id = store.new_run("ideas")
    assert store.read_run(run_id)["state"] == "writing"
    store.update_run(run_id, state="ready", payload={"ideas": []}, problems=[])
    assert store.read_run(run_id)["payload"] == {"ideas": []}


# --- the character sheet (spec 018: AC-1.4) ---------------------------------------------------


def test_create_book_stores_the_character_sheet_and_page_names(root):  # AC-1.4
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    sent = store.get(draft_id)["book"]
    data = json.loads(BookFolder(store.create_book(draft_id), root).book_json.read_text("utf-8"))
    assert data["style_bible"]["characters"] == sent["characters"]
    assert "characters" not in data
    assert [p["characters_on_page"] for p in data["pages"]] == [
        p["characters_on_page"] for p in sent["pages"]
    ]


def test_create_book_with_a_blueprint_keeps_both(root):  # AC-1.4 with spec 011
    from tests.blueprint_helpers import valid_blueprint
    from video_pipeline.episode_tools import write_atomic
    from video_pipeline.library import PROJECT_ROOT

    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "sound_library").symlink_to(PROJECT_ROOT / "assets" / "sound_library")
    store = dr.DraftStore(root)
    draft_id = ready_draft(store)
    write_atomic(store.blueprint_path(draft_id), valid_blueprint())
    data = json.loads(BookFolder(store.create_book(draft_id), root).book_json.read_text("utf-8"))
    assert data["style_bible"]["style_prompt_prefix"]
    assert [c["name"] for c in data["style_bible"]["characters"]] == ["Tilly", "Hoot"]


def pre_018_book() -> dict:
    data = book(8)
    del data["characters"]
    for page in data["pages"]:
        del page["characters_on_page"]
    return data


def test_draft_written_before_spec_018_still_becomes_a_book(root):  # AC-2.3
    store = dr.DraftStore(root)
    record = store.new_draft(idea(), pages=8, author="Test Author")
    store.update(record["id"], state="ready", book=pre_018_book(), attempts=1, problems=[])
    data = json.loads(BookFolder(store.create_book(record["id"]), root).book_json.read_text("utf-8"))
    assert "characters" not in data["style_bible"]
    assert all("characters_on_page" not in p for p in data["pages"])


def test_draft_written_before_spec_018_can_still_be_edited(root):
    store = dr.DraftStore(root)
    record = store.new_draft(idea(), pages=8, author="Test Author")
    store.update(record["id"], state="ready", book=pre_018_book(), attempts=1, problems=[])
    assert store.update_page_text(record["id"], 2, "The teapot sang a happy song.") == []
