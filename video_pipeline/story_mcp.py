"""MCP server for the story writer (spec 006, FR-8): one submit tool, chosen by `--mode`.

Run: uv run python -m video_pipeline.story_mcp --mode ideas|candidate|book --out PATH
       [--pages N] [--root PATH]
The mode, output path and page maximum come from the command line, never from the model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.contract import load_disallowed_terms
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.story_tools import StoryTools, load_gentle_terms

SERVER_NAME = "kiddo"
MODES = ("ideas", "candidate", "book")
TOOL_FOR_MODE = {"ideas": "submit_ideas", "candidate": "submit_candidate", "book": "submit_book"}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.story_mcp")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def create_server(tools: StoryTools, mode: str) -> MCPServer:
    server = MCPServer(SERVER_NAME)
    if mode == "ideas":

        @server.tool()
        def submit_ideas(ideas: dict) -> dict:
            """Submit {"ideas": [...]} with 3 to 5 ideas. Each idea has title, premise (1-2
            sentences), age_range (Ages 3-6, Ages 4-7 or Ages 5-8) and lesson (one sentence).
            Returns {saved, errors}; fix every error and submit again."""
            return tools.submit_ideas(ideas)

    elif mode == "candidate":

        @server.tool()
        def submit_candidate(candidate: dict) -> dict:
            """Submit one improved idea: title, premise (1-2 sentences), age_range (Ages 3-6,
            Ages 4-7 or Ages 5-8) and lesson (one sentence). Returns {saved, errors}."""
            return tools.submit_candidate(candidate)

    else:

        @server.tool()
        def submit_book(book: dict) -> dict:
            """Submit the whole book: {"metadata": {title, subtitle, age_range, blurb, dedication},
            "pages": [{page_number, title, text, layout, prompt, illustration_file}]}.
            Returns {saved, errors}; fix every error and submit again."""
            return tools.submit_book(book)

    return server


def build_tools(args: argparse.Namespace) -> StoryTools:
    config = Path(args.root) / "config"
    return StoryTools(
        terms=load_disallowed_terms(config / "disallowed_terms.txt"),
        gentle=load_gentle_terms(config / "gentle_terms.txt"),
        out=args.out,
        pages=args.pages,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    create_server(build_tools(args), args.mode).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
