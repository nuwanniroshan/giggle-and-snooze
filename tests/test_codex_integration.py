"""Real Codex (ChatGPT) run on the pilot book. Run with: uv run pytest -m codex -v -s"""

import time

import pytest

from tests.helpers import PILOT_BOOK, load_json, make_root, plan_signature
from video_pipeline.agents import parse_codex_event
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode_tools import CONFIG_DIR
from video_pipeline.generate_script import main
from video_pipeline.mcp_server import TOOL_NAMES

pytestmark = pytest.mark.codex


def test_pilot_book_end_to_end_codex(tmp_path):
    folder = make_root(tmp_path, PILOT_BOOK)
    args = ["--book", folder.slug, "--root", str(tmp_path)]
    started = time.monotonic()
    assert main(args) == 0
    elapsed = time.monotonic() - started
    out = folder.script_path
    terms = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
    first = load_json(out)
    assert validate_script(first, load_json(PILOT_BOOK), terms) == []
    assert elapsed < 600, f"took {elapsed:.0f} s"  # NFR Performance

    [log] = (folder.build_dir / "agent").glob("*-codex.jsonl")  # AC-6.8
    text = log.read_text(encoding="utf-8")
    events = [e for line in text.splitlines() for e in parse_codex_event(line)]
    assert {e.tool for e in events if e.kind == "tool"} <= set(TOOL_NAMES)
    assert '"command_execution"' not in text and '"file_change"' not in text

    assert main([*args, "--force"]) == 0  # AC-6.3 with the real provider
    assert plan_signature(load_json(out)) == plan_signature(first)
