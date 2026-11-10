"""MCP server for the motion plan (spec 009, FR-4/FR-5): one save tool.

Run: uv run python -m video_pipeline.plan_mcp --picture PATH --page N --out PATH [--root PATH]
The picture identity, page number and output path come from the command line, never from
the model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.library import PROJECT_ROOT
from video_pipeline.motion_plan import PlanTools, load_limits, picture_identity

SERVER_NAME = "kiddo"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.plan_mcp")
    parser.add_argument("--picture", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def build_tools(args: argparse.Namespace) -> PlanTools:
    return PlanTools(
        limits=load_limits(Path(args.root) / "config"),
        picture=picture_identity(args.picture),
        page_number=args.page,
        out=args.out,
    )


def create_server(tools: PlanTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def save_motion_plan(plan: dict) -> dict:
        """Save the motion plan {"layers": [...], "effects": [...]} for the attached picture.
        Coordinates are in the picture's own pixels (its size is in the prompt). Returns
        {saved, errors}; fix every error and save again. At most 3 saves."""
        return tools.save_motion_plan(plan)

    return server


def main(argv: list[str] | None = None) -> int:
    create_server(build_tools(parse_args(argv))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
