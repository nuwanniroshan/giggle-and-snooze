"""Book library: one folder per book under books/<slug>/ (spec 005, AC-1.x, FR-2, FR-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_pipeline.library import (
    BOOK_FILE,
    BookFolder,
    LibraryError,
    book_folder,
    file_stem,
    list_book_slugs,
    validate_slug,
)


def make_book(root: Path, slug: str) -> Path:
    folder = root / "books" / slug
    folder.mkdir(parents=True)
    (folder / BOOK_FILE).write_text(json.dumps({"metadata": {"title": slug}}), encoding="utf-8")
    return folder


@pytest.mark.parametrize("slug", ["the-snail-mail-express", "book2", "a", "x-1-2"])
def test_valid_slugs_pass(slug: str) -> None:
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    ["Upper", "has space", "a.b", "../x", "a/b", "-lead", "trail-", "a--b", ""],
)
def test_invalid_slugs_are_refused_and_named(slug: str) -> None:
    with pytest.raises(LibraryError) as err:
        validate_slug(slug)
    assert repr(slug) in str(err.value)


def test_book_folder_requires_book_json(tmp_path: Path) -> None:
    (tmp_path / "books" / "empty-book").mkdir(parents=True)
    with pytest.raises(LibraryError) as err:
        book_folder("empty-book", tmp_path)
    assert BOOK_FILE in str(err.value)


def test_list_book_slugs_reports_problems_and_keeps_valid(tmp_path: Path) -> None:
    make_book(tmp_path, "good-book")
    (tmp_path / "books" / "missing-json").mkdir()
    (tmp_path / "books" / "Bad_Name").mkdir()

    slugs, problems = list_book_slugs(tmp_path)

    assert slugs == ["good-book"]
    assert [name for name, _ in problems] == ["Bad_Name", "missing-json"]
    assert BOOK_FILE in dict(problems)["missing-json"]
    assert "Bad_Name" in dict(problems)["Bad_Name"]


def test_new_folder_is_listed_without_code_change(tmp_path: Path) -> None:
    make_book(tmp_path, "first-book")
    make_book(tmp_path, "new-book")
    slugs, problems = list_book_slugs(tmp_path)
    assert slugs == ["first-book", "new-book"]
    assert problems == []


def test_list_is_empty_without_books_folder(tmp_path: Path) -> None:
    assert list_book_slugs(tmp_path) == ([], [])


def test_file_stem_matches_pilot_filenames() -> None:
    assert file_stem("The Snail Mail Express") == "The_Snail_Mail_Express"
    assert file_stem("  Moon -- Kite!  ") == "Moon_Kite"


def test_paths_are_under_the_book_folder(tmp_path: Path) -> None:
    b = BookFolder("the-snail-mail-express", tmp_path)
    base = tmp_path / "books" / "the-snail-mail-express"
    assert b.folder == base
    assert b.book_json == base / "book.json"
    assert b.illustrations == base / "illustrations"
    assert b.references == base / "references"
    assert b.nooks == base / "nooks"
    assert b.script_path == base / "episode" / "episode_script.json"
    assert b.reviewed_path == base / "episode" / "reviewed.json"
    assert b.build_dir == base / "build"
