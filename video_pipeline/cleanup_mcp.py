"""MCP server for artifact cleanup (spec 026, FR-2): one save tool.

Run: uv run python -m video_pipeline.cleanup_mcp --size WxH --out PATH [--root PATH]
The picture size and output path come from the command line, never from the model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.cleanup import RegionTools, load_settings
from video_pipeline.library import PROJECT_ROOT

SERVER_NAME = "kiddo"


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.cleanup_mcp")
    parser.add_argument("--size", type=parse_size, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def build_tools(args: argparse.Namespace) -> RegionTools:
    settings = load_settings(Path(args.root) / "config")
    return RegionTools(tuple(args.size), settings, args.out)


def create_server(tools: RegionTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def save_artifact_regions(data: dict) -> dict:
        """Save the AI-artifact regions of the attached picture as {"regions": [{"box":
        [x0, y0, x1, y1], "include_points": [[x, y], ...], "reason": "<short>"}, ...]} in the
        picture's own pixels (its size is in the prompt). An empty list means nothing to
        clean. Returns {saved, errors}; fix every error and save again. At most 3 saves."""
        return tools.save_artifact_regions(data)

    return server


def main(argv: list[str] | None = None) -> int:
    create_server(build_tools(parse_args(argv))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
