"""MCP server for the story writer (spec 006, FR-8): one submit tool, chosen by `--mode`.

Run: uv run python -m video_pipeline.story_mcp --mode ideas|candidate|book|blueprint --out PATH
       [--pages N] [--root PATH] [--subject draft:ID|book:SLUG]
The mode, output path and page maximum come from the command line, never from the model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from video_pipeline.blueprint import load_config, load_context, others_for
from video_pipeline.contract import load_disallowed_terms
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.story_tools import StoryTools, load_gentle_terms

SERVER_NAME = "kiddo"
MODES = ("ideas", "candidate", "book", "blueprint")
TOOL_FOR_MODE = {
    "ideas": "submit_ideas",
    "candidate": "submit_candidate",
    "book": "submit_book",
    "blueprint": "submit_blueprint",
}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.story_mcp")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--subject", help="blueprint mode: draft:<id> or book:<slug> being designed"
    )
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

    elif mode == "blueprint":

        @server.tool()
        def submit_blueprint(blueprint: dict) -> dict:
            """Submit the creative direction: {"version": 1, "mood": {key, label, summary, roles},
            "art": {medium_key, medium_label, palette (5 of {name, hex}), line, lighting,
            composition, style_prompt_prefix, avoid}, "qa": {format_key, format_label,
            question_types, intro_hook, outro_hook, moments_min, moments_max, kid_notes},
            "sound": {ambience, sfx, music_direction}, "animation": {intensity, effects},
            "rationale"}. Returns {saved, errors}; fix every error and submit again."""
            return tools.submit_blueprint(blueprint)

    else:

        @server.tool()
        def submit_book(book: dict) -> dict:
            """Submit the whole book: {"metadata": {title, subtitle, age_range, blurb, dedication},
            "characters": [{name, look}], "pages": [{page_number, title, text, layout, prompt,
            illustration_file, characters_on_page}]}.
            Returns {saved, errors}; fix every error and submit again."""
            return tools.submit_book(book)

    return server


def build_tools(args: argparse.Namespace) -> StoryTools:
    config = Path(args.root) / "config"
    tools = StoryTools(
        terms=load_disallowed_terms(config / "disallowed_terms.txt"),
        gentle=load_gentle_terms(config / "gentle_terms.txt"),
        out=args.out,
        pages=args.pages,
    )
    if args.mode == "blueprint":
        ctx = load_context(args.root)
        exclude = tuple(args.subject.split(":", 1)) if args.subject else None
        tools.blueprint_ctx = ctx
        tools.blueprint_others = others_for(Path(args.root), ctx, exclude)
        tools.blueprint_cfg = load_config(config)
    return tools


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    create_server(build_tools(args), args.mode).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
