"""CLI: generate episodes/<slug>/episode_script.json from a book data file (spec 001 v2.0.0).

Default provider: the Codex agent on the producer's ChatGPT login, using only the episode tools.
Opt-in: --provider gemini-api (GEMINI_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from video_pipeline.agents import (
    AgentEvent,
    AgentUnavailable,
    build_codex_command,
    codex_status,
    run_codex,
)
from video_pipeline.contract import ConfigError, validate_script
from video_pipeline.episode import BookError
from video_pipeline.episode_tools import (
    CONFIG_DIR,
    DEFAULT_SEED,
    ROOT,
    SAVE_LIMIT_ERROR,
    EpisodeTools,
    write_atomic,
)
from video_pipeline.llm import (
    CLOUD_MODEL,
    LLMError,
    LLMUnavailable,
    build_prompt,
    generate_cloud,
)

PROVIDERS = ("codex", "gemini-api")
MAX_ATTEMPTS = 3
REVIEW_NOTICE = (
    "Review required: this script must be reviewed by a person before voice synthesis "
    "(age-suitability, no spoilers from later pages, no invented story events)."
)

Generate = Callable[[str, int], str]


class GenerationFailed(Exception):
    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


def generate_episode_script(tools: EpisodeTools, generate: Generate) -> dict:
    """Gemini API: one request per attempt, errors fed back, at most MAX_ATTEMPTS (FR-7)."""
    feedback: list[str] | None = None
    errors: list[str] = []
    for attempt in range(MAX_ATTEMPTS):
        prompt = build_prompt(tools.book, tools.cast, tools.plan, feedback)
        script, errors = tools.check(generate(prompt, tools.seed + attempt))
        if script is not None:
            return script
        feedback = errors
    raise GenerationFailed(f"generation failed after {MAX_ATTEMPTS} attempts", errors)


def mcp_server_args(args: argparse.Namespace) -> list[str]:
    server = [
        "-m",
        "video_pipeline.mcp_server",
        "--book",
        str(args.book.resolve()),
        "--seed",
        str(args.seed),
        "--episodes-dir",
        str(args.episodes_dir.resolve()),
        "--config-dir",
        str(args.config_dir.resolve()),
    ]
    return [*server, "--force"] if args.force else server


def _print_event(event: AgentEvent) -> None:
    if event.kind == "tool":
        print(f"  · {event.tool}")
    elif event.kind == "tool-result" and isinstance(event.data, dict) and event.data.get("errors"):
        print(f"    {len(event.data['errors'])} problem(s) to fix")
    elif event.kind == "error":
        print(f"  ! {event.text}")


def run_agent(args: argparse.Namespace, tools: EpisodeTools) -> None:
    binary = codex_status()
    command = build_codex_command(
        binary, sys.executable, mcp_server_args(args), {"PYTHONPATH": str(ROOT)}, args.model
    )
    log_path = tools.out.parent / "agent" / f"{time.strftime('%Y%m%d-%H%M%S')}-codex.jsonl"
    print(f"running the Codex (ChatGPT) agent; log: {log_path}")
    result = run_codex(command, log_path, on_event=_print_event)
    save = result.last_save or {}
    if save.get("saved") and tools.out.exists():
        errors = validate_script(
            json.loads(tools.out.read_text(encoding="utf-8")), tools.book, tools.terms
        )
        if not errors:
            return
        raise GenerationFailed(f"saved script failed re-validation; run log: {log_path}", errors)
    reason = result.reason or "the agent did not save a valid script"
    raise GenerationFailed(f"{reason}; run log: {log_path}", _last_save_errors(result.save_results))


def _last_save_errors(saves: list[dict]) -> list[str]:
    """Errors of the last refused save that reported real problems, not the save limit."""
    for save in reversed(saves):
        errors = save.get("errors") or []
        if errors and errors != [SAVE_LIMIT_ERROR]:
            return errors
    return []


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.generate_script",
        description="Generate episode_script.json for a book (ChatGPT via Codex by default).",
        allow_abbrev=False,  # otherwise the removed --mode would silently mean --model
    )
    parser.add_argument("--book", type=Path, default=Path("book_data.json"))
    parser.add_argument("--provider", choices=PROVIDERS, default="codex")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true", help="overwrite an existing script")
    parser.add_argument("--episodes-dir", type=Path, default=Path("episodes"))
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--model", default=None, help="override the provider's model")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        tools = EpisodeTools.load(
            args.book, args.seed, args.episodes_dir, args.config_dir, args.force
        )
        if tools.out.exists() and not args.force:
            print(
                f"error: {tools.out} already exists; re-run with --force to overwrite it",
                file=sys.stderr,
            )
            return 1
        if not tools.terms:
            print("warning: the disallowed-terms list is empty; no terms will be checked")
        if args.provider == "gemini-api":
            model = args.model or CLOUD_MODEL
            script = generate_episode_script(
                tools, lambda prompt, seed: generate_cloud(prompt, seed, model)
            )
            write_atomic(tools.out, script)
        else:
            run_agent(args, tools)
    except (BookError, ConfigError, LLMUnavailable, LLMError, AgentUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except GenerationFailed as exc:
        print(f"error: {exc}; problems in the last attempt:", file=sys.stderr)
        for problem in exc.errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"wrote {tools.out}")
    print(REVIEW_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
