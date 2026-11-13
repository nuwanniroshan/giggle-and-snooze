"""Spec 021 Phase 1: the reviewed mark and the audio approval can be written as automatic
(`"by": "autopilot"`, AC-3.1, AC-3.2, FR-4), and the studio says so."""

from __future__ import annotations

import pytest

from tests.helpers import MINI_BOOK, load_json
from tests.render_helpers import make_world
from tests.test_studio_app import PILOT_SLUG, Studio, text_of
from video_pipeline import synthesize_voices as sv
from video_pipeline.episode import load_book
from video_pipeline.studio import books as sb


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The mini book with a script and synthesized (not approved) audio."""
    return make_world(tmp_path, monkeypatch, MINI_BOOK, approve=False)


def _paths(world) -> sb.StudioPaths:
    return sb.StudioPaths(world["root"])


def _settings_sha(world) -> str | None:
    cfg = sv.load_voice_config(world["config"] / "voices.json")
    book = load_book(world["book"])
    return sv.current_settings_sha(cfg, book, world["book"], world["script_path"])


def _approve(world, by=None) -> dict:
    audio = sv.AudioPaths(world["audio"])
    return sv.approve(audio, world["script_path"], _settings_sha(world), by=by)


def test_mark_reviewed_records_by_autopilot(world):  # AC-3.1
    paths = _paths(world)
    book = sb.get_book(paths, PILOT_SLUG)
    mark = sb.mark_reviewed(paths, book, by="autopilot")
    assert mark["by"] == "autopilot"
    assert load_json(sb.reviewed_path(paths, book))["by"] == "autopilot"
    assert sb.reviewed_mark(paths, book)["by"] == "autopilot"  # still a valid mark


def test_manual_mark_has_no_by(world):  # AC-3.1: a person's mark replaces the automatic one
    paths = _paths(world)
    book = sb.get_book(paths, PILOT_SLUG)
    sb.mark_reviewed(paths, book, by="autopilot")
    mark = sb.mark_reviewed(paths, book)
    assert "by" not in mark
    assert "by" not in load_json(sb.reviewed_path(paths, book))


def test_approve_records_by_autopilot(world):  # AC-3.2
    status = _approve(world, by="autopilot")
    assert status["status"] == "approved" and status["by"] == "autopilot"
    assert load_json(sv.AudioPaths(world["audio"]).status)["by"] == "autopilot"


def test_manual_approve_clears_by(world):  # AC-3.2
    _approve(world, by="autopilot")
    status = _approve(world)
    assert "by" not in status
    assert "by" not in load_json(sv.AudioPaths(world["audio"]).status)


def test_approval_by_autopilot_still_passes_approval_problems(world):  # FR-4
    _approve(world, by="autopilot")
    audio = sv.AudioPaths(world["audio"])
    assert sv.approval_problems(audio, world["script_path"], _settings_sha(world)) == []


def test_approve_by_autopilot_is_refused_for_audio_that_no_longer_matches(world):  # AC-3.3
    path = world["script_path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(sv.ApproveError):
        _approve(world, by="autopilot")
    assert load_json(sv.AudioPaths(world["audio"]).status)["status"] == "pending review"


def test_script_page_says_reviewed_automatically(world):  # AC-3.1
    s = Studio(world["root"])
    try:
        sb.mark_reviewed(s.paths, s.book(), by="autopilot")
        page = text_of(s.client.get(f"/books/{PILOT_SLUG}/script").text)
        assert "Reviewed automatically" in page
        assert "Mark as reviewed" in page  # a person can still confirm it
        sb.mark_reviewed(s.paths, s.book())
        assert "Reviewed automatically" not in text_of(
            s.client.get(f"/books/{PILOT_SLUG}/script").text
        )
    finally:
        s.runner.close()


def test_audio_page_says_approved_automatically(world):  # AC-3.2
    s = Studio(world["root"])
    try:
        _approve(world, by="autopilot")
        page = text_of(s.client.get(f"/books/{PILOT_SLUG}/audio").text)
        assert "Approved automatically" in page
        _approve(world)
        assert "Approved automatically" not in text_of(
            s.client.get(f"/books/{PILOT_SLUG}/audio").text
        )
    finally:
        s.runner.close()
