"""CLI: ground each page's Q&A camera target and caption band (spec 025)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from video_pipeline.agents import AgentUnavailable
from video_pipeline.camera_ground import ground_book
from video_pipeline.cut_layers import ModelLoadError, ModelsMissing
from video_pipeline.library import PROJECT_ROOT, LibraryError, book_folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.ground_camera",
        description="Find the Q&A subject in each page picture for the camera and captions.",
    )
    parser.add_argument("--book", required=True)
    parser.add_argument("--page", type=int, action="append", help="ground only this page (repeat)")
    parser.add_argument("--force", action="store_true", help="replace current camera files")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        outcomes = ground_book(folder, root=Path(args.root), pages=args.page, force=args.force)
    except (
        LibraryError,
        AgentUnavailable,
        ModelsMissing,
        ModelLoadError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    count = {
        s: sum(o.status == s for o in outcomes) for s in ("written", "kept", "no_qa", "failed")
    }
    print(
        f"{count['written']} written, {count['kept']} kept, "
        f"{count['no_qa']} no Q&A, {count['failed']} failed"
    )
    return 1 if count["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
