"""Phase 2 of spec 004: the one-at-a-time job runner and the stage commands.

Jobs run tiny `python -c` commands in tmp dirs; no pipeline stage is ever run."""

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

PY = sys.executable


def py(code: str) -> list[str]:
    return [PY, "-c", code]


@pytest.fixture
def studio(tmp_path):
    return tmp_path / "build" / "studio"


@pytest.fixture
def runner(studio, tmp_path):
    r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
    yield r
    if r.current():
        r.stop()
    r.close()


def read_record(studio: Path, job_id: str) -> dict:
    return json.loads((studio / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))


def full_log(runner, job_id: str) -> str:
    text, _ = runner.log(job_id, 0)
    return text


# --- running a job ----------------------------------------------------------------------------


def test_start_runs_to_done_with_log_and_record(runner, studio, tmp_path):
    job = runner.start("moon", "script", py("import os; print('hello'); print(os.getcwd())"))
    assert re.fullmatch(r"\d{8}T\d{6}-moon-script-[0-9a-f]{4}", job.id)
    assert job.state == "running"
    assert runner.wait(10)
    record = read_record(studio, job.id)
    assert record["state"] == "done"
    assert record["exit_code"] == 0
    assert record["book"] == "moon" and record["stage"] == "script"
    assert record["cmd"][0] == PY
    assert record["started_at"] and record["ended_at"]
    log = full_log(runner, job.id)
    assert "hello" in log
    assert str(tmp_path.resolve()) in log or str(tmp_path) in log
    assert runner.current() is None
    assert runner.latest("moon", "script")["id"] == job.id


def test_job_logs_unicode_on_windows(runner):
    message = "Pig says \u201chello\u201d \u732a"
    job = runner.start("moon", "script", py(f"print({message!r})"))
    assert runner.wait(10)
    assert runner.latest("moon", "script")["exit_code"] == 0
    assert message in full_log(runner, job.id)


def test_failing_command_is_failed_with_exit_code_and_stderr(runner, studio):
    code = "import sys; print('to out'); print('boom', file=sys.stderr); sys.exit(3)"
    job = runner.start("moon", "voices", py(code))
    assert runner.wait(10)
    record = read_record(studio, job.id)
    assert record["state"] == "failed"
    assert record["exit_code"] == 3
    log = full_log(runner, job.id)
    assert "to out" in log and "boom" in log


def test_missing_program_fails_without_raising(runner, studio, tmp_path):
    job = runner.start("moon", "render", [str(tmp_path / "no-such-program")])
    assert runner.wait(10)
    record = read_record(studio, job.id)
    assert record["state"] == "failed"
    assert "no-such-program" in full_log(runner, job.id)


def test_environment_is_unbuffered_and_extra_env_passed(studio, tmp_path):
    r = sj.JobRunner(studio, env={"KIDDO_TEST_VAR": "xyz"}, cwd=tmp_path)
    code = "import os; print(os.environ['PYTHONUNBUFFERED'], os.environ['KIDDO_TEST_VAR'])"
    job = r.start("moon", "script", py(code))
    assert r.wait(10)
    assert "1 xyz" in full_log(r, job.id)


def test_second_start_while_running_is_busy(runner):
    first = runner.start("moon", "script", py("import time; time.sleep(30)"))
    with pytest.raises(sj.Busy) as info:
        runner.start("other", "render", py("print(1)"))
    assert info.value.current["id"] == first.id
    assert runner.current()["id"] == first.id
    assert runner.current()["state"] == "running"


def test_stop_terminates_the_process_group(runner, studio, tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(c.pid))\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    job = runner.start("moon", "render", py(code))
    deadline = time.monotonic() + 10
    while not pid_file.exists() or not pid_file.read_text():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    child = int(pid_file.read_text())
    stopped = runner.stop()
    assert stopped["id"] == job.id
    assert stopped["state"] == "stopped"
    assert stopped["exit_code"] is not None and stopped["exit_code"] != 0
    assert read_record(studio, job.id)["state"] == "stopped"
    assert runner.current() is None
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "grandchild still alive after stop"
        time.sleep(0.05)


def test_stop_escalates_to_sigkill_when_term_is_ignored(runner, studio):
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ignoring', flush=True)\n"
        "time.sleep(60)\n"
    )
    job = runner.start("moon", "render", py(code))
    deadline = time.monotonic() + 10
    while "ignoring" not in full_log(runner, job.id):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    started = time.monotonic()
    stopped = runner.stop()
    assert time.monotonic() - started < 5
    assert stopped["state"] == "stopped"
    assert stopped["exit_code"] == -9


def test_stop_with_nothing_running_returns_none(runner):
    assert runner.stop() is None


def test_can_start_again_after_finish(runner):
    runner.start("moon", "script", py("print(1)"))
    assert runner.wait(10)
    second = runner.start("moon", "script", py("print(2)"))
    assert runner.wait(10)
    assert runner.latest("moon", "script")["id"] == second.id


# --- persistence --------------------------------------------------------------------------------


def test_stale_running_record_becomes_interrupted(studio, tmp_path):
    jobs = studio / "jobs"
    jobs.mkdir(parents=True)
    stale = {
        "id": "20261109T100000-moon-voices-abcd",
        "book": "moon",
        "stage": "voices",
        "cmd": ["x"],
        "started_at": "2026-11-09T10:00:00+00:00",
        "ended_at": None,
        "state": "running",
        "exit_code": None,
    }
    (jobs / f"{stale['id']}.json").write_text(json.dumps(stale), encoding="utf-8")
    done = dict(stale, id="20261109T090000-moon-script-abcd", stage="script", state="done")
    done["ended_at"], done["exit_code"] = "2026-11-09T09:01:00+00:00", 0
    (jobs / f"{done['id']}.json").write_text(json.dumps(done), encoding="utf-8")

    r = sj.JobRunner(studio, cwd=tmp_path)
    record = read_record(studio, stale["id"])
    assert record["state"] == "interrupted"
    assert record["ended_at"]
    assert read_record(studio, done["id"])["state"] == "done"
    assert r.latest("moon", "voices")["state"] == "interrupted"
    assert r.current() is None
    assert not list(jobs.glob("*.tmp"))


def test_latest_survives_restart_and_picks_newest(studio, tmp_path):
    r = sj.JobRunner(studio, cwd=tmp_path)
    r.start("moon", "script", py("print('a')"))
    assert r.wait(10)
    second = r.start("moon", "script", py("print('b')"))
    assert r.wait(10)
    r.start("moon", "voices", py("print('c')"))
    assert r.wait(10)
    r.close()  # the studio shuts down; a new one starts on the same directory
    again = sj.JobRunner(studio, cwd=tmp_path)
    assert again.latest("moon", "script")["id"] == second.id
    assert again.latest("moon", "render") is None
    assert again.latest("nobody", "script") is None


def test_latest_on_empty_studio_is_none(tmp_path):
    r = sj.JobRunner(tmp_path / "nothing-here", cwd=tmp_path)
    assert r.latest("moon", "script") is None


def test_latest_can_feed_stage_status_shape(runner):
    runner.start("moon", "script", py("import sys; sys.exit(1)"))
    assert runner.wait(10)
    record = runner.latest("moon", "script")
    assert record["state"] in sb.JOB_OVERRIDES


# --- logs -----------------------------------------------------------------------------------------


def test_log_offsets_stream_incrementally(runner, tmp_path):
    gate = tmp_path / "gate"
    code = (
        "import os, time\n"
        "print('first line', flush=True)\n"
        f"while not os.path.exists({str(gate)!r}): time.sleep(0.02)\n"
        "print('second line', flush=True)\n"
    )
    job = runner.start("moon", "script", py(code))
    deadline = time.monotonic() + 10
    text, offset = "", 0
    while "first line" not in text:
        assert time.monotonic() < deadline
        chunk, offset = runner.log(job.id, offset)
        text += chunk
        time.sleep(0.02)
    assert "second line" not in text
    chunk, same = runner.log(job.id, offset)
    assert chunk == "" and same == offset
    gate.write_text("go")
    assert runner.wait(10)
    chunk, end = runner.log(job.id, offset)
    assert "second line" in chunk and "first line" not in chunk
    assert end > offset
    assert runner.log(job.id, end) == ("", end)


def test_log_strips_ansi_and_collapses_carriage_returns(runner, studio):
    code = (
        "import sys\n"
        "sys.stdout.write('\\x1b[32mgreen\\x1b[0m text\\n')\n"
        "sys.stdout.write('10%\\r50%\\r100% done\\n')\n"
        "sys.stdout.write('windows line\\r\\n')\n"
    )
    job = runner.start("moon", "render", py(code))
    assert runner.wait(10)
    text = full_log(runner, job.id)
    assert text.splitlines() == ["green text", "100% done", "windows line"]
    raw = (studio / "jobs" / f"{job.id}.log").read_bytes()
    assert b"\x1b[32m" in raw and b"10%\r50%" in raw


def test_log_unknown_or_unsafe_id_is_refused(runner):
    with pytest.raises(sj.UnknownJob):
        runner.log("20261109T100000-moon-script-abcd", 0)
    with pytest.raises(sj.UnknownJob):
        runner.log("../../etc/passwd", 0)


def test_clean_log_text_is_pure():
    assert sj.clean_log_text("a\x1b[1;31mb\x1b[0m\nx\ry\rz\n") == "ab\nz\n"
    assert sj.clean_log_text("keep\r\n") == "keep\n"


# --- stage commands -------------------------------------------------------------------------------


def book_and_added(tmp_path):
    paths = sb.StudioPaths(tmp_path)
    pilot = sb.Book(
        "the-lantern-bug-parade",
        "The Lantern Bug Parade",
        paths.books_dir / "the-lantern-bug-parade" / "book.json",
    )
    added = sb.Book("moon-kite", "Moon Kite", paths.books_dir / "moon-kite" / "book.json")
    return paths, pilot, added


def test_stage_command_script(tmp_path):
    paths, pilot, _ = book_and_added(tmp_path)
    cmd = sj.stage_command("script", pilot, paths)
    assert cmd[:3] == [sys.executable, "-m", "video_pipeline.generate_script"]
    assert cmd[3:] == ["--book", pilot.slug, "--root", str(paths.root)]
    assert sj.stage_command("script", pilot, paths, force=True)[-1] == "--force"


def test_stage_command_voices_parses_with_the_real_cli(tmp_path):
    paths, _, added = book_and_added(tmp_path)
    cmd = sj.stage_command("voices", added, paths, force=True)
    assert cmd[:3] == [sys.executable, "-m", "video_pipeline.synthesize_voices"]
    args = sv.parse_args(cmd[3:])
    assert args.command == "synthesize"
    assert args.book == added.slug
    assert args.root == paths.root
    assert args.reviewed is True and args.force is True
    assert "--force" not in sj.stage_command("voices", added, paths)


def test_stage_command_render_for_pilot_and_added_book(tmp_path):
    paths, pilot, added = book_and_added(tmp_path)
    for book in (pilot, added):
        cmd = sj.stage_command("render", book, paths)
        assert cmd[:3] == [sys.executable, "-m", "video_pipeline.render_episode"]
        args = re_.parse_args(cmd[3:])
        assert args.book == book.slug
        assert args.root == paths.root
        assert args.sfx_dir == paths.sfx_dir
        assert args.force is False
    assert re_.parse_args(sj.stage_command("render", added, paths, force=True)[3:]).force


def test_stage_command_preview_names_the_page(tmp_path):  # spec 010 FR-16
    paths, _, added = book_and_added(tmp_path)
    cmd = sj.stage_command("preview", added, paths, page=4)
    assert cmd[:3] == [sys.executable, "-m", "video_pipeline.render_episode"]
    args = re_.parse_args(cmd[3:])
    assert args.book == added.slug and args.root == paths.root
    assert args.preview_page == 4 and args.force is False
    assert cmd[-2:] == ["--preview-page", "4"]
    with pytest.raises(ValueError, match="page"):
        sj.stage_command("preview", added, paths)
    assert "--preview-page" not in sj.stage_command("render", added, paths, page=4)


def test_a_job_records_its_page_and_latest_filters_by_it(runner, studio):  # spec 010 FR-16
    first = runner.start("moon", "preview", py("print('4')"), page=4)
    assert runner.wait(10)
    second = runner.start("moon", "preview", py("print('5')"), page=5)
    assert runner.wait(10)
    assert read_record(studio, first.id)["page"] == 4
    assert runner.latest("moon", "preview", page=4)["id"] == first.id
    assert runner.latest("moon", "preview", page=5)["id"] == second.id
    assert runner.latest("moon", "preview")["id"] == second.id
    assert runner.latest("moon", "preview", page=6) is None
    plain = runner.start("moon", "script", py("print('s')"))
    assert runner.wait(10)
    assert read_record(studio, plain.id)["page"] is None


def test_stage_command_unknown_stage(tmp_path):
    paths, pilot, _ = book_and_added(tmp_path)
    with pytest.raises(ValueError):
        sj.stage_command("upload", pilot, paths)


# --- review fixes (round 1) -------------------------------------------------------------------


def _stale_record(jobs: Path, pid: int | None, proc_started: str | None = None) -> dict:
    jobs.mkdir(parents=True, exist_ok=True)
    stale = {
        "id": "20261109T100000-moon-render-beef",
        "book": "moon",
        "stage": "render",
        "cmd": ["x"],
        "started_at": "2026-11-09T10:00:00+00:00",
        "ended_at": None,
        "state": "running",
        "exit_code": None,
        "pid": pid,
        "proc_started": proc_started,
    }
    (jobs / f"{stale['id']}.json").write_text(json.dumps(stale), encoding="utf-8")
    return stale


def test_orphaned_stage_is_killed_on_restart(studio, tmp_path):
    import subprocess
    import threading

    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    orphan = subprocess.Popen([*py(code), "video_pipeline.render_episode"], start_new_session=True)
    reaper = threading.Thread(target=orphan.wait, daemon=True)
    reaper.start()
    try:
        time.sleep(0.3)  # let it install the SIGTERM handler
        stale = _stale_record(studio / "jobs", orphan.pid, sj._process_started(orphan.pid))
        started = time.monotonic()
        r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
        assert time.monotonic() - started < 5
        reaper.join(5)
        assert orphan.returncode == -9  # TERM ignored, so killed after the grace period
        record = read_record(studio, stale["id"])
        assert record["state"] == "interrupted"
        assert f"process group {orphan.pid}" in full_log(r, stale["id"])
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_stale_record_with_a_dead_pid_is_only_marked(studio, tmp_path):
    import subprocess

    gone = subprocess.Popen(py("pass"), start_new_session=True)
    gone.wait()
    stale = _stale_record(studio / "jobs", gone.pid)
    r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
    assert read_record(studio, stale["id"])["state"] == "interrupted"
    assert "process group" not in full_log(r, stale["id"])


def test_interrupted_record_ends_when_its_log_last_changed(studio, tmp_path):
    stale = _stale_record(studio / "jobs", None)
    log = studio / "jobs" / f"{stale['id']}.log"
    log.write_text("working\n", encoding="utf-8")
    os.utime(log, (1_700_000_000, 1_700_000_000))
    sj.JobRunner(studio, cwd=tmp_path)
    from datetime import datetime

    ended = datetime.fromisoformat(read_record(studio, stale["id"])["ended_at"])
    assert abs(ended.timestamp() - 1_700_000_000) < 1


# --- review fixes (round 2) -------------------------------------------------------------------

LOCK_PROBE = """
import sys
from pathlib import Path
from video_pipeline.studio import jobs as sj
try:
    sj.JobRunner(Path(sys.argv[1]), stop_grace=0.5)
except sj.StudioLocked as exc:
    print("locked:", exc)
else:
    print("started")
"""


def _probe_lock(studio: Path) -> str:
    import subprocess

    out = subprocess.run(
        [PY, "-c", LOCK_PROBE, str(studio)], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_second_studio_process_is_locked_out_and_leaves_the_live_stage_alone(runner, studio):
    job = runner.start("moon", "script", py("import time; time.sleep(30)"))
    time.sleep(0.3)
    assert _probe_lock(studio) == "locked: another studio is already running on this project"
    current = runner.current()
    assert current is not None and current["id"] == job.id
    assert read_record(studio, job.id)["state"] == "running"
    if os.name == "nt":
        import psutil

        assert psutil.pid_exists(job.pid)
    else:
        os.killpg(job.pid, 0)  # still alive


def test_second_runner_in_the_same_process_is_locked(runner, studio, tmp_path):
    with pytest.raises(sj.StudioLocked, match="another studio is already running"):
        sj.JobRunner(studio, cwd=tmp_path)


def test_close_releases_the_lock(studio, tmp_path):
    first = sj.JobRunner(studio, cwd=tmp_path)
    first.close()
    first.close()  # closing twice is harmless
    assert _probe_lock(studio) == "started"
    second = sj.JobRunner(studio, cwd=tmp_path)
    second.close()


def test_locked_runner_does_not_touch_stale_records(runner, studio, tmp_path):
    stale = _stale_record(studio / "jobs", None)
    with pytest.raises(sj.StudioLocked):
        sj.JobRunner(studio, cwd=tmp_path)
    assert read_record(studio, stale["id"])["state"] == "running"


def test_start_records_the_process_start_time(runner, studio):
    job = runner.start("moon", "script", py("import time; time.sleep(30)"))
    record = read_record(studio, job.id)
    assert record["proc_started"]
    assert record["proc_started"] == sj._process_started(job.pid)


def _sleeper(*extra: str):
    import subprocess

    proc = subprocess.Popen([*py("import time; time.sleep(60)"), *extra], start_new_session=True)
    time.sleep(0.2)
    return proc


def test_orphan_that_is_not_a_pipeline_stage_is_only_marked(studio, tmp_path):
    other = _sleeper()
    try:
        stale = _stale_record(studio / "jobs", other.pid, sj._process_started(other.pid))
        r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
        assert other.poll() is None
        assert read_record(studio, stale["id"])["state"] == "interrupted"
        assert "process group" not in full_log(r, stale["id"])
        r.close()
    finally:
        other.kill()
        other.wait()


def test_orphan_with_another_start_time_is_only_marked(studio, tmp_path):
    reused = _sleeper("video_pipeline.render_episode")
    try:
        stale = _stale_record(studio / "jobs", reused.pid, "Mon Jan  1 00:00:00 2001")
        r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
        assert reused.poll() is None
        assert read_record(studio, stale["id"])["state"] == "interrupted"
        r.close()
    finally:
        reused.kill()
        reused.wait()


def test_orphan_without_a_recorded_start_time_is_only_marked(studio, tmp_path):
    old = _sleeper("video_pipeline.render_episode")
    try:
        _stale_record(studio / "jobs", old.pid)
        r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
        assert old.poll() is None
        r.close()
    finally:
        old.kill()
        old.wait()


def test_stage_command_plan_cut_and_ground(tmp_path):  # spec 009 AC-5.4, spec 025 AC-4.3
    from video_pipeline import cut_layers, ground_camera, motion_plan

    paths, pilot, _ = book_and_added(tmp_path)
    for stage, module in (("plan", motion_plan), ("cut", cut_layers), ("ground", ground_camera)):
        cmd = sj.stage_command(stage, pilot, paths)
        assert cmd == [
            sys.executable,
            "-m",
            module.__name__,
            "--book",
            pilot.slug,
            "--root",
            str(paths.root),
        ]
        assert sj.stage_command(stage, pilot, paths, force=True)[-1] == "--force"


# --- spec 019: the book build runs a script, not a module -----------------------------------


def test_stage_markers_cover_the_book_builder():
    assert sj.STAGE_MARKERS == ("video_pipeline", "build_book.py")


def test_orphaned_build_is_recognised_by_its_marker(studio, tmp_path):
    orphan = _sleeper("scripts/build_book.py")
    try:
        stale = _stale_record(studio / "jobs", orphan.pid, sj._process_started(orphan.pid))
        r = sj.JobRunner(studio, cwd=tmp_path, stop_grace=0.5)
        orphan.wait(5)
        assert orphan.returncode is not None
        assert read_record(studio, stale["id"])["state"] == "interrupted"
        assert f"process group {orphan.pid}" in full_log(r, stale["id"])
        r.close()
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_job_env_carries_the_job_id(runner, studio):  # spec 021 FR-2: the autopilot's run file
    job = runner.start("moon", "autopilot", py("import os; print(os.environ['KIDDO_JOB_ID'])"))
    assert runner.wait(10)
    assert full_log(runner, job.id).strip() == job.id


# --- spec 022: the print cover is a repaint side ------------------------------------------------


def test_repaint_print_cover_command(tmp_path):
    from tests.helpers import make_root

    paths = sb.StudioPaths(tmp_path)
    folder = make_root(tmp_path)
    book = sb.get_book(paths, folder.slug)
    cmd = sj.stage_command("repaint", book, paths, cover="print")
    assert cmd[-2:] == ["--cover", "print"] and "--cut" not in cmd
    with pytest.raises(ValueError, match="expected front, print or back"):
        sj.stage_command("repaint", book, paths, cover="spine")
