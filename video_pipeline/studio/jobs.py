"""The one-at-a-time background job runner (spec 004, Phase 2, FR-3).

Each stage runs as a subprocess of its existing CLI (the same code path as the command line), in
its own process group so it can be stopped as a whole. Output (stdout and stderr merged) is
appended raw to `<studio_dir>/jobs/<id>.log`, flushed per line; the job record
`<studio_dir>/jobs/<id>.json` is written atomically when the job starts and when it ends, so the
last run per book and stage survives a restart. A record still `running` when a runner is created
belonged to a studio that died, and becomes `interrupted`; if its process group is still alive
(the studio was killed hard), that orphaned stage is stopped first, but only when the process is
recognisably that stage: its command line names `video_pipeline` and its start time matches the
one recorded when the job started (a reused pid is left alone).

Only one runner may own a studio directory (review fixes, round 2). A runner holds an exclusive
`flock` on `<studio_dir>/studio.lock` from creation until `close()` (or garbage collection), and a
second runner, in another process or in the same one (each runner opens the file itself, and flock
locks belong to the open file, so two opens conflict even within a process), gets `StudioLocked`
before it touches any record.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from video_pipeline.episode_tools import write_atomic
from video_pipeline.studio.books import STAGES, StudioPaths

STOP_GRACE_SECONDS = 5.0
LOCK_FILE = "studio.lock"
STAGE_MARKER = "video_pipeline"
STAGE_MODULES = {
    "script": "video_pipeline.generate_script",
    "voices": "video_pipeline.synthesize_voices",
    "render": "video_pipeline.render_episode",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


class Busy(Exception):
    """A job is already running; `current` is its record."""

    def __init__(self, current: dict) -> None:
        super().__init__(f"a job is already running: {current['id']}")
        self.current = current


class StudioLocked(RuntimeError):
    """Another studio (job runner) already owns this studio directory."""


class UnknownJob(LookupError):
    """No job with that id (or the id is not a safe file name)."""


@dataclass
class Job:
    id: str
    book: str
    stage: str
    cmd: list[str]
    started_at: str
    ended_at: str | None = None
    state: str = "running"
    exit_code: int | None = None
    pid: int | None = None
    proc_started: str | None = None
    _stopping: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        record = asdict(self)
        record.pop("_stopping")
        return record


STORY_STAGES = ("ideas", "candidate", "book")


def story_command(stage: str, subject: str, paths: StudioPaths) -> list[str]:
    """The CLI argv for a story run (spec 006): `subject` is the run id or the draft id."""
    flag = "--draft" if stage == "book" else "--run"
    return [
        sys.executable,
        "-m",
        "video_pipeline.write_story",
        stage,
        flag,
        subject,
        "--root",
        str(paths.root),
    ]


def stage_command(stage: str, book, paths: StudioPaths, force: bool = False) -> list[str]:
    """The CLI argv that runs `stage` for `book`, as `python -m video_pipeline.<module> ...`.
    Story stages take a string subject (a run id or draft id) instead of a book."""
    if stage in STORY_STAGES:
        return story_command(stage, book if isinstance(book, str) else book.slug, paths)
    if stage not in STAGE_MODULES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {', '.join(STAGES)}")
    cmd = [
        sys.executable,
        "-m",
        STAGE_MODULES[stage],
        "--book",
        book.slug,
        "--root",
        str(paths.root),
    ]
    if stage == "voices":
        cmd.append("--reviewed")
    if stage == "render":
        cmd += ["--sfx-dir", str(paths.sfx_dir)]
    if force:
        cmd.append("--force")
    return cmd


def clean_log_text(text: str) -> str:
    """`text` for display: ANSI escape codes removed, and on each line only the text after the
    last carriage return kept (progress bars overwrite themselves with `\\r`)."""
    text = _ANSI.sub("", text)
    return "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))


def _now() -> datetime:
    return datetime.now(UTC)


class JobRunner:
    """Runs at most one stage subprocess at a time and keeps its log and record on disk."""

    def __init__(
        self,
        studio_dir: Path,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        stop_grace: float = STOP_GRACE_SECONDS,
    ) -> None:
        self.jobs_dir = Path(studio_dir) / "jobs"
        self._lock_fd: int | None = None
        self._take_studio_lock(Path(studio_dir))
        self.env = dict(env or {})
        self.cwd = cwd
        self.stop_grace = stop_grace
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._mark_interrupted()

    def _take_studio_lock(self, studio_dir: Path) -> None:
        studio_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(studio_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise StudioLocked("another studio is already running on this project") from exc
        self._lock_fd = fd

    def close(self) -> None:
        """Release the studio lock (a running job is not stopped; see `stop`). Idempotent."""
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            os.close(fd)  # closing the only descriptor releases the flock

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    # --- paths and records --------------------------------------------------------------------

    def _record_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _log_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.log"

    def _write(self, job: Job) -> None:
        write_atomic(self._record_path(job.id), job.to_dict())

    def _records(self) -> list[dict]:
        if not self.jobs_dir.is_dir():
            return []
        found = []
        for path in self.jobs_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                found.append(record)
        return found

    def _mark_interrupted(self) -> None:
        """Mark stale `running` records `interrupted`, ending when their log last changed (the
        last sign of life). A stage still running in its own process group is stopped first
        (SIGTERM, SIGKILL after the grace period) and that is noted in its log, but only when it
        is recognisably the recorded stage (`_is_recorded_stage`)."""
        for record in self._records():
            if record.get("state") != "running":
                continue
            log = self._log_path(record["id"]) if _SAFE_ID.fullmatch(record["id"]) else None
            pgid = record.get("pid")
            if (
                log is not None
                and isinstance(pgid, int)
                and _group_alive(pgid)
                and _is_recorded_stage(pgid, record.get("proc_started"))
            ):
                how = _stop_group(pgid, self.stop_grace)
                with log.open("ab") as fh:
                    fh.write(
                        f"\nstudio restarted: stopped the orphaned stage (process group {pgid},"
                        f" {how})\n".encode()
                    )
            ended = _now()
            if log is not None and log.is_file():
                ended = datetime.fromtimestamp(log.stat().st_mtime, UTC)
            record["state"] = "interrupted"
            record["ended_at"] = ended.isoformat()
            write_atomic(self._record_path(record["id"]), record)

    # --- running --------------------------------------------------------------------------------

    def start(self, book_slug: str, stage: str, cmd: list[str]) -> Job:
        """Start `cmd` in the background for (`book_slug`, `stage`). Raises `Busy` when a job is
        already running. A program that cannot be started gives a `failed` job, not an error."""
        if not _SAFE_ID.fullmatch(book_slug) or not _SAFE_ID.fullmatch(stage):
            raise ValueError(f"unsafe book slug or stage: {book_slug!r}, {stage!r}")
        with self._lock:
            if self._job is not None:
                raise Busy(self._job.to_dict())
            started = _now()
            job_id = f"{started:%Y%m%dT%H%M%S}-{book_slug}-{stage}-{secrets.token_hex(2)}"
            job = Job(job_id, book_slug, stage, list(cmd), started.isoformat())
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            log = self._log_path(job_id).open("ab")
            env = {**os.environ, **self.env, "PYTHONUNBUFFERED": "1"}
            try:
                proc = subprocess.Popen(  # noqa: S603 - argv list built by stage_command
                    job.cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=self.cwd,
                    env=env,
                    start_new_session=True,
                )
            except OSError as exc:
                log.write(f"error: could not start {job.cmd[0]}: {exc}\n".encode())
                log.close()
                job.state, job.ended_at = "failed", _now().isoformat()
                self._write(job)
                return Job(**asdict(job))
            job.pid = proc.pid
            job.proc_started = _process_started(proc.pid)
            self._write(job)
            self._job, self._proc = job, proc
            self._thread = threading.Thread(
                target=self._pump, args=(job, proc, log), name=f"job-{job_id}", daemon=True
            )
            self._thread.start()
            return Job(**asdict(job))

    def _pump(self, job: Job, proc: subprocess.Popen, log) -> None:
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                log.write(line)
                log.flush()
            proc.stdout.close()
            code = proc.wait()
        finally:
            log.close()
        with self._lock:
            job.exit_code = code
            job.ended_at = _now().isoformat()
            if job._stopping:
                job.state = "stopped"
            else:
                job.state = "done" if code == 0 else "failed"
            self._write(job)
            self._job = self._proc = None

    def current(self) -> dict | None:
        """The running job's record, or None."""
        with self._lock:
            return self._job.to_dict() if self._job else None

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the running job (if any) to finish; True when nothing is running after."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def stop(self) -> dict | None:
        """Stop the running job: SIGTERM to its process group, SIGKILL after the grace period.
        Returns the final record (state `stopped`), or None when nothing was running."""
        with self._lock:
            job, proc, thread = self._job, self._proc, self._thread
            if job is None or proc is None or thread is None:
                return None
            job._stopping = True
        _signal_group(proc.pid, signal.SIGTERM)
        thread.join(self.stop_grace)
        if thread.is_alive():
            _signal_group(proc.pid, signal.SIGKILL)
            thread.join()
        return job.to_dict()

    # --- reading --------------------------------------------------------------------------------

    def latest(self, book: str, stage: str) -> dict | None:
        """The newest record for (`book`, `stage`), read from disk; None if it never ran."""
        matches = [r for r in self._records() if r.get("book") == book and r.get("stage") == stage]
        if not matches:
            return None
        return max(matches, key=lambda r: (str(r.get("started_at") or ""), r["id"]))

    def log(self, job_id: str, offset: int = 0) -> tuple[str, int]:
        """Log text from byte `offset` and the offset to ask for next. ANSI codes are removed and
        `\\r`-overwritten text collapsed; while the job runs only complete lines are returned."""
        path = self._log_path(job_id)
        if not _SAFE_ID.fullmatch(job_id) or not self._record_path(job_id).is_file():
            raise UnknownJob(job_id)
        with self._lock:
            running = self._job is not None and self._job.id == job_id
        try:
            with path.open("rb") as fh:
                fh.seek(max(offset, 0))
                data = fh.read()
        except FileNotFoundError:
            return "", max(offset, 0)
        start = max(offset, 0)
        if running:
            cut = data.rfind(b"\n") + 1
            data = data[:cut]
        return clean_log_text(data.decode("utf-8", errors="replace")), start + len(data)


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _group_alive(pgid: int) -> bool:
    """Whether process group `pgid` still has a process we may signal. A stage is started with
    `start_new_session`, so its pid is its group id; a pid reused by an unrelated process that is
    not a group leader is left alone."""
    if pgid <= 1 or pgid == os.getpgrp():
        return False
    try:
        if os.getpgid(pgid) != pgid:
            return False
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _ps(field_name: str, pid: int) -> str | None:
    """One `ps` column for `pid`, whitespace-normalised; None when there is no such process."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["ps", "-o", f"{field_name}=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = " ".join(out.stdout.split())
    return value if out.returncode == 0 and value else None


def _process_started(pid: int) -> str | None:
    """When process `pid` started, as `ps -o lstart` prints it (None if it is gone)."""
    return _ps("lstart", pid)


def _is_recorded_stage(pid: int, proc_started: object) -> bool:
    """Whether `pid` is still the stage a job record names: a `video_pipeline` command that
    started exactly when the record says. Anything else (an old record with no start time, a
    reused pid) is not ours to kill."""
    if not isinstance(proc_started, str) or not proc_started:
        return False
    command = _ps("command", pid)
    if command is None or STAGE_MARKER not in command:
        return False
    return _process_started(pid) == proc_started


def _stop_group(pgid: int, grace: float) -> str:
    """SIGTERM process group `pgid`, then SIGKILL if it is still alive after `grace` seconds.
    Returns how it ended ("terminated" or "killed")."""
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return "terminated"
        time.sleep(0.05)
    _signal_group(pgid, signal.SIGKILL)
    deadline = time.monotonic() + grace
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return "killed"
