"""Spec 019, Phase 2: repaint one page (or a cover) and edit a page's picture prompt in the studio.

Stage commands are replaced by tiny `python -c` programs run by a real JobRunner (the `Commands`
fake); the real illustrate CLI, Codex and the cut models never run."""

from __future__ import annotations

import json
import sys

import pytest

from tests.helpers import make_root
from tests.render_helpers import FakeRunner, cli_args, picture
from tests.test_studio_app import (
    PILOT_SLUG,
    Commands,
    Studio,
    pilot_folder,
    pilot_path,
    text_of,
    with_world,
)
from video_pipeline import render_episode as re_
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

SLOW = "import time; print('repainting', flush=True); time.sleep(30)"


@pytest.fixture
def studio(tmp_path):
    made = []

    def build(commands: Commands | None = None) -> Studio:
        make_root(tmp_path)
        s = Studio(tmp_path, commands)
        made.append(s)
        return s

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()


def repaint(s: Studio, number: int = 3, confirm: bool = True):
    data = {"confirm": "1"} if confirm else {}
    return s.client.post(f"/books/{PILOT_SLUG}/animation/{number}/repaint", data=data)


# --- the command (FR-1, FR-2) ------------------------------------------------------------------


def test_repaint_stage_contract():
    assert sb.REPAINT_STAGE == "repaint"
    assert sb.STAGE_LABELS["repaint"] == "picture repaint"
    assert sj.STAGE_MODULES["repaint"] == "video_pipeline.illustrate"


def test_repaint_command_needs_page_or_cover(tmp_path):
    paths = sb.StudioPaths(tmp_path)
    folder = make_root(tmp_path)
    book = sb.get_book(paths, folder.slug)
    page = sj.stage_command("repaint", book, paths, page=3)
    assert page[1:3] == ["-m", "video_pipeline.illustrate"]
    assert page[-3:] == ["--page", "3", "--cut"]
    assert "--force" not in page
    cover = sj.stage_command("repaint", book, paths, cover="back")
    assert cover[-2:] == ["--cover", "back"] and "--cut" not in cover
    for kwargs in ({}, {"page": 3, "cover": "front"}, {"cover": "side"}):
        with pytest.raises(ValueError):
            sj.stage_command("repaint", book, paths, **kwargs)


# --- starting a repaint (AC-1.1, AC-1.3, AC-1.4, AC-1.6) -------------------------------------


def test_repaint_page_runs_one_job_with_page_and_cut(studio):  # AC-1.1
    s = studio()
    r = repaint(s, 3)
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}/animation/3"
    s.wait()
    assert s.commands.calls == [("repaint", PILOT_SLUG, 3)]
    job = s.runner.latest(PILOT_SLUG, "repaint", 3)
    assert job["state"] == "done" and job["page"] == 3
    real = sj.stage_command("repaint", s.book(), s.paths, page=3)
    assert real[-3:] == ["--page", "3", "--cut"]


def test_repaint_without_confirm_is_refused_and_nothing_starts(studio):  # AC-1.3
    s = studio()
    r = repaint(s, 3, confirm=False)
    assert r.status_code == 409
    assert "Confirm repainting this picture" in text_of(r.text)
    r = s.client.post(f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "front"})
    assert r.status_code == 409
    assert s.commands.calls == [] and s.runner.current() is None
    assert s.runner.latest(PILOT_SLUG, "repaint") is None


@pytest.mark.parametrize("side", ["front", "back"])
def test_repaint_cover_runs_cover_only(studio, side):  # AC-1.4
    s = studio()
    r = s.client.post(f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": side, "confirm": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}"
    s.wait()
    assert s.commands.calls == [("repaint", PILOT_SLUG, f"cover:{side}")]


def test_repaint_cover_refuses_an_unknown_side(studio):
    s = studio()
    r = s.client.post(f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "spine", "confirm": "1"})
    assert r.status_code == 404
    assert s.commands.calls == []


def test_repaint_of_an_unknown_page_is_404(studio):
    s = studio()
    assert repaint(s, 42).status_code == 404
    assert s.commands.calls == []


def test_repaint_refused_while_any_job_runs_naming_it(studio):  # AC-1.6
    s = studio(Commands(SLOW))
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    r = repaint(s, 3)
    assert r.status_code == 409
    assert "script generation is running for The Lantern Bug Parade" in text_of(r.text)
    r = s.client.post(f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "back", "confirm": "1"})
    assert r.status_code == 409
    assert [c[0] for c in s.commands.calls] == ["script"]


def test_repaint_blocked_for_a_broken_book(studio):
    s = studio()
    data = json.loads(pilot_path(s.paths.root).read_text(encoding="utf-8"))
    data["metadata"]["title"] = ""
    pilot_path(s.paths.root).write_text(json.dumps(data), encoding="utf-8")
    r = s.client.post(f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "back", "confirm": "1"})
    assert r.status_code == 409
    assert s.commands.calls == []


# --- the picture prompt (AC-1.2) ---------------------------------------------------------------


def test_save_prompt_writes_book_json_by_page_number(studio):  # AC-1.2
    s = studio()
    path = pilot_path(s.paths.root)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert sb.save_page_prompt(s.paths, s.book(), 3, "A new prompt for page three.") == []
    saved = json.loads(path.read_text(encoding="utf-8"))
    for before, after in zip(data["pages"], saved["pages"], strict=True):
        if after["page_number"] == 3:
            assert after["prompt"] == "A new prompt for page three."
            assert {**after, "prompt": before["prompt"]} == before
        else:
            assert after == before
    assert {k: v for k, v in saved.items() if k != "pages"} == {
        k: v for k, v in data.items() if k != "pages"
    }
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_prompt_through_the_page_view(studio):  # AC-1.2
    s = studio()
    url = f"/books/{PILOT_SLUG}/animation/3/prompt"
    r = s.client.post(url, data={"prompt": "Clara waves from a red post box."})
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}/animation/3"
    saved = json.loads(pilot_path(s.paths.root).read_text(encoding="utf-8"))
    page = next(p for p in saved["pages"] if p["page_number"] == 3)
    assert page["prompt"] == "Clara waves from a red post box."
    assert s.commands.calls == []  # saved without repainting


@pytest.mark.parametrize("text", ["", "   \n "])
def test_save_prompt_with_problems_writes_nothing(studio, text):
    s = studio()
    before = pilot_path(s.paths.root).read_bytes()
    problems = sb.save_page_prompt(s.paths, s.book(), 3, text)
    assert problems and "prompt" in problems[0]
    assert pilot_path(s.paths.root).read_bytes() == before
    r = s.client.post(f"/books/{PILOT_SLUG}/animation/3/prompt", data={"prompt": text})
    assert r.status_code == 422
    assert "prompt" in text_of(r.text)
    assert pilot_path(s.paths.root).read_bytes() == before


def test_save_prompt_for_an_unknown_page_is_refused(studio):
    s = studio()
    before = pilot_path(s.paths.root).read_bytes()
    assert sb.save_page_prompt(s.paths, s.book(), 42, "x") == ["page 42 is not in the book"]
    assert pilot_path(s.paths.root).read_bytes() == before


@pytest.mark.parametrize("stage", ["paint", "repaint"])
def test_save_prompt_refused_while_painting(studio, stage):
    s = studio()
    before = pilot_path(s.paths.root).read_bytes()
    s.runner.start(PILOT_SLUG, stage, [sys.executable, "-c", SLOW])
    r = s.client.post(f"/books/{PILOT_SLUG}/animation/3/prompt", data={"prompt": "new"})
    assert r.status_code == 409
    assert "running for this book" in text_of(r.text)
    assert pilot_path(s.paths.root).read_bytes() == before


def test_page_view_shows_prompt_and_repaint_log(studio):
    s = studio(Commands("print('repainted page_03'); print('plan written')"))
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/3").text
    data = json.loads(pilot_path(s.paths.root).read_text(encoding="utf-8"))
    prompt = next(p for p in data["pages"] if p["page_number"] == 3)["prompt"]
    assert "<textarea" in html and 'name="prompt"' in html
    assert text_of(prompt).strip() in text_of(html)
    assert f'action="/books/{PILOT_SLUG}/animation/3/repaint"' in html
    assert 'name="confirm"' in html and "required" in html
    assert repaint(s, 3).status_code == 303
    s.wait()
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}/animation/3").text)
    assert "repainted page_03" in page
    assert "repainted page_03" not in text_of(s.client.get(f"/books/{PILOT_SLUG}/animation/4").text)


def test_page_view_disables_repaint_while_busy(studio):
    s = studio(Commands(SLOW))
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/3").text
    assert "script generation is running" in text_of(html)


def test_book_page_offers_cover_repaints(studio):
    s = studio()
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert f'action="/books/{PILOT_SLUG}/repaint-cover"' in html
    assert 'value="front"' in html and 'value="back"' in html


# --- AC-1.5: a repainted picture makes the video out of date ---------------------------------


def test_changed_picture_makes_video_out_of_date(tmp_path, monkeypatch):  # AC-1.5
    s, world = with_world(tmp_path, monkeypatch)
    try:
        assert re_.main(cli_args(world, "--sfx-dir", str(s.paths.sfx_dir)), run=FakeRunner()) == 0
        assert sb._video_state(s.paths, s.book())["state"] == "done"
        picture(pilot_folder(s.paths.root).illustrations / "page_03.png", (1, 2, 3))
        assert sb._video_state(s.paths, s.book())["state"] == "out_of_date"
    finally:
        if s.runner.current():
            s.runner.stop()
