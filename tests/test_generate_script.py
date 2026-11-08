import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter, plan_signature
from video_pipeline import generate_script, mcp_server
from video_pipeline.agents import AgentEvent, AgentResult, AgentUnavailable
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode import plan_episode
from video_pipeline.episode_tools import CONFIG_DIR, EpisodeTools
from video_pipeline.llm import LLMUnavailable

TERMS = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
FAKE_CODEX = Path("/fake/codex")


def server_args_from(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    setting = next(c for c in command if c.startswith(prefix))
    return json.loads(setting[len(prefix) :])[2:]  # drop "-m video_pipeline.mcp_server"


class FakeAgent:
    """Plays the agent: starts the real EpisodeTools from the server args in the real command."""

    def __init__(self, banter=None, saves=1, reason=""):
        self.banter, self.saves, self.reason = banter, saves, reason
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=600, on_event=None):
        self.commands.append(command)
        args = mcp_server.parse_args(server_args_from(command))
        tools = EpisodeTools.load(
            args.book, args.seed, args.episodes_dir, args.config_dir, args.force
        )
        banter = self.banter(tools) if callable(self.banter) else self.banter
        if banter is None:
            banter = make_banter(tools.book, tools.plan).model_dump()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        events = [
            AgentEvent(
                "tool-result", tool="save_episode_script", data=tools.save_episode_script(banter)
            )
            for _ in range(self.saves)
        ]
        return AgentResult(ok=not self.reason, events=events, log_path=log_path, reason=self.reason)


def never_called(*args, **kwargs):
    raise AssertionError("this provider must not be used")


def run(monkeypatch, tmp_path, book_path=MINI_BOOK, extra=(), agent=None, cloud=None, status=None):
    monkeypatch.setattr(generate_script, "codex_status", status or (lambda: FAKE_CODEX))
    monkeypatch.setattr(generate_script, "run_codex", agent or never_called)
    monkeypatch.setattr(generate_script, "generate_cloud", cloud or never_called)
    args = ["--book", str(book_path), "--episodes-dir", str(tmp_path / "episodes"), *extra]
    return generate_script.main(args)


def out_path(tmp_path, slug="the-lantern-bug-parade"):
    return tmp_path / "episodes" / slug / "episode_script.json"


def test_default_provider_writes_valid_script(monkeypatch, tmp_path):  # AC-1.1, AC-1.2
    agent = FakeAgent()
    assert run(monkeypatch, tmp_path, agent=agent) == 0
    script = load_json(out_path(tmp_path))
    assert validate_script(script, load_json(MINI_BOOK), TERMS) == []
    command = agent.commands[0]
    assert command[0] == str(FAKE_CODEX) and "read-only" in command  # AC-6.8


def test_agent_log_goes_under_the_episode(monkeypatch, tmp_path):  # AC-6.8
    assert run(monkeypatch, tmp_path, agent=FakeAgent()) == 0
    logs = list((out_path(tmp_path).parent / "agent").glob("*-codex.jsonl"))
    assert len(logs) == 1


def test_pilot_book_needs_no_changes(monkeypatch, tmp_path):  # AC-1.3
    assert run(monkeypatch, tmp_path, PILOT_BOOK, agent=FakeAgent()) == 0
    script = load_json(out_path(tmp_path, "the-snail-mail-express"))
    assert validate_script(script, load_json(PILOT_BOOK), TERMS) == []
    assert sum(p["has_qa"] for p in script["pages"]) in (7, 8)


def test_existing_script_refused_without_force(monkeypatch, tmp_path, capsys):  # AC-6.1
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"hand edited")
    assert run(monkeypatch, tmp_path, status=never_called) == 1
    assert target.read_bytes() == b"hand edited"
    err = capsys.readouterr().err
    assert str(target) in err and "--force" in err


def test_force_overwrites(monkeypatch, tmp_path):  # AC-6.2
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert run(monkeypatch, tmp_path, extra=["--force"], agent=FakeAgent()) == 0
    assert validate_script(load_json(target), load_json(MINI_BOOK), TERMS) == []


def test_same_seed_gives_the_same_plan(monkeypatch, tmp_path):  # AC-6.3
    def other_text(tools):
        banter = make_banter(tools.book, tools.plan).model_dump()
        for moment in banter["moments"]:
            moment["lines"][-1]["text"] = "What a lovely page!"
        return banter

    extra = ["--seed", "11", "--force"]
    assert run(monkeypatch, tmp_path, extra=extra, agent=FakeAgent()) == 0
    first = load_json(out_path(tmp_path))
    assert run(monkeypatch, tmp_path, extra=extra, agent=FakeAgent(other_text)) == 0
    second = load_json(out_path(tmp_path))
    assert first != second
    assert plan_signature(first) == plan_signature(second)


def test_other_seed_still_valid(monkeypatch, tmp_path):  # AC-6.4
    assert run(monkeypatch, tmp_path, extra=["--seed", "3"], agent=FakeAgent()) == 0
    assert validate_script(load_json(out_path(tmp_path)), load_json(MINI_BOOK), TERMS) == []


def test_gemini_api_one_request_per_attempt(monkeypatch, tmp_path):  # AC-6.5
    book = load_json(MINI_BOOK)
    valid = make_banter(book, plan_episode(book, 7)).model_dump_json()
    calls = []

    def cloud(prompt, seed, model=None):
        calls.append((prompt, seed))
        return "{}" if len(calls) == 1 else valid

    extra = ["--provider", "gemini-api"]
    assert run(monkeypatch, tmp_path, extra=extra, cloud=cloud, status=never_called) == 0
    assert len(calls) == 2 and "REJECTED" in calls[1][0]
    assert [seed for _, seed in calls] == [7, 8]
    assert validate_script(load_json(out_path(tmp_path)), book, TERMS) == []


def test_gemini_api_gives_up_after_three_attempts(monkeypatch, tmp_path, capsys):
    calls = []

    def cloud(prompt, seed, model=None):
        calls.append(seed)
        return "{}"

    extra = ["--provider", "gemini-api"]
    assert run(monkeypatch, tmp_path, extra=extra, cloud=cloud, status=never_called) == 1
    assert len(calls) == 3 and not out_path(tmp_path).exists()
    assert "intro" in capsys.readouterr().err


def test_gemini_api_missing_key(monkeypatch, tmp_path, capsys):
    def cloud(prompt, seed, model=None):
        raise LLMUnavailable(
            "the gemini-api provider needs the GEMINI_API_KEY environment variable"
        )

    extra = ["--provider", "gemini-api"]
    assert run(monkeypatch, tmp_path, extra=extra, cloud=cloud, status=never_called) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_codex_unavailable_has_no_fallback(monkeypatch, tmp_path, capsys):  # AC-6.6
    def down():
        raise AgentUnavailable("Codex (ChatGPT) provider is not logged in: run `codex login`")

    assert run(monkeypatch, tmp_path, status=down) == 1
    assert "codex login" in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_refused_saves_fail_the_run(monkeypatch, tmp_path, capsys):  # AC-6.7
    agent = FakeAgent(banter={"intro": []}, saves=5)
    assert run(monkeypatch, tmp_path, agent=agent) == 1
    err = capsys.readouterr().err
    assert "did not save a valid script" in err and "intro" in err
    assert not out_path(tmp_path).exists()


def test_agent_time_limit_fails_the_run(monkeypatch, tmp_path, capsys):  # FR-7
    agent = FakeAgent(banter={}, reason="agent stopped after 600 s (time limit)")
    assert run(monkeypatch, tmp_path, agent=agent) == 1
    assert "time limit" in capsys.readouterr().err


def test_mode_flag_is_gone(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, extra=["--mode", "local"])


def test_missing_terms_file_stops_before_any_request(monkeypatch, tmp_path, capsys):  # AC-7.2
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    assert run(monkeypatch, tmp_path, extra=["--config-dir", str(config)], status=never_called) == 1
    assert str(config / "disallowed_terms.txt") in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_empty_terms_file_warns(monkeypatch, tmp_path, capsys):  # AC-7.3
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    (config / "disallowed_terms.txt").write_text("# nothing yet\n", encoding="utf-8")
    assert run(monkeypatch, tmp_path, extra=["--config-dir", str(config)], agent=FakeAgent()) == 0
    assert "disallowed-terms list is empty" in capsys.readouterr().out


def test_success_prints_review_notice(monkeypatch, tmp_path, capsys):  # AC-7.4
    assert run(monkeypatch, tmp_path, agent=FakeAgent()) == 0
    out = capsys.readouterr().out
    assert "reviewed by a person" in out and "before voice synthesis" in out


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b["pages"].pop(), "supported range is 8-32"),
        (lambda b: b["pages"][2].pop("prompt"), "page 3: 'prompt' is missing or empty"),
        (lambda b: b["pages"][4].update(page_number=9), "page 5: page_number must be 5"),
    ],
)
def test_invalid_book_writes_nothing(monkeypatch, tmp_path, capsys, mutate, message):
    book = load_json(MINI_BOOK)
    mutate(book)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(book), encoding="utf-8")
    assert run(monkeypatch, tmp_path, bad, status=never_called) == 1
    assert message in capsys.readouterr().err


def test_missing_book_file(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, tmp_path / "nope.json", status=never_called) == 1
    assert "book data file not found" in capsys.readouterr().err
