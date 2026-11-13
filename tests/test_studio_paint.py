"""Spec 016: paint pictures from the studio (the Paint stage, the Pictures panel, Create book).

Stage commands are replaced by tiny `python -c` programs run by a real JobRunner; the real
illustrate CLI (and Codex) never runs."""

from __future__ import annotations

import re
import shutil
import sys
import time

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import make_root
from tests.render_helpers import picture
from tests.test_studio_app import PILOT_SLUG, Commands, Studio, pilot_folder, text_of
from video_pipeline.blueprint import blueprint_sha256, record_use
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

PAGE_FILES = [f"page_{n:02d}.png" for n in range(1, 9)]
COVERS = ["cover_front.png", "cover_back.png"]


def no_jobs(book_slug, stage):
    return None


@pytest.fixture
def paint_studio(tmp_path):
    made = []

    def build(commands: Commands | None = None) -> Studio:
        make_root(tmp_path)
        shutil.copytree(
            PROJECT_ROOT / "assets" / "sound_library", tmp_path / "assets" / "sound_library"
        )  # blueprints are checked against the sound library
        s = Studio(tmp_path, commands)
        made.append(s)
        return s

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()


def paint_files(s: Studio, names: list[str]) -> None:
    for name in names:
        picture(pilot_folder(s.paths.root).illustrations / name)


def paint_of(s: Studio) -> dict:
    return sb.paint_state(s.paths, s.book())


# --- Phase 1: the command and the status (FR-1, FR-4; AC-3.1-3.4) ---------------------------


def test_paint_command_runs_the_illustrate_cli(tmp_path):  # AC-2.1, AC-2.2
    paths = sb.StudioPaths(tmp_path)
    folder = make_root(tmp_path)
    book = sb.get_book(paths, folder.slug)
    cmd = sj.stage_command("paint", book, paths)
    assert cmd[1:] == [
        "-m",
        "video_pipeline.illustrate",
        "--book",
        folder.slug,
        "--root",
        str(tmp_path),
    ]
    assert sj.stage_command("paint", book, paths, force=True)[-1] == "--force"


def test_paint_has_its_own_label_and_is_not_in_the_review_flow():
    assert sb.PAINT_STAGE == "paint"
    assert sb.STAGE_LABELS["paint"] == "picture painting"
    assert "paint" not in sb.STAGES and "paint" not in sb.ANIMATION_STAGES


def test_counts_and_missing_names_for_a_partial_book(paint_studio):  # AC-3.1
    s = paint_studio()
    paint_files(s, PAGE_FILES[:7])
    state = paint_of(s)
    assert state["state"] == "not_started"
    assert state["detail"] == "7 of 8 pages, 0 of 2 covers"
    assert state["missing"] == ["page_08.png", "cover_front.png", "cover_back.png"]


def test_nothing_painted_is_all_missing(paint_studio):  # AC-3.1
    s = paint_studio()
    state = paint_of(s)
    assert state["detail"] == "0 of 8 pages, 0 of 2 covers"
    assert state["missing"] == [*PAGE_FILES, *COVERS]


def test_every_picture_present_is_done(paint_studio):  # AC-3.2
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    state = paint_of(s)
    assert state == {"state": "done", "detail": "8 of 8 pages, 2 of 2 covers", "missing": []}


def test_a_directory_with_a_picture_name_does_not_count(paint_studio):  # FR-4
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, "cover_front.png"])
    (pilot_folder(s.paths.root).illustrations / "cover_back.png").mkdir()
    assert paint_of(s)["missing"] == ["cover_back.png"]


def test_changed_blueprint_art_marks_pictures_out_of_date(paint_studio):  # AC-3.3
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    folder = pilot_folder(s.paths.root)
    write_atomic(folder.blueprint_path, valid_blueprint())
    record_use(folder, "art", blueprint_sha256(folder.blueprint_path))
    assert paint_of(s)["state"] == "done"
    write_atomic(folder.blueprint_path, valid_blueprint(**{"mood.summary": "Hushed and dreamy."}))
    state = paint_of(s)
    assert state["state"] == "out_of_date" and state["missing"] == []
    assert "repaint all" in state["detail"]


def test_missing_pictures_win_over_a_stale_blueprint(paint_studio):  # AC-3.3
    s = paint_studio()
    paint_files(s, PAGE_FILES[:3])
    folder = pilot_folder(s.paths.root)
    write_atomic(folder.blueprint_path, valid_blueprint())
    assert paint_of(s)["state"] == "not_started"


def test_book_without_a_blueprint_and_all_pictures_is_just_done(paint_studio):  # AC-3.4
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    status = sb.stage_status(s.paths, s.book(), no_jobs)
    assert status["pictures"]["state"] == "done"
    assert status["next_action"] == "Generate script"
    assert [status[k]["state"] for k in ("script", "voices", "video")] == ["not_started"] * 3


def test_status_has_pictures_and_next_action_paints_first(paint_studio):  # AC-3.1
    s = paint_studio()
    status = sb.stage_status(s.paths, s.book(), no_jobs)
    assert status["pictures"]["state"] == "not_started"
    assert status["next_action"] == "Paint pictures"


def test_running_paint_job_shows_and_next_action_waits(paint_studio):  # AC-2.3
    s = paint_studio()
    job = {"state": "running", "started_at": "2026-11-13T10:00:00+00:00"}

    def jobs(slug, stage):
        return job if stage == "paint" else None

    status = sb.stage_status(s.paths, s.book(), jobs)
    assert status["pictures"]["state"] == "running"
    assert status["pictures"]["missing"]  # the file names stay known while painting
    assert status["next_action"] == "Wait for picture painting to finish"


def test_failed_paint_job_shows_failed_until_a_newer_output(paint_studio):  # AC-2.5
    s = paint_studio()
    job = {
        "state": "failed",
        "exit_code": 1,
        "started_at": "2020-01-01T10:00:00+00:00",
        "ended_at": "2020-01-01T10:01:00+00:00",
    }

    def jobs(slug, stage):
        return job if stage == "paint" else None

    assert sb.stage_status(s.paths, s.book(), jobs)["pictures"]["state"] == "failed"
    paint_files(s, [*PAGE_FILES, *COVERS])  # written after the job ended: the files win
    assert sb.stage_status(s.paths, s.book(), jobs)["pictures"]["state"] == "done"


def test_a_book_with_broken_data_has_pictures_not_started(paint_studio):
    s = paint_studio()
    folder = pilot_folder(s.paths.root)
    folder.book_json.write_text("{ nope", encoding="utf-8")
    status = sb.stage_status(s.paths, s.book(), no_jobs)
    assert status["pictures"]["state"] == "not_started"
    assert status["next_action"] == "Fix the book data"


def test_paint_is_a_known_stage_in_stage_command_errors(tmp_path):
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, make_root(tmp_path).slug)
    with pytest.raises(ValueError, match="paint"):
        sj.stage_command("nope", book, paths)


# --- Phase 2: the run route and the Pictures panel (FR-1, FR-3; AC-2.1-2.5, AC-3.x) ----------

SLOW = "import time; print('painting', flush=True); time.sleep(30)"


def run_paint(s: Studio, **form):
    return s.client.post(f"/books/{PILOT_SLUG}/run/paint", data=form)


def book_page(s: Studio) -> str:
    r = s.client.get(f"/books/{PILOT_SLUG}")
    assert r.status_code == 200
    return r.text


def test_paint_runs_the_illustrate_command_for_missing_pictures(paint_studio):  # AC-2.1
    commands = Commands()
    s = paint_studio(commands)
    r = run_paint(s)
    assert r.status_code == 303 and r.headers["location"] == f"/books/{PILOT_SLUG}"
    s.wait()
    assert commands.calls == [("paint", PILOT_SLUG, False)]
    assert s.runner.latest(PILOT_SLUG, "paint")["state"] == "done"


def test_repaint_all_without_confirmation_is_refused(paint_studio):  # AC-2.2, FR-3
    commands = Commands()
    s = paint_studio(commands)
    paint_files(s, [*PAGE_FILES, *COVERS])
    r = run_paint(s, force="1")
    assert r.status_code == 409
    assert "Confirm repainting every picture" in text_of(r.text)
    assert commands.calls == [] and s.runner.current() is None
    assert s.runner.latest(PILOT_SLUG, "paint") is None


def test_repaint_all_with_confirmation_runs_with_force(paint_studio):  # AC-2.2
    commands = Commands()
    s = paint_studio(commands)
    paint_files(s, [*PAGE_FILES, *COVERS])
    assert run_paint(s, force="1", confirm="1").status_code == 303
    s.wait()
    assert commands.calls == [("paint", PILOT_SLUG, True)]


def test_confirm_alone_does_not_force(paint_studio):  # AC-2.2
    commands = Commands()
    s = paint_studio(commands)
    assert run_paint(s, confirm="1").status_code == 303
    s.wait()
    assert commands.calls == [("paint", PILOT_SLUG, False)]


def test_paint_refused_while_any_job_runs_and_names_it(paint_studio):  # AC-2.4
    commands = Commands(SLOW)
    s = paint_studio(commands)
    s.runner.start(PILOT_SLUG, "script", [sys.executable, "-c", SLOW])
    r = run_paint(s)
    assert r.status_code == 409
    page = text_of(r.text)
    assert "script generation is running" in page and "Lantern Bug Parade" in page
    assert commands.calls == []
    assert s.runner.current()["stage"] == "script"


def test_second_paint_while_painting_is_refused(paint_studio):  # AC-2.4
    s = paint_studio(Commands(SLOW))
    assert run_paint(s).status_code == 303
    r = run_paint(s, force="1", confirm="1")
    assert r.status_code == 409 and "picture painting is running" in text_of(r.text)


def test_running_paint_shows_its_live_log_and_stop_keeps_painted_pictures(
    paint_studio, tmp_path
):  # AC-2.3
    code = (
        "import pathlib, time\n"
        f"d = pathlib.Path({str(pilot_folder(tmp_path).illustrations)!r}); d.mkdir(parents=True)\n"
        "(d / 'page_01.png').write_bytes(b'x')\n"
        "print('painted page 1', flush=True); time.sleep(30)"
    )
    s = paint_studio(Commands(code))
    assert run_paint(s).status_code == 303
    for _ in range(100):
        page = book_page(s)
        if "painted page 1" in page:
            break
        time.sleep(0.05)
    assert "data-job-id" in page and 'data-live="1"' in page
    assert "RUNNING" in text_of(page) and "painting" in text_of(page).lower()
    assert re.search(r"<button[^>]*disabled[^>]*>\s*Paint pictures", page)
    r = s.client.post("/jobs/stop")
    assert r.status_code == 303
    assert s.runner.current() is None
    assert (pilot_folder(tmp_path).illustrations / "page_01.png").is_file()
    assert s.runner.latest(PILOT_SLUG, "paint")["state"] == "stopped"


def test_failed_paint_job_shows_failed_and_its_reason(paint_studio):  # AC-2.5
    code = "import sys; print('codex is not logged in', flush=True); sys.exit(3)"
    s = paint_studio(Commands(code))
    assert run_paint(s).status_code == 303
    s.wait()
    page = book_page(s)
    assert "FAILED" in text_of(page)
    assert "codex is not logged in" in page and "exit code 3" in page


def test_pictures_panel_counts_and_names_missing_files(paint_studio):  # AC-3.1
    s = paint_studio()
    paint_files(s, PAGE_FILES[:7])
    page = book_page(s)
    text = text_of(page)
    assert "Pictures" in text and "7 of 8 pages, 0 of 2 covers" in text
    for name in ("page_08.png", "cover_front.png", "cover_back.png"):
        assert name in text
    assert "NOT STARTED" in text
    assert re.search(r"<button[^>]*>\s*Paint pictures", page)
    assert not re.search(r"<button[^>]*disabled[^>]*>\s*Paint pictures", page)
    # the Pictures panel sits above Script in the rail
    assert page.index('id="stage-paint"') < page.index('id="stage-script"')


def test_pictures_panel_marks_done_and_offers_repaint_all_with_confirmation(paint_studio):  # AC-3.2
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    page = book_page(s)
    text = text_of(page)
    assert "8 of 8 pages, 2 of 2 covers" in text and "DONE" in text
    assert "Repaint all" in text
    form = re.search(
        r'<form[^>]*run/paint[^>]*>(?:(?!</form>).)*name="force"(?:(?!</form>).)*</form>',
        page,
        re.S,
    )
    assert form, "a Repaint all form posting force=1"
    assert 'name="confirm"' in form.group(0) and "required" in form.group(0)
    assert "Missing:" not in text


def test_pictures_panel_marks_a_changed_blueprint_and_offers_repaint_all(paint_studio):  # AC-3.3
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    folder = pilot_folder(s.paths.root)
    write_atomic(folder.blueprint_path, valid_blueprint())
    record_use(folder, "art", blueprint_sha256(folder.blueprint_path))
    assert "OUT OF DATE" not in text_of(book_page(s))
    write_atomic(folder.blueprint_path, valid_blueprint(**{"mood.summary": "Hushed and dreamy."}))
    text = text_of(book_page(s))
    assert "OUT OF DATE" in text and "older blueprint" in text and "Repaint all" in text


def test_book_with_all_pictures_and_no_blueprint_only_adds_the_done_panel(paint_studio):  # AC-3.4
    s = paint_studio()
    paint_files(s, [*PAGE_FILES, *COVERS])
    text = text_of(book_page(s))
    assert "stale" not in text.lower() and "older blueprint" not in text
    assert "Next Generate script" in text


def test_pictures_flow_step_follows_book_and_is_current_until_painted(paint_studio):
    s = paint_studio()
    page = book_page(s)
    assert page.index("lane-book") < page.index("lane-pictures") < page.index("lane-script")
    assert re.search(r"lane-pictures is-current", page)
    paint_files(s, [*PAGE_FILES, *COVERS])
    page = book_page(s)
    assert "lane-pictures is-current" not in page and "lane-script is-current" in page
    assert 'href="#stage-paint"' not in page  # the Go to next step link now points at the script
    assert 'href="#stage-script"' in page
