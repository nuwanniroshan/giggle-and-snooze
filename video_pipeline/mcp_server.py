"""MCP server exposing the episode tools (FR-8) over stdio.

Run: uv run python -m video_pipeline.mcp_server --book book_data.json [--seed N] [--force]
The book, seed and force flag come from this command line, never from the model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.contract import ConfigError
from video_pipeline.episode import BookError
from video_pipeline.episode_tools import CONFIG_DIR, DEFAULT_SEED, ROOT, EpisodeTools

SERVER_NAME = "kiddo"
TOOL_NAMES = ("get_episode_plan", "validate_banter", "save_episode_script")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.mcp_server")
    parser.add_argument("--book", type=Path, default=ROOT / "book_data.json")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--episodes-dir", type=Path, default=ROOT / "episodes")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    return parser.parse_args(argv)


def create_server(tools: EpisodeTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def get_episode_plan() -> dict:
        """Call this first. Returns the book pages, cast, the Q&A pages with the kids who must
        speak in each, the writing brief with every rule, and the banter JSON schema."""
        return tools.get_episode_plan()

    @server.tool()
    def validate_banter(banter: dict) -> dict:
        """Check a banter object {intro, moments, outro} against every rule.
        Returns {valid, errors}. Fix every error and validate again."""
        return tools.validate_banter(banter)

    @server.tool()
    def save_episode_script(banter: dict) -> dict:
        """Validate the banter and save the episode script. Returns {saved, path, errors}.
        Nothing is written when errors are returned. At most 5 saves per run."""
        return tools.save_episode_script(banter)

    return server


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        tools = EpisodeTools.load(
            args.book, args.seed, args.episodes_dir, args.config_dir, args.force
        )
    except (BookError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    create_server(tools).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
