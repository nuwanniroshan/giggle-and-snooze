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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from video_pipeline import synthesize_voices as sv
from video_pipeline.animate import page_eligibility
from video_pipeline.contract import ConfigError, load_disallowed_terms, validate_script
from video_pipeline.cut_layers import (
    MANIFEST,
    cut_settings,
    cut_state,
    layers_dir,
    manifest_files,
)
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BOOK_FILE, PROJECT_ROOT, BookFolder, list_book_slugs
from video_pipeline.motion_plan import (
    LIMITS_FILE,
    load_limits,
    page_picture,
    plan_path,
    plan_state,
)
from video_pipeline.render_episode import VideoPaths, render_check_detail
from video_pipeline.timeline import AnimationSettings, load_render_config

COVER_FILES = {"cover_front.png": "front cover", "cover_back.png": "back cover"}
NOOK_FILES = ("intro_nook.png", "outro_nook.png")
STAGES = ("script", "voices", "render")
STATUS_KEYS = {"script": "script", "voices": "voices", "render": "video"}
STAGE_LABELS = {
    "script": "script generation",
    "voices": "voice synthesis",
    "render": "video render",
    "plan": "motion planning",
    "cut": "layer cutting",
    "preview": "page preview",
}
# Spec 009: run as studio jobs, but outside the script → voices → render flow.
ANIMATION_STAGES = ("plan", "cut")
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


# --- the script and its reviewed mark (AC-4.2-4.4) ------------------------------------------


def script_path(paths: StudioPaths, book: Book) -> Path:
    return folder_of(paths, book).script_path


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


def mark_reviewed(paths: StudioPaths, book: Book) -> dict:
    """Record that this exact script was reviewed against this exact book file (AC-4.3)."""
    path = script_path(paths, book)
    if not path.exists():
        raise StudioError(_no_script(book))
    mark = {
        "script_sha256": sv.script_sha256(path),
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
    return mark if mark.get("script_sha256") == sv.script_sha256(path) else None


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
