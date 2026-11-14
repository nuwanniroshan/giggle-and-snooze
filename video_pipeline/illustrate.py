"""CLI: paint a book's missing illustrations with ChatGPT image generation via Codex.

    python -m video_pipeline.illustrate --book <slug> [--force] [--root PATH]
    python -m video_pipeline.illustrate --book <slug> --page N [--cut]
    python -m video_pipeline.illustrate --book <slug> --cover front|print|back

Paints `page_NN.png` for every page, then `cover_front.png` and `cover_back.png`, into
`books/<slug>/illustrations/`. A book marked with titled covers (spec 022) instead gets a lettered
4:3 video cover `cover_front.png` and a square print cover `cover_front_print.png`, each with the
channel logo; every titled cover is read back by Codex (title, subtitle, whole logo) and painted
again until it passes, up to `tries` in `config/covers.json` (a failed cover is never saved). Existing files are kept unless `--force`. Page 1 is painted first
and goes to every later image as a reference so the characters look the same on every page.
Uses the ChatGPT login of the Codex CLI (the same one the script and story stages use).
Each freshly painted story page then gets a fresh motion plan (spec 009); covers get none.

`--page N` or `--cover front|print|back` repaints exactly one picture (spec 019), keeping the old one as
`<name>.png.bak` and restoring it if painting fails. A repainted page is planned again, and with
`--cut` its layers are cut too; a cover gets neither. `--cover print` exists only for books with
titled covers; an older book's `--cover front` is repainted the old way, without lettering.
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image

from video_pipeline import cleanup, cut_layers
from video_pipeline.agents import AgentUnavailable, codex_status
from video_pipeline.blueprint import (
    BlueprintError,
    blueprint_sha256,
    load_blueprint,
    load_context,
    record_use,
    style_text,
)
from video_pipeline.characters import cover_characters_text, page_characters_text
from video_pipeline.cleanup import CleanupError
from video_pipeline.covers import (
    CHECK_SCHEMA,
    TITLED_STEMS,
    CoverError,
    CoverSettings,
    build_check_command,
    build_cover_command,
    check_problem,
    cover_request,
    load_cover_settings,
    parse_reading,
    titled_covers,
    trim_to_ratio,
)
from video_pipeline.cut_layers import ModelLoadError, ModelsMissing
from video_pipeline.library import PROJECT_ROOT, LibraryError, book_folder
from video_pipeline.motion_plan import PlanError, PlanOutcome, load_limits, plan_page

IMAGE_TIMEOUT_S = 600
MIN_SIDE = 512
LANDSCAPE_RATIO = (16, 9)
STYLE_LINE = (
    "Wide landscape picture-book illustration, generated at landscape size 1536x1024 (never "
    "square, never portrait). Compose it for a 16:9 film frame: keep every important character "
    "and object inside the central 80% of the height, and fill the full width with scenery. "
    "Keep the exact art style, colours and character designs of the reference image when one is "
    "attached. No text, letters or logos anywhere."
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
            "story's setting, with calm, uncluttered sky or scenery across the top 35% of the picture "
            "(the title is added there later). Leave the cover without any lettering."
        ),
        "cover_back": (
            f'Back cover art for the picture book "{title}": a quiet, cozy scene from the story '
            "world without people, painted in the same style, with open space in the middle. "
            "Leave the cover without any lettering."
        ),
    }


def plan_images(
    book: dict,
    style: str | None = None,
    *,
    art: dict | None = None,
    settings: CoverSettings | None = None,
) -> list[tuple[str, str]]:
    """(file stem, prompt) in painting order: pages by number, then the covers.

    `style` (the blueprint's art text) is put in front of every prompt, covers included. A book
    with a character sheet (spec 018) adds the looks of each page's characters, and of the whole
    sheet on the covers; a book without one gets exactly the old prompts. A book with titled
    covers (spec 022), given `settings`, gets the video and print front covers from
    `cover_request` (with `art`, not prefixed by `style`) before the unchanged back cover."""

    def with_characters(prompt: str, block: str) -> str:
        return f"{prompt}\n\n{block}" if block else prompt

    pages = sorted(book["pages"], key=lambda p: p["page_number"])
    items = [
        (Path(_page_file(p)).stem, with_characters(p["prompt"], page_characters_text(book, p)))
        for p in pages
    ]
    covers = cover_prompts(book)
    cast = cover_characters_text(book)
    titled = settings is not None and titled_covers(book)
    if not titled:
        items.append(("cover_front", with_characters(covers["cover_front"], cast)))
    items.append(("cover_back", with_characters(covers["cover_back"], cast)))
    if style:
        items = [(stem, f"{style}\n\n{prompt}") for stem, prompt in items]
    if titled:
        front = [(stem, cover_request(book, art, stem, settings)) for stem in TITLED_STEMS]
        items[-1:-1] = front
    return items


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


def crop_to_landscape(path: Path) -> None:
    """Centre-crop a landscape picture to exactly 16:9, in place. A square or portrait result is
    refused (IllustrateError), since cropping it would cut away most of the picture."""
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        if width / height < 1.3:
            raise IllustrateError(f"{path.name}: came back {width}x{height}, not landscape")
        want = LANDSCAPE_RATIO[0] / LANDSCAPE_RATIO[1]
        if abs(width / height - want) < 0.005:
            return
        if width / height > want:
            new_w, new_h = round(height * want), height
        else:
            new_w, new_h = width, round(width / want)
        left, top = (width - new_w) // 2, (height - new_h) // 2
        cropped = im.convert("RGB").crop((left, top, left + new_w, top + new_h))
    cropped.save(path, format="PNG")


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
    """Paint one picture; a result that is not landscape is painted again (up to 3 tries)."""
    for attempt in range(3):
        try:
            return _paint_once(binary, prompt, reference, target)
        except IllustrateError as exc:
            if "not landscape" not in str(exc) or attempt == 2:
                raise


def _paint_once(binary: Path, prompt: str, reference: Path | None, target: Path) -> None:
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
        crop_to_landscape(out)
        check_image(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".tmp")
        partial.write_bytes(out.read_bytes())
        partial.replace(target)


def _paint_cover_once(
    binary: Path, prompt: str, logo: Path, reference: Path | None, work: Path, run
) -> Path:
    """One titled-cover painting into `work/out.png` (returned)."""
    try:
        result = run(
            build_cover_command(binary, prompt, logo, reference),
            cwd=work,
            capture_output=True,
            text=True,
            timeout=IMAGE_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise IllustrateError(f"painting timed out after {IMAGE_TIMEOUT_S} s") from None
    out = work / "out.png"
    if not out.is_file():
        tail = (result.stderr or result.stdout or "").strip()[-300:]
        raise IllustrateError(f"no image was produced. {tail}")
    return out


def _read_cover(binary: Path, cover: Path, settings: CoverSettings, work: Path, run):
    """Codex reads the trimmed cover back (title, subtitle, whole logo)."""
    schema = work / "check_schema.json"
    schema.write_text(json.dumps(CHECK_SCHEMA), encoding="utf-8")
    reply = work / "reply.json"
    try:
        run(
            build_check_command(binary, cover, schema, reply, settings.logo_name),
            cwd=work,
            capture_output=True,
            text=True,
            timeout=IMAGE_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise IllustrateError(f"cover check timed out after {IMAGE_TIMEOUT_S} s") from None
    if not reply.is_file():
        raise IllustrateError("cover check gave no answer")
    return parse_reading(reply.read_text(encoding="utf-8"))


def paint_titled_cover(
    binary: Path,
    prompt: str,
    reference: Path | None,
    target: Path,
    *,
    words: tuple[str, str],
    settings: CoverSettings,
    root: Path,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    log: Callable[[str], None] = print,
) -> None:
    """Paint one titled cover (spec 022): paint with the logo and `reference` attached, trim to the
    cover's shape, read it back and compare with `words` (title, subtitle). A failed try is
    painted again, up to `settings.tries`; only a passing cover is written to `target`."""
    logo = root / settings.logo
    if not logo.is_file():
        raise IllustrateError(f"{target.name}: logo not found: {logo}")
    shape = settings.shapes[target.stem]
    reason = ""
    for attempt in range(1, settings.tries + 1):
        with tempfile.TemporaryDirectory(prefix="kiddo-cover-") as tmp:
            work = Path(tmp)
            try:
                out = _paint_cover_once(binary, prompt, logo, reference, work, run)
                trim_to_ratio(out, shape)
                check_image(out)
                reading = _read_cover(binary, out, settings, work, run)
                reason = check_problem(reading, *words) or ""
            except (IllustrateError, CoverError) as exc:
                reason = str(exc)
            if not reason:
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".tmp")
                partial.write_bytes(out.read_bytes())
                partial.replace(target)
                log(
                    f"  {target.stem}: accepted after {attempt} painting(s); read title "
                    f"{reading.title!r}, subtitle {reading.subtitle!r}"
                )
                return
            log(f"  {target.stem}: try {attempt} failed: {reason}")
    raise IllustrateError(
        f"{target.name}: failed the cover check {settings.tries} times; last: {reason}"
    )


def illustrate(
    book: dict,
    folder: Path,
    binary: Path,
    force: bool = False,
    paint: Callable[[Path, str, Path | None, Path], None] = paint_one,
    log: Callable[[str], None] = print,
    plan: Callable[[dict], PlanOutcome] | None = None,
    style: str | None = None,
    *,
    art: dict | None = None,
    settings: CoverSettings | None = None,
    paint_cover: Callable[[Path, str, Path | None, Path], None] | None = None,
) -> list[str]:
    """Paint what is missing (or everything with force). Returns the stems painted.

    `plan` is called with the page right after each painted story page (never a kept picture or
    a cover); a failed plan is logged and painting goes on. Titled covers (spec 022) are painted
    with `paint_cover` when given; its IllustrateError stops the run (pictures already painted
    stay saved)."""
    pages = {Path(_page_file(p)).stem: p for p in book["pages"]}
    painted: list[str] = []
    reference: Path | None = None
    for stem, prompt in plan_images(book, style, art=art, settings=settings):
        target = folder / f"{stem}.png"
        if target.exists() and not force:
            log(f"  {stem}: kept")
        else:
            log(f"  {stem}: painting...")
            painter = paint_cover if stem in TITLED_STEMS and paint_cover else paint
            painter(binary, prompt, reference, target)
            painted.append(stem)
            if plan is not None and stem in pages:
                outcome = plan(pages[stem])
                reason = f" – {outcome.reason}" if outcome.reason else ""
                log(f"  {stem}: plan {outcome.status}{reason}")
        if reference is None:
            reference = target
    return painted


def select_images(
    book: dict,
    style: str | None,
    stem: str,
    *,
    art: dict | None = None,
    settings: CoverSettings | None = None,
) -> tuple[str, str]:
    """The one `(stem, prompt)` of `plan_images` for `stem` (so a single repaint gets exactly the
    request a full painting would, character looks included)."""
    for item in plan_images(book, style, art=art, settings=settings):
        if item[0] == stem:
            return item
    raise IllustrateError(f"{stem} is not a picture of this book")


def page_stem(book: dict, number: int) -> str:
    """The file stem of story page `number` (by `page_number`, not list position)."""
    for page in book["pages"]:
        if int(page["page_number"]) == number:
            return Path(_page_file(page)).stem
    raise IllustrateError(f"page {number} is not in the book")


def repaint_reference(book: dict, folder: Path, stem: str) -> Path | None:
    """The picture a single repaint attaches: the first story page (by number) that exists and is
    not the target itself; None when no other page picture exists."""
    for page in sorted(book["pages"], key=lambda p: p["page_number"]):
        candidate = folder / _page_file(page)
        if candidate.stem != stem and candidate.is_file():
            return candidate
    return None


def repaint_one(
    book: dict,
    folder: Path,
    binary: Path,
    stem: str,
    paint: Callable[[Path, str, Path | None, Path], None] = paint_one,
    style: str | None = None,
    log: Callable[[str], None] = print,
    art: dict | None = None,
    settings: CoverSettings | None = None,
) -> Path:
    """Repaint one picture (spec 019 FR-1). The old file is kept as `<name>.png.bak`, and put back
    when painting fails (the error is raised again). Returns the picture's path."""
    stem, prompt = select_images(book, style, stem, art=art, settings=settings)
    target = folder / f"{stem}.png"
    backup = target.with_name(target.name + ".bak")
    if target.is_file():
        shutil.copyfile(target, backup)
    log(f"  {stem}: repainting...")
    try:
        paint(binary, prompt, repaint_reference(book, folder, stem), target)
    except IllustrateError:
        if backup.is_file():
            shutil.copyfile(backup, target)
            log(f"  {stem}: repaint failed; the old picture was put back")
        raise
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="illustrate", description=__doc__.splitlines()[0])
    parser.add_argument("--book", required=True, help="book slug")
    one = parser.add_mutually_exclusive_group()
    one.add_argument("--force", action="store_true", help="repaint existing images")
    one.add_argument("--page", type=int, help="repaint only this story page (keeps a .bak)")
    one.add_argument(
        "--cover",
        choices=("front", "print", "back"),
        help="repaint only this cover (print: the titled square print cover; keeps a .bak)",
    )
    parser.add_argument(
        "--cut", action="store_true", help="with --page: cut the page's layers after its plan"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="with --page: remove AI artifacts with Codex, SAM and LaMa (keeps a .bak)",
    )
    parser.add_argument("--yes", action="store_true", help="with --cleanup: overwrite an old .bak")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    if args.cut and args.page is None:
        parser.error("--cut needs --page")
    if args.cleanup and args.cover:
        parser.error("--cleanup applies to story pages only")
    if args.cleanup and args.page is None:
        parser.error("--cleanup needs --page")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        book = json.loads(folder.book_json.read_text(encoding="utf-8"))
        binary = codex_status()
        limits = load_limits(args.root / "config")
        settings = load_cover_settings(args.root / "config")
        style, art = None, None
        if folder.blueprint_path.is_file():
            bp = load_blueprint(folder.blueprint_path, load_context(args.root))
            style = style_text(bp)
            art = bp.art.model_dump() if hasattr(bp, "art") else None
        paint_cover = None
        if titled_covers(book):
            meta = book["metadata"]
            paint_cover = functools.partial(
                paint_titled_cover,
                words=(meta.get("title", ""), meta.get("subtitle", "")),
                settings=settings,
                root=args.root,
            )
        covers = {"art": art, "settings": settings}
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

        if args.cleanup:
            return _cleanup_main(args, book, folder, binary)
        if args.page is not None or args.cover:
            return _repaint_main(args, book, folder, binary, style, planner, covers, paint_cover)
        print(f"painting illustrations into {folder.illustrations}")
        painted = illustrate(
            book,
            folder.illustrations,
            binary,
            force=args.force,
            paint=paint_one,
            plan=planner,
            style=style,
            paint_cover=paint_cover,
            **covers,
        )
        if style and len(painted) == len(plan_images(book, style, **covers)):
            record_use(folder, "art", blueprint_sha256(folder.blueprint_path))
    except (
        LibraryError,
        AgentUnavailable,
        BlueprintError,
        CleanupError,
        CoverError,
        IllustrateError,
        ModelLoadError,
        ModelsMissing,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
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


COVER_STEMS = {"front": "cover_front", "print": "cover_front_print", "back": "cover_back"}


def _cleanup_main(args, book, folder, binary) -> int:
    """Clean one story page's artifacts; errors propagate to `main` (exit 1)."""
    stem = page_stem(book, args.page)
    outcome = cleanup.cleanup_page(
        folder, book, args.page, root=args.root, binary=binary, yes=args.yes
    )
    if outcome.status == "cleaned":
        print(
            f"cleaned page {args.page}: {len(outcome.applied)} region(s) filled, "
            f"{len(outcome.skipped)} skipped (old picture kept as {stem}.png.bak)"
        )
        return 0
    if outcome.status == "nothing":
        print(f"nothing to clean on page {args.page}")
        return 0
    print(f"error: cleanup of page {args.page} failed: {outcome.reason}", file=sys.stderr)
    return 1


def _repaint_main(args, book, folder, binary, style, planner, covers, paint_cover) -> int:
    """Repaint one picture; a page is then planned and (with --cut) cut. Errors propagate to
    `main`, which reports them and exits 1."""
    if args.cover:
        stem = COVER_STEMS[args.cover]
        if stem == "cover_front_print" and paint_cover is None:
            raise IllustrateError("this book has no print cover (it was made before titled covers)")
        paint = paint_cover if stem in TITLED_STEMS and paint_cover else paint_one
        repaint_one(book, folder.illustrations, binary, stem, paint=paint, style=style, **covers)
        print(f"repainted {stem} (old picture kept as {stem}.png.bak)")
        return 0
    stem = page_stem(book, args.page)
    page = next(p for p in book["pages"] if int(p["page_number"]) == args.page)
    repaint_one(book, folder.illustrations, binary, stem, paint=paint_one, style=style)
    print(f"repainted {stem} (old picture kept as {stem}.png.bak)")
    outcome = planner(page)
    reason = f" – {outcome.reason}" if outcome.reason else ""
    print(f"  {stem}: plan {outcome.status}{reason}")
    if outcome.status == "failed":
        print(
            f"error: no motion plan for {stem} (picture kept); run `python -m "
            f"video_pipeline.motion_plan --book {args.book} --page {args.page}` to retry",
            file=sys.stderr,
        )
        return 1
    if not args.cut:
        return 0
    print(f"cutting the layers of page {args.page}")
    return cut_layers.main(
        ["--book", args.book, "--page", str(args.page), "--root", str(args.root)]
    )


if __name__ == "__main__":
    raise SystemExit(main())
