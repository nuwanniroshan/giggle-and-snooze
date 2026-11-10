"""The add-a-book-from-an-idea pages (spec 006: AC-1.1..1.3, AC-2.1..2.3, AC-3.1..3.8).

The writing agent is replaced by a tiny program that stores the result through the real draft
store, so the studio runs the same job, state and file paths as in use."""

from __future__ import annotations

import json
import sys

import pytest

from tests.helpers import make_root
from tests.test_story_tools import book as book_payload
from tests.test_story_tools import idea as idea_payload
from tests.test_studio_app import Studio, text_of
from video_pipeline.drafts import DraftStore
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.studio import app as sa
from video_pipeline.studio import books as sb


class StoryCommands:
    """Stands in for the story stage commands: each run writes its result (or failure)."""

    def __init__(self, ideas=None, candidate=None, book=None, failure=None):
        self.ideas = (
            ideas
            if ideas is not None
            else {"ideas": [idea_payload(f"Story Number {n}") for n in range(3)]}
        )
        self.candidate = (
            candidate if candidate is not None else idea_payload("The Brave Little Teapot")
        )
        self.book = book if book is not None else book_payload(8)
        self.failure = failure
        self.calls: list[str] = []

    def __call__(self, stage, subject, paths, force=False):
        self.calls.append(stage)
        if stage == "ideas":
            update = (
                f"store.update_run({subject!r}, state=STATE, payload=PAYLOAD, problems=PROBLEMS)"
            )
            payload = self.ideas
        elif stage == "candidate":
            update = (
                f"store.update_run({subject!r}, state=STATE, payload=PAYLOAD, problems=PROBLEMS)"
            )
            payload = self.candidate
        else:
            update = f"store.update({subject!r}, state=STATE, book=PAYLOAD, problems=PROBLEMS, attempts=1)"
            payload = self.book
        state, problems = ("failed", [self.failure]) if self.failure else ("ready", [])
        code = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
            "from pathlib import Path\n"
            "from video_pipeline.drafts import DraftStore\n"
            f"store = DraftStore(Path({str(paths.root)!r}))\n"
            f"STATE = {state!r}\n"
            f"PROBLEMS = {problems!r}\n"
            f"PAYLOAD = json.loads({json.dumps(payload)!r})\n"
            f"{update}\n"
        )
        return [sys.executable, "-c", code]


@pytest.fixture
def make_studio(tmp_path):
    made = []

    def build(commands=None):
        make_root(tmp_path)
        s = Studio(tmp_path, commands or StoryCommands())
        made.append(s)
        return s

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()


def only_draft(s: Studio) -> dict:
    (draft,) = DraftStore(s.paths.root).list()
    return draft


def test_add_book_page_offers_ideas_and_no_file_field(make_studio):  # AC-4.1
    s = make_studio()
    html = s.client.get("/books/new").text
    assert "Suggest ideas" in html and "Improve idea" in html
    assert 'type="file"' not in html


def test_suggest_ideas_lists_three_to_five_with_write_buttons(make_studio):  # AC-1.1
    s = make_studio()
    assert s.client.post("/ideas").status_code == 303
    s.wait()
    page = text_of(s.client.get("/books/new").text)
    assert "Story Number 0" in page and "Story Number 2" in page
    assert "Write this book" in page


def test_suggest_ideas_reports_when_the_provider_is_unavailable(make_studio):  # AC-1.3
    s = make_studio(StoryCommands(failure="install the ChatGPT desktop app"))
    s.client.post("/ideas")
    s.wait()
    page = text_of(s.client.get("/books/new").text)
    assert "could not be written" in page and "install the ChatGPT desktop app" in page
    assert "Write this book" not in page


def test_improve_refuses_a_short_idea_without_a_job(make_studio):  # AC-2.2
    commands = StoryCommands()
    s = make_studio(commands)
    r = s.client.post("/ideas/improve", data={"idea": "tea"})
    assert r.status_code == 422 and "10 to 500 characters" in r.text
    assert commands.calls == []


def test_improve_shows_one_candidate(make_studio):  # AC-2.1
    s = make_studio()
    s.client.post("/ideas/improve", data={"idea": "a teapot who is scared of the dark"})
    s.wait()
    page = text_of(s.client.get("/books/new").text)
    assert "The Brave Little Teapot" in page and "Write this book" in page


def test_write_book_refuses_pages_outside_the_range_before_any_job(make_studio):  # AC-3.2a
    commands = StoryCommands()
    s = make_studio(commands)
    s.client.post("/ideas")
    s.wait()
    r = s.client.post(
        "/drafts",
        data={
            "run": DraftStore(s.paths.root).latest_run("ideas")["id"],
            "index": "0",
            "pages": "40",
            "author": "A",
        },
    )
    assert r.status_code == 422 and "8 to 32" in r.text
    assert commands.calls == ["ideas"]
    assert DraftStore(s.paths.root).list() == []


def test_write_book_starts_a_draft_for_review(make_studio):  # AC-3.1
    s = make_studio()
    s.client.post("/ideas")
    s.wait()
    run = DraftStore(s.paths.root).latest_run("ideas")["id"]
    r = s.client.post(
        "/drafts", data={"run": run, "index": "1", "pages": "12", "author": "Test Author"}
    )
    assert r.status_code == 303
    s.wait()
    draft = only_draft(s)
    assert (
        draft["state"] == "ready"
        and draft["pages"] == 12
        and draft["idea"]["title"] == "Story Number 1"
    )
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "Page 1:" in page and "Create book" in page


def test_saving_a_page_checks_the_rules(make_studio):  # AC-3.4
    s = make_studio()
    draft = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    DraftStore(s.paths.root).update(draft["id"], state="ready", book=book_payload(8))
    refused = s.client.post(
        f"/drafts/{draft['id']}/pages/2", data={"text": "A monster came to visit."}
    )
    assert refused.status_code == 422 and "monster" in refused.text
    assert (
        DraftStore(s.paths.root).get(draft["id"])["book"]["pages"][1]["text"]
        == "The teapot walked on. She was brave."
    )
    ok = s.client.post(
        f"/drafts/{draft['id']}/pages/2",
        data={"text": "The teapot sang a song."},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert (
        DraftStore(s.paths.root).get(draft["id"])["book"]["pages"][1]["text"]
        == "The teapot sang a song."
    )


def test_create_book_makes_the_book_and_removes_the_draft(make_studio):  # AC-3.5
    s = make_studio()
    draft = DraftStore(s.paths.root).new_draft(
        idea_payload("The Moon Kite Race"), pages=8, author="Test Author"
    )
    DraftStore(s.paths.root).update(draft["id"], state="ready", book=book_payload(8))
    r = s.client.post(f"/drafts/{draft['id']}/create", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/books/the-brave-little-teapot"
    assert sb.get_book(s.paths, "the-brave-little-teapot") is not None
    assert DraftStore(s.paths.root).get(draft["id"]) is None


def test_create_refused_for_an_existing_book_keeps_the_draft(make_studio):  # AC-3.6
    s = make_studio()
    first = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    DraftStore(s.paths.root).update(first["id"], state="ready", book=book_payload(8))
    s.client.post(f"/drafts/{first['id']}/create")
    second = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    DraftStore(s.paths.root).update(second["id"], state="ready", book=book_payload(8))
    r = s.client.post(f"/drafts/{second['id']}/create")
    assert r.status_code == 409 and "already exists" in r.text
    assert DraftStore(s.paths.root).get(second["id"]) is not None


def test_discard_needs_confirmation(make_studio):  # AC-3.8
    s = make_studio()
    draft = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    refused = s.client.post(f"/drafts/{draft['id']}/discard")
    assert refused.status_code == 422
    assert DraftStore(s.paths.root).get(draft["id"]) is not None
    assert (
        s.client.post(
            f"/drafts/{draft['id']}/discard", data={"confirm": "yes"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert DraftStore(s.paths.root).get(draft["id"]) is None


def test_interrupted_draft_is_marked_and_can_be_written_again(make_studio):  # AC-3.7
    s = make_studio()
    draft = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    # The app factory marks drafts left writing as interrupted when the studio starts.
    sa.create_app(s.paths, runner=s.runner, command_for=StoryCommands(), port=8765)
    assert DraftStore(s.paths.root).get(draft["id"])["state"] == "interrupted"
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "Writing was interrupted" in page
    assert (
        s.client.post(f"/drafts/{draft['id']}/write-again", follow_redirects=False).status_code
        == 303
    )
    s.wait()
    assert DraftStore(s.paths.root).get(draft["id"])["state"] == "ready"
