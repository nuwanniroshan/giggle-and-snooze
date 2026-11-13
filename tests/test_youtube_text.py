"""Spec 024 Phase 4: Codex writes the YouTube text (Story 2, FR-2, FR-3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers import FIXTURES, load_json, make_root
from tests.test_youtube_mcp import BOOK_TITLE, good_text, write_record
from video_pipeline import youtube, youtube_mcp
from video_pipeline.agents import AgentEvent, AgentResult
from video_pipeline.youtube import YouTubeError, YouTubePaths, load_settings


def server_args(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    setting = next(c for c in command if c.startswith(prefix))
    return json.loads(setting[len(prefix) :])


def prompt_of(command: list[str]) -> str:
    return command[-1]


def bad_text() -> dict:
    return {"title": "A story", "description": "Please subscribe!", "tags": ["kids"]}


class FakeCodex:
    """Plays the agent: each attempt saves the next payload through the real YouTube tool;
    `None` saves nothing (as a provider failure would)."""

    def __init__(self, payloads: list):
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=300, on_event=None, max_refused_saves=5):
        self.commands.append(command)
        args = youtube_mcp.parse_args(server_args(command)[2:])  # drop "-m <module>"
        tools = youtube_mcp.build_tools(args)
        payload = self.payloads.pop(0) if self.payloads else None
        events = []
        if payload is not None:
            result = tools.save_youtube_text(**payload)
            events.append(AgentEvent("tool-result", tool="save_youtube_text", data=result))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        reason = "" if payload is not None else "agent exited with status 1"
        return AgentResult(ok=not reason, events=events, log_path=log_path, reason=reason)


def up_to_date(folder, root):
    return ("up_to_date", "")


@pytest.fixture
def folder(tmp_path):
    folder = make_root(tmp_path)
    write_record(folder)
    return folder


def write(folder, fake, *, force=False, check=up_to_date):
    return youtube.write_youtube_text(
        folder,
        folder.root,
        force=force,
        provider=fake,
        binary=Path("/fake/codex"),
        check=check,
    )


def test_text_written_after_good_draft(folder):
    fake = FakeCodex([good_text()])
    outcome = write(folder, fake)
    assert outcome.status == "written" and outcome.attempts == 1 and outcome.problems == []
    saved = json.loads(YouTubePaths(folder).text.read_text(encoding="utf-8"))
    assert saved["title"] == good_text()["title"]
    assert saved["written_by"] == "codex"
    assert list((folder.build_dir / "agent").glob("*-youtube-1.jsonl"))


def test_failed_attempt_feeds_problems_into_next_prompt(folder):
    fake = FakeCodex([bad_text(), good_text()])
    outcome = write(folder, fake)
    assert outcome.status == "written" and outcome.attempts == 2
    first, second = prompt_of(fake.commands[0]), prompt_of(fake.commands[1])
    assert "The last attempt was refused" not in first
    assert "The last attempt was refused. Fix these problems and submit again:" in second
    assert "- title: must contain the book title" in second
    assert "subscribe" in second.split("The last attempt was refused")[1]


def test_nothing_saved_feeds_reason_into_next_prompt(folder):
    fake = FakeCodex([None, good_text()])
    outcome = write(folder, fake)
    assert outcome.status == "written"
    assert "- agent exited with status 1" in prompt_of(fake.commands[1])


def test_three_failures_save_nothing(folder):
    fake = FakeCodex([bad_text(), bad_text(), bad_text()])
    outcome = write(folder, fake)
    assert outcome.status == "failed" and outcome.attempts == 3
    assert any("book title" in p for p in outcome.problems)
    assert len(fake.commands) == 3
    assert not YouTubePaths(folder).dir.exists()


def test_existing_text_needs_force(folder):
    paths = YouTubePaths(folder)
    paths.dir.mkdir(parents=True)
    paths.text.write_text('{"title": "old"}', encoding="utf-8")
    fake = FakeCodex([good_text()])
    with pytest.raises(YouTubeError, match="--force"):
        write(folder, fake)
    assert fake.commands == []


def test_force_keeps_bak(folder):
    paths = YouTubePaths(folder)
    paths.dir.mkdir(parents=True)
    paths.text.write_text('{"title": "old"}', encoding="utf-8")
    outcome = write(folder, FakeCodex([good_text()]), force=True)
    assert outcome.status == "written"
    assert json.loads(paths.backup.read_text(encoding="utf-8")) == {"title": "old"}
    assert json.loads(paths.text.read_text(encoding="utf-8"))["title"] == good_text()["title"]


def test_refused_when_video_not_up_to_date(folder):
    fake = FakeCodex([good_text()])
    with pytest.raises(YouTubeError, match="out_of_date.*the pictures changed"):
        write(folder, fake, check=lambda f, r: ("out_of_date", "the pictures changed"))
    assert fake.commands == []


def test_record_without_chapters_refused(folder):
    write_record(folder, chapters=False)
    fake = FakeCodex([good_text()])
    with pytest.raises(YouTubeError, match="--force"):
        write(folder, fake)
    assert fake.commands == []


def test_prompt_carries_this_books_pages_not_another(tmp_path):
    mini = make_root(tmp_path)
    other_data = load_json(FIXTURES / "mini_book.json")
    other_data["metadata"]["title"] = "The Pebble Who Wanted Wings"
    other_data["pages"][0]["text"] = "Pip the pebble dreamed of flying over the quarry."
    other_path = tmp_path / "other.json"
    other_path.write_text(json.dumps(other_data), encoding="utf-8")
    other = make_root(tmp_path, other_path)
    settings = load_settings(tmp_path / "config")

    mini_prompt = youtube.build_prompt(load_json(mini.book_json), None, settings)
    other_prompt = youtube.build_prompt(load_json(other.book_json), None, settings)

    assert BOOK_TITLE in mini_prompt and "Glowmoss Meadow" in mini_prompt
    assert "Pip the pebble" not in mini_prompt
    assert "The Pebble Who Wanted Wings" in other_prompt and "Pip the pebble" in other_prompt
    assert settings.series in mini_prompt
    assert "save_youtube_text" in mini_prompt
    assert "Ages 3-6" in mini_prompt


def test_prompt_includes_mood_when_blueprint_exists(folder):
    blueprint = {
        "mood": {
            "key": "hushed-wonder",
            "label": "Hushed wonder",
            "summary": "A whispery, starlit calm.",
            "roles": {},
        }
    }
    folder.blueprint_path.write_text(json.dumps(blueprint), encoding="utf-8")
    fake = FakeCodex([good_text()])
    write(folder, fake)
    prompt = prompt_of(fake.commands[0])
    assert "Hushed wonder" in prompt and "A whispery, starlit calm." in prompt


def test_prompt_without_blueprint_has_no_mood(folder):
    fake = FakeCodex([good_text()])
    write(folder, fake)
    assert "Mood:" not in prompt_of(fake.commands[0])


def test_cli_text_exit_codes(folder, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    argv = ["text", "--book", folder.slug, "--root", str(folder.root)]

    monkeypatch.setattr(youtube, "run_codex", FakeCodex([bad_text()] * 3))
    assert youtube.main(argv) == 1
    assert "book title" in capsys.readouterr().err

    monkeypatch.setattr(youtube, "run_codex", FakeCodex([good_text()]))
    assert youtube.main(argv) == 0
    out = capsys.readouterr().out
    assert f"Wrote books/{folder.slug}/youtube/text.json" in out
    assert good_text()["title"] in out

    assert youtube.main(argv) == 1  # exists, no --force
    assert "--force" in capsys.readouterr().err

    monkeypatch.setattr(youtube, "_render_check", lambda f, r: ("not_rendered", ""))
    assert youtube.main([*argv, "--force"]) == 1
    assert "not_rendered" in capsys.readouterr().err


@pytest.mark.codex
def test_real_codex_writes_text_for_the_mini_book(folder):
    outcome = youtube.write_youtube_text(folder, folder.root, force=False, check=up_to_date)
    assert outcome.status == "written", outcome.problems
    saved = json.loads(YouTubePaths(folder).text.read_text(encoding="utf-8"))
    assert BOOK_TITLE.lower() in saved["title"].lower()
