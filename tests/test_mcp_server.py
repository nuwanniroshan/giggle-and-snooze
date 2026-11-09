"""Runs the real MCP server over stdio (local, no network) with hand-written JSON-RPC."""

import json
import subprocess
import sys

from tests.helpers import MINI_BOOK, ROOT, load_json, make_banter, make_root
from video_pipeline import mcp_server
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode import plan_episode
from video_pipeline.episode_tools import CONFIG_DIR


def call_server(args: list[str], requests: list[tuple[str, dict]]) -> list[dict]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "video_pipeline.mcp_server", *args],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def receive(request_id: int) -> dict:
        while True:
            line = proc.stdout.readline()
            if not line:
                raise AssertionError(f"server closed: {proc.stderr.read()}")
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        receive(0)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        replies = []
        for request_id, (method, params) in enumerate(requests, start=1):
            send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            replies.append(receive(request_id))
        return replies
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)


def tool_payload(reply: dict) -> object:
    result = reply["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    return json.loads(result["content"][0]["text"])


def server_args(tmp_path, *extra: str) -> list[str]:
    folder = make_root(tmp_path)
    return ["--book", folder.slug, "--root", str(tmp_path), *extra]


def test_lists_exactly_the_episode_tools(tmp_path):
    [reply] = call_server(server_args(tmp_path), [("tools/list", {})])
    assert {tool["name"] for tool in reply["result"]["tools"]} == set(mcp_server.TOOL_NAMES)


def test_plan_tool_uses_the_server_seed(tmp_path):
    [reply] = call_server(
        server_args(tmp_path, "--seed", "11"),
        [("tools/call", {"name": "get_episode_plan", "arguments": {}})],
    )
    book = load_json(MINI_BOOK)
    expected = sorted(plan_episode(book, 11).qa_speakers)
    assert [p["page_number"] for p in tool_payload(reply)["qa_pages"]] == expected


def test_save_over_mcp_refuses_invalid_then_saves_valid(tmp_path):  # AC-6.7
    book = load_json(MINI_BOOK)
    banter = make_banter(book, plan_episode(book, 7)).model_dump()
    refused, saved = call_server(
        server_args(tmp_path),
        [
            ("tools/call", {"name": "save_episode_script", "arguments": {"banter": {"intro": []}}}),
            ("tools/call", {"name": "save_episode_script", "arguments": {"banter": banter}}),
        ],
    )
    assert tool_payload(refused)["saved"] is False and tool_payload(refused)["errors"]
    assert tool_payload(saved)["saved"] is True
    out = tmp_path / "books" / "the-lantern-bug-parade" / "episode" / "episode_script.json"
    terms = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
    assert validate_script(load_json(out), book, terms) == []


def test_bad_book_exits_before_serving(tmp_path, capsys):
    assert mcp_server.main(["--book", "nope", "--root", str(tmp_path)]) == 1
    assert "nope" in capsys.readouterr().err
