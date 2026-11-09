"""Pilot migration into books/<slug>/ (spec 005, AC-3.1, AC-3.3, AC-3.4, FR-7)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from tests.helpers import MINI_BOOK
from video_pipeline import migrate_library as ml

SLUG = "the-snail-mail-express"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else path.name.encode() * 3)
    return path


@pytest.fixture
def old_layout(tmp_path: Path) -> Path:
    """The pre-spec layout, with pilot files and shared settings."""
    root = tmp_path
    shutil.copy(MINI_BOOK, root / "book_data.json")
    write(root / "assets" / "illustrations" / "page_01.png")
    write(root / "assets" / "illustrations" / "cover_front.png")
    write(root / "assets" / "references" / "ref2.jpg")
    write(root / "assets" / "audio_sfx" / "page_turn.wav")  # shared, stays
    write(root / "assets" / "nook" / "intro_nook.png")  # shared, stays
    write(root / "episodes" / SLUG / "episode_script.json")
    write(root / "episodes" / SLUG / "reviewed.json")
    write(root / "episodes" / SLUG / "agent" / "run.jsonl")
    write(root / "build" / SLUG / "audio" / "status.json")
    write(root / "build" / SLUG / "audio" / "tracks" / "page-01.wav")
    write(root / "build" / SLUG / "video" / "clip.mp4")
    write(root / "build" / "studio" / "jobs" / "job.json")  # project-level, stays
    write(root / "build" / "sfx" / "page_turn.wav")  # project-level cache, stays
    return root


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def test_migration_moves_every_file_with_equal_hashes(old_layout: Path) -> None:
    before = snapshot(old_layout)
    ml.migrate(old_layout, SLUG)
    after = snapshot(old_layout)
    base = f"books/{SLUG}"

    expected = {
        f"{base}/book.json": before["book_data.json"],
        f"{base}/illustrations/page_01.png": before["assets/illustrations/page_01.png"],
        f"{base}/illustrations/cover_front.png": before["assets/illustrations/cover_front.png"],
        f"{base}/references/ref2.jpg": before["assets/references/ref2.jpg"],
        f"{base}/episode/episode_script.json": before[f"episodes/{SLUG}/episode_script.json"],
        f"{base}/episode/reviewed.json": before[f"episodes/{SLUG}/reviewed.json"],
        f"{base}/build/agent/run.jsonl": before[f"episodes/{SLUG}/agent/run.jsonl"],
        f"{base}/build/audio/status.json": before[f"build/{SLUG}/audio/status.json"],
        f"{base}/build/audio/tracks/page-01.wav": before[f"build/{SLUG}/audio/tracks/page-01.wav"],
        f"{base}/build/video/clip.mp4": before[f"build/{SLUG}/video/clip.mp4"],
    }
    for rel, digest in expected.items():
        assert after.get(rel) == digest, rel

    # Originals of moved files are gone; shared settings and project-level output stay.
    for gone in ["book_data.json", f"episodes/{SLUG}/episode_script.json"]:
        assert gone not in after
    for kept in [
        "assets/audio_sfx/page_turn.wav",
        "assets/nook/intro_nook.png",
        "build/studio/jobs/job.json",
        "build/sfx/page_turn.wav",
    ]:
        assert after[kept] == before[kept], kept
    assert not (old_layout / "episodes").exists()
    assert not (old_layout / "assets" / "illustrations").exists()


def test_rerun_changes_nothing(old_layout: Path) -> None:
    ml.migrate(old_layout, SLUG)
    once = snapshot(old_layout)

    results = ml.migrate(old_layout, SLUG)

    assert snapshot(old_layout) == once
    assert results == []  # nothing left to move: the CLI reports "nothing to migrate"


def test_copy_failure_leaves_originals_intact(
    old_layout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(old_layout)
    real_copy = shutil.copy2
    calls = {"n": 0}

    def flaky_copy(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(ml.shutil, "copy2", flaky_copy)

    with pytest.raises(ml.MigrationError) as err:
        ml.migrate(old_layout, SLUG)

    assert "disk full" in str(err.value) or "copy" in str(err.value)
    assert snapshot(old_layout) == before  # every original, unchanged
    assert not (old_layout / "books").exists() or not any((old_layout / "books").rglob("*.json"))


def test_conflicting_destination_stops_before_any_removal(old_layout: Path) -> None:
    before = snapshot(old_layout)
    write(old_layout / "books" / SLUG / "book.json", b"someone else's edit")

    with pytest.raises(ml.MigrationError) as err:
        ml.migrate(old_layout, SLUG)

    assert "book.json" in str(err.value)
    assert (old_layout / "book_data.json").exists()
    assert sha(old_layout / "book_data.json") == before["book_data.json"]


def test_dry_run_writes_nothing(old_layout: Path) -> None:
    before = snapshot(old_layout)
    results = ml.migrate(old_layout, SLUG, dry_run=True)

    assert snapshot(old_layout) == before
    assert results and all(r.status == "would move" for r in results)
