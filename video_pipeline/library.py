"""One folder per book: books/<slug>/ (spec 005).

Pure path logic. The only I/O is checking that files exist and listing the books folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOK_FILE = "book.json"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LibraryError(ValueError):
    """A slug or book folder the library cannot use. The message names the problem."""


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.fullmatch(slug):
        raise LibraryError(
            f"invalid book slug {slug!r}: use lowercase letters, digits and single hyphens"
        )
    return slug


@dataclass(frozen=True)
class BookFolder:
    """Every path a book uses, derived from its slug and the project root."""

    slug: str
    root: Path

    @property
    def folder(self) -> Path:
        return self.root / "books" / self.slug

    @property
    def book_json(self) -> Path:
        return self.folder / BOOK_FILE

    @property
    def illustrations(self) -> Path:
        return self.folder / "illustrations"

    @property
    def references(self) -> Path:
        return self.folder / "references"

    @property
    def nooks(self) -> Path:
        return self.folder / "nooks"

    @property
    def episode_dir(self) -> Path:
        return self.folder / "episode"

    @property
    def script_path(self) -> Path:
        return self.episode_dir / "episode_script.json"

    @property
    def reviewed_path(self) -> Path:
        return self.episode_dir / "reviewed.json"

    @property
    def blueprint_path(self) -> Path:
        return self.folder / "blueprint.json"

    @property
    def used_path(self) -> Path:
        return self.folder / "blueprint_used.json"

    @property
    def build_dir(self) -> Path:
        return self.folder / "build"


def book_folder(slug: str, root: Path = PROJECT_ROOT) -> BookFolder:
    validate_slug(slug)
    folder = BookFolder(slug, Path(root))
    if not folder.book_json.is_file():
        raise LibraryError(f"book {slug!r} has no {BOOK_FILE} at {folder.book_json}")
    return folder


def list_book_slugs(root: Path = PROJECT_ROOT) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (usable slugs, problems). A problem is (folder name, reason)."""
    books_dir = Path(root) / "books"
    slugs: list[str] = []
    problems: list[tuple[str, str]] = []
    if not books_dir.is_dir():
        return slugs, problems
    for entry in sorted(books_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            validate_slug(entry.name)
        except LibraryError as exc:
            problems.append((entry.name, str(exc)))
            continue
        if not (entry / BOOK_FILE).is_file():
            problems.append((entry.name, f"book {entry.name!r} has no {BOOK_FILE}"))
            continue
        slugs.append(entry.name)
    return slugs, problems


def file_stem(title: str) -> str:
    """`The Snail Mail Express` → `The_Snail_Mail_Express`, for output filenames."""
    return re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
