"""Agent provider (FR-7): run a tool-using model headless with only the episode tools.

Ported from video-studio's packages/ai (status, binary discovery, headless run, event parsing)
for the Codex CLI that ships with the ChatGPT app and uses the producer's ChatGPT login.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.episode_tools import MAX_SAVES
from video_pipeline.mcp_server import SERVER_NAME

CODEX_BIN_ENV = "KIDDO_CODEX_BIN"
CODEX_SEARCH = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path.home() / ".local/bin/codex",
    Path("/opt/homebrew/bin/codex"),
    Path("/usr/local/bin/codex"),
    Path.home() / ".npm-global/bin/codex",
)
AGENT_TIMEOUT_S = 600
DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "browser_use",
    "computer_use",
    "in_app_browser",
    "multi_agent",
    "view_image",
    "sleep_tool",
)
INSTALL_HINT = (
    "install the ChatGPT desktop app (it includes Codex) or set "
    f"{CODEX_BIN_ENV} to the codex binary"
)
LOGIN_HINT = "run `codex login` and sign in with ChatGPT"

AGENT_PROMPT = """You write the spoken banter for a children's read-aloud storytime episode.
Use only the kiddo tools.
1. Call get_episode_plan. Its "brief" holds the book, the cast, the Q&A pages with the kids who \
must speak in each, and every rule; "banter_schema" is the JSON shape of a banter.
2. Write the complete banter: intro, exactly one moment per Q&A page, outro. Every "grounding" \
is 2-6 consecutive words copied exactly from the TEXT or ILLUSTRATION of that same page.
3. Call validate_banter with it. Fix every error it lists and validate again until it returns \
no errors.
4. Call save_episode_script with the valid banter. If it returns errors, fix them and save \
again. You have at most 5 saves.
5. Finish with one short line: the saved path, or the errors you could not fix."""


class AgentUnavailable(Exception):
    """The agent provider is not installed, not logged in, or could not start (AC-6.6)."""


@dataclass(frozen=True)
class AgentEvent:
    kind: str  # "say" | "tool" | "tool-result" | "error" | "done"
    text: str = ""
    tool: str = ""
    ok: bool = True
    data: object = None


@dataclass
class AgentResult:
    ok: bool
    events: list[AgentEvent]
    log_path: Path
    reason: str = ""

    @property
    def save_results(self) -> list[dict]:
        return [event.data for event in self.events if _is_save_result(event)]

    @property
    def last_save(self) -> dict | None:
        saves = self.save_results
        return saves[-1] if saves else None


def _is_save_result(event: AgentEvent) -> bool:
    return (
        event.kind == "tool-result"
        and event.tool == "save_episode_script"
        and isinstance(event.data, dict)
    )


def find_codex() -> Path | None:
    if os.environ.get(CODEX_BIN_ENV):
        return Path(os.environ[CODEX_BIN_ENV])
    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path)
    return next((p for p in CODEX_SEARCH if os.access(p, os.X_OK)), None)


def codex_status() -> Path:
    """The codex binary when installed and logged in; AgentUnavailable otherwise."""
    binary = find_codex()
    if binary is None:
        raise AgentUnavailable(f"Codex (ChatGPT) provider not found: {INSTALL_HINT}")
    try:
        result = subprocess.run(
            [str(binary), "login", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentUnavailable(
            f"Codex (ChatGPT) provider could not start ({exc}): {INSTALL_HINT}"
        ) from None
    status = (result.stdout + result.stderr).strip().lower()
    if result.returncode != 0 or not status.startswith("logged in"):
        raise AgentUnavailable(f"Codex (ChatGPT) provider is not logged in: {LOGIN_HINT}")
    return binary


def build_codex_command(
    binary: Path,
    server_command: str,
    server_args: list[str],
    server_env: dict[str, str],
    model: str | None = None,
    prompt: str = AGENT_PROMPT,
    images: Sequence[Path] = (),
) -> list[str]:
    """The headless `codex exec` argv; `images` are attached to the prompt with `-i`."""
    key = f"mcp_servers.{SERVER_NAME}"
    command = [
        str(binary),
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
    ]
    for feature in DISABLED_FEATURES:
        command += ["--disable", feature]
    settings = [
        f"{key}.command={json.dumps(server_command)}",
        f"{key}.args={json.dumps(server_args)}",
        *(f"{key}.env.{name}={json.dumps(value)}" for name, value in server_env.items()),
        f"{key}.startup_timeout_sec=60",
        f'{key}.default_tools_approval_mode="approve"',
        'web_search="disabled"',
    ]
    for setting in settings:
        command += ["-c", setting]
    if model:
        command += ["--model", model]
    for image in images:
        command += ["-i", str(image)]
    # "--" ends -i's file list, so the prompt is never read as an image or a flag.
    command += ["--", prompt]
    return command


def _tool_payload(result: dict) -> object:
    structured = result.get("structured_content")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    text = "\n".join(
        c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_codex_event(line: str) -> list[AgentEvent]:
    """One `codex exec --json` line → agent events (pure; unit-tested)."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(event, dict):
        return []
    kind, item = event.get("type"), event.get("item") or {}
    item_type = item.get("type")
    if kind == "item.completed" and item_type == "agent_message":
        return [AgentEvent("say", text=str(item.get("text", "")).strip())]
    if kind == "item.started" and item_type == "mcp_tool_call":
        return [AgentEvent("tool", tool=item.get("tool", ""), data=item.get("arguments"))]
    if kind == "item.completed" and item_type == "mcp_tool_call":
        error = item.get("error")
        payload = None if error else _tool_payload(item.get("result") or {})
        text = error.get("message", "") if error else json.dumps(payload, ensure_ascii=False)
        return [
            AgentEvent(
                "tool-result", text=text, tool=item.get("tool", ""), ok=not error, data=payload
            )
        ]
    if kind == "item.completed" and item_type == "error":
        return [AgentEvent("error", text=str(item.get("message", "")))]
    if kind in ("turn.failed", "error"):
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else event.get("message")
        return [AgentEvent("error", text=str(message or kind))]
    if kind == "turn.completed":
        return [AgentEvent("done")]
    return []


def run_codex(
    command: list[str],
    log_path: Path,
    timeout_s: float = AGENT_TIMEOUT_S,
    on_event: Callable[[AgentEvent], None] | None = None,
    max_refused_saves: int = MAX_SAVES,
) -> AgentResult:
    """Run the agent in an empty temporary directory; keep its JSONL log and stderr.

    The agent is stopped at the time limit or once `max_refused_saves` saves were refused (FR-7).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = log_path.with_suffix(".stderr.log")
    events: list[AgentEvent] = []
    refused = 0
    timed_out = threading.Event()
    with (
        tempfile.TemporaryDirectory(prefix="kiddo-agent-") as workdir,
        log_path.open("w", encoding="utf-8") as log,
        stderr_path.open("w", encoding="utf-8") as stderr,
    ):
        try:
            proc = subprocess.Popen(
                command,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
        except OSError as exc:
            raise AgentUnavailable(f"Codex (ChatGPT) provider could not start: {exc}") from None

        def stop() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout_s, stop)
        timer.start()
        try:
            for line in proc.stdout:
                log.write(line)
                for event in parse_codex_event(line):
                    events.append(event)
                    if on_event:
                        on_event(event)
                    if _is_save_result(event) and not event.data.get("saved"):
                        refused += 1
                if refused >= max_refused_saves:
                    proc.kill()
                    break
            proc.wait()
        finally:
            timer.cancel()
    if refused >= max_refused_saves:
        reason = f"agent stopped after {refused} refused saves"
    elif timed_out.is_set():
        reason = f"agent stopped after {timeout_s:.0f} s (time limit)"
    elif proc.returncode != 0:
        reason = f"agent exited with status {proc.returncode}; see {stderr_path}"
    elif not any(event.kind == "done" for event in events):
        reason = "agent ended without finishing its turn"
    else:
        reason = ""
    return AgentResult(ok=not reason, events=events, log_path=log_path, reason=reason)
