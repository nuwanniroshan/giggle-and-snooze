"""Books, uploads, the script review mark, line edits and stage status (spec 004, Phase 1).

No web code: the app layer calls these. State is derived from the files the pipeline already
writes (FR-2); the only new file is `episodes/<slug>/reviewed.json`.

Book layout: the pilot is `<root>/book_data.json` with pictures in `<root>/assets/illustrations/`.
An added book is `<root>/books/<slug>/book_data.json` with pictures in
`<root>/books/<slug>/illustrations/`, so its render assets folder is `books/<slug>`; its uploaded
nooks go to `episodes/<slug>/`.
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import ConfigError, load_disallowed_terms, slugify, validate_script
from video_pipeline.episode import BookError, book_problems, load_book
from video_pipeline.episode_tools import ROOT, write_atomic
from video_pipeline.render_episode import VideoPaths, render_check_detail

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 100  # pictures per upload; the request is refused above (this + 1) x 25 MB
BOOK_FILE = "book_data.json"
COVER_FILES = {"cover_front.png": "front cover", "cover_back.png": "back cover"}
NOOK_FILES = ("intro_nook.png", "outro_nook.png")
# Slugs a book may not take because a studio route already uses that path (`/books/new`).
RESERVED_SLUGS = frozenset({"new"})
STAGES = ("script", "voices", "render")
STATUS_KEYS = {"script": "script", "voices": "voices", "render": "video"}
STAGE_LABELS = {
    "script": "script generation",
    "voices": "voice synthesis",
    "render": "video render",
}
JOB_OVERRIDES = ("running", "failed", "interrupted")

LatestJob = Callable[[str, str], dict | None]


class StudioError(Exception):
    """A studio request was refused; the message says why."""


@dataclass(frozen=True)
class StudioPaths:
    """Where the studio finds books and pipeline files; `root` is the project folder."""

    root: Path = ROOT

    @property
    def books_dir(self) -> Path:
        return Path(self.root) / "books"

    @property
    def episodes_dir(self) -> Path:
        return Path(self.root) / "episodes"

    @property
    def build_dir(self) -> Path:
        return Path(self.root) / "build"

    @property
    def config_dir(self) -> Path:
        return Path(self.root) / "config"

    @property
    def studio_dir(self) -> Path:
        return self.build_dir / "studio"

    @property
    def pilot_book(self) -> Path:
        return Path(self.root) / BOOK_FILE

    @property
    def pilot_assets(self) -> Path:
        return Path(self.root) / "assets"

    @property
    def sfx_dir(self) -> Path:
        return self.pilot_assets / "audio_sfx"


@dataclass(frozen=True)
class Book:
    """A book the studio knows. `error` is set (and the book not runnable) when its data is
    broken; such a book is listed by its folder name."""

    slug: str
    title: str
    book_path: Path
    assets_dir: Path
    is_pilot: bool
    error: str | None = None


# --- the registry (FR-1) --------------------------------------------------------------------


def _pilot_slug(paths: StudioPaths) -> str | None:
    try:
        return slugify(load_book(paths.pilot_book)["metadata"]["title"])
    except BookError:
        return None


def list_books(paths: StudioPaths) -> list[Book]:
    """The pilot (when its data file exists) and every `books/*/book_data.json`, by title."""
    found: list[Book] = []
    pilot_slug = None
    if paths.pilot_book.exists():
        try:
            title = load_book(paths.pilot_book)["metadata"]["title"].strip()
        except BookError as exc:
            found.append(
                Book("pilot", "pilot", paths.pilot_book, paths.pilot_assets, True, str(exc))
            )
        else:
            pilot_slug = slugify(title)
            found.append(Book(pilot_slug, title, paths.pilot_book, paths.pilot_assets, True))
    if paths.books_dir.is_dir():
        for folder in sorted(paths.books_dir.iterdir()):
            book_file = folder / BOOK_FILE
            if folder.name.startswith(".") or not folder.is_dir() or not book_file.is_file():
                continue
            try:
                title = load_book(book_file)["metadata"]["title"].strip()
            except BookError as exc:
                found.append(Book(folder.name, folder.name, book_file, folder, False, str(exc)))
                continue
            slug, error = slugify(title), None
            if slug != folder.name:
                error = f"folder {folder.name!r} does not match the book's title slug {slug!r}"
            elif slug == pilot_slug:
                error = f"{slug!r} is the pilot book's slug"
            found.append(Book(folder.name, title, book_file, folder, False, error))
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


def expected_files(book: dict) -> tuple[dict[str, str], list[str]]:
    """Required upload names (name → what it is) for `book`, and problems with the names the book
    data gives. A page without `illustration_file` expects `page_PP.png`, as render does.

    A page may not use a cover or nook name (those files are stored elsewhere or mean something
    else), and no two names may differ only by case (the Mac's file system would merge them)."""
    required: dict[str, str] = {}
    problems: list[str] = []
    reserved = {*COVER_FILES, *NOOK_FILES}
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
    for name, label in COVER_FILES.items():
        required.setdefault(name, label)
    seen: dict[str, str] = {name.casefold(): name for name in NOOK_FILES}
    for name in required:
        other = seen.setdefault(name.casefold(), name)
        if other != name:
            problems.append(
                f"file names {other!r} and {name!r} differ only by case; rename one of them"
            )
    return required, problems


def slug_problem(slug: str) -> str | None:
    """Why `slug` cannot name an added book (it is a studio route), or None."""
    if slug in RESERVED_SLUGS:
        return f"the slug {slug!r} is reserved by the studio (it comes from the title); change the title"
    return None


def _image_problem(name: str, data: bytes) -> str | None:
    if len(data) > MAX_UPLOAD_BYTES:
        limit = MAX_UPLOAD_BYTES / (1024 * 1024)
        return f"{name}: file is too large ({len(data) / (1024 * 1024):.1f} MB; limit {limit:g} MB)"
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError) as exc:
        return f"{name}: not a readable image ({exc})"
    return None


def validate_upload(book_json: bytes, files: dict[str, bytes]) -> tuple[dict | None, list[str]]:
    """Parse and check an upload without saving anything: the book data (the same rules as
    `episode.load_book`), every expected illustration and both covers present and readable, no
    unexpected or path-like names, no file over `MAX_UPLOAD_BYTES`. Returns the parsed book (None
    when it is not a JSON object) and every problem found."""
    if len(book_json) > MAX_UPLOAD_BYTES:
        return None, [f"book data is larger than {MAX_UPLOAD_BYTES / (1024 * 1024):g} MB"]
    try:
        book = json.loads(book_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"book data is not valid JSON ({exc})"]
    problems = book_problems(book, "book data")
    if not isinstance(book, dict):
        return None, problems
    metadata = book.get("metadata")
    title = metadata.get("title") if isinstance(metadata, dict) else None
    if isinstance(title, str) and (problem := slug_problem(slugify(title.strip()))):
        problems.append(problem)
    required, name_problems = expected_files(book)
    problems += name_problems
    for name, data in files.items():
        if not _plain_name(name):
            problems.append(f"file name not allowed: {name!r}")
        elif name not in required and name not in NOOK_FILES:
            problems.append(f"unexpected file: {name!r} (the book data does not use it)")
        elif problem := _image_problem(name, data):
            problems.append(problem)
    for name, label in required.items():
        if name not in files:
            problems.append(f"missing {label}: {name}")
    return book, problems


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)  # noqa: PTH105 - atomic replace
    finally:
        tmp.unlink(missing_ok=True)


def save_book(
    paths: StudioPaths, book: dict, files: dict[str, bytes], replace: bool = False
) -> Book:
    """Store a validated upload as `books/<slug>/` (AC-2.3) and its nooks in `episodes/<slug>/`
    (AC-2.5). Refuses the pilot's slug, a reserved slug, and an existing slug unless `replace`
    (AC-2.4). Replacing a book deletes its reviewed mark: the script was reviewed against the
    old book.

    The book is written to a temporary folder first, then swapped in: the old folder is moved
    aside, the new one renamed into place, and the old one deleted."""
    _parsed, problems = validate_upload(json.dumps(book).encode("utf-8"), files)
    if problems:
        raise StudioError("; ".join(problems))
    title = book["metadata"]["title"].strip()
    slug = slugify(title)
    if slug == _pilot_slug(paths):
        raise StudioError(f"{slug!r} is the pilot book; it cannot be added or replaced here")
    target = paths.books_dir / slug
    if target.exists() and not replace:
        raise StudioError(f"a book {slug!r} already exists; confirm to replace it")
    paths.books_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:8]
    new, old = paths.books_dir / f".{slug}.new-{token}", paths.books_dir / f".{slug}.old-{token}"
    try:
        (new / "illustrations").mkdir(parents=True)
        (new / BOOK_FILE).write_text(
            json.dumps(book, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for name, data in files.items():
            if name not in NOOK_FILES:
                (new / "illustrations" / name).write_bytes(data)
        if target.exists():
            os.replace(target, old)  # noqa: PTH105
        try:
            os.replace(new, target)  # noqa: PTH105
        except OSError:
            if old.exists():
                os.replace(old, target)  # noqa: PTH105 - put the previous book back
            raise
    finally:
        shutil.rmtree(new, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    saved = Book(slug, title, target / BOOK_FILE, target, False)
    reviewed_path(paths, saved).unlink(missing_ok=True)
    for name in NOOK_FILES:
        if name in files:
            _write_bytes_atomic(paths.episodes_dir / slug / name, files[name])
    return saved


# --- the script and its reviewed mark (AC-4.2-4.4) ------------------------------------------


def script_path(paths: StudioPaths, book: Book) -> Path:
    return paths.episodes_dir / book.slug / "episode_script.json"


def reviewed_path(paths: StudioPaths, book: Book) -> Path:
    return paths.episodes_dir / book.slug / "reviewed.json"


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


def mark_reviewed(paths: StudioPaths, book: Book) -> dict:
    """Record that this exact script was reviewed against this exact book file (AC-4.3)."""
    path = script_path(paths, book)
    if not path.exists():
        raise StudioError(_no_script(book))
    mark = {
        "script_sha256": sv.sha256_file(path),
        "book_sha256": sv.sha256_file(book.book_path),
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
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
    return mark if mark.get("script_sha256") == sv.sha256_file(path) else None


def reviewed_ok(paths: StudioPaths, book: Book) -> bool:
    """Whether the script on disk is the one that was marked reviewed (AC-4.4)."""
    return reviewed_mark(paths, book) is not None


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


def save_line_edits(paths: StudioPaths, book: Book, edits: dict[str, str]) -> list[str]:
    """Apply `edits` (line id → new text), validate the whole script with the pipeline's rules
    and write it atomically (AC-4.2, FR-5). Returns the problems; when there are any nothing is
    written. A save that changes a line clears the reviewed mark (AC-4.3)."""
    try:
        script = load_script(paths, book)
    except StudioError as exc:
        return [str(exc)]
    if script is None:
        return [_no_script(book)]
    edited = copy.deepcopy(script)
    by_id = dict(script_lines(edited))
    problems = [f"unknown line id: {line_id}" for line_id in edits if line_id not in by_id]
    problems += [
        f"{line_id}: text must be a string"
        for line_id, text in edits.items()
        if line_id in by_id and not isinstance(text, str)
    ]
    if problems:
        return problems
    changed = False
    for line_id, text in edits.items():
        if by_id[line_id].get("text") != text:
            by_id[line_id]["text"] = text
            changed = True
    if not changed:
        return []
    try:
        book_data = load_book(book.book_path)
        terms = load_disallowed_terms(paths.config_dir / "disallowed_terms.txt")
    except (BookError, ConfigError) as exc:
        return [str(exc)]
    problems = validate_script(edited, book_data, terms)
    if problems:
        return problems
    write_atomic(script_path(paths, book), edited)
    reviewed_path(paths, book).unlink(missing_ok=True)
    return []


# --- stage status (AC-1.2, AC-1.3, AC-5.3, AC-6.2) ------------------------------------------


def _state(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


def _audio_record(paths: StudioPaths, book: Book) -> dict | None:
    return _read_dict(sv.AudioPaths(paths.build_dir / book.slug / "audio").status)


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
    if record is not None and record.get("script_sha256") == sv.sha256_file(path):
        # Synthesis (CLI --reviewed) was run on exactly this script, so it was reviewed.
        return _state("done", "the audio was made from this script")
    return _state("needs_review", "review the script, then mark it as reviewed")


def _voices_state(paths: StudioPaths, book: Book) -> dict:
    audio = sv.AudioPaths(paths.build_dir / book.slug / "audio")
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
    return render_check_detail(
        book.book_path,
        paths.episodes_dir,
        paths.build_dir,
        paths.config_dir,
        book.assets_dir,
        paths.sfx_dir,
    )


def _video_state(paths: StudioPaths, book: Book) -> dict:
    final = paths.build_dir / book.slug / "video" / f"{book.slug}.mp4"
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
        return sv.AudioPaths(paths.build_dir / book.slug / "audio").status
    return VideoPaths(paths.build_dir / book.slug / "video").record(book.slug)


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
    for stage in STAGES:
        if status[STATUS_KEYS[stage]]["state"] == "running":
            return f"Wait for {STAGE_LABELS[stage]} to finish"
    script, voices, video = (status[k]["state"] for k in ("script", "voices", "video"))
    if script == "needs_review":
        return "Review script"
    if script != "done":
        return "Generate script"
    if voices == "needs_review":
        return "Listen and approve"
    if voices != "approved":
        return "Synthesize voices"
    return "Watch video" if video == "done" else "Render video"


def stage_status(paths: StudioPaths, book: Book, latest_job: LatestJob) -> dict:
    """Each stage's `{state, detail}` under `script`, `voices`, `video`, plus `next_action`.

    States come from the pipeline's own files; the latest job of a stage (`latest_job(slug,
    stage)` with stage in `STAGES`) overrides them while it is running, or when it failed or was
    interrupted after the stage's output file was last written."""
    if book.error:
        status = {key: _state("not_started") for key in ("script", "voices", "video")}
        status["script"] = _state("not_started", book.error)
    else:
        status = {
            "script": _script_state(paths, book),
            "voices": _voices_state(paths, book),
            "video": _video_state(paths, book),
        }
    for stage in STAGES:
        job = latest_job(book.slug, stage)
        if job and _job_overrides(paths, book, stage, job):
            status[STATUS_KEYS[stage]] = _job_state(job)
    status["next_action"] = "Fix the book data" if book.error else _next_action(status)
    return status
