"""Spec 024 Phase 4: the YouTube text save tool (FR-2, FR-3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.helpers import make_root
from video_pipeline import youtube_mcp

BOOK_TITLE = "The Lantern Bug Parade"


def write_record(folder, chapters: bool = True) -> Path:
    path = folder.build_dir / "video" / f"{folder.slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = [("intro", 0.0)] + [(f"page-{n}", 20.0 + 30.0 * (n - 1)) for n in range(1, 9)]
    sections.append(("outro", 260.0))
    record: dict = {"final_key": "abc", "duration": 300.0}
    if chapters:
        record["chapters"] = [{"name": n, "start": s} for n, s in sections]
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def good_text() -> dict:
    return {
        "title": f"{BOOK_TITLE} | A Cosy Bedtime Read-Aloud",
        "description": (
            "Tilly the firefly has a lantern that flickers instead of glowing.\n\n"
            "Join her on a gentle evening journey where a little light that dances turns out "
            "to be exactly what a lost ladybug needs."
        ),
        "tags": ["bedtime story", "read aloud", "fireflies", "kids story"],
    }


@pytest.fixture
def setup(tmp_path):
    folder = make_root(tmp_path)
    record = write_record(folder)
    out = tmp_path / "out" / "text.json"
    args = youtube_mcp.parse_args(
        [
            "--book-json",
            str(folder.book_json),
            "--record",
            str(record),
            "--out",
            str(out),
            "--root",
            str(folder.root),
        ]
    )
    return youtube_mcp.build_tools(args), out


def test_server_exposes_only_save_youtube_text(setup):
    tools, _ = setup
    server = youtube_mcp.create_server(tools)
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert names == ["save_youtube_text"]


def test_save_refuses_with_all_problems(setup):
    tools, out = setup
    result = tools.save_youtube_text(
        title="A story", description="Visit www.example.com and subscribe!", tags=[]
    )
    assert result["saved"] is False
    errors = result["errors"]
    assert any(e.startswith("title:") and BOOK_TITLE in e for e in errors)
    assert any("link" in e for e in errors)
    assert any("subscribe" in e for e in errors)
    assert any(e.startswith("tags:") for e in errors)
    assert not out.exists()


def test_save_limit_3(setup):
    tools, out = setup
    for _ in range(3):
        assert tools.save_youtube_text(title="x", description="y", tags=[])["saved"] is False
    text = good_text()
    result = tools.save_youtube_text(text["title"], text["description"], text["tags"])
    assert result["saved"] is False
    assert "save limit" in result["errors"][0]
    assert not out.exists()


def test_saved_text_has_language_codex_mark_and_no_category(setup):
    tools, out = setup
    text = good_text()
    result = tools.save_youtube_text(text["title"], text["description"], text["tags"])
    assert result == {"saved": True, "errors": []}
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["title"] == text["title"]
    assert saved["description"] == text["description"]
    assert saved["tags"] == text["tags"]
    assert saved["language"] == "en"
    assert saved["category_id"] is None
    assert saved["written_by"] == "codex"
    assert saved["written_at"]
