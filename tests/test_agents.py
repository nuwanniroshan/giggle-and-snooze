import json
import time
from pathlib import Path

import pytest

from tests.helpers import FIXTURES
from video_pipeline import agents
from video_pipeline.agents import (
    AGENT_PROMPT,
    DISABLED_FEATURES,
    AgentEvent,
    AgentResult,
    AgentUnavailable,
    build_codex_command,
    codex_status,
    find_codex,
    parse_codex_event,
    run_codex,
)

EVENTS = FIXTURES / "codex_events.jsonl"


def fake_bin(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "codex"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_find_codex_prefers_env(monkeypatch, tmp_path):
    binary = fake_bin(tmp_path, "true")
    monkeypatch.setenv(agents.CODEX_BIN_ENV, str(binary))
    assert find_codex() == binary


@pytest.mark.skipif(agents.os.name != "nt", reason="Windows app discovery")
def test_find_codex_uses_installed_windows_app(monkeypatch, tmp_path):
    from types import SimpleNamespace

    binary = tmp_path / "app/resources/codex.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.delenv(agents.CODEX_BIN_ENV, raising=False)
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    monkeypatch.setattr(agents, "CODEX_SEARCH", ())
    monkeypatch.setattr(
        agents.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=f"{tmp_path}\n")
    )
    assert find_codex() == binary


def test_find_codex_checks_known_locations(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "windows_codex", lambda: None)
    monkeypatch.delenv(agents.CODEX_BIN_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    binary = fake_bin(tmp_path, "true")
    monkeypatch.setattr(agents, "CODEX_SEARCH", (tmp_path / "missing", binary))
    assert find_codex() == binary
    monkeypatch.setattr(agents, "CODEX_SEARCH", (tmp_path / "missing",))
    assert find_codex() is None


def test_codex_status_logged_in(monkeypatch, tmp_path):
    binary = fake_bin(tmp_path, 'echo "Logged in using ChatGPT"')
    monkeypatch.setenv(agents.CODEX_BIN_ENV, str(binary))
    assert codex_status() == binary


def test_codex_status_not_logged_in(monkeypatch, tmp_path):  # AC-6.6
    binary = fake_bin(tmp_path, 'echo "Not logged in"; exit 1')
    monkeypatch.setenv(agents.CODEX_BIN_ENV, str(binary))
    with pytest.raises(AgentUnavailable, match="codex login"):
        codex_status()


def test_codex_status_not_installed(monkeypatch, tmp_path):  # AC-6.6
    monkeypatch.setattr(agents, "windows_codex", lambda: None)
    monkeypatch.delenv(agents.CODEX_BIN_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(agents, "CODEX_SEARCH", ())
    with pytest.raises(AgentUnavailable, match="ChatGPT desktop app"):
        codex_status()


def test_build_codex_command_allows_only_the_episode_tools():  # AC-6.8
    command = build_codex_command(
        Path("/bin/codex"),
        "/py",
        ["-m", "video_pipeline.mcp_server", "--seed", "7"],
        {"PYTHONPATH": "/repo"},
        model="gpt-x",
    )
    assert command[:2] == ["/bin/codex", "exec"]
    for flag in (
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
    ):
        assert flag in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    disabled = {command[i + 1] for i, arg in enumerate(command) if arg == "--disable"}
    assert disabled == set(DISABLED_FEATURES)
    settings = {command[i + 1] for i, arg in enumerate(command) if arg == "-c"}
    assert 'mcp_servers.kiddo.command="/py"' in settings
    assert 'mcp_servers.kiddo.args=["-m", "video_pipeline.mcp_server", "--seed", "7"]' in settings
    assert 'mcp_servers.kiddo.env.PYTHONPATH="/repo"' in settings
    assert 'mcp_servers.kiddo.default_tools_approval_mode="approve"' in settings
    assert 'web_search="disabled"' in settings
    assert command[command.index("--model") + 1] == "gpt-x"
    assert command[-1] == AGENT_PROMPT
    assert not any(arg.startswith("--dangerously") for arg in command)


def test_codex_command_attaches_images():
    args = (Path("/bin/codex"), "/py", ["-m", "x"], {})
    command = build_codex_command(*args, prompt="go", images=[Path("/x/p.png")])
    i = command.index("-i")
    assert command[i + 1] == "/x/p.png"
    assert i < command.index("--") < len(command) - 1  # "--" ends -i's file list
    assert command[-2:] == ["--", "go"]
    assert "-i" not in build_codex_command(*args, prompt="go")


def test_agent_prompt_names_every_tool():
    for name in ("get_episode_plan", "validate_banter", "save_episode_script"):
        assert name in AGENT_PROMPT


def test_parse_codex_event_fixture():
    events = [e for line in EVENTS.read_text().splitlines() for e in parse_codex_event(line)]
    assert [e.kind for e in events] == [
        "say",
        "tool",
        "tool-result",
        "tool-result",
        "tool-result",
        "done",
    ]
    assert events[0].text == "Getting the plan."
    assert events[2].data == {"title": "T"}
    assert events[3].tool == "save_episode_script"
    assert events[3].data == {"saved": False, "errors": ["bad"]}
    assert events[4].ok is False and "approval" in events[4].text


def test_last_save_is_the_latest_save_result(tmp_path):
    result = AgentResult(
        ok=True,
        events=[
            AgentEvent("tool-result", tool="save_episode_script", data={"saved": False}),
            AgentEvent("tool-result", tool="validate_banter", data={"valid": True}),
            AgentEvent("tool-result", tool="save_episode_script", data={"saved": True}),
        ],
        log_path=tmp_path / "log.jsonl",
    )
    assert result.last_save == {"saved": True}
    assert AgentResult(ok=True, events=[], log_path=tmp_path).last_save is None


def test_run_codex_streams_events_and_keeps_log(tmp_path):
    binary = fake_bin(tmp_path, f'cat "{EVENTS}"')
    log_path = tmp_path / "agent" / "run.jsonl"
    seen: list[AgentEvent] = []
    result = run_codex([str(binary)], log_path, on_event=seen.append)
    assert result.ok and result.reason == ""
    assert seen == result.events and result.events[-1].kind == "done"
    assert log_path.read_text() == EVENTS.read_text()
    assert log_path.with_suffix(".stderr.log").exists()


def test_run_codex_reads_utf8_events(tmp_path):
    import sys

    message = "A wolf says \u201cHello!\u201d \u2014 \u732a"
    event = {"type": "item.completed", "item": {"type": "agent_message", "text": message}}
    payload = (json.dumps(event, ensure_ascii=False) + '\n{"type":"turn.completed"}\n').encode("utf-8")
    code = f"import sys; sys.stdout.buffer.write({payload!r})"
    result = run_codex([sys.executable, "-c", code], tmp_path / "run.jsonl")
    assert result.ok
    assert result.events[0].text == message


def test_run_codex_time_limit(tmp_path):  # FR-7: 10-minute limit
    binary = fake_bin(tmp_path, "exec sleep 5")  # exec: kill reaches sleep, closing stdout
    result = run_codex([str(binary)], tmp_path / "run.jsonl", timeout_s=0.5)
    assert result.ok is False and "time limit" in result.reason


def refused_save_line(errors: list[str]) -> str:
    item = {
        "type": "mcp_tool_call",
        "tool": "save_episode_script",
        "result": {"structured_content": {"saved": False, "errors": errors}},
        "error": None,
    }
    return json.dumps({"type": "item.completed", "item": item})


def test_run_codex_stops_after_five_refused_saves(tmp_path):  # FR-7, AC-6.7
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(refused_save_line([f"intro: problem {n}"]) for n in range(6)) + "\n",
        encoding="utf-8",
    )
    binary = fake_bin(tmp_path, f'cat "{events}"; exec sleep 5')
    started = time.monotonic()
    result = run_codex([str(binary)], tmp_path / "run.jsonl")
    assert time.monotonic() - started < 3
    assert result.ok is False and "5 refused saves" in result.reason
    assert result.last_save == {"saved": False, "errors": ["intro: problem 4"]}


def test_run_codex_failure_exit(tmp_path):
    binary = fake_bin(tmp_path, "echo boom >&2; exit 3")
    result = run_codex([str(binary)], tmp_path / "run.jsonl")
    assert result.ok is False and "status 3" in result.reason
    assert "boom" in (tmp_path / "run.stderr.log").read_text()


def test_run_codex_runs_in_an_empty_directory(tmp_path):  # AC-6.8: no project files
    binary = fake_bin(tmp_path, 'ls -A | wc -l | tr -d " " >&2')
    run_codex([str(binary)], tmp_path / "run.jsonl")
    assert (tmp_path / "run.stderr.log").read_text().strip() == "0"


def test_run_codex_missing_binary(tmp_path):
    with pytest.raises(AgentUnavailable, match="could not start"):
        run_codex([str(tmp_path / "nope")], tmp_path / "run.jsonl")


@pytest.mark.skipif(agents.os.name != "nt", reason="Windows directory locking")
def test_completed_run_survives_locked_working_directory(tmp_path):
    import subprocess
    import sys

    holders = []
    directories = []

    def hold_directory(event):
        if event.kind != "say":
            return
        directory = Path(event.text)
        directories.append(directory)
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
            cwd=directory,
            stdout=subprocess.PIPE,
            text=True,
        )
        holders.append(holder)
        assert holder.stdout.readline().strip() == "ready"

    code = (
        "import json, os; "
        "print(json.dumps({'type': 'item.completed', 'item': "
        "{'type': 'agent_message', 'text': os.getcwd()}}), flush=True); "
        "print(json.dumps({'type': 'turn.completed'}), flush=True)"
    )
    try:
        result = run_codex(
            [sys.executable, "-c", code], tmp_path / "run.jsonl", on_event=hold_directory
        )
        assert result.ok and result.events[-1].kind == "done"
        assert directories[0].is_dir()  # locked during best-effort cleanup
    finally:
        for holder in holders:
            holder.terminate()
            holder.wait(timeout=10)
            holder.stdout.close()
        for directory in directories:
            directory.rmdir()  # only the empty temporary directory created by this run
