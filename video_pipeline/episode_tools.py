"""The episode tools (FR-8): plan, validate and save. Shared by the MCP server and the CLI.

The book, seed, output folder and force flag are fixed when the tools are created, so a model
using them can only read this book's plan and write this book's script.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from video_pipeline.contract import (
    Banter,
    BanterParseError,
    ConfigError,
    load_disallowed_terms,
    parse_banter,
    slugify,
    validate_script,
)
from video_pipeline.episode import (
    EpisodePlan,
    assemble_script,
    banter_plan_errors,
    load_book,
    plan_episode,
)
from video_pipeline.llm import build_brief

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_SEED = 7
MAX_SAVES = 5
SAVE_LIMIT_ERROR = f"save limit reached ({MAX_SAVES} saves); stop"


def output_path(book: dict, episodes_dir: Path) -> Path:
    return Path(episodes_dir) / slugify(book["metadata"]["title"]) / "episode_script.json"


def load_cast(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"cast configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"cast configuration is not valid JSON: {path} ({exc})") from None


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)  # noqa: PTH105 - tests patch episode_tools.os.replace
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class EpisodeTools:
    book: dict
    cast: dict
    terms: list[str]
    seed: int
    out: Path
    force: bool = False
    saves: int = 0
    plan: EpisodePlan = field(init=False)

    def __post_init__(self) -> None:
        self.plan = plan_episode(self.book, self.seed)

    @classmethod
    def load(
        cls,
        book_path: Path,
        seed: int,
        episodes_dir: Path,
        config_dir: Path,
        force: bool = False,
    ) -> EpisodeTools:
        book = load_book(Path(book_path))
        terms = load_disallowed_terms(Path(config_dir) / "disallowed_terms.txt")
        cast = load_cast(Path(config_dir) / "cast.json")
        return cls(book, cast, terms, seed, output_path(book, episodes_dir), force)

    def check(self, banter: dict | str) -> tuple[dict | None, list[str]]:
        """Assemble and validate; returns (script, []) when valid, else (None, errors)."""
        raw = banter if isinstance(banter, str) else json.dumps(banter)
        try:
            parsed = parse_banter(raw)
        except BanterParseError as exc:
            return None, exc.errors
        script = assemble_script(self.book, self.cast, self.plan, parsed)
        errors = banter_plan_errors(parsed, self.plan) + validate_script(
            script, self.book, self.terms
        )
        return (None, errors) if errors else (script, [])

    def get_episode_plan(self) -> dict:
        meta = self.book["metadata"]
        return {
            "title": meta["title"],
            "age_range": meta.get("age_range", "young children"),
            "pages": [
                {"page_number": p["page_number"], "text": p["text"], "illustration": p["prompt"]}
                for p in self.book["pages"]
            ],
            "cast": self.cast,
            "qa_pages": [
                {"page_number": number, "kids": list(kids)}
                for number, kids in sorted(self.plan.qa_speakers.items())
            ],
            "brief": build_brief(self.book, self.cast, self.plan),
            "banter_schema": Banter.model_json_schema(),
        }

    def validate_banter(self, banter: dict | str) -> dict:
        _, errors = self.check(banter)
        return {"valid": not errors, "errors": errors}

    def save_episode_script(self, banter: dict | str) -> dict:
        if self.saves >= MAX_SAVES:
            return {"saved": False, "errors": [SAVE_LIMIT_ERROR]}
        self.saves += 1
        script, errors = self.check(banter)
        if script is None:
            return {"saved": False, "errors": errors}
        if self.out.exists() and not self.force:
            return {
                "saved": False,
                "errors": [f"{self.out} already exists; the producer must re-run with --force"],
            }
        write_atomic(self.out, script)
        return {"saved": True, "path": str(self.out), "errors": []}
