"""Spec 021 Phase 3: autopilot on the book page (Run / Stop / resume, the run's stage, failure,
log and finish, and manual starts refused while it runs).

The autopilot command is replaced by a tiny `python -c` program run by a real JobRunner: it writes
the run file the way the CLI does (with the job id from `KIDDO_JOB_ID`) and then waits."""

from __future__ import annotations

import json
import sys
import time

import pytest

from tests.helpers import make_root
from tests.test_studio_app import PILOT_SLUG, Commands, Studio, scripted, text_of
from video_pipeline import autopilot as ap
from video_pipeline.episode_tools import write_atomic
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj


def autopilot_code(paths: sb.StudioPaths, stage: str = "voices", sleep: float = 30) -> str:
    path = ap.run_path(paths, PILOT_SLUG)
    return (
        "import json, os, pathlib, time\n"
        f"p = pathlib.Path({str(path)!r})\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"record = {{'book': {PILOT_SLUG!r}, 'draft': None, 'state': 'running',"
        f" 'stage': {stage!r}, 'reason': None, 'started_at': '2026-11-13T10:00:00+00:00',"
        " 'updated_at': '2026-11-13T10:00:00+00:00', 'finished_at': None, 'by': 'autopilot',"
        " 'steps': []}\n"
        "record['job_id'] = os.environ['KIDDO_JOB_ID']\n"
        "p.write_text(json.dumps(record))\n"
        f"print('== autopilot: {stage} ==', flush=True)\n"
        f"time.sleep({sleep})\n"
    )


def write_run(paths: sb.StudioPaths, **fields) -> dict:
    record = {
        "book": PILOT_SLUG, "draft": None, "job_id": None, "state": "running", "stage": None,
        "reason": None, "started_at": "2026-11-13T10:00:00+00:00",
        "updated_at": "2026-11-13T10:05:00+00:00", "finished_at": None, "by": "autopilot",
        "steps": [], **fields,
    }  # fmt: skip
    write_atomic(ap.run_path(paths, PILOT_SLUG), record)
    return record


def read_run(paths: sb.StudioPaths) -> dict:
    return json.loads(ap.run_path(paths, PILOT_SLUG).read_text(encoding="utf-8"))


@pytest.fixture
def make_studio(tmp_path):
    made = []

    def build(commands=None) -> Studio:
        make_root(tmp_path)
        s = Studio(tmp_path, commands)
        made.append(s)
        return s

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()
        s.runner.close()


def start_live_autopilot(make_studio, stage="voices") -> Studio:
    """A studio whose autopilot job is running and has written its run file."""
    s = make_studio(Commands("pass"))
    s.commands.code = autopilot_code(s.paths, stage)
    assert s.client.post(f"/books/{PILOT_SLUG}/run/autopilot").status_code == 303
    path = ap.run_path(s.paths, PILOT_SLUG)
    deadline = time.monotonic() + 10
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.is_file()
    return s


# --- the stage command and the job (FR-1) -----------------------------------------------------


def test_stage_command_runs_the_autopilot_cli(tmp_path):
    make_root(tmp_path)
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, PILOT_SLUG)
    assert sj.stage_command("autopilot", book, paths) == [
        sys.executable, "-m", "video_pipeline.autopilot", "--book", PILOT_SLUG,
        "--root", str(tmp_path),
    ]  # fmt: skip
    assert sj.stage_command("autopilot", "abc123", paths)[3:5] == ["--draft", "abc123"]


def test_run_autopilot_starts_one_autopilot_job(make_studio):  # AC-1.2
    s = make_studio()
    r = s.client.post(f"/books/{PILOT_SLUG}/run/autopilot")
    assert r.status_code == 303 and r.headers["location"] == f"/books/{PILOT_SLUG}"
    assert s.commands.calls == [("autopilot", PILOT_SLUG, False)]
    s.wait()
    assert s.runner.latest(PILOT_SLUG, "autopilot")["state"] == "done"


def test_run_autopilot_refused_while_busy_naming_the_job(make_studio):  # FR-3
    s = make_studio(Commands("import time; time.sleep(30)"))
    s.client.post(f"/books/{PILOT_SLUG}/run/script")
    r = s.client.post(f"/books/{PILOT_SLUG}/run/autopilot")
    assert r.status_code == 409
    assert "script generation is running for The Lantern Bug Parade" in text_of(r.text)
    assert [c[0] for c in s.commands.calls] == ["script"]


def test_manual_stage_refused_while_autopilot_runs(make_studio):  # FR-3
    s = start_live_autopilot(make_studio)
    for stage in ("script", "paint", "render", "build", "plan"):
        r = s.client.post(f"/books/{PILOT_SLUG}/run/{stage}")
        assert r.status_code == 409, stage
        assert "autopilot is running for The Lantern Bug Parade" in text_of(r.text)
    assert [c[0] for c in s.commands.calls] == ["autopilot"]


def test_script_edit_and_approve_refused_while_autopilot_runs(make_studio):  # FR-3
    s = start_live_autopilot(make_studio)
    scripted(s)
    r = s.client.post(f"/books/{PILOT_SLUG}/script", data={"intro-01": "Changed."})
    assert r.status_code == 409 and "autopilot is running for this book" in text_of(r.text)
    r = s.client.post(f"/books/{PILOT_SLUG}/script/reviewed")
    assert r.status_code == 409
    assert not sb.reviewed_path(s.paths, s.book()).exists()
    r = s.client.post(f"/books/{PILOT_SLUG}/audio/approve")
    assert r.status_code == 409 and "autopilot is running for this book" in text_of(r.text)


# --- stop (AC-1.3) ---------------------------------------------------------------------------


def test_stop_ends_the_run_and_marks_it_stopped(make_studio):  # AC-1.3
    s = start_live_autopilot(make_studio)
    r = s.client.post("/jobs/stop")
    assert r.status_code == 303 and r.headers["location"] == f"/books/{PILOT_SLUG}"
    assert s.runner.current() is None
    assert s.runner.latest(PILOT_SLUG, "autopilot")["state"] == "stopped"
    record = read_run(s.paths)
    assert record["state"] == "stopped" and record["stage"] == "voices"
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "STOPPED" in page and "Resume autopilot" in page


# --- what the book page shows (AC-2.3, AC-4.1-4.3) ------------------------------------------


def test_running_run_shows_its_stage_running(make_studio):
    s = start_live_autopilot(make_studio, stage="voices")
    status = s.app.state.studio.status_of(s.book())
    assert status["voices"]["state"] == "running"
    assert status["next_action"] == "Wait for autopilot"
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "Autopilot" in page and "RUNNING" in page and "voice synthesis" in page
    assert "Run autopilot" not in page.replace("Run autopilot again", "")


def test_failed_run_shows_stage_reason_and_log(make_studio):  # AC-4.1
    s = make_studio(
        Commands("print('== autopilot: voices =='); print('error: page-03-02 clipping')")
    )
    s.client.post(f"/books/{PILOT_SLUG}/run/autopilot")
    s.wait()
    job = s.runner.latest(PILOT_SLUG, "autopilot")
    write_run(
        s.paths, job_id=job["id"], state="failed", stage="voices",
        reason="voice synthesis failed (exit code 1): error: page-03-02 clipping",
        finished_at="2026-11-13T10:06:00+00:00",
    )  # fmt: skip
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "FAILED" in page and "voice synthesis" in page
    assert "page-03-02 clipping" in page
    assert "== autopilot: voices ==" in page  # the job log


def test_failed_run_offers_resume(make_studio):  # AC-4.2
    s = make_studio()
    write_run(s.paths, job_id="old-job", state="failed", stage="render", reason="boom")
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert "Resume autopilot" in text_of(html)
    assert f'action="/books/{PILOT_SLUG}/run/autopilot"' in html


def test_finished_run_says_finished_by_autopilot_with_links(make_studio):  # AC-2.3
    s = make_studio()
    write_run(
        s.paths, job_id="old-job", state="finished",
        finished_at="2026-11-13T11:22:00+00:00",
    )  # fmt: skip
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert "Finished by autopilot at 2026-11-13T11:22:00+00:00" in text_of(html)
    assert f'href="/books/{PILOT_SLUG}/video"' in html
    assert f'href="/books/{PILOT_SLUG}#stage-build"' in html


def test_running_run_file_without_live_job_is_interrupted(make_studio):  # AC-4.3
    s = make_studio()
    write_run(s.paths, job_id="a-job-that-is-gone", stage="render")
    run = sb.autopilot_run(s.paths, PILOT_SLUG, s.runner.current())
    assert run["state"] == "interrupted" and run["stage"] == "render"
    status = s.app.state.studio.status_of(s.book())
    assert status["video"]["state"] != "running"
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "INTERRUPTED" in page and "Resume autopilot" in page


def test_studio_start_marks_running_runs_interrupted(tmp_path):  # AC-4.3
    make_root(tmp_path)
    paths = sb.StudioPaths(tmp_path)
    write_run(paths, job_id="job-from-a-dead-studio", stage="voices")
    s = Studio(tmp_path)
    try:
        assert read_run(paths)["state"] == "interrupted"
        assert s.runner.current() is None  # not resumed by itself
    finally:
        s.runner.close()


def test_no_run_shows_run_autopilot(make_studio):  # AC-1.2
    s = make_studio()
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert "Run autopilot" in text_of(html)
    assert f'action="/books/{PILOT_SLUG}/run/autopilot"' in html
