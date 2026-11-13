"""The add-a-book-from-an-idea pages (spec 006: AC-1.1..1.3, AC-2.1..2.3, AC-3.1..3.8).

The writing agent is replaced by a tiny program that stores the result through the real draft
store, so the studio runs the same job, state and file paths as in use."""

from __future__ import annotations

import json
import re
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
from video_pipeline.studio.jobs import Busy


class StoryCommands:
    """Stands in for the story stage commands: each run writes its result (or failure)."""

    def __init__(
        self, ideas=None, candidate=None, book=None, failure=None, paint_code=None,
        autopilot_code=None,
    ):  # fmt: skip
        self.paint_code = paint_code
        self.autopilot_code = autopilot_code
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

    def __call__(self, stage, subject, paths, force=False, page=None):
        self.calls.append(stage)
        if stage == "autopilot":  # spec 021: prints only (the real CLI is tested on its own)
            code = self.autopilot_code or f"print('autopilot for {subject}')"
            return [sys.executable, "-c", code]
        if stage == "paint":  # spec 016: Create book paints the new book's pictures
            code = self.paint_code or f"print('painting {subject.slug}')"
            return [sys.executable, "-c", code]
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
    assert r.status_code == 422 and "5 to 32" in r.text
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


def ready_draft(s: Studio, title: str = "The Moon Kite Race") -> dict:
    store = DraftStore(s.paths.root)
    draft = store.new_draft(idea_payload(title), pages=8, author="Test Author")
    return store.update(draft["id"], state="ready", book=book_payload(8))


def test_create_book_starts_painting_its_pictures(make_studio):  # AC-1.1
    s = make_studio(
        StoryCommands(paint_code="import time; print('painting', flush=True); time.sleep(30)")
    )
    draft = ready_draft(s)
    r = s.client.post(f"/drafts/{draft['id']}/create", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/books/the-brave-little-teapot"
    job = s.runner.current()
    assert job["stage"] == "paint" and job["book"] == "the-brave-little-teapot"
    page = text_of(s.client.get("/books/the-brave-little-teapot").text)
    assert "RUNNING" in page and "picture painting" in page


def test_create_book_never_starts_script_voices_or_render(make_studio):  # FR-5
    commands = StoryCommands()
    s = make_studio(commands)
    draft = ready_draft(s)
    s.client.post(f"/drafts/{draft['id']}/create")
    s.wait()
    assert commands.calls == ["paint"]
    for stage in ("script", "voices", "render", "plan", "cut"):
        assert s.runner.latest("the-brave-little-teapot", stage) is None
    assert s.runner.latest("the-brave-little-teapot", "paint")["state"] == "done"


@pytest.mark.parametrize("error", [Busy({"id": "other"}), ValueError("unsafe")])
def test_create_book_keeps_the_book_when_painting_cannot_start(make_studio, monkeypatch, error):
    s = make_studio()  # AC-1.2

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(s.runner, "start", refuse)
    draft = ready_draft(s)
    r = s.client.post(f"/drafts/{draft['id']}/create", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/books/the-brave-little-teapot"
    assert sb.get_book(s.paths, "the-brave-little-teapot") is not None
    assert DraftStore(s.paths.root).get(draft["id"]) is None
    page = s.client.get("/books/the-brave-little-teapot").text
    assert "0 of 8 pages, 0 of 2 covers" in text_of(page)
    assert re.search(r"<button[^>]*>\s*Paint pictures", page)
    assert not re.search(r"<button[^>]*disabled[^>]*>\s*Paint pictures", page)


def test_create_book_refused_while_a_job_runs_keeps_the_draft(make_studio):  # AC-1.3
    commands = StoryCommands()
    s = make_studio(commands)
    s.runner.start("other", "script", [sys.executable, "-c", "import time; time.sleep(30)"])
    draft = ready_draft(s)
    r = s.client.post(f"/drafts/{draft['id']}/create")
    assert r.status_code == 409 and "is running" in r.text
    assert DraftStore(s.paths.root).get(draft["id"]) is not None
    assert sb.get_book(s.paths, "the-brave-little-teapot") is None
    assert commands.calls == [] and s.runner.current()["stage"] == "script"


def test_create_refused_for_an_existing_book_keeps_the_draft(make_studio):  # AC-3.6
    s = make_studio()
    first = DraftStore(s.paths.root).new_draft(idea_payload(), pages=8, author="Test Author")
    DraftStore(s.paths.root).update(first["id"], state="ready", book=book_payload(8))
    s.client.post(f"/drafts/{first['id']}/create")
    s.wait()  # spec 016: creating the first book started its paint job
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


def test_add_book_page_never_reloads_itself_while_ideas_are_written(make_studio):
    # A timed reload took the cursor out of the idea field while the user typed.
    s = make_studio()
    DraftStore(s.paths.root).new_run("ideas")  # left "writing" with no job running
    html = s.client.get("/books/new").text
    assert 'http-equiv="refresh"' not in html
    assert "stopped before it finished" in html and "Writing ideas" not in html


def test_draft_page_never_reloads_itself_while_writing(make_studio):
    s = make_studio()
    draft = DraftStore(s.paths.root).new_draft(idea_payload(), 12, "Ann")
    html = s.client.get(f"/drafts/{draft['id']}").text
    assert 'http-equiv="refresh"' not in html


def test_improve_keeps_the_typed_idea_in_the_field(make_studio):
    s = make_studio()
    s.client.post("/ideas/improve", data={"idea": "a teapot who is scared of the dark"})
    s.wait()
    html = s.client.get("/books/new").text
    assert ">a teapot who is scared of the dark</textarea>" in html


def test_add_book_page_shows_the_steps_in_order(make_studio):
    s = make_studio()
    page = text_of(s.client.get("/books/new").text)
    first, second, third = (
        page.index("Choose a story idea"),
        page.index("Write the book"),
        page.index("Review your drafts"),
    )
    assert first < second < third


# --- character sheet (spec 018: AC-3.2) -------------------------------------------------------


def test_draft_page_lists_the_sheet_and_each_pages_characters(make_studio):  # AC-3.2
    from tests.test_story_tools import OWL, TEAPOT

    s = make_studio()
    draft = ready_draft(s)
    html = s.client.get(f"/drafts/{draft['id']}").text
    page = text_of(html)
    assert 'id="characters"' in html
    for entry in (TEAPOT, OWL):
        assert entry["name"] in page and entry["look"] in page
    assert page.count("Characters: Tilly, Hoot") == 4  # the even pages of book(8)
    assert page.count("Characters: Tilly ") == 4  # the odd pages


def test_pre_018_draft_renders_without_the_characters_panel(make_studio):
    s = make_studio()
    payload = book_payload(8)
    del payload["characters"]
    for p in payload["pages"]:
        del p["characters_on_page"]
    store = DraftStore(s.paths.root)
    draft = store.new_draft(idea_payload(), pages=8, author="Test Author")
    store.update(draft["id"], state="ready", book=payload)
    r = s.client.get(f"/drafts/{draft['id']}")
    assert r.status_code == 200 and 'id="characters"' not in r.text
    assert "Characters:" not in text_of(r.text)


# --- autopilot from the Add book page (spec 021 Phase 4, AC-1.1) ------------------------------


def _choose(s: Studio, **extra):
    s.client.post("/ideas")
    s.wait()
    run = DraftStore(s.paths.root).latest_run("ideas")["id"]
    data = {"run": run, "index": "0", "pages": "8", "author": "Test Author", **extra}
    return s.client.post("/drafts", data=data)


def test_autopilot_checkbox_is_on_by_default(make_studio):  # AC-1.1
    s = make_studio()
    s.client.post("/ideas")
    s.wait()
    html = s.client.get("/books/new").text
    assert re.search(r'<input type="checkbox" name="autopilot" value="1" checked>', html)
    assert "Autopilot" in text_of(html)


def test_choosing_an_idea_with_autopilot_starts_the_autopilot_job(make_studio):  # AC-1.1
    commands = StoryCommands()
    s = make_studio(commands)
    r = _choose(s, autopilot="1")
    draft = only_draft(s)
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{draft['id']}"
    assert commands.calls == ["ideas", "autopilot"]  # no blueprint job, no paint job
    s.wait()
    job = s.runner.latest(draft["id"], "autopilot")
    assert job is not None and job["state"] == "done"
    assert job["cmd"][-1] == f"print('autopilot for {draft['id']}')"


def test_choosing_an_idea_without_autopilot_starts_the_blueprint_job(make_studio):
    commands = StoryCommands()
    s = make_studio(commands)
    assert _choose(s).status_code == 303
    assert commands.calls == ["ideas", "blueprint"]


def test_autopilot_refused_while_busy_discards_the_new_draft(make_studio):
    s = make_studio()
    s.client.post("/ideas")
    s.wait()
    run = DraftStore(s.paths.root).latest_run("ideas")["id"]
    s.runner.start("busy", "script", [sys.executable, "-c", "import time; time.sleep(30)"])
    r = s.client.post(
        "/drafts",
        data={"run": run, "index": "0", "pages": "8", "author": "A", "autopilot": "1"},
    )
    assert r.status_code == 409 and "Refused" in r.text
    assert DraftStore(s.paths.root).list() == []


def test_stop_during_autopilot_story_marks_the_draft_interrupted(make_studio):  # AC-1.3
    s = make_studio(StoryCommands(autopilot_code="import time; time.sleep(30)"))
    _choose(s, autopilot="1")
    draft = only_draft(s)
    assert draft["state"] == "designing"
    r = s.client.post("/jobs/stop")
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{draft['id']}"
    assert only_draft(s)["state"] == "interrupted"


def test_draft_page_says_autopilot_will_create_the_book(make_studio):
    s = make_studio(StoryCommands(autopilot_code="import time; time.sleep(30)"))
    _choose(s, autopilot="1")
    draft = only_draft(s)
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "Autopilot will create the book when the story is written" in page
    assert "Create book" not in page


def test_draft_page_links_the_book_once_created(make_studio):  # AC-1.1
    from video_pipeline import autopilot as ap
    from video_pipeline.episode_tools import write_atomic

    s = make_studio()
    store = DraftStore(s.paths.root)
    draft = store.new_draft(idea_payload(), pages=8, author="Test Author")
    store.update(draft["id"], state="ready", book=book_payload(8))
    slug = store.create_book(draft["id"])  # as the autopilot does; the draft is gone
    write_atomic(
        ap.run_path(s.paths, slug),
        {"book": slug, "draft": draft["id"], "job_id": None, "state": "finished", "steps": []},
    )
    r = s.client.get(f"/drafts/{draft['id']}")
    assert r.status_code == 303 and r.headers["location"] == f"/books/{slug}"
    assert s.client.get("/drafts/unknown0").status_code == 404
