"""Draft store for books written from an idea (spec 006, FR-4).

Everything lives under `<root>/drafts/` (git-ignored):

- `drafts/<id>/draft.json`: one book in progress (state, idea, pages, attempts, problems, book).
- `drafts/<id>/blueprint.json`: the designed creative direction (spec 011), if any.
- `drafts/<id>/agent/`: the writing agent's run logs.
- `drafts/ideas/<run-id>/result.json`: one suggestion or improvement run (ideas or a candidate).

A finished draft becomes `books/<slug>/book.json` through `create_book`, which removes the draft.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from video_pipeline.blueprint import (
    BLUEPRINT_FILE,
    BlueprintError,
    load_blueprint,
    load_context,
)
from video_pipeline.contract import ConfigError, load_disallowed_terms, slugify
from video_pipeline.episode import book_problems
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BOOK_FILE, BookFolder
from video_pipeline.story_tools import book_errors, load_gentle_terms

STATES = ("designing", "writing", "ready", "failed", "design_failed", "interrupted")
IDEAS_DIR = "ideas"
# Slugs a book may not take because a studio route already uses that path (`/books/new`).
RESERVED_SLUGS = frozenset({"new"})


class DraftError(Exception):
    """A draft request was refused; the message says why."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Missing, not a folder (e.g. Finder's `.DS_Store` in drafts/), or unreadable: no record.
        return None
    return data if isinstance(data, dict) else None


class DraftStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def drafts_dir(self) -> Path:
        return self.root / "drafts"

    def _draft_path(self, draft_id: str) -> Path:
        if not draft_id.isalnum():
            raise DraftError(f"unknown draft: {draft_id!r}")
        return self.drafts_dir / draft_id / "draft.json"

    def draft_folder(self, draft_id: str) -> Path:
        return self.drafts_dir / draft_id

    def _terms(self) -> tuple[list[str], list[str]]:
        config = self.root / "config"
        try:
            return (
                load_disallowed_terms(config / "disallowed_terms.txt"),
                load_gentle_terms(config / "gentle_terms.txt"),
            )
        except ConfigError as exc:
            raise DraftError(str(exc)) from None

    def _defaults_file(self) -> dict:
        path = self.root / "config" / "book_defaults.json"
        data = _read(path)
        if data is None:
            raise DraftError(f"book defaults not found or not valid JSON: {path}")
        return data

    def _defaults(self) -> dict:
        return self._defaults_file().get("metadata", {})

    def settings(self) -> dict:
        """Page range, default, idea length and age ranges (config/story.json)."""
        path = self.root / "config" / "story.json"
        data = _read(path)
        if data is None:
            raise DraftError(f"story settings not found or not valid JSON: {path}")
        return data

    def default_author(self) -> str:
        return str(self._defaults_file().get("author", ""))

    # --- drafts -------------------------------------------------------------------------------

    def blueprint_path(self, draft_id: str) -> Path:
        return self.draft_folder(draft_id) / BLUEPRINT_FILE

    def new_draft(self, idea: dict, pages: int, author: str, state: str = "designing") -> dict:
        draft_id = uuid.uuid4().hex[:12]
        now = _now()
        record = {
            "id": draft_id,
            "state": state,
            "phase": "design",
            "idea": idea,
            "pages": pages,
            "author": author,
            "attempts": 0,
            "problems": [],
            "book": None,
            "created_at": now,
            "updated_at": now,
        }
        write_atomic(self._draft_path(draft_id), record)
        return record

    def get(self, draft_id: str) -> dict | None:
        try:
            return _read(self._draft_path(draft_id))
        except DraftError:
            return None

    def list(self) -> list[dict]:
        if not self.drafts_dir.is_dir():
            return []
        records = [r for p in self.drafts_dir.iterdir() if (r := _read(p / "draft.json"))]
        return sorted(records, key=lambda r: r.get("updated_at", ""), reverse=True)

    def update(self, draft_id: str, **fields) -> dict:
        record = self.get(draft_id)
        if record is None:
            raise DraftError(f"unknown draft: {draft_id!r}")
        record.update(fields, updated_at=_now())
        write_atomic(self._draft_path(draft_id), record)
        return record

    def update_page_text(self, draft_id: str, number: int, text: str) -> list[str]:
        """Change one page's text when the whole book still passes the rules (AC-3.4).
        Returns the problems; when there are any nothing is written."""
        record = self.get(draft_id)
        if record is None or not record.get("book"):
            return ["the draft has no book yet"]
        book = copy.deepcopy(record["book"])
        pages = book.get("pages", [])
        if not 1 <= number <= len(pages):
            return [f"page {number} does not exist"]
        pages[number - 1]["text"] = text
        terms, gentle = self._terms()
        # A draft written before spec 018 has no sheet; it stays editable.
        errors = book_errors(
            book, record["pages"], terms, gentle, require_characters="characters" in book
        )
        if errors:
            return errors
        self.update(draft_id, book=book)
        return []

    def create_book(self, draft_id: str) -> str:
        """Turn a ready draft into `books/<slug>/` and remove the draft (AC-3.5, AC-3.6)."""
        record = self.get(draft_id)
        if record is None:
            raise DraftError(f"unknown draft: {draft_id!r}")
        if record["state"] != "ready" or not record.get("book"):
            raise DraftError("the draft is not ready to become a book")
        book = record["book"]
        title = book["metadata"]["title"].strip()
        slug = slugify(title)
        if slug in RESERVED_SLUGS:
            raise DraftError(f"the slug {slug!r} is reserved by the studio; change the title")
        folder = BookFolder(slug, self.root)
        if folder.folder.exists():
            raise DraftError(f"a book {slug!r} already exists; the draft is kept")
        metadata = {
            **self._defaults(),
            "title": title,
            "subtitle": book["metadata"].get("subtitle", ""),
            "author": record["author"],
            "age_range": record["idea"]["age_range"],
            "blurb": book["metadata"].get("blurb", ""),
            "dedication": book["metadata"].get("dedication", ""),
            "digital_page_count": len(book["pages"]),
        }
        style_bible: dict = {}
        blueprint_file = self.blueprint_path(draft_id)
        if blueprint_file.is_file():
            try:
                bp = load_blueprint(blueprint_file, load_context(self.root))
            except BlueprintError as exc:
                raise DraftError(str(exc)) from None
            style_bible = {"style_prompt_prefix": bp.art.style_prompt_prefix}
        if book.get("characters"):  # spec 018: the sheet moves into the style bible (AC-1.4)
            style_bible["characters"] = book["characters"]
        data = {"metadata": metadata, "style_bible": style_bible, "pages": book["pages"]}
        problems = book_problems(data, slug)
        if problems:
            raise DraftError(problems[0])
        folder.folder.parent.mkdir(parents=True, exist_ok=True)
        staging = folder.folder.parent / f".{slug}.new-{uuid.uuid4().hex[:8]}"
        try:
            (staging / "illustrations").mkdir(parents=True)
            write_atomic(staging / BOOK_FILE, data)
            if blueprint_file.is_file():
                shutil.copyfile(blueprint_file, staging / BLUEPRINT_FILE)
            os.replace(staging, folder.folder)  # noqa: PTH105 - one step into place
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(self.draft_folder(draft_id), ignore_errors=True)
        return slug

    def discard(self, draft_id: str) -> None:
        shutil.rmtree(self.draft_folder(draft_id), ignore_errors=True)

    def mark_interrupted_all(self) -> int:
        """Drafts left in `writing` or `designing` when the studio stopped become `interrupted`
        (AC-3.7); their `phase` says which stage was running."""
        count = 0
        for record in self.list():
            if record["state"] in ("writing", "designing"):
                self.update(record["id"], state="interrupted")
                count += 1
        return count

    # --- suggestion and improvement runs -------------------------------------------------------

    def new_run(self, kind: str) -> str:
        run_id = f"{kind}-{uuid.uuid4().hex[:8]}"
        write_atomic(
            self._run_path(run_id),
            {
                "id": run_id,
                "kind": kind,
                "state": "writing",
                "payload": None,
                "problems": [],
                "attempts": 0,
                "updated_at": _now(),
            },
        )
        return run_id

    def _run_path(self, run_id: str) -> Path:
        if not run_id.replace("-", "").isalnum():
            raise DraftError(f"unknown run: {run_id!r}")
        return self.drafts_dir / IDEAS_DIR / run_id / "result.json"

    def read_run(self, run_id: str) -> dict | None:
        return _read(self._run_path(run_id))

    def update_run(self, run_id: str, **fields) -> dict:
        record = self.read_run(run_id) or {"id": run_id}
        record.update(fields, updated_at=_now())
        write_atomic(self._run_path(run_id), record)
        return record

    def latest_run(self, kind: str) -> dict | None:
        folder = self.drafts_dir / IDEAS_DIR
        if not folder.is_dir():
            return None
        runs = [
            r for p in folder.iterdir() if (r := _read(p / "result.json")) and r.get("kind") == kind
        ]
        return max(runs, key=lambda r: r.get("updated_at", ""), default=None)
