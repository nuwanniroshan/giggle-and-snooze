"""The design job, draft states and the story brief (spec 011 Phase 2: AC-1.1..1.6, 2.5, 2.8-2.10, 3.3)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.test_story_tools import book, idea
from tests.test_write_story import FakeCodex, prompt_of, server_args, use
from video_pipeline import story_mcp
from video_pipeline import write_story as ws
from video_pipeline.drafts import DraftStore
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder

DIFFERENT_PALETTE = [
    {"name": "red", "hex": "#ff0000"},
    {"name": "green", "hex": "#00ff00"},
    {"name": "blue", "hex": "#0000ff"},
    {"name": "white", "hex": "#ffffff"},
    {"name": "yellow", "hex": "#ffff00"},
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    from tests.helpers import make_root

    make_root(tmp_path)
    (tmp_path / "assets").mkdir()
    shutil.copytree(
        PROJECT_ROOT / "assets" / "sound_library", tmp_path / "assets" / "sound_library"
    )
    return tmp_path


def distinct_blueprint(**changes) -> dict:
    return valid_blueprint(
        **{
            "art.medium_key": "ink-and-wash",
            "mood.key": "x-mood",
            "qa.format_key": "x-fmt",
            "art.palette": DIFFERENT_PALETTE,
            **changes,
        }
    )


def write_book_with_blueprint(root: Path, slug: str, data: dict) -> BookFolder:
    from tests.helpers import make_root

    folder = make_root(root, slug=slug)
    write_atomic(folder.blueprint_path, data)
    return folder


def close_failure_reasons(record: dict) -> str:
    return " ".join(record.get("problems", []))


def test_blueprint_job_saves_a_valid_blueprint_and_starts_the_story(
    root, monkeypatch
):  # AC-1.1, AC-1.2
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    fake = FakeCodex([valid_blueprint(), book()])
    use(monkeypatch, fake)
    assert ws.run_blueprint(root, draft_id=draft["id"], then_book=True) == 0
    record = store.get(draft["id"])
    assert record["state"] == "ready" and record["book"]
    assert (store.draft_folder(draft["id"]) / "blueprint.json").is_file()
    assert "Sleepy drift" in prompt_of(fake.commands[1])  # AC-3.3
    assert "without naming a painting medium" in prompt_of(fake.commands[1])


def test_design_alone_does_not_start_the_story(root, monkeypatch):  # AC-1.1
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    fake = FakeCodex([valid_blueprint(), book()])
    use(monkeypatch, fake)
    assert ws.run_blueprint(root, draft_id=draft["id"]) == 0
    assert len(fake.commands) == 1
    assert (store.draft_folder(draft["id"]) / "blueprint.json").is_file()
    assert store.get(draft["id"])["book"] is None


def test_design_runs_as_the_blueprint_mode_with_the_draft_as_subject(root, monkeypatch):  # AC-1.1
    draft = DraftStore(root).new_draft(idea(), 20, "A")
    fake = FakeCodex([valid_blueprint()])
    use(monkeypatch, fake)
    ws.run_blueprint(root, draft_id=draft["id"])
    args = server_args(fake.commands[0])
    assert args[args.index("--mode") + 1] == "blueprint"
    assert args[args.index("--subject") + 1] == f"draft:{draft['id']}"


def test_too_close_blueprint_is_refused_with_the_book_named_then_retried(
    root, monkeypatch
):  # AC-2.5
    write_book_with_blueprint(root, "moon-book", valid_blueprint())
    fake = FakeCodex([valid_blueprint(), distinct_blueprint()])
    use(monkeypatch, fake)
    draft = DraftStore(root).new_draft(idea(), 20, "A")
    assert ws.run_blueprint(root, draft_id=draft["id"]) == 0
    assert "too close to 'moon-book'" not in prompt_of(fake.commands[0])
    assert "too close to 'moon-book'" in prompt_of(fake.commands[1])  # reasons go back


def test_three_failed_attempts_mark_design_failed_with_reasons(root, monkeypatch):  # AC-1.4
    write_book_with_blueprint(root, "moon-book", valid_blueprint())
    use(monkeypatch, FakeCodex([valid_blueprint()] * 3))
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    assert ws.run_blueprint(root, draft_id=draft["id"], then_book=True) == 1
    record = store.get(draft["id"])
    assert record["state"] == "design_failed"
    assert "moon-book" in close_failure_reasons(record)
    assert not (store.draft_folder(draft["id"]) / "blueprint.json").exists()


def test_brief_lists_every_other_blueprint(root, monkeypatch):  # AC-2.10
    write_book_with_blueprint(root, "moon-book", valid_blueprint())
    other = distinct_blueprint()
    write_book_with_blueprint(root, "sun-book", other)
    fake = FakeCodex([distinct_blueprint(**{"mood.key": "y-mood", "qa.format_key": "y-fmt"})])
    use(monkeypatch, fake)
    draft = DraftStore(root).new_draft(idea(), 20, "A")
    ws.run_blueprint(root, draft_id=draft["id"])
    prompt = prompt_of(fake.commands[0])
    for label, bp in (("moon-book", valid_blueprint()), ("sun-book", other)):
        assert label in prompt
        for key in (bp["mood"]["key"], bp["art"]["medium_key"], bp["qa"]["format_key"]):
            assert key in prompt
        for swatch in bp["art"]["palette"]:
            assert swatch["hex"] in prompt


def test_regenerating_a_book_excludes_its_own_blueprint(root, monkeypatch):  # AC-2.8
    folder = write_book_with_blueprint(root, "moon-book", valid_blueprint())
    old = folder.blueprint_path.read_text(encoding="utf-8")
    fresh = valid_blueprint(rationale="A new hushed night look, redone.")
    use(monkeypatch, FakeCodex([fresh]))
    assert ws.run_blueprint(root, slug="moon-book") == 0
    assert json.loads(folder.blueprint_path.read_text(encoding="utf-8")) == fresh
    assert (folder.folder / "blueprint.json.bak").read_text(encoding="utf-8") == old


def test_failed_regeneration_keeps_the_old_book_blueprint(root, monkeypatch):  # AC-2.8
    write_book_with_blueprint(root, "sun-book", distinct_blueprint())
    folder = write_book_with_blueprint(root, "moon-book", valid_blueprint())
    old = folder.blueprint_path.read_text(encoding="utf-8")
    use(monkeypatch, FakeCodex([distinct_blueprint()] * 3))  # too close to sun-book
    assert ws.run_blueprint(root, slug="moon-book") == 1
    assert folder.blueprint_path.read_text(encoding="utf-8") == old
    assert not (folder.folder / "blueprint.json.bak").exists()


def test_other_drafts_blueprints_are_compared(root, monkeypatch):  # AC-2.9
    store = DraftStore(root)
    first = store.new_draft(idea("Other Story Title"), 20, "A")
    write_atomic(store.draft_folder(first["id"]) / "blueprint.json", valid_blueprint())
    fake = FakeCodex([valid_blueprint(), distinct_blueprint()])
    use(monkeypatch, fake)
    second = store.new_draft(idea("Second Story Title"), 20, "A")
    assert ws.run_blueprint(root, draft_id=second["id"]) == 0
    assert "too close to 'Other Story Title'" in prompt_of(fake.commands[1])


def test_a_drafts_own_blueprint_is_not_compared_with_itself(root, monkeypatch):  # AC-2.9
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    write_atomic(store.draft_folder(draft["id"]) / "blueprint.json", valid_blueprint())
    use(monkeypatch, FakeCodex([valid_blueprint()]))
    assert ws.run_blueprint(root, draft_id=draft["id"]) == 0


def test_redesigning_a_ready_draft_keeps_it_ready_and_records_problems(root, monkeypatch):  # AC-1.4
    write_book_with_blueprint(root, "moon-book", valid_blueprint())
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    store.update(draft["id"], state="ready", book=book(8))
    use(monkeypatch, FakeCodex([valid_blueprint()] * 3))
    assert ws.run_blueprint(root, draft_id=draft["id"]) == 1
    record = store.get(draft["id"])
    assert record["state"] == "ready" and "moon-book" in " ".join(record["design_problems"])


def test_store_new_draft_starts_designing_and_interrupt_marks_it(root):  # AC-1.1, AC-1.3
    store = DraftStore(root)
    d = store.new_draft(idea(), 20, "A")
    assert d["state"] == "designing" and d["phase"] == "design"
    store.mark_interrupted_all()
    interrupted = store.get(d["id"])
    assert interrupted["state"] == "interrupted" and interrupted["phase"] == "design"


def test_writing_phase_is_recorded_when_the_story_starts(root, monkeypatch):  # AC-1.3
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")
    use(monkeypatch, FakeCodex([book()]))
    ws.run_book(root, draft["id"])
    assert store.get(draft["id"])["phase"] == "story"


def test_blueprint_path_is_in_the_draft_folder(root):
    store = DraftStore(root)
    d = store.new_draft(idea(), 20, "A")
    assert store.blueprint_path(d["id"]) == store.draft_folder(d["id"]) / "blueprint.json"


def test_create_book_copies_blueprint_and_sets_style_bible(root):  # AC-1.6
    store = DraftStore(root)
    d = store.new_draft(idea(), 20, "A")
    store.update(d["id"], state="ready", book=book(8))
    write_atomic(store.blueprint_path(d["id"]), valid_blueprint())
    slug = store.create_book(d["id"])
    folder = BookFolder(slug, root)
    assert json.loads(folder.blueprint_path.read_text(encoding="utf-8")) == valid_blueprint()
    data = json.loads(folder.book_json.read_text(encoding="utf-8"))
    assert (
        data["style_bible"]["style_prompt_prefix"]
        == valid_blueprint()["art"]["style_prompt_prefix"]
    )
    assert store.get(d["id"]) is None


def test_write_without_a_blueprint_makes_a_plain_book(root, monkeypatch):  # AC-1.5
    store = DraftStore(root)
    d = store.new_draft(idea(), 20, "A")
    store.update(d["id"], state="design_failed", problems=["too close"])
    fake = FakeCodex([book()])
    use(monkeypatch, fake)
    assert ws.run_book(root, d["id"]) == 0
    assert "without naming a painting medium" not in prompt_of(fake.commands[0])
    assert store.get(d["id"])["state"] == "ready"
    slug = store.create_book(d["id"])
    folder = BookFolder(slug, root)
    assert not folder.blueprint_path.exists()
    assert json.loads(folder.book_json.read_text(encoding="utf-8"))["style_bible"] == {}


def test_mcp_blueprint_mode_takes_a_subject_and_builds_blueprint_tools(root):  # AC-2.5
    args = story_mcp.parse_args(
        [
            "--mode",
            "blueprint",
            "--out",
            str(root / "o.json"),
            "--root",
            str(root),
            "--subject",
            "book:moon-book",
        ]
    )
    tools = story_mcp.build_tools(args)
    assert tools.submit_blueprint(valid_blueprint()) == {"saved": True, "errors": []}


def test_cli_blueprint_command_accepts_draft_with_then_book_or_book():
    args = ws.parse_args(["blueprint", "--draft", "abc", "--then-book"])
    assert args.command == "blueprint" and args.draft == "abc" and args.then_book
    assert ws.parse_args(["blueprint", "--book", "moon-book"]).book == "moon-book"
    with pytest.raises(SystemExit):
        ws.parse_args(["blueprint", "--draft", "a", "--book", "b"])
