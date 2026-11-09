"""CLI: move one book's files from the pre-spec layout into books/<slug>/ (spec 005, FR-7).

Order of work: plan every move, refuse conflicts, copy and hash-verify every file into place,
and only then remove the originals. A failure before the removal step leaves every original
where it was (AC-3.4). Rerunning finds nothing to move (AC-3.3).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.library import PROJECT_ROOT, BookFolder, validate_slug


class MigrationError(RuntimeError):
    """The migration stopped. No original has been removed when this is raised."""


@dataclass(frozen=True)
class Move:
    src: Path
    dst: Path


@dataclass(frozen=True)
class Result:
    move: Move
    status: str  # "moved", "already in place" or "would move"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(src_dir: Path, dst_dir: Path) -> list[Move]:
    if not src_dir.is_dir():
        return []
    return [
        Move(path, dst_dir / path.relative_to(src_dir))
        for path in sorted(src_dir.rglob("*"))
        if path.is_file()
    ]


def plan_moves(root: Path, slug: str) -> list[Move]:
    """Every file of the pre-spec layout that belongs to `slug`, with its new location."""
    validate_slug(slug)
    book = BookFolder(slug, Path(root))
    episode = Path(root) / "episodes" / slug
    build = Path(root) / "build" / slug
    moves: list[Move] = []
    if (Path(root) / "book_data.json").is_file():
        moves.append(Move(Path(root) / "book_data.json", book.book_json))
    moves += _tree(Path(root) / "assets" / "illustrations", book.illustrations)
    moves += _tree(Path(root) / "assets" / "references", book.references)
    if (episode / "episode_script.json").is_file():
        moves.append(Move(episode / "episode_script.json", book.script_path))
    if (episode / "reviewed.json").is_file():
        moves.append(Move(episode / "reviewed.json", book.reviewed_path))
    for nook in sorted(episode.glob("*_nook.png")):
        moves.append(Move(nook, book.nooks / nook.name))
    moves += _tree(episode / "agent", book.build_dir / "agent")
    moves += _tree(build / "audio", book.build_dir / "audio")
    moves += _tree(build / "video", book.build_dir / "video")
    return moves


def _prune_empty_parents(path: Path, root: Path) -> None:
    for parent in path.parents:
        if parent == root or root not in parent.parents:
            return
        try:
            parent.rmdir()  # only succeeds when empty
        except OSError:
            return


def migrate(root: Path, slug: str, dry_run: bool = False) -> list[Result]:
    root = Path(root)
    moves = plan_moves(root, slug)
    if dry_run:
        return [Result(m, "would move") for m in moves]

    to_copy: list[Move] = []
    to_remove_only: list[Move] = []
    for m in moves:
        if m.dst.exists():
            if sha256(m.src) != sha256(m.dst):
                raise MigrationError(
                    f"{m.dst.relative_to(root)} already exists with different content; "
                    "nothing was moved. Resolve it by hand and rerun."
                )
            to_remove_only.append(m)
        else:
            to_copy.append(m)

    created: list[Path] = []
    try:
        for m in to_copy:
            m.dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = m.dst.with_name(m.dst.name + ".tmp")
            shutil.copy2(m.src, tmp)
            if sha256(tmp) != sha256(m.src):
                tmp.unlink(missing_ok=True)
                raise MigrationError(f"hash mismatch copying {m.src.relative_to(root)}")
            tmp.replace(m.dst)
            created.append(m.dst)
    except OSError as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise MigrationError(f"copy failed: {exc}; no original was removed") from exc

    for m in to_copy + to_remove_only:
        m.src.unlink()
        _prune_empty_parents(m.src, root)

    return [Result(m, "moved") for m in moves]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move the pilot's files into books/<slug>/.")
    parser.add_argument("--slug", required=True, help="book slug, e.g. the-snail-mail-express")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    parser.add_argument("--dry-run", action="store_true", help="print the moves, change nothing")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = migrate(args.root, args.slug, dry_run=args.dry_run)
    except (MigrationError, ValueError) as exc:
        print(f"migration stopped: {exc}", file=sys.stderr)
        return 1
    root = args.root
    for r in results:
        print(f"{r.status}: {r.move.src.relative_to(root)} -> {r.move.dst.relative_to(root)}")
    if not results:
        print("nothing to migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
