"""MCP server for the Q&A camera subject (spec 025, FR-2): one save tool.

Run: uv run python -m video_pipeline.camera_mcp --layers a,b --size WxH --out PATH
The layer names, picture size and output path come from the command line, never from
the model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.camera_ground import SubjectTools

SERVER_NAME = "kiddo"


def parse_size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    return int(width), int(height)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.camera_mcp")
    parser.add_argument("--layers", default="")
    parser.add_argument("--size", type=parse_size, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def build_tools(args: argparse.Namespace) -> SubjectTools:
    names = [n.strip() for n in args.layers.split(",") if n.strip()]
    return SubjectTools(names, args.size, args.out)


def create_server(tools: SubjectTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def save_camera_subject(subject: dict) -> dict:
        """Save the thing the kids' question is about. Use {"layer": name} when it is one
        of the listed layers, otherwise {"phrase": "<2-6 words>", "box": [x0, y0, x1, y1]}
        with the box in the picture's own pixels (its size is in the prompt). Include
        narration: [{line: zero-based script line index, phrase: subject, box: pixel box}]
        for narration focus changes; on pages without Q&A save only narration. Returns
        {saved, errors}; fix every error and save again. At most 3 saves."""
        return tools.save_camera_subject(subject)

    return server


def main(argv: list[str] | None = None) -> int:
    create_server(build_tools(parse_args(argv))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
