"""Spec 026, Phase 4: artifact cleanup in the studio (stage, rows, media, Accept and Revert).

Stage commands are replaced by tiny programs (the `Commands` fake); no model or Codex runs."""

from __future__ import annotations

import json

import pytest

from tests.helpers import make_root
from tests.render_helpers import picture
from tests.test_studio_app import PILOT_SLUG, Commands, Studio, pilot_folder, text_of
from video_pipeline import cleanup
from video_pipeline.motion_plan import page_picture
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

SLOW = "import time; print('cleaning', flush=True); time.sleep(30)"


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


def pic(s: Studio, number: int = 3):
    return picture(pilot_folder(s.paths.root).illustrations / f"page_{number:02d}.png")


def record(pic_path, reviewed=False):
    cleanup.record_path(pic_path).write_text(
        json.dumps(
            {
                "applied": [{"index": 1, "reason": "stray blob"}],
                "skipped": [{"index": 2, "reason": "extra finger", "why": "too large"}],
                "reviewed": reviewed,
            }
        ),
        encoding="utf-8",
    )


def post(s: Studio, tail: str, number: int = 3, **data):
    return s.client.post(f"/books/{PILOT_SLUG}/animation/{number}/{tail}", data=data)


# --- the command ------------------------------------------------------------------------------


def test_cleanup_stage_contract(tmp_path):
    assert sj.STAGE_MODULES["cleanup"] == "video_pipeline.illustrate"
    assert sb.STAGE_LABELS["cleanup"] == "artifact cleanup"
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, make_root(tmp_path).slug)
    cmd = sj.stage_command("cleanup", book, paths, page=3)
    assert cmd[1:3] == ["-m", "video_pipeline.illustrate"]
    assert "--page 3 --cleanup" in " ".join(cmd) and "--yes" not in cmd
    forced = sj.stage_command("cleanup", book, paths, page=3, force=True)
    assert "--page 3 --cleanup --yes" in " ".join(forced) and "--force" not in forced
    with pytest.raises(ValueError):
        sj.stage_command("cleanup", book, paths)


# --- starting ---------------------------------------------------------------------------------


def test_start_without_bak_runs_without_confirm(studio):
    s = studio()
    pic(s)
    r = post(s, "cleanup")
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}/animation/3"
    s.wait()
    assert s.commands.calls == [("cleanup", PILOT_SLUG, 3)]
    assert s.runner.latest(PILOT_SLUG, "cleanup", 3)["state"] == "done"


def test_start_with_bak_needs_confirm(studio):
    s = studio()
    p = pic(s)
    cleanup.bak_path(p).write_bytes(b"old")
    r = post(s, "cleanup")
    assert r.status_code == 409
    text = text_of(r.text)
    assert "Confirm cleaning this picture" in text and "overwritten" in text
    assert s.commands.calls == [] and s.runner.current() is None
    assert post(s, "cleanup", confirm="1").status_code == 303
    s.wait()
    assert s.commands.calls == [("cleanup", PILOT_SLUG, 3)]


def test_start_needs_a_picture(studio):
    s = studio()
    r = post(s, "cleanup")
    assert r.status_code == 409 and "paint the picture first" in text_of(r.text)
    assert s.commands.calls == []


def test_start_unknown_page_404(studio):
    s = studio()
    assert post(s, "cleanup", number=42).status_code == 404


def test_start_refused_while_busy(studio):
    s = studio(Commands(SLOW))
    pic(s)
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    r = post(s, "cleanup")
    assert r.status_code == 409 and "script generation is running" in text_of(r.text)


# --- the row ----------------------------------------------------------------------------------


def test_cleanup_row_states(studio):
    s = studio()
    p = pic(s)
    folder = pilot_folder(s.paths.root)
    assert sb.cleanup_row(folder, 3) == {
        "state": "none", "applied": [], "skipped": [], "has_bak": False,
    }  # fmt: skip
    record(p)
    cleanup.bak_path(p).write_bytes(b"old")
    row = sb.cleanup_row(folder, 3)
    assert row["state"] == "review" and row["has_bak"]
    assert row["applied"][0]["reason"] == "stray blob"
    assert row["skipped"][0]["why"] == "too large"
    record(p, reviewed=True)
    assert sb.cleanup_row(folder, 3)["state"] == "accepted"
    cleanup.record_path(p).write_text("not json", encoding="utf-8")
    assert sb.cleanup_row(folder, 3)["state"] == "none"


def test_page_rows_carry_cleanup(studio):
    s = studio()
    rows = sb.page_animation(s.paths, s.book())
    assert rows and all(r["cleanup"]["state"] == "none" for r in rows)


def test_page_plan_state_is_whatever_existing_code_reports_after_cleanup(studio):
    s = studio()
    p = pic(s)
    before = {r["page_number"]: r["plan_state"] for r in sb.page_animation(s.paths, s.book())}
    record(p)
    cleanup.bak_path(p).write_bytes(b"old")
    after = {r["page_number"]: r["plan_state"] for r in sb.page_animation(s.paths, s.book())}
    assert after == before  # no plan-staleness logic is added for cleanup


# --- media ------------------------------------------------------------------------------------


def test_bak_media_served_only_for_story_page_with_bak(studio):
    s = studio()
    folder = pilot_folder(s.paths.root)
    p = pic(s)
    url = f"/media/{PILOT_SLUG}/illustration_bak/{p.name}"
    assert s.client.get(url).status_code == 404  # no .bak yet
    cleanup.bak_path(p).write_bytes(b"old-bytes")
    r = s.client.get(url)
    assert r.status_code == 200 and r.content == b"old-bytes"
    assert s.client.get(url, headers={"Range": "bytes=0-2"}).status_code in (200, 206)
    cover = picture(folder.illustrations / "cover_front.png")
    cleanup.bak_path(cover).write_bytes(b"x")
    for bad in (
        "cover_front.png",
        "book.json",
        "..%2Fbook.json",
        "..%2F..%2Fbook.json",
        "page_03.png.bak",
        "page_99.png",
    ):
        assert s.client.get(f"/media/{PILOT_SLUG}/illustration_bak/{bad}").status_code == 404


# --- accept and revert ------------------------------------------------------------------------


def test_accept_marks_reviewed_and_keeps_bak(studio):
    s = studio()
    p = pic(s)
    record(p)
    cleanup.bak_path(p).write_bytes(b"old")
    r = post(s, "cleanup/accept")
    assert r.status_code == 303 and r.headers["location"] == f"/books/{PILOT_SLUG}/animation/3"
    assert json.loads(cleanup.record_path(p).read_text())["reviewed"] is True
    assert cleanup.bak_path(p).is_file()


def test_revert_restores_bytes_and_removes_bak_and_record(studio):
    s = studio()
    p = pic(s)
    record(p)
    cleanup.bak_path(p).write_bytes(b"previous")
    assert post(s, "cleanup/revert").status_code == 303
    assert p.read_bytes() == b"previous"
    assert not cleanup.bak_path(p).exists() and not cleanup.record_path(p).exists()


def test_accept_and_revert_refuse_when_nothing_to_do(studio):
    s = studio()
    pic(s)
    for tail in ("cleanup/accept", "cleanup/revert"):
        assert post(s, tail).status_code == 409
    assert post(s, "cleanup/accept", number=42).status_code == 404


def test_accept_and_revert_refused_while_a_job_runs(studio):
    s = studio(Commands(SLOW))
    p = pic(s)
    record(p)
    cleanup.bak_path(p).write_bytes(b"old")
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    for tail in ("cleanup/accept", "cleanup/revert"):
        r = post(s, tail)
        assert r.status_code == 409 and "is running" in text_of(r.text)
    assert p.read_bytes() != b"old" and cleanup.bak_path(p).is_file()
    assert json.loads(cleanup.record_path(p).read_text())["reviewed"] is False


def test_cross_origin_post_refused(studio):
    s = studio()
    pic(s)
    r = s.client.post(
        f"/books/{PILOT_SLUG}/animation/3/cleanup", headers={"Origin": "http://evil.example"}
    )
    assert r.status_code in (403, 400)


# --- the page view ----------------------------------------------------------------------------


def test_page_view_panel(studio):
    s = studio()
    p = pic(s)
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/3").text
    assert "Clean artifacts" in html and "Accept" not in text_of(html).split("Cleanup")[-1][:400]
    record(p)
    cleanup.bak_path(p).write_bytes(b"old")
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/3").text
    text = text_of(html)
    assert (
        f"/media/{PILOT_SLUG}/illustration_bak/{page_picture(pilot_folder(s.paths.root), {'page_number': 3}).name}"
        in html
    )
    assert "stray blob" in text and "extra finger" in text and "too large" in text
    assert "cleanup/accept" in html and "cleanup/revert" in html
    record(p, reviewed=True)
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/3").text
    assert "cleanup/accept" not in html and "cleanup/revert" in html
