"""CLI: generate episodes/<slug>/episode_script.json from a book data file (spec 001)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from video_pipeline.contract import (
    BanterParseError,
    ConfigError,
    load_disallowed_terms,
    parse_banter,
    validate_script,
)
from video_pipeline.episode import (
    BookError,
    assemble_script,
    banter_plan_errors,
    load_book,
    plan_episode,
)
from video_pipeline.episode_tools import (
    CONFIG_DIR,
    DEFAULT_SEED,
    load_cast,
    output_path,
    write_atomic,
)
from video_pipeline.llm import (
    CLOUD_MODEL,
    LOCAL_MODEL,
    LLMError,
    LLMUnavailable,
    build_prompt,
    generate_cloud,
    generate_local,
)

MAX_ATTEMPTS = 3
REVIEW_NOTICE = (
    "Review required: this script must be reviewed by a person before voice synthesis "
    "(age-suitability, no spoilers from later pages, no invented story events)."
)

Generate = Callable[[str, int], str]


class GenerationFailed(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"generation failed after {MAX_ATTEMPTS} attempts")
        self.errors = errors


def generate_episode_script(
    book: dict, cast: dict, terms: list[str], seed: int, generate: Generate
) -> dict:
    plan = plan_episode(book, seed)
    feedback: list[str] | None = None
    errors: list[str] = []
    for attempt in range(MAX_ATTEMPTS):
        raw = generate(build_prompt(book, cast, plan, feedback), seed + attempt)
        try:
            banter = parse_banter(raw)
        except BanterParseError as exc:
            errors = exc.errors
        else:
            script = assemble_script(book, cast, plan, banter)
            errors = banter_plan_errors(banter, plan) + validate_script(script, book, terms)
            if not errors:
                return script
        feedback = errors
    raise GenerationFailed(errors)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.generate_script",
        description="Generate episode_script.json for a book (local model by default).",
    )
    parser.add_argument("--book", type=Path, default=Path("book_data.json"))
    parser.add_argument("--mode", choices=("local", "cloud"), default="local")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true", help="overwrite an existing script")
    parser.add_argument("--episodes-dir", type=Path, default=Path("episodes"))
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--model", default=None, help="override the text model name")
    return parser.parse_args(argv)


def _backend(mode: str, model: str | None) -> Generate:
    if mode == "cloud":
        return lambda prompt, seed: generate_cloud(prompt, seed, model or CLOUD_MODEL)
    return lambda prompt, seed: generate_local(prompt, seed, model or LOCAL_MODEL)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        book = load_book(args.book)
        out = output_path(book, args.episodes_dir)
        if out.exists() and not args.force:
            print(
                f"error: {out} already exists; re-run with --force to overwrite it",
                file=sys.stderr,
            )
            return 1
        terms = load_disallowed_terms(args.config_dir / "disallowed_terms.txt")
        if not terms:
            print("warning: the disallowed-terms list is empty; no terms will be checked")
        cast = load_cast(args.config_dir / "cast.json")
        script = generate_episode_script(
            book, cast, terms, args.seed, _backend(args.mode, args.model)
        )
    except (BookError, ConfigError, LLMUnavailable, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except GenerationFailed as exc:
        print(f"error: {exc}; problems in the last attempt:", file=sys.stderr)
        for problem in exc.errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    write_atomic(out, script)
    print(f"wrote {out}")
    print(REVIEW_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
