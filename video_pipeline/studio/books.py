"""Books, uploads, the script review mark, line edits and stage status (spec 004).

No web code: the app layer calls these. State is derived from the files the pipeline already
writes (FR-2); the only new file is `books/<slug>/episode/reviewed.json`.

Every book is one folder, `books/<slug>/` (spec 005): its source files, and `build/` for generated
output. Shared settings (config, sound effects, the fallback nook) stay at the project root.
"""

from __future__ import annotations

import copy
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from video_pipeline import cleanup
from video_pipeline import synthesize_voices as sv
from video_pipeline import youtube as yt
from video_pipeline import youtube_api as yapi
from video_pipeline.animate import page_eligibility
from video_pipeline.blueprint import (
    BlueprintError,
    blueprint_sha256,
    load_blueprint,
    load_context,
    read_use,
)
from video_pipeline.camera_ground import load_current
from video_pipeline.characters import sheet
from video_pipeline.contract import ConfigError, load_disallowed_terms, validate_script
from video_pipeline.covers import titled_covers
from video_pipeline.cut_layers import (
    MANIFEST,
    cut_settings,
    cut_state,
    layers_dir,
    manifest_files,
)
from video_pipeline.emotions import NEUTRAL, load_emotions
from video_pipeline.episode import BookError, book_problems, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BOOK_FILE, PROJECT_ROOT, BookFolder, list_book_slugs
from video_pipeline.motion_plan import (
    LIMITS_FILE,
    load_limits,
    page_picture,
    picture_identity,
    plan_path,
    plan_state,
)
from video_pipeline.render_episode import VideoPaths, render_check_detail
from video_pipeline.timeline import AnimationSettings, load_render_config

COVER_FILES = {"cover_front.png": "front cover", "cover_back.png": "back cover"}
# Spec 022: a titled book also has a square print cover beside the 4:3 video cover.
PRINT_COVER = "cover_front_print.png"
TITLED_COVER_FILES = {
    "cover_front.png": "video cover",
    PRINT_COVER: "print cover",
    "cover_back.png": "back cover",
}
NOOK_FILES = ("intro_nook.png", "outro_nook.png")
STAGES = ("script", "voices", "render")
# Spec 016: paints the book's pictures (the illustrate CLI); a studio job like the others, but
# outside the script -> voices -> render flow, because it needs no review.
PAINT_STAGE = "paint"
# Spec 019: repaints one page (then plans and cuts it) or one cover; shown on the page view and the
# Pictures panel only, never as a pipeline stage state.
REPAINT_STAGE = "repaint"
# Spec 026: removes AI artifacts from one story page (illustrate --page N --cleanup); page view only.
CLEANUP_STAGE = "cleanup"
COVER_SIDES = ("front", "print", "back")  # every side a repaint job accepts (spec 022)
# Spec 024: Codex writes the book's YouTube text; the upload sends the video (never autopilot).
YOUTUBE_TEXT_STAGE = "youtube_text"
YOUTUBE_UPLOAD_STAGE = "youtube_upload"
YOUTUBE_STAGES = (YOUTUBE_TEXT_STAGE, YOUTUBE_UPLOAD_STAGE)
# Spec 019: builds the PDF, EPUB, KDP files and web reader (scripts/build_book.py) into build/.
BUILD_STAGE = "build"
EDITION_SUFFIXES = (".pdf", ".epub", ".png", ".html")
STATUS_KEYS = {
    "script": "script",
    "voices": "voices",
    "render": "video",
    "paint": "pictures",
    BUILD_STAGE: "editions",
}
STAGE_LABELS = {
    "script": "script generation",
    "voices": "voice synthesis",
    "render": "video render",
    "plan": "motion planning",
    "cut": "layer cutting",
    "ground": "camera grounding",
    "preview": "page preview",
    PAINT_STAGE: "picture painting",
    REPAINT_STAGE: "picture repaint",
    CLEANUP_STAGE: "artifact cleanup",
    BUILD_STAGE: "book build",
    "blueprint": "blueprint design",
    "redesign": "blueprint design",
    "autopilot": "autopilot",
    YOUTUBE_TEXT_STAGE: "YouTube text",
    YOUTUBE_UPLOAD_STAGE: "YouTube upload",
}
# Spec 021: one job that runs every unfinished stage in order (video_pipeline/autopilot.py). Its
# run state is `<studio_dir>/autopilot/<slug>.json` (`draft-<id>.json` until the book exists).
AUTOPILOT_STAGE = "autopilot"
AUTOPILOT_DIR = "autopilot"
# The book page's stage (status key) each autopilot step belongs to; plan, cut, story and create
# have none.
AUTOPILOT_STEP_STATUS = {
    "paint": "pictures",
    "script": "script",
    "review": "script",
    "voices": "voices",
    "approve": "voices",
    "render": "video",
    "build": "editions",
}
# Spec 009: run as studio jobs, but outside the script → voices → render flow.
ANIMATION_STAGES = ("plan", "cut", "ground")
# Spec 010: one story page rendered with its voice; started per page, not from the stage buttons.
PREVIEW_STAGE = "preview"
NO_CUT = "—"
# One fixed tint per layer (in plan order) for the masks overlay; the picture under it is dimmed.
MASK_PALETTE = (
    (230, 25, 75),
    (60, 180, 75),
    (255, 225, 25),
    (0, 130, 200),
    (245, 130, 48),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
)
MASK_ALPHA = 0.65
MASK_DIM = 0.35
JOB_OVERRIDES = ("running", "failed", "interrupted")

LatestJob = Callable[[str, str], dict | None]


class StudioError(Exception):
    """A studio request was refused; the message says why."""


@dataclass(frozen=True)
class StudioPaths:
    """Where the studio finds books and shared files; `root` is the project folder."""

    root: Path = PROJECT_ROOT

    @property
    def books_dir(self) -> Path:
        return Path(self.root) / "books"

    @property
    def build_dir(self) -> Path:
        """Project-level output only (studio job records, the page-turn cache)."""
        return Path(self.root) / "build"

    @property
    def config_dir(self) -> Path:
        return Path(self.root) / "config"

    @property
    def studio_dir(self) -> Path:
        return self.build_dir / "studio"

    @property
    def sfx_dir(self) -> Path:
        return Path(self.root) / "assets" / "audio_sfx"

    @property
    def shared_nooks(self) -> Path:
        return Path(self.root) / "assets" / "nook"


@dataclass(frozen=True)
class Book:
    """A book the studio lists. `error` is set (and the book not runnable) when its data is
    broken or its folder is not usable; such a book is listed by its folder name."""

    slug: str
    title: str
    book_path: Path
    error: str | None = None


def folder_of(paths: StudioPaths, book: Book) -> BookFolder:
    return BookFolder(book.slug, Path(paths.root))


# --- the registry (FR-1, spec 005 FR-9) -----------------------------------------------------


def list_books(paths: StudioPaths) -> list[Book]:
    """Every usable `books/<slug>/` by title, then the folders that cannot be used, each with
    the reason (spec 005 AC-5.2)."""
    found: list[Book] = []
    slugs, problems = list_book_slugs(paths.root)
    for slug in slugs:
        folder = BookFolder(slug, Path(paths.root))
        try:
            title = load_book(folder.book_json)["metadata"]["title"].strip()
        except BookError as exc:
            found.append(Book(slug, slug, folder.book_json, str(exc)))
        else:
            found.append(Book(slug, title, folder.book_json))
    for name, reason in problems:
        found.append(Book(name, name, paths.books_dir / name / BOOK_FILE, reason))
    return sorted(found, key=lambda b: (b.title.casefold(), b.slug))


def get_book(paths: StudioPaths, slug: str) -> Book | None:
    return next((b for b in list_books(paths) if b.slug == slug), None)


# --- uploads (AC-2.1, AC-2.2, FR-6) ---------------------------------------------------------


def _plain_name(name: str) -> bool:
    return (
        bool(name)
        and name not in (".", "..")
        and not any(c in name for c in "/\\\0")
        and Path(name).name == name
    )


def cover_files(book: dict) -> dict[str, str]:
    """The book's cover file names (name → what it is): a titled book (spec 022) has a video, a
    print and a back cover; an older book a front and a back cover."""
    return dict(TITLED_COVER_FILES if titled_covers(book) else COVER_FILES)


def cover_sides(book: dict) -> dict[str, str]:
    """The cover sides the book can repaint (side → label), in display order (spec 022)."""
    if titled_covers(book):
        return {"front": "video cover", "print": "print cover", "back": "back cover"}
    return {"front": "front cover", "back": "back cover"}


def book_cover_sides(book: Book) -> dict[str, str]:
    """`cover_sides` of the book's data as stored, read without validation so a broken book still
    shows its sides (its repaint is then refused by the job gate); unreadable data gives an older
    book's sides."""
    try:
        data = json.loads(book.book_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return cover_sides(data if isinstance(data, dict) else {})


def expected_files(book: dict) -> tuple[dict[str, str], list[str]]:
    """Required upload names (name → what it is) for `book`, and problems with the names the book
    data gives. A page without `illustration_file` expects `page_PP.png`, as render does.

    A page may not use a cover or nook name (those files are stored elsewhere or mean something
    else), and no two names may differ only by case (the Mac's file system would merge them)."""
    required: dict[str, str] = {}
    problems: list[str] = []
    reserved = {*COVER_FILES, PRINT_COVER, *NOOK_FILES}
    pages = book.get("pages")
    for index, page in enumerate(pages if isinstance(pages, list) else [], start=1):
        if not isinstance(page, dict):
            continue
        number = page.get("page_number") if isinstance(page.get("page_number"), int) else index
        name = page.get("illustration_file") or f"page_{number:02d}.png"
        if not isinstance(name, str) or not _plain_name(name):
            problems.append(f"page {number}: illustration_file {name!r} must be a plain file name")
            continue
        if name in reserved:
            problems.append(
                f"page {number}: illustration_file {name!r} is reserved for the cover or nook"
                " pictures; give the page its own file name"
            )
            continue
        required.setdefault(name, f"illustration for page {number}")
    for name, label in cover_files(book).items():
        required.setdefault(name, label)
    seen: dict[str, str] = {name.casefold(): name for name in NOOK_FILES}
    for name in required:
        other = seen.setdefault(name.casefold(), name)
        if other != name:
            problems.append(
                f"file names {other!r} and {name!r} differ only by case; rename one of them"
            )
    return required, problems


# --- the script and its reviewed mark (AC-4.2-4.4) ------------------------------------------


def script_path(paths: StudioPaths, book: Book) -> Path:
    return folder_of(paths, book).script_path


def character_sheet(paths: StudioPaths, book: Book) -> list[dict]:
    """The book's character sheet (spec 018, AC-3.1): `style_bible.characters`, or [] when the
    book has none or its data cannot be read (the book page reports a broken book already)."""
    try:
        data = json.loads(book.book_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sheet(data) if isinstance(data, dict) else []


def blueprint_status(paths: StudioPaths, book: Book) -> dict:
    """The book's blueprint for the studio (spec 011 FR-8, FR-9): `present`, the load `error`
    (file, field and reason), the parsed `view`, and the `stale` outputs, each with the command
    that refreshes it. Nothing here changes a stage's own status (AC-6.6)."""
    folder = folder_of(paths, book)
    if not folder.blueprint_path.exists():
        return {"present": False, "error": None, "view": None, "stale": []}
    try:
        view = load_blueprint(folder.blueprint_path, load_context(Path(paths.root)))
    except (BlueprintError, ConfigError, OSError, KeyError, ValueError) as exc:
        return {"present": True, "error": str(exc), "view": None, "stale": []}
    sha, used = blueprint_sha256(folder.blueprint_path), read_use(folder)
    stale = []
    if any(folder.illustrations.glob("*.png")) and used.get("art") != sha:
        command = f"python -m video_pipeline.illustrate --book {book.slug} --force"
        stale.append({"output": "pictures", "command": command})
    if script_path(paths, book).exists() and used.get("script") != sha:
        command = f"python -m video_pipeline.generate_script --book {book.slug} --force"
        stale.append({"output": "episode script", "command": command})
    return {"present": True, "error": None, "view": view, "stale": stale}


def reviewed_path(paths: StudioPaths, book: Book) -> Path:
    return folder_of(paths, book).reviewed_path


def _read_dict(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_script(paths: StudioPaths, book: Book) -> dict | None:
    """The episode script, or None when there is none; unreadable → StudioError."""
    path = script_path(paths, book)
    if not path.exists():
        return None
    script = _read_dict(path)
    if script is None:
        raise StudioError(f"episode script is not a readable JSON object: {path}")
    return script


def _no_script(book: Book) -> str:
    return f"no episode script for {book.slug}; generate it first"


def mark_reviewed(paths: StudioPaths, book: Book, by: str | None = None) -> dict:
    """Record that this exact script was reviewed against this exact book file (AC-4.3). `by`
    ("autopilot", spec 021 AC-3.1) records who marked it; a person's mark has no `by`."""
    path = script_path(paths, book)
    if not path.exists():
        raise StudioError(_no_script(book))
    mark = {
        "script_sha256": sv.script_sha256(path),
        "book_sha256": sv.sha256_file(book.book_path),
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if by:
        mark["by"] = by
    write_atomic(reviewed_path(paths, book), mark)
    return mark


def reviewed_mark(paths: StudioPaths, book: Book) -> dict | None:
    """The reviewed mark when it still matches the script and the book file on disk, else None
    (a mark without `book_sha256` predates that check and does not count)."""
    path = script_path(paths, book)
    mark = _read_dict(reviewed_path(paths, book))
    if mark is None or not path.exists() or not book.book_path.is_file():
        return None
    if mark.get("book_sha256") != sv.sha256_file(book.book_path):
        return None
    return mark if mark.get("script_sha256") == sv.script_sha256(path) else None


def reviewed_ok(paths: StudioPaths, book: Book) -> bool:
    """Whether the script on disk is the one that was marked reviewed (AC-4.4)."""
    return reviewed_mark(paths, book) is not None


def feeling_names(paths: StudioPaths) -> tuple[str, ...]:
    """The feelings a line may carry (spec 017); empty when the config cannot be read."""
    try:
        return load_emotions(paths.config_dir).names
    except ConfigError:
        return ()


def script_lines(script: dict) -> list[tuple[str, dict]]:
    """Every line with its id, numbered as voice synthesis does: `intro-NN`, `page-PP-NN`,
    `outro-NN` (spec 002)."""
    lines = [(f"intro-{i:02d}", line) for i, line in enumerate(script["intro"]["lines"], start=1)]
    for page in script["pages"]:
        number = page["page_number"]
        lines += [
            (f"page-{number:02d}-{i:02d}", line) for i, line in enumerate(page["lines"], start=1)
        ]
    lines += [(f"outro-{i:02d}", line) for i, line in enumerate(script["outro"]["lines"], start=1)]
    return lines


EMOTION_FIELD = "emotion:"  # form key prefix of a line's feeling (spec 017 FR-5)


def save_line_edits(paths: StudioPaths, book: Book, edits: dict[str, str]) -> list[str]:
    """Apply `edits` (line id → new text, `emotion:<line id>` → feeling), validate the whole
    script with the pipeline's rules and write it atomically (AC-4.2, FR-5). Returns the
    problems; when there are any nothing is written. A save that changes a line's text or
    feeling clears the reviewed mark (AC-4.3, spec 017 FR-6). Picking neutral removes the
    line's `emotion`."""
    try:
        script = load_script(paths, book)
    except StudioError as exc:
        return [str(exc)]
    if script is None:
        return [_no_script(book)]
    edited = copy.deepcopy(script)
    by_id = dict(script_lines(edited))
    feelings = {
        key.removeprefix(EMOTION_FIELD): value
        for key, value in edits.items()
        if key.startswith(EMOTION_FIELD)
    }
    texts = {key: value for key, value in edits.items() if not key.startswith(EMOTION_FIELD)}
    problems = [
        f"unknown line id: {line_id}" for line_id in [*texts, *feelings] if line_id not in by_id
    ]
    problems += [
        f"{line_id}: {what} must be a string"
        for what, given in (("text", texts), ("emotion", feelings))
        for line_id, value in given.items()
        if line_id in by_id and not isinstance(value, str)
    ]
    if problems:
        return problems
    changed = False
    for line_id, text in texts.items():
        if by_id[line_id].get("text") != text:
            by_id[line_id]["text"] = text
            changed = True
    for line_id, feeling in feelings.items():
        line = by_id[line_id]
        if (line.get("emotion") or NEUTRAL) == (feeling or NEUTRAL):
            continue
        if feeling in ("", NEUTRAL):
            line.pop("emotion", None)
        else:
            line["emotion"] = feeling
        changed = True
    if not changed:
        return []
    try:
        book_data = load_book(book.book_path)
        terms = load_disallowed_terms(paths.config_dir / "disallowed_terms.txt")
        emotions = load_emotions(paths.config_dir)
    except (BookError, ConfigError) as exc:
        return [str(exc)]
    allowed = None
    blueprint_file = folder_of(paths, book).blueprint_path
    if blueprint_file.is_file():
        from video_pipeline.blueprint import BlueprintError, load_blueprint, load_context

        try:
            allowed = set(load_blueprint(blueprint_file, load_context(paths.root)).sound.ambience)
        except BlueprintError as exc:
            return [str(exc)]
    problems = validate_script(edited, book_data, terms, allowed, emotions.names)
    if problems:
        return problems
    write_atomic(script_path(paths, book), edited)
    reviewed_path(paths, book).unlink(missing_ok=True)
    return []


def page_prompt(paths: StudioPaths, book: Book, number: int) -> str | None:
    """Story page `number`'s picture prompt as `book.json` has it, or None."""
    data = _read_dict(book.book_path) or {}
    pages = data.get("pages")
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, dict) and page.get("page_number") == number:
            prompt = page.get("prompt")
            return prompt if isinstance(prompt, str) else None
    return None


def save_page_prompt(paths: StudioPaths, book: Book, number: int, text: str) -> list[str]:
    """Set story page `number`'s picture prompt in `book.json` (found by `page_number`, spec 019
    AC-1.2), validated as all book data is; returns the problems, and writes nothing when there
    are any. The picture is not repainted."""
    data = _read_dict(book.book_path)
    if data is None:
        return [f"book data is not a readable JSON object: {book.book_path}"]
    pages = data.get("pages")
    page = next(
        (
            p
            for p in (pages if isinstance(pages, list) else [])
            if isinstance(p, dict) and p.get("page_number") == number
        ),
        None,
    )
    if page is None:
        return [f"page {number} is not in the book"]
    page["prompt"] = text
    problems = book_problems(data, book.slug)
    if problems:
        return problems
    write_atomic(book.book_path, data)
    return []


# --- stage status (AC-1.2, AC-1.3, AC-5.3, AC-6.2) ------------------------------------------


def _state(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


def paint_state(paths: StudioPaths, book: Book) -> dict:
    """The pictures' `{state, detail, missing}` from the files in `illustrations/` (spec 016
    FR-4): `not_started` while any story page or cover is missing (`missing` names them),
    `out_of_date` when all exist but the blueprint's art mark is stale (spec 011), else `done`."""
    try:
        data = load_book(book.book_path)
    except BookError as exc:
        return {**_state("not_started", str(exc)), "missing": []}
    required, _ = expected_files(data)
    cover_names = cover_files(data)
    have = folder_of(paths, book).illustrations
    missing = [name for name in required if not (have / name).is_file()]
    pages = [name for name in required if name not in cover_names]
    covers = [name for name in required if name in cover_names]
    detail = (
        f"{sum(name not in missing for name in pages)} of {len(pages)} pages, "
        f"{sum(name not in missing for name in covers)} of {len(covers)} covers"
    )
    if missing:
        return {**_state("not_started", detail), "missing": missing}
    stale = blueprint_status(paths, book)["stale"]
    if any(item["output"] == "pictures" for item in stale):
        detail = "made from an older blueprint; repaint all pictures"
        return {**_state("out_of_date", detail), "missing": []}
    return {**_state("done", detail), "missing": []}


def edition_files(paths: StudioPaths, book: Book) -> list[dict]:
    """The files the book builder wrote, sorted by name: `{name, size, mtime}` for every regular
    file directly in the book's `build/` with a .pdf, .epub, .png or .html suffix, except the
    builder's temporary files (names starting with `_` or `.`). No edition name is hardcoded
    (spec 020 renames them); these are also the only files the studio serves from `build/`."""
    out = folder_of(paths, book).build_dir
    if not out.is_dir():
        return []
    found = []
    for path in out.iterdir():
        name = path.name
        if name.startswith(("_", ".")) or path.suffix.lower() not in EDITION_SUFFIXES:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        found.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
    return sorted(found, key=lambda f: f["name"])


def edition_path(paths: StudioPaths, book: Book, name: str) -> Path | None:
    """The edition file called `name`, only when `edition_files` lists it (FR-4)."""
    if not any(f["name"] == name for f in edition_files(paths, book)):
        return None
    return folder_of(paths, book).build_dir / name


READER_FILES = frozenset({"tokens.css", "reader.css", "reader.js", "logo.png"})
READER_FONT_SUFFIXES = (".ttf", ".txt")
_READER_FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def reader_file(paths: StudioPaths, book: Book, name: str, *, font: bool = False) -> Path | None:
    """A file of the web reader in `build/reader/` (spec 023 FR-5): the four reader files by
    name, or a font or licence text in `build/reader/fonts/`; never a symlink or anything else."""
    folder = folder_of(paths, book).build_dir / "reader"
    if font:
        ok = bool(_READER_FONT_NAME.match(name)) and name.lower().endswith(READER_FONT_SUFFIXES)
        folder = folder / "fonts"
    else:
        ok = name in READER_FILES
    path = folder / name
    if not ok or path.is_symlink() or not path.is_file():
        return None
    return path


def _edition_sources(paths: StudioPaths, book: Book) -> list[Path]:
    """What the editions are made from: book.json, the pictures and the blueprint."""
    folder = folder_of(paths, book)
    sources = [book.book_path, folder.blueprint_path]
    if folder.illustrations.is_dir():
        sources += sorted(folder.illustrations.glob("*.png"))
    return [path for path in sources if path.is_file()]


def editions_state(paths: StudioPaths, book: Book) -> dict:
    """The editions' `{state, detail, files, missing}` from files (spec 019 FR-3): `blocked`
    while pictures are missing (named), `not_started` ("not built") with no edition file,
    `out_of_date` when book.json, a picture or the blueprint is newer than the builder's last
    output (the web reader), else `done` ("built")."""
    files = edition_files(paths, book)
    missing = paint_state(paths, book)["missing"]
    if missing:
        detail = f"paint the missing pictures first: {', '.join(missing)}"
        return {**_state("blocked", detail), "files": files, "missing": missing}
    if not files:
        return {**_state("not_started", "not built"), "files": [], "missing": []}
    marker = stage_output(paths, book, BUILD_STAGE)
    built_at = marker.stat().st_mtime if marker.is_file() else min(f["mtime"] for f in files)
    changed = [p.name for p in _edition_sources(paths, book) if p.stat().st_mtime > built_at]
    if changed:
        detail = f"changed since the build: {', '.join(changed[:5])}"
        if len(changed) > 5:
            detail += f" and {len(changed) - 5} more"
        return {**_state("out_of_date", detail), "files": files, "missing": []}
    return {**_state("done", "built"), "files": files, "missing": []}


def _audio_record(paths: StudioPaths, book: Book) -> dict | None:
    return _read_dict(sv.AudioPaths(folder_of(paths, book).build_dir / "audio").status)


def _script_state(paths: StudioPaths, book: Book) -> dict:
    path = script_path(paths, book)
    if not path.exists():
        return _state("not_started", "no episode script yet")
    if _read_dict(path) is None:
        return _state("needs_review", f"episode script is not readable JSON: {path}")
    mark = reviewed_mark(paths, book)
    if mark is not None:
        return _state("done", f"reviewed at {mark.get('reviewed_at')}")
    record = _audio_record(paths, book)
    if record is not None and record.get("script_sha256") == sv.script_sha256(path):
        # Synthesis (CLI --reviewed) was run on exactly this script, so it was reviewed.
        return _state("done", "the audio was made from this script")
    return _state("needs_review", "review the script, then mark it as reviewed")


def _voices_state(paths: StudioPaths, book: Book) -> dict:
    audio = sv.AudioPaths(folder_of(paths, book).build_dir / "audio")
    if not audio.status.exists():
        return _state("not_started", "no synthesized audio yet")
    try:
        record = sv.read_record(audio.status)
        cfg = sv.load_voice_config(paths.config_dir / "voices.json")
        book_data = load_book(book.book_path)
    except (sv.UnreadableRecord, ConfigError, BookError) as exc:
        return _state("out_of_date", str(exc))
    path = script_path(paths, book)
    settings_sha = sv.current_settings_sha(cfg, book_data, book.book_path, path)
    problems = sv._validity_problems(audio, record, path, settings_sha, "re-run synthesis")
    if problems:
        return _state("out_of_date", problems[0])
    if record.get("status") == "approved":
        return _state("approved", f"approved at {record.get('approved_at')}")
    return _state("needs_review", f"synthesized at {record.get('synthesized_at')}; listen to it")


def video_check(paths: StudioPaths, book: Book) -> tuple[str, str]:
    """`render_check_detail` for `book` with the studio's shared page-turn folder: the state and,
    when "blocked", why (an audio problem, or a missing or unreadable picture or input)."""
    return render_check_detail(folder_of(paths, book), paths.root, paths.sfx_dir)


def _video_state(paths: StudioPaths, book: Book) -> dict:
    final = folder_of(paths, book).build_dir / "video" / f"{book.slug}.mp4"
    check, reason = video_check(paths, book)
    if check == "up_to_date":
        return _state("done", str(final))
    if check == "out_of_date":
        return _state("out_of_date", "the inputs changed since the video was rendered")
    if check == "blocked" and final.exists():
        return _state("out_of_date", f"the video cannot be checked: {reason}")
    if check == "blocked":
        return _state("not_started", f"cannot render yet: {reason}")
    return _state("not_started", "not rendered yet")


def stage_output(paths: StudioPaths, book: Book, stage: str) -> Path:
    """The file a successful run of `stage` writes last; a failed or interrupted job only says
    something about the stage when it ended after this file was last written (FR-2)."""
    if stage == "script":
        return script_path(paths, book)
    if stage == "voices":
        return sv.AudioPaths(folder_of(paths, book).build_dir / "audio").status
    if stage == PAINT_STAGE:
        return folder_of(paths, book).illustrations / "cover_back.png"  # painted last
    if stage == BUILD_STAGE:
        return folder_of(paths, book).build_dir / "index.html"  # the web reader is written last
    return VideoPaths(folder_of(paths, book).build_dir / "video").record(book.slug)


def _job_overrides(paths: StudioPaths, book: Book, stage: str, job: dict | None) -> bool:
    """A running job always shows; a failed or interrupted one only when it is newer than the
    stage's output file (or there is none), so files written later (say by the CLI) win."""
    state = job.get("state") if job else None
    if state == "running":
        return True
    if state not in JOB_OVERRIDES:
        return False
    try:
        mtime = stage_output(paths, book, stage).stat().st_mtime
    except OSError:
        return True
    try:
        ended = datetime.fromisoformat(str(job.get("ended_at"))).timestamp()
    except ValueError:
        return False
    return ended > mtime


def _job_state(job: dict) -> dict:
    state = job["state"]
    if state == "running":
        return _state(state, f"started at {job.get('started_at')}")
    if state == "failed":
        return _state(state, f"failed (exit code {job.get('exit_code')})")
    return _state(state, "interrupted when the studio stopped")


def _next_action(status: dict) -> str:
    for stage in (PAINT_STAGE, *STAGES, BUILD_STAGE):
        if status[STATUS_KEYS[stage]]["state"] == "running":
            return f"Wait for {STAGE_LABELS[stage]} to finish"
    script, voices, video = (status[k]["state"] for k in ("script", "voices", "video"))
    if script == "needs_review":
        return "Review script"
    if script != "done":
        # Spec 016: a book with no pictures is painted before its script is generated.
        return "Paint pictures" if status["pictures"]["missing"] else "Generate script"
    if voices == "needs_review":
        return "Listen and approve"
    if voices != "approved":
        return "Synthesize voices"
    if video != "done":
        return "Render video"
    # Spec 019: the editions come after the video, so the flow before it is unchanged.
    return "Watch video" if status["editions"]["state"] == "done" else "Build book"


def stage_status(
    paths: StudioPaths, book: Book, latest_job: LatestJob, autopilot: dict | None = None
) -> dict:
    """Each stage's `{state, detail}` under `script`, `voices`, `video`, `pictures` (spec 016,
    which also lists the `missing` files) and `editions` (spec 019, with the built `files`), plus
    `next_action`.

    States come from the pipeline's own files; the latest job of a stage (`latest_job(slug,
    stage)` with stage in `STAGES`) overrides them while it is running, or when it failed or was
    interrupted after the stage's output file was last written."""
    if book.error:
        status = {key: _state("not_started") for key in ("script", "voices", "video")}
        status["script"] = _state("not_started", book.error)
        status["pictures"] = {**_state("not_started"), "missing": []}
        status["editions"] = {**_state("not_started"), "files": [], "missing": []}
    else:
        status = {
            "script": _script_state(paths, book),
            "voices": _voices_state(paths, book),
            "video": _video_state(paths, book),
            "pictures": paint_state(paths, book),
            "editions": editions_state(paths, book),
        }
    for stage in STAGES:
        job = latest_job(book.slug, stage)
        if job and _job_overrides(paths, book, stage, job):
            status[STATUS_KEYS[stage]] = _job_state(job)
    job = latest_job(book.slug, PAINT_STAGE)
    if job and _job_overrides(paths, book, PAINT_STAGE, job):
        status["pictures"] = {**status["pictures"], **_job_state(job)}  # keeps `missing`
    job = latest_job(book.slug, BUILD_STAGE)
    if job and _job_overrides(paths, book, BUILD_STAGE, job):
        status["editions"] = {**status["editions"], **_job_state(job)}  # keeps `files`
    status["next_action"] = "Fix the book data" if book.error else _next_action(status)
    if autopilot is not None and autopilot.get("state") == "running":  # spec 021
        step = str(autopilot.get("stage") or "")
        key = AUTOPILOT_STEP_STATUS.get(step)
        if key is not None:
            status[key] = {**status[key], **_state("running", f"autopilot: {step}")}
        status["next_action"] = "Wait for autopilot"
    return status


# --- autopilot runs (spec 021 FR-2, AC-4.3) -------------------------------------------------


def autopilot_path(paths: StudioPaths, name: str) -> Path:
    """The run file of book `name` (a slug), or `draft-<id>` before the book exists."""
    return paths.studio_dir / AUTOPILOT_DIR / f"{name}.json"


def _autopilot_files(paths: StudioPaths) -> list[tuple[Path, dict]]:
    folder = paths.studio_dir / AUTOPILOT_DIR
    if not folder.is_dir():
        return []
    return [(p, r) for p in sorted(folder.glob("*.json")) if (r := _read_dict(p)) is not None]


def _live(record: dict | None, current_job: dict | None) -> dict | None:
    """`record` as the studio shows it: a `running` run whose job is not the running job belonged
    to a studio (or a job) that died, and is shown `interrupted` (AC-4.3)."""
    if record is None:
        return None
    if record.get("state") == "running" and (
        current_job is None or current_job.get("id") != record.get("job_id")
    ):
        return {**record, "state": "interrupted"}
    return record


def autopilot_run(paths: StudioPaths, slug: str, current_job: dict | None) -> dict | None:
    """Book `slug`'s last autopilot run, or None when it never ran."""
    return _live(_read_dict(autopilot_path(paths, slug)), current_job)


def autopilot_run_for_draft(
    paths: StudioPaths, draft_id: str, current_job: dict | None
) -> dict | None:
    """The autopilot run started from draft `draft_id`: its draft file, or the book's file once
    the book was created (which names the draft)."""
    record = _read_dict(autopilot_path(paths, f"draft-{draft_id}"))
    if record is None:
        record = next((r for _p, r in _autopilot_files(paths) if r.get("draft") == draft_id), None)
    return _live(record, current_job)


def stop_autopilot_run(paths: StudioPaths, job_id: str) -> dict | None:
    """After the studio stopped autopilot job `job_id`: its run file, written `stopped` if it is
    still `running` (the CLI writes that itself on SIGTERM, but not when it was killed)."""
    for path, record in _autopilot_files(paths):
        if record.get("job_id") != job_id:
            continue
        if record.get("state") == "running":
            now = datetime.now(UTC).isoformat(timespec="seconds")
            record.update(state="stopped", reason="stopped by the producer", finished_at=now,
                          updated_at=now)  # fmt: skip
            write_atomic(path, record)
        return record
    return None


def mark_autopilot_interrupted(paths: StudioPaths) -> int:
    """At studio start, every `running` run belonged to a studio that stopped (one studio per
    project): mark it `interrupted`; nothing resumes by itself (AC-4.3)."""
    count = 0
    for path, record in _autopilot_files(paths):
        if record.get("state") == "running":
            now = datetime.now(UTC).isoformat(timespec="seconds")
            record.update(state="interrupted", updated_at=now)
            write_atomic(path, record)
            count += 1
    return count


# --- animation plans and cuts (spec 009 FR-12) ----------------------------------------------


def _cut_config(paths: StudioPaths) -> dict | None:
    """config/animation.json when its `cut` and `models` blocks are usable, else None (cut state
    then shows as unknown)."""
    config = _read_dict(paths.config_dir / LIMITS_FILE)
    try:
        cut_settings(config)
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    return config


def _manifest(folder: BookFolder, page_number: int) -> dict | None:
    return _read_dict(layers_dir(folder, page_number) / MANIFEST)


def _animation_settings(paths: StudioPaths) -> AnimationSettings | str:
    """The render config's animation block, or why it cannot be read."""
    try:
        return load_render_config(paths.config_dir / "render.json").animation
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        return f"render config unreadable – {exc}"


def _script_pages(paths: StudioPaths, book: Book) -> dict[int, dict]:
    """The episode script's pages by number; none when there is no readable script."""
    try:
        script = load_script(paths, book)
    except StudioError:
        return {}
    pages = (script or {}).get("pages")
    if not isinstance(pages, list):
        return {}
    out = {}
    for page in pages:
        try:
            out[int(page["page_number"])] = page
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _will_animate(
    paths: StudioPaths,
    folder: BookFolder,
    page: dict,
    script_pages: dict[int, dict],
    settings: AnimationSettings | str,
) -> dict:
    """`will_animate` and `still_reason` for a row, as the render decides (spec 010 AC-4.3)."""
    if isinstance(settings, str):
        return {"will_animate": False, "still_reason": settings}
    number = int(page["page_number"])
    script_page = script_pages.get(number, {"page_number": number})
    el = page_eligibility(folder, page, script_page, settings, Path(paths.root))
    return {"will_animate": el.animated, "still_reason": el.reason}


def camera_row(folder: BookFolder, page: dict, plan: object) -> dict:
    """The page's camera grounding (spec 025) for the Animation view: `state` (current, missing,
    stale, failed, or "—" when the page has no plan), and the file's `subject` name, `box`,
    `target`, `band` and the `reason` (the failure, or why the file cannot be used)."""
    row = {"state": NO_CUT, "subject": "", "box": None, "target": None, "band": "", "reason": ""}
    if plan is None:
        return row
    try:
        sha = picture_identity(page_picture(folder, page)).sha256
    except (OSError, ValueError):
        return row
    data, reason = load_current(folder, int(page["page_number"]), sha)
    if data is None:
        # an unreadable or invalid file counts as failed; the reason says why
        state = {"no": "missing", "stale": "stale"}.get(reason.split()[0], "failed")
        return {**row, "state": state, "reason": reason}
    subject = data.get("subject") or {}
    failed = data.get("status") == "failed"
    return {
        "state": "failed" if failed else "current",
        "subject": str(subject.get("name") or ""),
        "box": subject.get("box"),
        "target": data.get("target"),
        "band": data["band"],
        "reason": str(data.get("reason") or ""),
    }


def cleanup_row(folder: BookFolder, page: dict | int) -> dict:
    """The page's artifact cleanup (spec 026) from `<stem>.cleanup.json` and the `.bak`: `state`
    (none without a readable record, review until accepted, then accepted), the record's
    `applied` and `skipped` regions and whether the `.bak` exists."""
    picture = (
        folder.illustrations / f"page_{page:02d}.png"
        if isinstance(page, int)
        else page_picture(folder, page)
    )
    row = {
        "state": "none",
        "applied": [],
        "skipped": [],
        "has_bak": cleanup.bak_path(picture).is_file(),
    }
    try:
        data = json.loads(cleanup.record_path(picture).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return row
    if not isinstance(data, dict):
        return row
    return {
        **row,
        "state": "accepted" if data.get("reviewed") is True else "review",
        "applied": [a for a in data.get("applied") or [] if isinstance(a, dict)],
        "skipped": [a for a in data.get("skipped") or [] if isinstance(a, dict)],
    }


def page_animation(paths: StudioPaths, book: Book) -> list[dict]:
    """Per story page, read from disk on every call (AC-5.5): `page_number`, `plan_state` (none,
    ok, stale, invalid, no_picture, or unavailable when the animation config cannot be read),
    `detail`, `plan`, `cut_state` ("cut", "needs cut" or "—"), the manifest's `empty_layers`,
    and whether the render will animate the page (`will_animate`, else `still_reason`, spec 010
    AC-4.3; the script's per-page override counts when there is a script)."""
    if book.error:
        return []
    folder = folder_of(paths, book)
    try:
        pages = load_book(book.book_path)["pages"]
    except BookError:
        return []
    settings = _animation_settings(paths)
    script_pages = _script_pages(paths, book)
    try:
        limits = load_limits(paths.config_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        detail = f"{paths.config_dir / LIMITS_FILE}: {exc}"
        return [
            {
                "page_number": int(p["page_number"]),
                "picture": page_picture(folder, p).name,
                "plan_state": "unavailable",
                "detail": detail,
                "plan": None,
                "cut_state": NO_CUT,
                "empty_layers": [],
                "camera": camera_row(folder, p, None),
                "cleanup": cleanup_row(folder, p),
                **_will_animate(paths, folder, p, script_pages, settings),
            }
            for p in sorted(pages, key=lambda p: int(p["page_number"]))
        ]
    config = _cut_config(paths)
    rows = []
    for page in sorted(pages, key=lambda p: int(p["page_number"])):
        number = int(page["page_number"])
        st = plan_state(folder, page, limits)
        cut = NO_CUT
        if st.state == "ok" and config is not None:
            cut = cut_state(folder, st.plan, config)[0]
        manifest = _manifest(folder, number) if cut == "cut" else None
        empty = [
            str(e.get("name"))
            for e in (manifest or {}).get("layers") or []
            if isinstance(e, dict) and e.get("empty")
        ]
        rows.append(
            {
                "page_number": number,
                "picture": page_picture(folder, page).name,
                "plan_state": st.state,
                "detail": st.detail,
                "plan": st.plan,
                "cut_state": cut,
                "empty_layers": empty,
                "camera": camera_row(folder, page, st.plan),
                "cleanup": cleanup_row(folder, page),
                **_will_animate(paths, folder, page, script_pages, settings),
            }
        )
    return rows


def animation_summary(rows: list[dict]) -> str:
    """E.g. "12 of 20 plans, 8 cut"; plans are pages with a plan file in any state."""
    planned = sum(r["plan_state"] not in ("none", "unavailable") for r in rows)
    cut = sum(r["cut_state"] == "cut" for r in rows)
    return f"{planned} of {len(rows)} plans, {cut} cut"


def animation_attention(rows: list[dict]) -> dict:
    """What the book page lists under the summary (AC-5.1), compactly: the pages whose plan is
    stale, invalid or has no picture (with the message), the page numbers with no plan, and the
    config problem once when the animation config cannot be read. ok pages are not listed."""
    unavailable = next((r["detail"] for r in rows if r["plan_state"] == "unavailable"), "")
    return {
        "problems": [r for r in rows if r["plan_state"] in ("stale", "invalid", "no_picture")],
        "unplanned": [r["page_number"] for r in rows if r["plan_state"] == "none"],
        "unavailable": unavailable,
    }


def layer_files(paths: StudioPaths, book: Book, page_number: int) -> dict[str, Path]:
    """The cut files of a page the manifest names (file name → path), existing ones only."""
    folder = folder_of(paths, book)
    manifest = _manifest(folder, page_number)
    if manifest is None:
        return {}
    out = layers_dir(folder, page_number)
    return {n: out / n for n in manifest_files(manifest) if (out / n).is_file()}


def _plan_layer_order(folder: BookFolder, page_number: int) -> dict[str, int]:
    """Layer name → its index in the plan file (the colour the page view gives its box);
    empty when the plan cannot be read."""
    plan = _read_dict(plan_path(folder, page_number)) or {}
    layers = plan.get("layers")
    names = (
        [e.get("name") for e in layers if isinstance(e, dict)] if isinstance(layers, list) else []
    )
    return {name: i for i, name in reversed(list(enumerate(names))) if isinstance(name, str)}


def mask_overlay(paths: StudioPaths, book: Book, page_number: int) -> bytes | None:
    """A PNG of every cut layer's alpha, tinted with its palette colour at its offset, over the
    dimmed page picture (transparent where there is no picture). None when the page is not cut.
    A layer's colour is picked by its index in the plan (by name), as for its box in the page
    view; a layer the plan no longer has keeps its manifest index."""
    folder = folder_of(paths, book)
    manifest = _manifest(folder, page_number)
    if manifest is None:
        return None
    out = layers_dir(folder, page_number)
    background = out / str(manifest.get("background"))
    if not background.is_file():
        return None
    with Image.open(background) as im:
        width, height = im.size
    canvas = np.zeros((height, width, 4), np.float32)
    try:
        page = next(
            p for p in load_book(book.book_path)["pages"] if int(p["page_number"]) == page_number
        )
        with Image.open(page_picture(folder, page)) as im:
            if im.size == (width, height):
                canvas[..., :3] = np.asarray(im.convert("RGB"), np.float32) * MASK_DIM
                canvas[..., 3] = 255
    except (StopIteration, BookError, OSError):
        pass
    entries = [e for e in manifest.get("layers") or [] if isinstance(e, dict)]
    order = _plan_layer_order(folder, page_number)
    for position, entry in enumerate(entries):
        layer_name = entry.get("name")
        index = order.get(layer_name, position) if isinstance(layer_name, str) else position
        name, offset = entry.get("file"), entry.get("offset")
        if not (isinstance(name, str) and _plain_name(name) and (out / name).is_file()):
            continue
        x0, y0 = (int(v) for v in offset)
        with Image.open(out / name) as im:
            alpha = np.asarray(im.convert("RGBA"), np.float32)[..., 3] / 255 * MASK_ALPHA
        h = min(alpha.shape[0], height - y0)
        w = min(alpha.shape[1], width - x0)
        if h <= 0 or w <= 0 or x0 < 0 or y0 < 0:
            continue
        a = alpha[:h, :w, None]
        tint = np.array(MASK_PALETTE[index % len(MASK_PALETTE)], np.float32)
        region = canvas[y0 : y0 + h, x0 : x0 + w]
        region[..., :3] = region[..., :3] * (1 - a) + tint * a
        region[..., 3:] = np.maximum(region[..., 3:], a * 255)
    buf = io.BytesIO()
    Image.fromarray(canvas.round().astype(np.uint8), "RGBA").save(buf, format="PNG")
    return buf.getvalue()


# --- YouTube (spec 024) ---------------------------------------------------------------------

_UPLOADED = re.compile(r"\buploaded (\d{1,3})%")
_VIDEO_WORDS = {
    "not_rendered": "the video is not rendered yet",
    "out_of_date": "the video is out of date; render it again",
    "blocked": "the video cannot be rendered",
}


def upload_percent(log: str | None) -> int | None:
    """The last `uploaded N%` the upload CLI printed, or None before the first one."""
    found = _UPLOADED.findall(log or "")
    return int(found[-1]) if found else None


def _video_refusal(state: str, reason: str) -> str:
    text = _VIDEO_WORDS.get(state, f"the video is {state}")
    return f"{text}: {reason}" if reason else text


def youtube_connection(secrets: Path | None = None) -> dict:
    """The project's channel connection from the local files only (never asks Google):
    `state` is "connected", "not_connected" (a client file is ready) or "not_set_up"."""
    folder = Path(secrets) if secrets is not None else yapi.secrets_dir()
    client_ok, client_reason = yapi.client_status(folder)
    channel = yapi.connected_channel(folder)
    state = "not_connected" if client_ok else "not_set_up"
    if channel is not None:
        state = "connected"
    return {
        "state": state,
        "channel": channel,
        "client_ok": client_ok,
        "client_reason": client_reason,
        "client_file": folder / yapi.CLIENT_FILE,
    }


@dataclass(frozen=True)
class _YouTubeInputs:
    settings: yt.YouTubeSettings
    book: dict
    record: dict | None
    chapters: list[yt.Chapter] | None
    chapter_problem: str | None


def _youtube_inputs(paths: StudioPaths, book: Book) -> _YouTubeInputs:
    folder = folder_of(paths, book)
    settings = yt.load_settings(paths.config_dir)
    book_data = load_book(book.book_path)
    record = _read_dict(VideoPaths(folder.build_dir / "video").record(book.slug))
    chapters, problem = None, None
    if record is None:
        problem = "no rendered video yet: render it first"
    else:
        try:
            chapters = yt.chapters_from_record(record, book_data, settings)
        except yt.YouTubeError as exc:
            problem = str(exc).replace("<slug>", book.slug)
    return _YouTubeInputs(settings, book_data, record, chapters, problem)


def youtube_text_problems(paths: StudioPaths, book: Book, text: dict) -> list[str]:
    """Every FR-3 problem with `text` for this book's rendered video (its chapters included)."""
    inputs = _youtube_inputs(paths, book)
    if inputs.chapters is None:
        return [f"chapters: {inputs.chapter_problem}"]
    terms = load_disallowed_terms(paths.config_dir / "disallowed_terms.txt")
    duration = float(inputs.record.get("duration", 0.0))
    return yt.text_problems(text, inputs.book, inputs.chapters, duration, inputs.settings, terms)


def save_youtube_text(
    paths: StudioPaths, book: Book, title: str, description: str, tags: str, now: datetime
) -> list[str]:
    """Save the producer's edit of the YouTube text (AC-2.4): the same FR-3 checks as Codex's
    drafts; with any problem nothing is written and every problem is returned."""
    ypaths = yt.YouTubePaths(folder_of(paths, book))
    old = yt.read_text(ypaths.text) or {}
    text = {
        **old,
        "title": title.strip(),
        "description": description.strip(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "written_by": "producer",
        "written_at": now.astimezone(UTC).isoformat(timespec="seconds"),
    }
    problems = youtube_text_problems(paths, book, text)
    if not problems:
        yt.write_text(ypaths, text, keep_backup=False)
    return problems


def youtube_panel(
    paths: StudioPaths, book: Book, upload_log: str | None = None, secrets: Path | None = None
) -> dict:
    """The book page's YouTube panel (spec 024 FR-6): connection, text and its problems, the
    composed description, the upload state and record, why writing or uploading cannot start
    (upload: connection, then video, then text; AC-3.6, AC-3.7) and the confirmation summary
    (AC-3.1, AC-4.2). `upload_log` is the latest upload job's log, for its percentage."""
    connection = youtube_connection(secrets)
    channel = connection["channel"]
    panel = {
        "connection": connection["state"],
        "channel": channel,
        "text": None,
        "text_problems": [],
        "description": None,
        "upload_state": "none",
        "last_upload": None,
        "uploads_count": 0,
        "write_disabled": None,
        "upload_disabled": None,
        "summary": [],
        "percent": upload_percent(upload_log),
        "error": None,
    }
    if book.error:
        reason = f"the book data has a problem: {book.error}"
        return {**panel, "write_disabled": reason, "upload_disabled": reason}
    state, why = video_check(paths, book)
    video_reason = None if state == "up_to_date" else _video_refusal(state, why)
    ypaths = yt.YouTubePaths(folder_of(paths, book))
    try:
        inputs = _youtube_inputs(paths, book)
        text = yt.read_text(ypaths.text)
        uploads = yt.read_uploads(ypaths)
    except (yt.YouTubeError, ConfigError, BookError) as exc:
        reason = f"the YouTube files cannot be read: {exc}"
        return {**panel, "error": str(exc), "write_disabled": reason, "upload_disabled": reason}
    panel.update(
        text=text,
        uploads_count=len(uploads),
        last_upload=uploads[-1] if uploads else None,
        upload_state=yt.upload_state(ypaths, inputs.record),
        write_disabled=video_reason,
    )
    if text is not None:
        panel["text_problems"] = youtube_text_problems(paths, book, text)
        body = text.get("description")
        if inputs.chapters is not None and isinstance(body, str):
            panel["description"] = yt.compose_description(body, inputs.chapters, inputs.settings)
    if connection["state"] != "connected":
        panel["upload_disabled"] = "Connect a channel on the YouTube page first"
    elif video_reason:
        panel["upload_disabled"] = video_reason
    elif text is None:
        panel["upload_disabled"] = "Write YouTube text first"
    elif panel["text_problems"]:
        panel["upload_disabled"] = "Fix the YouTube text first"
    video = folder_of(paths, book).build_dir / "video" / f"{book.slug}.mp4"
    if channel is not None and text is not None and video.is_file():
        try:
            category = yt.category_for(inputs.book, text, inputs.settings)
        except yt.YouTubeError as exc:
            category = str(exc)
        size_mb = video.stat().st_size / (1024 * 1024)
        summary = [
            f"Channel: {channel.title}",
            f"Title: {text.get('title', '')}",
            "Made for kids: yes",
            "Privacy: Private",
            f"Category: {category}",
            f"File: {video.name} ({size_mb:.1f} MB)",
        ]
        if panel["last_upload"] is not None:
            last = panel["last_upload"]
            first = last.get("url") or yt.video_url(str(last.get("video_id", "")))
            summary.append(f"This makes a second video; the first is {first}")
        panel["summary"] = summary
    return panel
