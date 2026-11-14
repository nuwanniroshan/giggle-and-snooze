"""`illustrate --cleanup` exit codes and output (spec 026), with cleanup_page faked."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import make_root
from video_pipeline import cleanup
from video_pipeline import illustrate as module


@pytest.fixture
def argv(tmp_path, monkeypatch):
    folder = make_root(tmp_path)
    monkeypatch.setattr(module, "codex_status", lambda: Path("/fake/codex"))
    return ["--book", folder.slug, "--root", str(tmp_path), "--page", "1", "--cleanup"]


def fake(monkeypatch, result):
    def cleanup_page(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cleanup, "cleanup_page", cleanup_page)


def test_cleaned_exits_0_and_prints_summary(argv, monkeypatch, capsys):
    fake(monkeypatch, cleanup.CleanupOutcome("cleaned", [{"index": 0}], [{"index": 1}]))
    assert module.main(argv) == 0
    out = capsys.readouterr().out
    assert "cleaned page 1: 1 region(s) filled, 1 skipped" in out


def test_nothing_exits_0(argv, monkeypatch, capsys):
    fake(monkeypatch, cleanup.CleanupOutcome("nothing"))
    assert module.main(argv) == 0
    assert "nothing to clean on page 1" in capsys.readouterr().out


def test_failed_exits_1_with_reason_on_stderr(argv, monkeypatch, capsys):
    fake(monkeypatch, cleanup.CleanupOutcome("failed", reason="mask too big"))
    assert module.main(argv) == 1
    assert "mask too big" in capsys.readouterr().err


def test_cleanup_error_exits_1_with_error_line(argv, monkeypatch, capsys):
    fake(monkeypatch, cleanup.CleanupError("page_01.png.bak already exists"))
    assert module.main(argv) == 1
    assert capsys.readouterr().err.startswith("error: page_01.png.bak already exists")


def test_cleanup_with_a_cover_is_refused(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        module.main(["--book", "x", "--cleanup", "--cover", "front"])
    assert exc.value.code == 2
    assert "story pages only" in capsys.readouterr().err
