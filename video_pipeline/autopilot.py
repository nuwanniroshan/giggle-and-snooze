"""Autopilot: carry a book from a chosen idea to a finished video and editions (spec 021).

    python -m video_pipeline.autopilot (--book SLUG | --draft ID) [--root R] [--no-editions]

The stages run one at a time, in this order, each as a child process with exactly the argv the
studio uses (`jobs.stage_command`, FR-1): paint (which also plans each page) -> plan -> cut layers
-> script -> automatic review mark -> voices -> automatic approval -> render -> build editions.
With `--no-editions` the run finishes after the video and never calls build_book.py.
A stage that is already done and up to date is skipped (AC-2.2), so a new run resumes at the first
unfinished stage (AC-4.2). The first failure stops the run with the stage and the reason; nothing
later starts (AC-4.1). With `--draft`, the draft's blueprint and story are written first, then the
book is created (without the draft review stop, AC-1.1) and the book stages follow.

The two human gates stay in the CLI (FR-4): autopilot writes the reviewed mark and the audio
approval itself, recorded as `"by": "autopilot"` (AC-3.1, AC-3.2). It approves only audio the
voices stage left valid for the current script and settings; voices whose clips fail their checks
exit non-zero with the failing lines named, and the run stops there (AC-3.3).

Pictures or a script that are stale only because the blueprint changed (spec 011) are not
regenerated (that costs a full repaint or overwrites hand edits): the run stops at that stage with
the reason and the button to use.

The run state (FR-2) is `<root>/build/studio/autopilot/<slug>.json` (`draft-<id>.json` until the
book exists), written atomically. The studio starts this command as one job (stage `autopilot`)
and passes its id in `KIDDO_JOB_ID`, so a `running` file whose job is gone shows as interrupted.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import ConfigError
from video_pipeline.drafts import DraftError, DraftStore
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.studio import books as sb
from video_pipeline.studio.jobs import stage_command

BY = "autopilot"
TAIL_LINES = 60
MAX_REASON_LINES = 12
STOPPED_EXIT = 143
STEP_LABELS = {
    "book": "book check",
    "story": "blueprint design and story writing",
    "create": "book creation",
    "paint": sb.STAGE_LABELS[sb.PAINT_STAGE],
    "plan": sb.STAGE_LABELS["plan"],
    "cut": sb.STAGE_LABELS["cut"],
    "script": sb.STAGE_LABELS["script"],
    "review": "automatic review mark",
    "voices": sb.STAGE_LABELS["voices"],
    "approve": "automatic audio approval",
    "render": sb.STAGE_LABELS["render"],
    "build": sb.STAGE_LABELS[sb.BUILD_STAGE],
}
RunChild = Callable[[list[str]], tuple[int, list[str]]]


class Blocked(Exception):
    """The step cannot be done by autopilot; a person has to act (the message says how)."""


class Stopped(BaseException):  # noqa: N818 - a signal, not an error
    """SIGTERM arrived (the studio's Stop, AC-1.3)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_path(paths: sb.StudioPaths, name: str) -> Path:
    """The run file for a book slug, or `draft-<id>` before the book exists (FR-2)."""
    return sb.autopilot_path(paths, name)


# --- probes: each stage's state, from the studio's file-derived functions ---------------------


def _pictures_state(paths: sb.StudioPaths, book: sb.Book) -> dict:
    return sb.paint_state(paths, book)


def _animation_rows(paths: sb.StudioPaths, book: sb.Book) -> list[dict]:
    return sb.page_animation(paths, book)


def _script_state(paths: sb.StudioPaths, book: sb.Book) -> str:
    """ "missing", "unreadable", "stale_blueprint" (made from an older blueprint) or "ok"."""
    path = sb.script_path(paths, book)
    if not path.exists():
        return "missing"
    try:
        if sb.load_script(paths, book) is None:
            return "missing"
    except sb.StudioError:
        return "unreadable"
    stale = sb.blueprint_status(paths, book)["stale"]
    if any(item["output"] == "episode script" for item in stale):
        return "stale_blueprint"
    return "ok"


def _script_reviewed(paths: sb.StudioPaths, book: sb.Book) -> bool:
    """Whether a reviewed mark matches the script and book file on disk (plan 021: audio made
    from this script is not enough, so the automatic review is always visible on the script)."""
    return sb.reviewed_mark(paths, book) is not None


def _voices_state(paths: sb.StudioPaths, book: sb.Book) -> dict:
    return sb._voices_state(paths, book)


def _video_state(paths: sb.StudioPaths, book: sb.Book) -> dict:
    return sb._video_state(paths, book)


def _editions_state(paths: sb.StudioPaths, book: sb.Book) -> dict:
    return sb.editions_state(paths, book)


def _mark_review(paths: sb.StudioPaths, book: sb.Book) -> None:
    sb.mark_reviewed(paths, book, by=BY)


def _approve_audio(paths: sb.StudioPaths, book: sb.Book) -> None:
    """Approve as the studio's Approve button does, recorded as automatic. `sv.approve` refuses
    audio that is not valid for the current script and voice settings (AC-3.3)."""
    cfg = sv.load_voice_config(paths.config_dir / "voices.json")
    book_data = load_book(book.book_path)
    script = sb.script_path(paths, book)
    settings_sha = sv.current_settings_sha(cfg, book_data, book.book_path, script)
    audio = sv.AudioPaths(sb.folder_of(paths, book).build_dir / "audio")
    sv.approve(audio, script, settings_sha, by=BY)


# Looked up at call time, so tests can stand in for them.
pictures_state = _pictures_state
animation_rows = _animation_rows
script_state = _script_state
script_reviewed = _script_reviewed
voices_state = _voices_state
video_state = _video_state
editions_state = _editions_state
mark_review = _mark_review
approve_audio = _approve_audio


# --- the steps -------------------------------------------------------------------------------


def _pages(numbers: list[int]) -> str:
    return ", ".join(str(n) for n in numbers)


def _needs_paint(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = pictures_state(paths, book)
    if state["missing"]:
        return f"missing pictures: {', '.join(state['missing'])}"
    if state["state"] == "out_of_date":
        raise Blocked(
            "the pictures were made from an older blueprint; autopilot does not repaint them. "
            "Use Repaint all on the book page (or run python -m video_pipeline.illustrate "
            f"--book {book.slug} --force), then run autopilot again"
        )
    return None


def _needs_plan(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    rows = animation_rows(paths, book)
    unavailable = next((r for r in rows if r["plan_state"] == "unavailable"), None)
    if unavailable is not None:
        raise Blocked(f"the motion plans cannot be checked: {unavailable['detail']}")
    todo = [r["page_number"] for r in rows if r["plan_state"] in ("none", "stale", "invalid")]
    return f"pages without a current plan: {_pages(todo)}" if todo else None


def _needs_cut(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    rows = animation_rows(paths, book)
    todo = [r["page_number"] for r in rows if r["plan_state"] == "ok" and r["cut_state"] != "cut"]
    return f"pages not cut: {_pages(todo)}" if todo else None


def _needs_script(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = script_state(paths, book)
    if state == "missing":
        return "no episode script yet"
    if state == "unreadable":
        raise Blocked(
            f"the episode script is not readable JSON: {sb.script_path(paths, book)}; fix it or "
            "use Regenerate script on the book page"
        )
    if state == "stale_blueprint":
        raise Blocked(
            "the episode script was made from an older blueprint; autopilot does not overwrite "
            "it. Use Regenerate script on the book page, then run autopilot again"
        )
    return None


def _needs_review(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    return None if script_reviewed(paths, book) else "the script is not marked reviewed"


def _needs_voices(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = voices_state(paths, book)
    if state["state"] in ("not_started", "out_of_date"):
        return state.get("detail") or "no usable audio"
    return None


def _needs_approve(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = voices_state(paths, book)
    if state["state"] == "approved":
        return None
    if state["state"] == "needs_review":
        return "the audio is not approved"
    return f"the audio is not usable: {state.get('detail') or state['state']}"


def _needs_render(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = video_state(paths, book)
    return None if state["state"] == "done" else (state.get("detail") or "not rendered")


def _needs_build(paths: sb.StudioPaths, book: sb.Book) -> str | None:
    state = editions_state(paths, book)
    if state["state"] == "blocked":
        raise Blocked(state.get("detail") or "the editions cannot be built yet")
    return None if state["state"] == "done" else (state.get("detail") or "not built")


@dataclass(frozen=True)
class Step:
    name: str
    needs: Callable[[sb.StudioPaths, sb.Book], str | None]
    stage: str | None = None  # the studio stage run as a child; None for a mark
    mark: Callable[[sb.StudioPaths, sb.Book], None] | None = None


STEPS = (
    Step("paint", _needs_paint, stage=sb.PAINT_STAGE),
    Step("plan", _needs_plan, stage="plan"),
    Step("cut", _needs_cut, stage="cut"),
    Step("script", _needs_script, stage="script"),
    Step("review", _needs_review, mark=lambda p, b: mark_review(p, b)),
    Step("voices", _needs_voices, stage="voices"),
    Step("approve", _needs_approve, mark=lambda p, b: approve_audio(p, b)),
    Step("render", _needs_render, stage="render"),
    Step("build", _needs_build, stage=sb.BUILD_STAGE),
)


# --- the run file (FR-2) ------------------------------------------------------------------------


class RunRecord:
    """The run state on disk: one file per book (or draft), rewritten atomically on every change."""

    def __init__(
        self, paths: sb.StudioPaths, name: str, *, book=None, draft=None, job_id=None
    ) -> None:
        self.paths = paths
        self.path = run_path(paths, name)
        now = _now()
        self.data = {
            "book": book,
            "draft": draft,
            "job_id": job_id,
            "state": "running",
            "stage": None,
            "reason": None,
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "by": BY,
            "steps": [],
        }
        self.save()

    def save(self, **fields) -> None:
        self.data.update(fields, updated_at=_now())
        write_atomic(self.path, self.data)

    def move_to(self, name: str, **fields) -> None:
        old, self.path = self.path, run_path(self.paths, name)
        self.save(**fields)
        if old != self.path:
            old.unlink(missing_ok=True)

    def step(self, stage: str, result: str, **fields) -> None:
        self.data["steps"].append({"stage": stage, "result": result, "at": _now()})
        self.save(stage=stage, **fields)

    def fail(self, stage: str, reason: str) -> int:
        self.step(stage, "failed", state="failed", reason=reason, finished_at=_now())
        print(f"autopilot stopped at {STEP_LABELS.get(stage, stage)}: {reason}", file=sys.stderr)
        return 1

    def stopped(self) -> int:
        stage = self.data.get("stage")
        self.data["steps"].append({"stage": stage, "result": "stopped", "at": _now()})
        self.save(state="stopped", reason="stopped by the producer", finished_at=_now())
        return STOPPED_EXIT


# --- running -----------------------------------------------------------------------------------


def _on_sigterm(signum, frame) -> None:
    raise Stopped()


def _run_child(cmd: list[str]) -> tuple[int, list[str]]:
    """Run one stage (argv list, never a shell), streaming its output into ours (the job log)
    and keeping the last lines for the failure reason. On Stop the child, which is in the same
    process group, gets the studio's SIGTERM too; it is also terminated here in case it did not."""
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    proc = subprocess.Popen(  # noqa: S603 - argv built by stage_command
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            sys.stdout.buffer.write(raw)
            sys.stdout.flush()
            tail.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        return proc.wait(), list(tail)
    except BaseException:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise


def _failure_reason(label: str, code: int, tail: list[str]) -> str:
    """ "<label> failed (exit code N)" with the child's error lines (e.g. the voice lines that
    failed their checks, AC-3.3), else a pointer to the log."""
    start = next((i for i, line in enumerate(tail) if line.startswith("error")), None)
    head = f"{label} failed (exit code {code})"
    if start is None:
        return f"{head}; see the log"
    lines = [line.strip() for line in tail[start:] if line.strip()][:MAX_REASON_LINES]
    return f"{head}: " + "\n".join(lines)


def _run_steps(
    paths: sb.StudioPaths, slug: str, record: RunRecord, run_child: RunChild, *, editions: bool = True
) -> int:
    book = sb.get_book(paths, slug)
    if book is None:
        return record.fail("book", f"no book {slug!r}")
    if book.error:
        return record.fail("book", f"the book data has a problem: {book.error}")
    for step in STEPS:
        if step.name == "build" and not editions:
            record.step("build", "skipped")
            continue
        label = STEP_LABELS[step.name]
        try:
            why = step.needs(paths, book)
        except Blocked as exc:
            return record.fail(step.name, str(exc))
        if why is None:
            record.step(step.name, "skipped")
            continue
        print(f"== autopilot: {step.name} ==  ({why})", flush=True)
        record.save(stage=step.name, reason=None)
        if step.stage is not None:
            code, tail = run_child(stage_command(step.stage, book, paths))
            if code != 0:
                return record.fail(step.name, _failure_reason(label, code, tail))
        else:
            try:
                step.mark(paths, book)
            except (sb.StudioError, sv.ApproveError, ConfigError, BookError) as exc:
                return record.fail(step.name, f"{label} failed: {exc}")
        try:
            still = step.needs(paths, book)
        except Blocked as exc:
            return record.fail(step.name, str(exc))
        if still is not None:
            return record.fail(step.name, f"{label} finished but is still not done: {still}")
        record.step(step.name, "done")
    record.save(state="finished", stage=None, reason=None, finished_at=_now())
    print("== autopilot: finished ==", flush=True)
    return 0


def run(
    paths: sb.StudioPaths,
    slug: str,
    *,
    run_child: RunChild = _run_child,
    job_id: str | None = None,
    editions: bool = True,
) -> int:
    """Run every unfinished stage of book `slug` in order; 0 when all are done, 1 on the first
    failure, 143 when stopped. The run file says which stage and why."""
    record = RunRecord(paths, slug, book=slug, job_id=job_id)
    try:
        return _run_steps(paths, slug, record, run_child, editions=editions)
    except Stopped:
        return record.stopped()


def run_draft(
    paths: sb.StudioPaths,
    draft_id: str,
    *,
    run_child: RunChild = _run_child,
    job_id: str | None = None,
    editions: bool = True,
) -> int:
    """Write the draft's blueprint and story (unless it is ready), create the book (no draft
    review stop, AC-1.1), then run the book's stages."""
    store = DraftStore(paths.root)
    record = RunRecord(paths, f"draft-{draft_id}", draft=draft_id, job_id=job_id)
    try:
        draft = store.get(draft_id)
        if draft is None:
            return record.fail("story", f"no draft {draft_id!r}")
        if draft.get("state") == "ready" and draft.get("book"):
            record.step("story", "skipped")
        else:
            print("== autopilot: story ==", flush=True)
            record.save(stage="story")
            code, tail = run_child(stage_command("blueprint", draft_id, paths))
            if code != 0:
                return record.fail("story", _failure_reason(STEP_LABELS["story"], code, tail))
            draft = store.get(draft_id) or {}
            if draft.get("state") != "ready" or not draft.get("book"):
                problems = "; ".join(str(p) for p in draft.get("problems") or [])
                state = draft.get("state") or "missing"
                reason = f"the story was not written (draft state: {state})"
                return record.fail("story", f"{reason}: {problems}" if problems else reason)
            record.step("story", "done")
        print("== autopilot: create ==", flush=True)
        record.save(stage="create")
        try:
            slug = store.create_book(draft_id)
        except DraftError as exc:
            return record.fail("create", f"the book could not be created: {exc}")
        record.move_to(slug, book=slug)
        record.step("create", "done")
        return _run_steps(paths, slug, record, run_child, editions=editions)
    except Stopped:
        return record.stopped()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.autopilot",
        description="Run every unfinished stage of a book, in order, without stopping (spec 021).",
        allow_abbrev=False,
    )
    subject = parser.add_mutually_exclusive_group(required=True)
    subject.add_argument("--book", help="book slug")
    subject.add_argument("--draft", help="draft id: write the story and create the book first")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    parser.add_argument(
        "--no-editions", action="store_true", help="stop after the video; do not build EPUB/PDFs"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, _on_sigterm)
    paths = sb.StudioPaths(Path(args.root))
    job_id = os.environ.get("KIDDO_JOB_ID") or None
    editions = not args.no_editions
    if args.draft:
        return run_draft(paths, args.draft, job_id=job_id, editions=editions)
    return run(paths, args.book, job_id=job_id, editions=editions)


if __name__ == "__main__":
    raise SystemExit(main())
