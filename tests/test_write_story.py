"""The story writer CLI with a fake Codex (spec 006: AC-1.1..1.4, AC-2.1, AC-3.1..3.3, FR-7, FR-8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_story_tools import book, idea
from video_pipeline import story_mcp
from video_pipeline import write_story as ws
from video_pipeline.agents import AgentEvent, AgentResult, AgentUnavailable
from video_pipeline.drafts import DraftStore


@pytest.fixture
def root(tmp_path: Path) -> Path:
    from tests.helpers import make_root

    make_root(tmp_path)
    return tmp_path


def server_args(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    setting = next(c for c in command if c.startswith(prefix))
    return json.loads(setting[len(prefix) :])


def prompt_of(command: list[str]) -> str:
    return command[-1]


class FakeCodex:
    """Plays the agent: each attempt submits the next payload through the real story tools."""

    def __init__(self, payloads: list):
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=300, on_event=None, max_refused_saves=5):
        self.commands.append(command)
        args = story_mcp.parse_args(server_args(command)[2:])  # drop "-m video_pipeline.story_mcp"
        tools = story_mcp.build_tools(args)
        submit = {
            "ideas": tools.submit_ideas,
            "candidate": tools.submit_candidate,
            "book": tools.submit_book,
            "blueprint": tools.submit_blueprint,
        }[args.mode]
        events = []
        if self.payloads:
            result = submit(self.payloads.pop(0))
            events.append(AgentEvent("tool-result", tool=f"submit_{args.mode}", data=result))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        return AgentResult(ok=True, events=events, log_path=log_path, reason="")


def use(monkeypatch, fake: FakeCodex) -> None:
    monkeypatch.setattr(ws, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(ws, "run_codex", fake)


def ideas_payload(count: int = 3) -> dict:
    return {"ideas": [idea(f"Story Number {n}") for n in range(count)]}


def test_ideas_are_saved_to_the_run(root, monkeypatch):  # AC-1.1
    store = DraftStore(root)
    run = store.new_run("ideas")
    use(monkeypatch, FakeCodex([ideas_payload()]))
    assert ws.run_ideas(root, run) == 0
    record = store.read_run(run)
    assert record["state"] == "ready" and len(record["payload"]["ideas"]) == 3


def test_invalid_ideas_are_retried_with_the_problems(root, monkeypatch):  # FR-7
    store = DraftStore(root)
    run = store.new_run("ideas")
    bad = {"ideas": [idea("Story Number 0", premise="A monster hides under the bed.")] * 3}
    fake = FakeCodex([bad, ideas_payload()])
    use(monkeypatch, fake)
    assert ws.run_ideas(root, run) == 0
    assert len(fake.commands) == 2
    assert "monster" in prompt_of(fake.commands[1])  # the refusal is passed back to the agent


def test_all_attempts_failing_marks_the_run_failed(root, monkeypatch, capsys):  # AC-1.4
    store = DraftStore(root)
    run = store.new_run("ideas")
    bad = {"ideas": []}
    fake = FakeCodex([bad, bad, bad])
    use(monkeypatch, fake)
    assert ws.run_ideas(root, run) == 1
    record = store.read_run(run)
    assert record["state"] == "failed" and record["problems"]
    assert record["attempts"] == 3


def test_no_provider_saves_nothing(root, monkeypatch, capsys):  # AC-1.3
    store = DraftStore(root)
    run = store.new_run("ideas")

    def down():
        raise AgentUnavailable("install the ChatGPT desktop app")

    monkeypatch.setattr(ws, "codex_status", down)
    monkeypatch.setattr(ws, "run_codex", lambda *a, **k: pytest.fail("must not run"))
    assert ws.run_ideas(root, run) == 1
    record = store.read_run(run)
    assert record["state"] == "failed" and record["payload"] is None
    assert "install the ChatGPT desktop app" in record["problems"][0]


def test_improve_uses_the_typed_idea(root, monkeypatch):  # AC-2.1
    store = DraftStore(root)
    run = store.new_run("candidate")
    store.update_run(run, request="a teapot who is scared of the dark")
    fake = FakeCodex([idea("The Brave Little Teapot")])
    use(monkeypatch, fake)
    assert ws.run_improve(root, run) == 0
    assert "scared of the dark" in prompt_of(fake.commands[0])
    assert store.read_run(run)["payload"]["title"] == "The Brave Little Teapot"


def test_book_draft_is_written_and_ready(root, monkeypatch):  # AC-3.1, AC-3.2
    store = DraftStore(root)
    draft = store.new_draft(idea(), pages=12, author="Test Author")
    fake = FakeCodex([book(8)])
    use(monkeypatch, fake)
    assert ws.run_book(root, draft["id"]) == 0
    record = store.get(draft["id"])
    assert record["state"] == "ready" and len(record["book"]["pages"]) == 8
    assert "--pages" in server_args(fake.commands[0]) and "12" in server_args(fake.commands[0])


def test_book_that_breaks_the_rules_fails_the_draft(root, monkeypatch):  # AC-3.3
    store = DraftStore(root)
    draft = store.new_draft(idea(), pages=8, author="Test Author")
    too_long = book(10)
    use(monkeypatch, FakeCodex([too_long, too_long, too_long]))
    assert ws.run_book(root, draft["id"]) == 1
    record = store.get(draft["id"])
    assert record["state"] == "failed" and any(
        "more than the maximum" in p for p in record["problems"]
    )


def test_writing_agent_is_read_only_and_has_only_story_tools(root, monkeypatch):  # FR-8
    store = DraftStore(root)
    draft = store.new_draft(idea(), pages=8, author="Test Author")
    fake = FakeCodex([book(8)])
    use(monkeypatch, fake)
    ws.run_book(root, draft["id"])
    command = fake.commands[0]
    assert "read-only" in command
    args = server_args(command)
    assert args[:2] == ["-m", "video_pipeline.story_mcp"]
    assert "video_pipeline.mcp_server" not in " ".join(args)


def test_run_logs_stay_with_the_draft(root, monkeypatch):  # FR-9
    store = DraftStore(root)
    draft = store.new_draft(idea(), pages=8, author="Test Author")
    use(monkeypatch, FakeCodex([book(8)]))
    ws.run_book(root, draft["id"])
    assert list((store.draft_folder(draft["id"]) / "agent").glob("*-codex.jsonl"))


# --- the character sheet (spec 018: AC-1.1..1.3, FR-1) ----------------------------------------


def test_book_prompt_asks_for_the_sheet_and_page_names():  # FR-1
    assert '"characters"' in ws.BOOK_PROMPT and '"characters_on_page"' in ws.BOOK_PROMPT
    assert "look" in ws.BOOK_PROMPT


def test_submit_book_tool_describes_the_sheet():  # FR-1
    import inspect

    source = inspect.getsource(story_mcp.create_server)
    assert "characters_on_page" in source and '"characters"' in source


def test_validate_payload_refuses_a_book_without_a_sheet(root):  # AC-1.2
    data = book(8)
    del data["characters"]
    problems = ws.validate_payload("book", data, 20, root)
    assert any(p.startswith("characters:") for p in problems)
    assert ws.validate_payload("book", book(8), 20, root) == []
