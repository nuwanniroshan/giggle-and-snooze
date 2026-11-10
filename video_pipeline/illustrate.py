"""CLI: paint a book's missing illustrations with ChatGPT image generation via Codex.

    python -m video_pipeline.illustrate --book <slug> [--force] [--root PATH]

Paints `page_NN.png` for every page, then `cover_front.png` and `cover_back.png`, into
`books/<slug>/illustrations/`. Existing files are kept unless `--force`. Page 1 is painted first
and goes to every later image as a reference so the characters look the same on every page.
Uses the ChatGPT login of the Codex CLI (the same one the script and story stages use).
Each freshly painted story page then gets a fresh motion plan (spec 009); covers get none.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image

from video_pipeline.agents import AgentUnavailable, codex_status
from video_pipeline.library import PROJECT_ROOT, LibraryError, book_folder
from video_pipeline.motion_plan import PlanError, PlanOutcome, load_limits, plan_page

IMAGE_TIMEOUT_S = 600
MIN_SIDE = 512
STYLE_LINE = (
    "Square 1:1 picture-book illustration. Keep the exact art style, colours and character "
    "designs of the reference image when one is attached. No text, letters or logos anywhere."
)


class IllustrateError(Exception):
    """A picture could not be painted; the message names the file and the reason."""


def cover_prompts(book: dict) -> dict[str, str]:
    meta = book["metadata"]
    title, blurb = meta.get("title", ""), meta.get("blurb", "")
    return {
        "cover_front": (
            f'Front cover art for the children\'s picture book "{title}". Story: {blurb} '
            "Show the main character from the reference image in a joyful, inviting pose in the "
            "story's setting, with calm space near the top. Leave the cover without any lettering."
        ),
        "cover_back": (
            f'Back cover art for the picture book "{title}": a quiet, cozy scene from the story '
            "world without people, painted in the same style, with open space in the middle. "
            "Leave the cover without any lettering."
        ),
    }


def plan_images(book: dict) -> list[tuple[str, str]]:
    """(file stem, prompt) in painting order: pages by number, then the two covers."""
    pages = sorted(book["pages"], key=lambda p: p["page_number"])
    items = [(Path(_page_file(p)).stem, p["prompt"]) for p in pages]
    covers = cover_prompts(book)
    return items + [("cover_front", covers["cover_front"]), ("cover_back", covers["cover_back"])]


def _page_file(page: dict) -> str:
    return page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png"


def build_image_command(binary: Path, prompt: str, reference: Path | None) -> list[str]:
    command = [
        str(binary),
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "workspace-write",
        "-c",
        'web_search="disabled"',
    ]
    if reference is not None:
        command += ["-i", str(reference)]
    task = (
        "Use the image generation tool to create this picture, then copy the generated image "
        f"file to ./out.png with the shell and reply with only that path.\n\n{STYLE_LINE}\n\n"
        f"{prompt}"
    )
    return [*command, "--", task]


def check_image(path: Path) -> None:
    try:
        with Image.open(path) as im:
            im.load()
            width, height = im.size
    except Exception as exc:  # noqa: BLE001 - any unreadable file is the same failure
        raise IllustrateError(f"{path.name}: not a readable image ({exc})") from None
    if min(width, height) < MIN_SIDE:
        raise IllustrateError(f"{path.name}: image too small ({width}x{height})")


def paint_one(binary: Path, prompt: str, reference: Path | None, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="kiddo-image-") as tmp:
        work = Path(tmp)
        try:
            result = subprocess.run(
                build_image_command(binary, prompt, reference),
                cwd=work,
                capture_output=True,
                text=True,
                timeout=IMAGE_TIMEOUT_S,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise IllustrateError(f"{target.name}: timed out after {IMAGE_TIMEOUT_S} s") from None
        out = work / "out.png"
        if not out.is_file():
            tail = (result.stderr or result.stdout).strip()[-300:]
            raise IllustrateError(f"{target.name}: no image was produced. {tail}")
        check_image(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".tmp")
        partial.write_bytes(out.read_bytes())
        partial.replace(target)


def illustrate(
    book: dict,
    folder: Path,
    binary: Path,
    force: bool = False,
    paint: Callable[[Path, str, Path | None, Path], None] = paint_one,
    log: Callable[[str], None] = print,
    plan: Callable[[dict], PlanOutcome] | None = None,
) -> list[str]:
    """Paint what is missing (or everything with force). Returns the stems painted.

    `plan` is called with the page right after each painted story page (never a kept picture or
    a cover); a failed plan is logged and painting goes on."""
    pages = {Path(_page_file(p)).stem: p for p in book["pages"]}
    painted: list[str] = []
    reference: Path | None = None
    for stem, prompt in plan_images(book):
        target = folder / f"{stem}.png"
        if target.exists() and not force:
            log(f"  {stem}: kept")
        else:
            log(f"  {stem}: painting...")
            paint(binary, prompt, reference, target)
            painted.append(stem)
            if plan is not None and stem in pages:
                outcome = plan(pages[stem])
                reason = f" – {outcome.reason}" if outcome.reason else ""
                log(f"  {stem}: plan {outcome.status}{reason}")
        if reference is None:
            reference = target
    return painted


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="illustrate", description=__doc__.splitlines()[0])
    parser.add_argument("--book", required=True, help="book slug")
    parser.add_argument("--force", action="store_true", help="repaint existing images")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        book = json.loads(folder.book_json.read_text(encoding="utf-8"))
        binary = codex_status()
        limits = load_limits(args.root / "config")
        unplanned: list[str] = []

        def planner(page: dict) -> PlanOutcome:
            # Only a just-painted page gets here, so any old plan is stale: force (it keeps .bak).
            # A planning error fails only this page's plan; painting goes on (FR-13).
            try:
                outcome = plan_page(
                    folder, page, limits, binary=binary, book=book, root=args.root, force=True
                )
            except (AgentUnavailable, PlanError, OSError, KeyError, ValueError) as exc:
                outcome = PlanOutcome(int(page["page_number"]), "failed", str(exc))
            if outcome.status == "failed":
                unplanned.append(Path(_page_file(page)).stem)
            return outcome

        print(f"painting illustrations into {folder.illustrations}")
        painted = illustrate(
            book, folder.illustrations, binary, force=args.force, paint=paint_one, plan=planner
        )
    except (LibraryError, AgentUnavailable, IllustrateError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"painted {len(painted)} image(s); run again to retry anything that failed")
    if unplanned:
        print(
            f"error: no motion plan for {', '.join(unplanned)} (pictures kept); "
            f"run `python -m video_pipeline.motion_plan --book {args.book}` to retry",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
