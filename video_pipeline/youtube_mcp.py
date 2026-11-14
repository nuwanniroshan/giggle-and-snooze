"""MCP server for the YouTube text (spec 024, FR-2/FR-3): one save tool.

Run: uv run python -m video_pipeline.youtube_mcp --book-json PATH --record PATH --out PATH
[--root PATH]
The book, the render record (for the chapters) and the output path come from the command line,
never from the model.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.contract import load_disallowed_terms
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.youtube import (
    Chapter,
    YouTubeSettings,
    chapters_from_record,
    language_for,
    load_settings,
    text_problems,
)

SERVER_NAME = "kiddo"
MAX_SAVES = 3


@dataclass
class YouTubeTools:
    """The one save tool. Book, chapters and output path are fixed by the server's command
    line; the model only sends title, description body and tags."""

    book: dict
    chapters: list[Chapter]
    duration: float
    settings: YouTubeSettings
    terms: list[str]
    out: Path
    saves: int = 0

    def save_youtube_text(self, title: str, description: str, tags: list[str]) -> dict:
        self.saves += 1
        if self.saves > MAX_SAVES:
            return {"saved": False, "errors": [f"save limit reached ({MAX_SAVES} saves); stop"]}
        text = {
            "title": title,
            "description": description,
            "tags": tags,
            "language": language_for(self.book, self.settings),
            "category_id": None,
            "written_by": "codex",
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        errors = text_problems(
            text, self.book, self.chapters, self.duration, self.settings, self.terms
        )
        if errors:
            return {"saved": False, "errors": errors}
        write_atomic(self.out, text)
        return {"saved": True, "errors": []}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.youtube_mcp")
    parser.add_argument("--book-json", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def build_tools(args: argparse.Namespace) -> YouTubeTools:
    config = Path(args.root) / "config"
    settings = load_settings(config)
    book = json.loads(Path(args.book_json).read_text(encoding="utf-8"))
    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    return YouTubeTools(
        book=book,
        chapters=chapters_from_record(record, book, settings),
        duration=float(record.get("duration", 0.0)),
        settings=settings,
        terms=load_disallowed_terms(config / "disallowed_terms.txt"),
        out=args.out,
    )


def create_server(tools: YouTubeTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def save_youtube_text(title: str, description: str, tags: list[str]) -> dict:
        """Save the YouTube title, description body (no chapters, no links: the pipeline adds
        them) and tags for this book. Returns {saved, errors}; fix every error and save again.
        At most 3 saves."""
        return tools.save_youtube_text(title, description, tags)

    return server


def main(argv: list[str] | None = None) -> int:
    create_server(build_tools(parse_args(argv))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
