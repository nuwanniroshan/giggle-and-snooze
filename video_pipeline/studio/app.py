"""The studio web app (spec 004, Phase 3): pages, stage controls, live logs and media.

Server-rendered Jinja pages (autoescaped) plus a small script that polls the job endpoints for
live progress; everything except live updating works without JavaScript. The app only binds the
loopback address and answers only requests addressed to it by a loopback name (Host header), so a
DNS-rebinding page cannot read it. POSTs carrying an Origin or Referer from another site are
refused, so a page on the web cannot drive the studio through the browser.

Handlers that touch files or wait on a job are plain functions, which Starlette runs in its
thread pool; the few that must await the request body hand the rest to the pool too, so a slow
stop or a page full of file hashing never holds up the polling endpoints.

Because handlers run concurrently in that pool, every check-then-act (busy or gate check, then
start a stage; no job running, then write the book, the script, the reviewed mark or the audio
approval) happens under one `Studio.write_lock`, so a stage cannot start between a handler's check
and its write, and no write can land between a stage's gate check and its start (review fixes,
round 2). Only the check and that one write sit inside the lock; stopping a job and page views do
not take it.

All state comes from `books` (pipeline files) and the `JobRunner`; the gates are the pipeline's
own: voices need the reviewed script, render needs approved audio (FR-4), a page preview needs
that page's voice track (spec 010 FR-15).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import socket
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import ConfigError
from video_pipeline.drafts import DraftError, DraftStore
from video_pipeline.episode import BookError, load_book
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.render_episode import VideoPaths
from video_pipeline.studio import books as sb
from video_pipeline.studio.jobs import Busy, JobRunner, StudioLocked, UnknownJob, stage_command
from video_pipeline.timeline import page_section_name

STUDIO_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
DEFAULT_PORT = 8765
LOG_TAIL_CHARS = 20_000
STATE_WORDS = {
    "not_started": "NOT STARTED",
    "running": "RUNNING",
    "done": "DONE",
    "needs_review": "NEEDS REVIEW",
    "approved": "APPROVED",
    "failed": "FAILED",
    "interrupted": "INTERRUPTED",
    "out_of_date": "OUT OF DATE",
    "stopped": "STOPPED",
    "pending review": "PENDING REVIEW",
}
STAGE_BUTTONS = {
    "script": "Generate script",
    "voices": "Synthesize voices",
    "render": "Render video",
    "plan": "Plan pictures",
    "cut": "Cut layers",
}
MASK_CACHE_SIZE = 32
_LAYER_NAME = re.compile(r"^page_(\d+)_(.+)$")
_MASK_NAME = re.compile(r"^page_(\d+)\.png$")
_PREVIEW_NAME = re.compile(r"^page_(\d{2,})\.mp4$")
_PROBLEM_LINE = re.compile(r"^(intro|outro) line (\d+)\b")
_PROBLEM_PAGE_LINE = re.compile(r"^page (\d+) (narration|Q&A) line (\d+)\b")
_PROBLEM_PAGE = re.compile(r"^page (\d+)\b")

CommandFor = Callable[..., list[str]]


def static_url(name: str) -> str:
    """A /static URL tagged with the file's modification time, so browsers drop stale copies."""
    path = STUDIO_DIR / "static" / name
    version = int(path.stat().st_mtime) if path.is_file() else 0
    return f"/static/{name}?v={version}"


def format_duration(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` from an hour."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _elapsed(job: dict) -> float:
    try:
        start = datetime.fromisoformat(job["started_at"])
        end = datetime.fromisoformat(job["ended_at"]) if job.get("ended_at") else datetime.now(UTC)
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max((end - start).total_seconds(), 0.0)


def _state_word(state: str | None) -> str:
    return STATE_WORDS.get(state or "", (state or "").upper())


# --- pipeline facts the pages and gates need ---------------------------------------------------


def _audio_paths(paths: sb.StudioPaths, book: sb.Book) -> sv.AudioPaths:
    return sv.AudioPaths(sb.folder_of(paths, book).build_dir / "audio")


def _video_paths(paths: sb.StudioPaths, book: sb.Book) -> VideoPaths:
    return VideoPaths(sb.folder_of(paths, book).build_dir / "video")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _approval_problems(paths: sb.StudioPaths, book: sb.Book) -> list[str]:
    """The render gate, exactly as render applies it (spec 003)."""
    try:
        cfg = sv.load_voice_config(paths.config_dir / "voices.json")
        book_data = load_book(book.book_path)
    except (ConfigError, BookError) as exc:
        return [str(exc)]
    script = sb.script_path(paths, book)
    settings_sha = sv.current_settings_sha(cfg, book_data, book.book_path, script)
    return sv.approval_problems(_audio_paths(paths, book), script, settings_sha, book.book_path)


def _script_reviewed(paths: sb.StudioPaths, book: sb.Book) -> bool:
    """Reviewed mark matches the script, or the audio was synthesized from exactly this script
    (which the command line only does with `--reviewed`), as `books.stage_status` decides."""
    script = sb.script_path(paths, book)
    if not script.exists():
        return False
    if sb.reviewed_ok(paths, book):
        return True
    record = _read_json(_audio_paths(paths, book).status)
    return record is not None and record.get("script_sha256") == sv.script_sha256(script)


def _track_names(audio: sv.AudioPaths) -> list[str]:
    """Tracks named by timing files, in episode order (intro, pages, outro)."""
    if not audio.tracks.is_dir():
        return []
    names = sorted(p.stem for p in audio.tracks.glob("*.json") if not p.name.endswith(".tmp"))
    order = {"intro": 0, "outro": 2}
    return sorted(names, key=lambda n: (order.get(n, 1), n))


def _problem_target(problem: str, page_lines: dict[int, list[dict]]) -> str | None:
    """The line id (or `page-PP` for page-level problems) a validation problem is about."""
    if m := _PROBLEM_LINE.match(problem):
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    if m := _PROBLEM_PAGE_LINE.match(problem):
        number, kind, index = int(m.group(1)), m.group(2), int(m.group(3))
        if kind == "Q&A":
            lines = page_lines.get(number, [])
            split = 0
            while split < len(lines) and lines[split].get("role") == "narration":
                split += 1
            index += split
        return f"page-{number:02d}-{index:02d}"
    if m := _PROBLEM_PAGE.match(problem):
        return f"page-{int(m.group(1)):02d}"
    return None


# --- the app -----------------------------------------------------------------------------------


class Studio:
    """Request handlers bound to one studio root, job runner and stage command builder."""

    def __init__(
        self, paths: sb.StudioPaths, runner: JobRunner, command_for: CommandFor, port: int
    ) -> None:
        self.paths = paths
        self.runner = runner
        self.command_for = command_for
        self.write_lock = threading.Lock()  # serialises every check-then-act (module docstring)
        self.masks: dict[tuple[str, int, str], bytes] = {}  # (slug, page, cut_hash) → PNG
        self.masks_lock = threading.Lock()
        self.drafts = DraftStore(paths.root)
        self.allowed_origins = {f"http://{HOST}:{port}", f"http://localhost:{port}"}
        self.templates = Jinja2Templates(directory=STUDIO_DIR / "templates")
        self.templates.env.autoescape = True
        self.templates.env.globals.update(
            state_word=_state_word,
            stage_labels=sb.STAGE_LABELS,
            format_duration=format_duration,
            static_url=static_url,
        )

    # --- helpers --------------------------------------------------------------------------------

    def running(self) -> dict | None:
        job = self.runner.current()
        if job is None:
            return None
        if job["stage"] in ("ideas", "candidate"):
            title = "story ideas"
        elif job["stage"] == "book":
            draft = self.drafts.get(job["book"])
            title = draft["idea"]["title"] if draft else "a draft book"
        else:
            book = sb.get_book(self.paths, job["book"])
            title = book.title if book else job["book"]
        return {
            **job,
            "title": title,
            "label": sb.STAGE_LABELS.get(job["stage"], job["stage"]),
            "elapsed": _elapsed(job),
        }

    def render(self, request: Request, name: str, context: dict, status: int = 200) -> Response:
        return self.templates.TemplateResponse(
            request, name, {"running": self.running(), **context}, status_code=status
        )

    def message(self, request: Request, title: str, lines: list[str], status: int, back=None):
        return self.render(
            request, "message.html", {"title": title, "lines": lines, "back": back}, status
        )

    def not_found(self, request: Request) -> Response:
        return self.message(request, "Not found", ["There is nothing here."], 404, "/")

    def book_or_none(self, request: Request) -> sb.Book | None:
        return sb.get_book(self.paths, request.path_params["slug"])

    def status_of(self, book: sb.Book) -> dict:
        return sb.stage_status(self.paths, book, self.runner.latest)

    def busy_reason(self) -> str | None:
        job = self.running()
        if job is None:
            return None
        return f"{job['label']} is running for {job['title']}"

    def gate(self, book: sb.Book, stage: str, page: int | None = None) -> str | None:
        """Why `stage` cannot start for `book` now (None when it can), busy aside. A page
        preview needs that page's voice track, not the audio approval (spec 010 FR-15)."""
        if book.error:
            return f"the book data has a problem: {book.error}"
        if stage == sb.PREVIEW_STAGE:
            audio = _audio_paths(self.paths, book)
            name = page_section_name(int(page or 0))
            if not (audio.track(name).is_file() and audio.track_timing(name).is_file()):
                return f"no voice track for page {page}: synthesize the voices first"
        if stage == "voices":
            if not sb.script_path(self.paths, book).exists():
                return "generate the script first"
            if not _script_reviewed(self.paths, book):
                return "review the script and mark it as reviewed first"
        if stage == "render":
            problems = _approval_problems(self.paths, book)
            if problems:
                return problems[0]
        return None

    def last_job(
        self, book: sb.Book, stages: tuple[str, ...] | None = None, page: int | None = None
    ) -> dict | None:
        stages = stages or sb.STAGES + sb.ANIMATION_STAGES
        jobs = [j for s in stages if (j := self.runner.latest(book.slug, s, page))]
        if not jobs:
            return None
        job = max(jobs, key=lambda j: (str(j.get("started_at") or ""), j["id"]))
        try:
            text, offset = self.runner.log(job["id"], 0)
        except UnknownJob:
            text, offset = "", 0
        live = self.runner.current()
        return {
            **job,
            "label": sb.STAGE_LABELS.get(job["stage"], job["stage"]),
            "elapsed": _elapsed(job),
            "log": text[-LOG_TAIL_CHARS:],
            "offset": offset,
            "live": live is not None and live["id"] == job["id"],
        }

    def same_site(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is not None:
            return origin in self.allowed_origins
        referer = request.headers.get("referer")
        if referer is not None:
            return any(referer == o or referer.startswith(o + "/") for o in self.allowed_origins)
        return True

    # --- pages ----------------------------------------------------------------------------------

    def home(self, request: Request) -> Response:
        rows = [{"book": b, "status": self.status_of(b)} for b in sb.list_books(self.paths)]
        return self.render(request, "home.html", {"rows": rows})

    def book_page(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        busy = self.busy_reason()
        stages = []
        for stage in sb.STAGES:
            stages.append(
                {
                    "stage": stage,
                    "button": STAGE_BUTTONS[stage],
                    "blocked": busy or self.gate(book, stage),
                }
            )
        context = {
            "book": book,
            "status": self.status_of(book),
            "stages": stages,
            "has_script": sb.script_path(self.paths, book).exists(),
            "job": self.last_job(book),
        }
        rows = sb.page_animation(self.paths, book)
        context["animation"] = sb.animation_summary(rows)
        context["animation_attention"] = sb.animation_attention(rows)
        return self.render(request, "book.html", context)

    async def run_stage(self, request: Request) -> Response:
        form = await request.form()
        return await run_in_threadpool(self._run_stage, request, form)

    def _run_stage(self, request: Request, form) -> Response:
        book = self.book_or_none(request)
        stage = request.path_params["stage"]
        if book is None or stage not in sb.STAGES + sb.ANIMATION_STAGES:
            return self.not_found(request)
        back = f"/books/{book.slug}"
        if stage in sb.ANIMATION_STAGES:
            back += "/animation"
        with self.write_lock:
            refused = self._start_stage(book, stage, form)
        if refused is not None:
            title, lines = refused
            return self.message(request, title, lines, 409, back)
        return RedirectResponse(back, status_code=303)

    def _start_stage(
        self, book: sb.Book, stage: str, form, page: int | None = None
    ) -> tuple[str, list[str]] | None:
        """Check and start `stage` (for story `page`, a preview) (call under `write_lock`); the
        refusal's title and lines, or None when it started."""
        if busy := self.busy_reason():
            return "Busy", [f"Refused: {busy}."]
        if reason := self.gate(book, stage, page):
            return f"Cannot start {sb.STAGE_LABELS[stage]}", [reason]
        force = form.get("force") == "1"
        if stage == "script" and sb.script_path(self.paths, book).exists():
            if form.get("confirm") != "1":
                lines = [
                    "An episode script already exists and may have been edited by hand.",
                    "Regenerating overwrites it; confirm to regenerate.",
                ]
                return "Confirm regenerating the script", lines
            force = True
        cmd = self.command_for(stage, book, self.paths, force=force, page=page)
        try:
            self.runner.start(book.slug, stage, cmd, page=page)
        except Busy:
            return "Busy", [f"Refused: {self.busy_reason()}."]
        return None

    def stop_job(self, request: Request) -> Response:
        job = self.runner.current()
        self.runner.stop()
        target = f"/books/{job['book']}" if job else "/"
        return RedirectResponse(target, status_code=303)

    # --- uploads --------------------------------------------------------------------------------

    # --- add a book from an idea (spec 006) ----------------------------------------------------

    def add_book_context(self, problems=None, text: str = "", **extra) -> dict:
        settings = self.drafts.settings()
        candidate = self.drafts.latest_run("candidate")
        if not text and candidate:
            text = candidate.get("request") or ""  # keep the typed idea for "Improve again"
        return {
            "ideas": self.drafts.latest_run("ideas"),
            "candidate": candidate,
            "drafts": self.drafts.list(),
            "problems": problems or [],
            "text": text,
            "settings": settings,
            "author": self.drafts.default_author(),
            **extra,
        }

    def new_book(self, request: Request) -> Response:
        return self.render(request, "add_book.html", self.add_book_context())

    def refuse_upload(self, request: Request) -> Response:
        """The JSON upload is gone (spec 006 AC-4.2): nothing is saved."""
        return self.message(
            request,
            "Upload no longer used",
            ["Books are added from an idea now: use Suggest ideas or Improve idea on this page."],
            410,
            "/books/new",
        )

    def _start_story(self, stage: str, subject: str, job_book: str):
        """Start a story run under the write lock; the refusal's reason or None when started."""
        if reason := self.busy_reason():
            return reason
        try:
            self.runner.start(job_book, stage, self.command_for(stage, subject, self.paths))
        except Busy:
            return self.busy_reason() or "another job is running"
        return None

    async def start_ideas(self, request: Request) -> Response:
        with self.write_lock:
            run = self.drafts.new_run("ideas")
            reason = self._start_story("ideas", run, "_ideas")
            if reason:
                self.drafts.update_run(run, state="failed", problems=[f"Refused: {reason}."])
        if reason:
            return self.render(
                request, "add_book.html", self.add_book_context([f"Refused: {reason}."])
            )
        return RedirectResponse("/books/new", status_code=303)

    async def start_improve(self, request: Request) -> Response:
        form = await request.form()
        text = str(form.get("idea") or "").strip()
        limits = self.drafts.settings()["idea_length"]
        if not limits[0] <= len(text) <= limits[1]:
            problem = (
                f"Describe the idea in {limits[0]} to {limits[1]} characters (it has {len(text)})."
            )
            return self.render(
                request, "add_book.html", self.add_book_context([problem], text), 422
            )
        with self.write_lock:
            run = self.drafts.new_run("candidate")
            self.drafts.update_run(run, request=text)
            reason = self._start_story("candidate", run, "_ideas")
            if reason:
                self.drafts.update_run(run, state="failed", problems=[f"Refused: {reason}."])
        if reason:
            return self.render(
                request, "add_book.html", self.add_book_context([f"Refused: {reason}."], text), 409
            )
        return RedirectResponse("/books/new", status_code=303)

    def _chosen_idea(self, run_id: str, index: str | None) -> tuple[dict | None, str | None]:
        run = self.drafts.read_run(run_id) if run_id else None
        if not run or run.get("state") != "ready" or not run.get("payload"):
            return None, "Choose an idea that has finished writing."
        payload = run["payload"]
        if run["kind"] == "candidate":
            return payload, None
        try:
            return payload["ideas"][int(index)], None
        except (TypeError, ValueError, IndexError, KeyError):
            return None, "Choose one of the ideas listed."

    async def write_book(self, request: Request) -> Response:
        form = await request.form()
        settings = self.drafts.settings()
        low, high = settings["page_range"]
        author = str(form.get("author") or "").strip() or self.drafts.default_author()
        problems = []
        try:
            pages = int(form.get("pages") or settings["default_pages"])
        except ValueError:
            pages = 0
        if not low <= pages <= high:
            problems.append(f"The maximum page count must be {low} to {high}.")
        if not author:
            problems.append("Enter the author name.")
        idea, problem = self._chosen_idea(str(form.get("run") or ""), form.get("index"))
        if problem:
            problems.append(problem)
        if problems:
            return self.render(request, "add_book.html", self.add_book_context(problems), 422)
        with self.write_lock:
            draft = self.drafts.new_draft(idea, pages, author)
            reason = self._start_story("book", draft["id"], draft["id"])
            if reason:
                self.drafts.discard(draft["id"])
        if reason:
            return self.render(
                request, "add_book.html", self.add_book_context([f"Refused: {reason}."]), 409
            )
        return RedirectResponse(f"/drafts/{draft['id']}", status_code=303)

    def draft_or_none(self, request: Request) -> dict | None:
        return self.drafts.get(request.path_params["draft_id"])

    def draft_page(self, request: Request) -> Response:
        draft = self.draft_or_none(request)
        if draft is None:
            return self.not_found(request)
        return self.render(request, "draft.html", {"draft": draft, "problems": []})

    async def save_page(self, request: Request) -> Response:
        draft = self.draft_or_none(request)
        if draft is None:
            return self.not_found(request)
        form = await request.form()
        number = int(request.path_params["number"])
        with self.write_lock:
            problems = self.drafts.update_page_text(
                draft["id"], number, str(form.get("text") or "")
            )
        if problems:
            fresh = self.drafts.get(draft["id"])
            return self.render(request, "draft.html", {"draft": fresh, "problems": problems}, 422)
        return RedirectResponse(f"/drafts/{draft['id']}", status_code=303)

    async def create_book_from_draft(self, request: Request) -> Response:
        draft = self.draft_or_none(request)
        if draft is None:
            return self.not_found(request)
        with self.write_lock:
            if self.busy_reason():
                problems = [f"Refused: {self.busy_reason()}."]
                return self.render(
                    request, "draft.html", {"draft": draft, "problems": problems}, 409
                )
            try:
                slug = self.drafts.create_book(draft["id"])
            except DraftError as exc:
                return self.render(
                    request, "draft.html", {"draft": draft, "problems": [str(exc)]}, 409
                )
        return RedirectResponse(f"/books/{slug}", status_code=303)

    async def discard_draft(self, request: Request) -> Response:
        draft = self.draft_or_none(request)
        if draft is None:
            return self.not_found(request)
        form = await request.form()
        if form.get("confirm") != "yes":
            problems = ["Confirm the discard to delete this draft and its generation log."]
            return self.render(request, "draft.html", {"draft": draft, "problems": problems}, 422)
        with self.write_lock:
            self.drafts.discard(draft["id"])
        return RedirectResponse("/books/new", status_code=303)

    async def write_again(self, request: Request) -> Response:
        draft = self.draft_or_none(request)
        if draft is None:
            return self.not_found(request)
        with self.write_lock:
            if draft["state"] not in ("failed", "interrupted"):
                problems = ["Only a failed or interrupted draft can be written again."]
                return self.render(
                    request, "draft.html", {"draft": draft, "problems": problems}, 409
                )
            if reason := self.busy_reason():
                return self.render(
                    request,
                    "draft.html",
                    {"draft": draft, "problems": [f"Refused: {reason}."]},
                    409,
                )
            self.drafts.update(draft["id"], state="writing", attempts=0, problems=[], book=None)
            reason = self._start_story("book", draft["id"], draft["id"])
            if reason:
                self.drafts.update(draft["id"], state="failed", problems=[f"Refused: {reason}."])
        return RedirectResponse(f"/drafts/{draft['id']}", status_code=303)

    # --- the script -----------------------------------------------------------------------------

    def script_context(self, book: sb.Book, attempted=None, problems=None) -> dict | None:
        try:
            script = sb.load_script(self.paths, book)
        except sb.StudioError as exc:
            return {"error": str(exc)}
        if script is None:
            return None
        attempted = attempted or {}
        problems = problems or []
        try:
            pages_by_number = {p["page_number"]: p for p in load_book(book.book_path)["pages"]}
        except BookError:
            pages_by_number = {}
        page_lines = {p["page_number"]: p.get("lines", []) for p in script.get("pages", [])}
        by_target: dict[str, list[str]] = {}
        for problem in problems:
            target = _problem_target(problem, page_lines)
            if target:
                by_target.setdefault(target, []).append(problem)
        ids = iter(sb.script_lines(script))

        def entries(lines):
            out = []
            for _line in lines:
                line_id, line = next(ids)
                out.append(
                    {
                        "id": line_id,
                        "speaker": line.get("speaker"),
                        "role": line.get("role"),
                        "text": attempted.get(line_id, line.get("text", "")),
                        "problems": by_target.get(line_id, []),
                    }
                )
            return out

        intro = entries(script["intro"]["lines"])
        pages = []
        for page in script["pages"]:
            number = page["page_number"]
            book_page = pages_by_number.get(number, {})
            pages.append(
                {
                    "number": number,
                    "anchor": f"page-{number:02d}",
                    "title": book_page.get("title"),
                    "text": book_page.get("text", ""),
                    "picture": book_page.get("illustration_file") or f"page_{number:02d}.png",
                    "lines": entries(page["lines"]),
                    "problems": by_target.get(f"page-{number:02d}", []),
                }
            )
        outro = entries(script["outro"]["lines"])
        listed = [{"text": p, "target": _problem_target(p, page_lines)} for p in problems]
        return {
            "error": None,
            "intro": intro,
            "pages": pages,
            "outro": outro,
            "problems": listed,
            "reviewed": sb.reviewed_mark(self.paths, book),
        }

    def script_page(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        context = {"book": book, "script": self.script_context(book)}
        return self.render(request, "script.html", context)

    def script_job_running(self, book: sb.Book) -> str | None:
        job = self.runner.current()
        if job and job["book"] == book.slug and job["stage"] in ("script", "voices"):
            return f"{sb.STAGE_LABELS[job['stage']]} is running for this book; wait for it"
        return None

    async def save_script(self, request: Request) -> Response:
        form = await request.form()
        edits = {k: v for k, v in form.items() if isinstance(v, str)}
        return await run_in_threadpool(self._save_script, request, edits)

    def _save_script(self, request: Request, edits: dict[str, str]) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        back = f"/books/{book.slug}/script"
        with self.write_lock:
            if reason := self.script_job_running(book):
                problems = None
            else:
                problems = sb.save_line_edits(self.paths, book, edits)
        if problems is None:
            return self.message(request, "Busy", [reason], 409, back)
        if problems:
            context = {"book": book, "script": self.script_context(book, edits, problems)}
            return self.render(request, "script.html", context, 422)
        return RedirectResponse(back, status_code=303)

    def mark_reviewed(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        back = f"/books/{book.slug}/script"
        with self.write_lock:
            if reason := self.script_job_running(book):
                refused = ("Busy", reason)
            else:
                try:
                    sb.mark_reviewed(self.paths, book)
                    refused = None
                except sb.StudioError as exc:
                    refused = ("Cannot mark as reviewed", str(exc))
        if refused is not None:
            return self.message(request, refused[0], [refused[1]], 409, back)
        return RedirectResponse(back, status_code=303)

    # --- audio ----------------------------------------------------------------------------------

    def audio_page(self, request: Request, error: str | None = None, status=200):
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        audio = _audio_paths(self.paths, book)
        tracks = []
        for name in _track_names(audio):
            timing = _read_json(audio.track_timing(name)) or {}
            lines = [line for line in timing.get("lines", []) if isinstance(line, dict)]
            tracks.append(
                {
                    "name": name,
                    "has_wav": audio.track(name).is_file(),
                    "duration": timing.get("duration"),
                    "lines": lines,
                }
            )
        context = {
            "book": book,
            "record": _read_json(audio.status),
            "has_preview": audio.preview.is_file(),
            "tracks": tracks,
            "voices": self.status_of(book)["voices"],
            "error": error,
        }
        return self.render(request, "audio.html", context, status)

    def approve_audio(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        with self.write_lock:
            job = self.runner.current()
            if job and job["book"] == book.slug and job["stage"] == "voices":
                error = "voice synthesis is still running"
            else:
                try:
                    cfg = sv.load_voice_config(self.paths.config_dir / "voices.json")
                    book_data = load_book(book.book_path)
                    script = sb.script_path(self.paths, book)
                    settings_sha = sv.current_settings_sha(cfg, book_data, book.book_path, script)
                    sv.approve(_audio_paths(self.paths, book), script, settings_sha)
                    error = None
                except (sv.ApproveError, ConfigError, BookError) as exc:
                    error = str(exc)
        if error is not None:
            return self.audio_page(request, error, 409)
        return RedirectResponse(f"/books/{book.slug}/audio", status_code=303)

    # --- video ----------------------------------------------------------------------------------

    def video_page(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        video = _video_paths(self.paths, book)
        final = video.final(book.slug)
        record = _read_json(video.record(book.slug)) if final.is_file() else None
        check, reason = sb.video_check(self.paths, book) if final.is_file() else (None, "")
        context = {
            "book": book,
            "final": final if final.is_file() else None,
            "record": record,
            "check": check,
            "reason": reason,
        }
        return self.render(request, "video.html", context)

    # --- animation (spec 009 FR-12) ------------------------------------------------------------

    def animation_controls(self, book: sb.Book) -> list[dict]:
        busy = self.busy_reason()
        return [
            {"stage": st, "button": STAGE_BUTTONS[st], "blocked": busy or self.gate(book, st)}
            for st in sb.ANIMATION_STAGES
        ]

    def animation_page(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return self.not_found(request)
        rows = sb.page_animation(self.paths, book)
        context = {
            "book": book,
            "rows": rows,
            "summary": sb.animation_summary(rows),
            "controls": self.animation_controls(book),
            "job": self.last_job(book, (*sb.ANIMATION_STAGES, sb.PREVIEW_STAGE)),
        }
        return self.render(request, "animation.html", context)

    def animation_page_detail(self, request: Request) -> Response:
        book = self.book_or_none(request)
        number = request.path_params["number"]
        rows = sb.page_animation(self.paths, book) if book else []
        row = next((r for r in rows if r["page_number"] == number), None)
        if book is None or row is None:
            return self.not_found(request)
        numbers = [r["page_number"] for r in rows]
        at = numbers.index(number)
        cut = row["cut_state"] == "cut"
        context = {
            "book": book,
            "row": row,
            "plan": row["plan"],
            "palette": [f"rgb{c}" for c in sb.MASK_PALETTE],
            "picture_url": f"/media/{book.slug}/illustration/{row['picture']}",
            "mask_url": f"/media/{book.slug}/mask/page_{number:02d}.png" if cut else None,
            "background_url": (
                f"/media/{book.slug}/layer/page_{number:02d}_background.png" if cut else None
            ),
            "prev": numbers[at - 1] if at > 0 else None,
            "next": numbers[at + 1] if at + 1 < len(numbers) else None,
            "controls": self.animation_controls(book),
            "preview_blocked": self.busy_reason() or self.gate(book, sb.PREVIEW_STAGE, number),
            "preview_job": self.last_job(book, (sb.PREVIEW_STAGE,), number),
            "preview_url": self.preview_url(book, number),
        }
        return self.render(request, "animation_page.html", context)

    def preview_url(self, book: sb.Book, number: int) -> str | None:
        """The page preview's media URL (tagged with its modification time), or None."""
        path = _video_paths(self.paths, book).preview(number)
        if not path.is_file():
            return None
        return f"/media/{book.slug}/preview_video/{path.name}?v={path.stat().st_mtime_ns}"

    async def preview_page(self, request: Request) -> Response:
        form = await request.form()
        return await run_in_threadpool(self._preview_page, request, form)

    def _preview_page(self, request: Request, form) -> Response:
        """Start the page preview job (spec 010 FR-16), back to the page view."""
        book = self.book_or_none(request)
        number = request.path_params["number"]
        rows = sb.page_animation(self.paths, book) if book else []
        if book is None or not any(r["page_number"] == number for r in rows):
            return self.not_found(request)
        back = f"/books/{book.slug}/animation/{number}"
        with self.write_lock:
            refused = self._start_stage(book, sb.PREVIEW_STAGE, form, page=number)
        if refused is not None:
            title, lines = refused
            return self.message(request, title, lines, 409, back)
        return RedirectResponse(back, status_code=303)

    def mask_png(self, book: sb.Book, number: int) -> bytes | None:
        """The masks overlay for a cut page, made once per cut (kept in memory by cut hash)."""
        manifest = _read_json(sb.layers_dir(sb.folder_of(self.paths, book), number) / sb.MANIFEST)
        if manifest is None:
            return None
        key = (book.slug, number, str(manifest.get("cut_hash")))
        with self.masks_lock:
            if key in self.masks:
                return self.masks[key]
        data = sb.mask_overlay(self.paths, book, number)
        if data is not None:
            with self.masks_lock:
                if len(self.masks) >= MASK_CACHE_SIZE:
                    self.masks.pop(next(iter(self.masks)))
                self.masks[key] = data
        return data

    # --- media (FR-7) ---------------------------------------------------------------------------

    def media_path(self, book: sb.Book, kind: str, name: str) -> Path | None:
        """The file behind a media URL, only for names the book itself defines."""
        if kind == "illustration":
            folder = sb.folder_of(self.paths, book)
            if name in sb.NOOK_FILES:
                for nook_dir in (folder.nooks, self.paths.shared_nooks):
                    if (nook_dir / name).is_file():
                        return nook_dir / name
                return None
            try:
                required, _problems = sb.expected_files(load_book(book.book_path))
            except BookError:
                return None
            return folder.illustrations / name if name in required else None
        audio = _audio_paths(self.paths, book)
        if kind == "preview":
            return audio.preview if name == "preview.wav" else None
        if kind == "track":
            track = name.removesuffix(".wav")
            return (
                audio.track(track)
                if name.endswith(".wav") and track in _track_names(audio)
                else None
            )
        if kind == "video":
            return (
                _video_paths(self.paths, book).final(book.slug)
                if name == f"{book.slug}.mp4"
                else None
            )
        if kind == "preview_video" and (m := _PREVIEW_NAME.match(name)):
            path = _video_paths(self.paths, book).preview(int(m.group(1)))
            return path if path.name == name else None
        if kind == "layer" and (m := _LAYER_NAME.match(name)):
            return sb.layer_files(self.paths, book, int(m.group(1))).get(m.group(2))
        return None

    def media(self, request: Request) -> Response:
        book = self.book_or_none(request)
        if book is None:
            return Response("not found", status_code=404)
        kind, name = request.path_params["kind"], request.path_params["name"]
        if kind == "mask":
            m = _MASK_NAME.match(name)
            data = self.mask_png(book, int(m.group(1))) if m else None
            if data is None:
                return Response("not found", status_code=404)
            return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})
        path = self.media_path(book, kind, name)
        if path is None or not path.is_file():
            return Response("not found", status_code=404)
        return FileResponse(path)

    # --- JSON for live progress -----------------------------------------------------------------

    def api_current(self, request: Request) -> Response:
        job = self.running()
        if job is None:
            return JSONResponse({"job": None, "elapsed_seconds": None})
        record = {
            k: job[k] for k in ("id", "book", "stage", "started_at", "state", "title", "label")
        }
        return JSONResponse({"job": record, "elapsed_seconds": round(job["elapsed"], 1)})

    def api_log(self, request: Request) -> Response:
        job_id = request.path_params["job_id"]
        try:
            offset = max(int(request.query_params.get("offset", "0")), 0)
        except ValueError:
            offset = 0
        try:
            text, new_offset = self.runner.log(job_id, offset)
        except UnknownJob:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        current = self.runner.current()
        running = current is not None and current["id"] == job_id
        return JSONResponse({"text": text, "offset": new_offset, "running": running})


class SameSiteGuard:
    """Refuses state-changing requests whose Origin or Referer names another site."""

    def __init__(self, app, studio: Studio) -> None:
        self.app = app
        self.studio = studio

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and not self.studio.same_site(Request(scope))
        ):
            response = Response("cross-site request refused", status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(
    paths: sb.StudioPaths,
    runner: JobRunner | None = None,
    command_for: CommandFor = stage_command,
    port: int = DEFAULT_PORT,
) -> Starlette:
    """The studio app for `paths`; `runner` and `command_for` are injectable for tests. Without a
    `runner` one is created, which takes the studio lock (raises `StudioLocked` if it is held).
    Shutdown stops the running stage and releases the lock."""
    runner = runner or JobRunner(paths.studio_dir, cwd=PROJECT_ROOT)
    studio = Studio(paths, runner, command_for, port)
    studio.drafts.mark_interrupted_all()  # a draft still writing belonged to a stopped studio

    @contextlib.asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            studio.runner.stop()  # a stage must not outlive the studio that shows it
            studio.runner.close()

    routes = [
        Route("/", studio.home),
        Route("/books/new", studio.new_book),
        Route("/books", studio.refuse_upload, methods=["POST"]),
        Route("/ideas", studio.start_ideas, methods=["POST"]),
        Route("/ideas/improve", studio.start_improve, methods=["POST"]),
        Route("/drafts", studio.write_book, methods=["POST"]),
        Route("/drafts/{draft_id}", studio.draft_page),
        Route("/drafts/{draft_id}/pages/{number:int}", studio.save_page, methods=["POST"]),
        Route("/drafts/{draft_id}/create", studio.create_book_from_draft, methods=["POST"]),
        Route("/drafts/{draft_id}/discard", studio.discard_draft, methods=["POST"]),
        Route("/drafts/{draft_id}/write-again", studio.write_again, methods=["POST"]),
        Route("/books/{slug}", studio.book_page),
        Route("/books/{slug}/run/{stage}", studio.run_stage, methods=["POST"]),
        Route("/books/{slug}/script", studio.script_page),
        Route("/books/{slug}/script", studio.save_script, methods=["POST"]),
        Route("/books/{slug}/script/reviewed", studio.mark_reviewed, methods=["POST"]),
        Route("/books/{slug}/audio", studio.audio_page),
        Route("/books/{slug}/audio/approve", studio.approve_audio, methods=["POST"]),
        Route("/books/{slug}/video", studio.video_page),
        Route("/books/{slug}/animation", studio.animation_page),
        Route("/books/{slug}/animation/{number:int}", studio.animation_page_detail),
        Route(
            "/books/{slug}/animation/{number:int}/preview", studio.preview_page, methods=["POST"]
        ),
        Route("/jobs/stop", studio.stop_job, methods=["POST"]),
        Route("/api/jobs/current", studio.api_current),
        Route("/api/jobs/{job_id}/log", studio.api_log),
        Route("/media/{slug}/{kind}/{name}", studio.media),
        Mount("/static", StaticFiles(directory=STUDIO_DIR / "static"), name="static"),
    ]
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS),
        Middleware(SameSiteGuard, studio=studio),
    ]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.studio = studio
    return app


# --- start-up (AC-1.1) -------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.studio",
        description="Start the episode studio on this Mac only (127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default 8765)")
    return parser.parse_args(argv)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn binds
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def main(argv: list[str] | None = None, paths: sb.StudioPaths | None = None) -> int:
    args = parse_args(argv)
    if not _port_free(args.port):
        print(f"error: port {args.port} is already in use", file=sys.stderr)
        return 1
    try:
        app = create_app(paths or sb.StudioPaths(), port=args.port)
    except StudioLocked:
        print("error: another studio is already running on this project", file=sys.stderr)
        return 1
    runner = app.state.studio.runner
    try:
        print(f"Studio running at http://{HOST}:{args.port}", flush=True)
        uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    finally:
        runner.stop()
        runner.close()
    return 0
