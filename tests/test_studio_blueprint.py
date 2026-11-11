"""The studio side of the book blueprint (spec 011 Phase 4: AC-1.1, 1.3, 1.4, 1.5, 1.7,
AC-6.1..6.9, AC-7.2, AC-3.4 button).

Stage commands are replaced by tiny programs run by a real JobRunner. The fake design job writes
a valid blueprint through the real paths; a "slow" fake just sleeps so a job stays running."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import make_root
from tests.test_story_tools import book as book_payload
from tests.test_story_tools import idea as idea_payload
from tests.test_studio_app import PILOT_SLUG, Studio, pilot_folder, scripted, text_of
from video_pipeline import studio  # noqa: F401  (package import)
from video_pipeline.blueprint import blueprint_sha256, record_use
from video_pipeline.drafts import DraftStore
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.studio import app as sa
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj


class DesignCommands:
    """Stands in for `stage_command` for story and design stages; records every call."""

    def __init__(self, slow: bool = False):
        self.slow = slow
        self.calls: list[tuple[str, str]] = []

    def __call__(self, stage, subject, paths, force=False, page=None):
        subject = subject if isinstance(subject, str) else subject.slug
        self.calls.append((stage, subject))
        if self.slow or stage not in ("blueprint", "redesign", "book"):
            return [sys.executable, "-c", "import time; time.sleep(30)"]
        code = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
            "from pathlib import Path\n"
            "from video_pipeline.drafts import DraftStore\n"
            f"store = DraftStore(Path({str(paths.root)!r}))\n"
            f"stage, subject = {stage!r}, {subject!r}\n"
            "if stage == 'blueprint':\n"
            "    store.update(subject, state='ready', phase='story',"
            f" book=json.loads({json.dumps(book_payload(8))!r}))\n"
            "elif stage == 'book':\n"
            "    store.update(subject, state='ready', phase='story',"
            f" book=json.loads({json.dumps(book_payload(8))!r}))\n"
        )
        return [sys.executable, "-c", code]


@pytest.fixture
def make_studio(tmp_path):
    made = []

    def build(commands=None):
        make_root(tmp_path)
        (tmp_path / "assets").mkdir(exist_ok=True)
        if not (tmp_path / "assets" / "sound_library").exists():
            shutil.copytree(
                PROJECT_ROOT / "assets" / "sound_library", tmp_path / "assets" / "sound_library"
            )
        commands = commands or DesignCommands()
        s = Studio(tmp_path, commands)
        made.append(s)
        return s, commands

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()


def start_write(s: Studio, pages: str = "12"):
    store = DraftStore(s.paths.root)
    run = store.new_run("ideas")
    store.update_run(run, state="ready", payload={"ideas": [idea_payload("Story Number 0")]})
    return s.client.post(
        "/drafts", data={"run": run, "index": "0", "pages": pages, "author": "Test Author"}
    )


def only_draft(s: Studio) -> dict:
    (draft,) = DraftStore(s.paths.root).list()
    return draft


def new_draft(s: Studio, state: str, **fields) -> dict:
    store = DraftStore(s.paths.root)
    draft = store.new_draft(idea_payload("Story Number 0"), pages=8, author="Test Author")
    return store.update(draft["id"], state=state, **fields)


def give_blueprint(s: Studio, data: dict | None = None, slug: str = PILOT_SLUG) -> Path:
    folder = pilot_folder(s.paths.root) if slug == PILOT_SLUG else None
    write_atomic(folder.blueprint_path, data or valid_blueprint())
    return folder.blueprint_path


# --- the job commands ---------------------------------------------------------------------------


def test_story_command_builds_blueprint_and_redesign_argv(tmp_path):  # FR-9
    paths = sb.StudioPaths(tmp_path)
    cmd = sj.story_command("blueprint", "abc123", paths)
    assert cmd[1:3] == ["-m", "video_pipeline.write_story"]
    assert cmd[3:] == ["blueprint", "--draft", "abc123", "--then-book", "--root", str(tmp_path)]
    assert sj.story_command("redesign", "draft:abc123", paths)[3:] == [
        "blueprint",
        "--draft",
        "abc123",
        "--root",
        str(tmp_path),
    ]
    assert sj.story_command("redesign", "book:my-book", paths)[3:] == [
        "blueprint",
        "--book",
        "my-book",
        "--root",
        str(tmp_path),
    ]
    assert "--then-book" not in sj.story_command("redesign", "draft:abc123", paths)
    assert "blueprint" in sj.STORY_STAGES and "redesign" in sj.STORY_STAGES
    assert sb.STAGE_LABELS["blueprint"] == sb.STAGE_LABELS["redesign"] == "blueprint design"


# --- the draft: designing, failed, restarted, discarded (Story 1) --------------------------------


def test_write_book_starts_the_blueprint_job_first(make_studio):  # AC-1.1
    s, commands = make_studio(DesignCommands(slow=True))
    r = start_write(s)
    assert r.status_code == 303
    draft = only_draft(s)
    assert draft["state"] == "designing" and draft["phase"] == "design"
    assert s.runner.current()["stage"] == "blueprint"
    assert commands.calls == [("blueprint", draft["id"])]
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "Designing" in page and "Create book" not in page


def test_draft_page_shows_designing_and_failed_actions(make_studio):  # AC-1.4
    s, _ = make_studio()
    failed = new_draft(
        s,
        "design_failed",
        problems=["too close to book 'the-snail-mail-express'", "art.palette: bad colour"],
    )
    html = s.client.get(f"/drafts/{failed['id']}").text
    page = text_of(html)
    assert "Try the design again" in page and "Write without a blueprint" in page
    assert "too close to book" in page and "art.palette: bad colour" in page
    assert f"/drafts/{failed['id']}/design-again" in html
    assert f"/drafts/{failed['id']}/write-without-blueprint" in html
    designing = new_draft(s, "designing")
    page = text_of(s.client.get(f"/drafts/{designing['id']}").text)
    assert "Designing" in page and "Try the design again" not in page


def test_ready_draft_shows_design_problems_and_regenerate(make_studio):  # AC-1.4, AC-6.2, AC-6.3
    s, _ = make_studio()
    store = DraftStore(s.paths.root)
    ready = new_draft(
        s, "ready", book=book_payload(8), design_problems=["too close to book 'x-book'"]
    )
    page = text_of(s.client.get(f"/drafts/{ready['id']}").text)
    assert "too close to book" in page
    assert "Regenerate blueprint" not in page  # no blueprint file yet (AC-6.3)
    write_atomic(store.blueprint_path(ready["id"]), valid_blueprint())
    html = s.client.get(f"/drafts/{ready['id']}").text
    assert f"/drafts/{ready['id']}/blueprint/regenerate" in html
    assert "Regenerate blueprint" in text_of(html)


def test_write_without_a_blueprint_starts_the_story_job(make_studio):  # AC-1.5
    s, commands = make_studio()
    draft = new_draft(s, "design_failed", problems=["no luck"])
    r = s.client.post(f"/drafts/{draft['id']}/write-without-blueprint")
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{draft['id']}"
    s.wait()
    assert commands.calls == [("book", draft["id"])]
    after = DraftStore(s.paths.root).get(draft["id"])
    assert after["state"] == "ready"
    assert not DraftStore(s.paths.root).blueprint_path(draft["id"]).exists()


def test_write_without_a_blueprint_needs_a_failed_design(make_studio):  # AC-1.5
    s, commands = make_studio()
    ready = new_draft(s, "ready", book=book_payload(8))
    r = s.client.post(f"/drafts/{ready['id']}/write-without-blueprint")
    assert r.status_code == 409 and commands.calls == []


def test_design_again_restarts_the_design_job(make_studio):  # AC-1.4
    s, commands = make_studio(DesignCommands(slow=True))
    draft = new_draft(s, "design_failed", problems=["no luck"])
    r = s.client.post(f"/drafts/{draft['id']}/design-again")
    assert r.status_code == 303
    assert commands.calls == [("blueprint", draft["id"])]
    after = DraftStore(s.paths.root).get(draft["id"])
    assert after["state"] == "designing" and after["phase"] == "design"
    assert after["problems"] == []


def test_interrupted_design_restarts_the_design(make_studio):  # AC-1.3
    s, commands = make_studio()
    draft = new_draft(s, "designing")
    # The app factory marks drafts left designing as interrupted when the studio starts.
    sa.create_app(s.paths, runner=s.runner, command_for=commands, port=8765)
    got = DraftStore(s.paths.root).get(draft["id"])
    assert got["state"] == "interrupted" and got["phase"] == "design"
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "Story Number 0" in page and "interrupted" in page.lower()
    r = s.client.post(f"/drafts/{draft['id']}/write-again")
    assert r.status_code == 303
    s.wait()
    assert commands.calls == [("blueprint", draft["id"])]
    assert DraftStore(s.paths.root).get(draft["id"])["state"] == "ready"


def test_interrupted_story_still_restarts_the_story(make_studio):  # AC-1.3 (unchanged path)
    s, commands = make_studio()
    draft = new_draft(s, "interrupted", phase="story")
    assert s.client.post(f"/drafts/{draft['id']}/write-again").status_code == 303
    s.wait()
    assert commands.calls == [("book", draft["id"])]


def test_discard_stops_a_designing_draft(make_studio):  # AC-1.7
    s, _ = make_studio(DesignCommands(slow=True))
    start_write(s)
    draft = only_draft(s)
    assert s.runner.current() is not None
    r = s.client.post(f"/drafts/{draft['id']}/discard", data={"confirm": "yes"})
    assert r.status_code == 303
    assert s.runner.current() is None
    assert DraftStore(s.paths.root).get(draft["id"]) is None
    assert not DraftStore(s.paths.root).draft_folder(draft["id"]).exists()


def test_discard_without_confirmation_keeps_the_job(make_studio):  # AC-1.7
    s, _ = make_studio(DesignCommands(slow=True))
    start_write(s)
    draft = only_draft(s)
    assert s.client.post(f"/drafts/{draft['id']}/discard").status_code == 422
    assert s.runner.current() is not None
    assert DraftStore(s.paths.root).get(draft["id"]) is not None


# --- the book page: the panel (Story 6, Story 7) -------------------------------------------------


def test_book_page_shows_the_blueprint_panel_and_hides_regenerate_without_one(
    make_studio,
):  # AC-6.1, AC-6.3, AC-7.2
    s, _ = make_studio()
    plain = s.client.get(f"/books/{PILOT_SLUG}")
    plain_page = text_of(plain.text)
    assert plain.status_code == 200
    for word in ("Blueprint", "Regenerate blueprint", "Rebuild style bible", "stale", "swatch"):
        assert word not in plain_page and word not in plain.text
    give_blueprint(s)
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    page = text_of(html)
    assert "Blueprint" in page and "Sleepy drift" in page and "Slow, hushed, dreamy." in page
    assert "Chalk pastel" in page and "Bedtime wonderings" in page
    assert "what if" in page and "who is sleepy" in page
    for name in ("midnight", "moon", "lantern", "moss", "rose"):
        assert name in page
    assert len(re.findall(r'class="swatch"', html)) == 5
    for hex_colour in ("#1b2a49", "#f4efd8", "#f2b441", "#5d8a66", "#d98c9a"):
        assert f"background:{hex_colour}" in html
    assert "crickets_night" in page and "owl_hoot" in page and "bell_chime" in page
    assert "slow music box lullaby" in page
    assert "0.5" in page and "fireflies" in page
    assert "Regenerate blueprint" in page and "Rebuild style bible" in page
    assert f"/books/{PILOT_SLUG}/blueprint/regenerate" in html
    assert f"/books/{PILOT_SLUG}/blueprint/style-bible" in html


def test_regenerate_without_a_blueprint_is_refused(make_studio):  # AC-6.3
    s, commands = make_studio()
    r = s.client.post(f"/books/{PILOT_SLUG}/blueprint/regenerate", data={"confirm": "yes"})
    assert r.status_code == 409 and "no blueprint" in r.text.lower()
    assert commands.calls == []
    draft = new_draft(s, "ready", book=book_payload(8))
    r = s.client.post(f"/drafts/{draft['id']}/blueprint/regenerate", data={"confirm": "yes"})
    assert r.status_code == 409 and "no blueprint" in r.text.lower()
    assert commands.calls == []


def test_regenerate_starts_redesign_and_keeps_the_old_file_on_stop(make_studio):  # AC-6.2, AC-6.9
    s, commands = make_studio(DesignCommands(slow=True))
    path = give_blueprint(s)
    before = path.read_bytes()
    refused = s.client.post(f"/books/{PILOT_SLUG}/blueprint/regenerate")
    assert refused.status_code == 422 and commands.calls == []  # needs the confirm field
    r = s.client.post(f"/books/{PILOT_SLUG}/blueprint/regenerate", data={"confirm": "yes"})
    assert r.status_code == 303
    assert commands.calls == [("redesign", f"book:{PILOT_SLUG}")]
    assert s.runner.current()["stage"] == "redesign"
    assert "blueprint design" in text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    s.client.post("/jobs/stop")
    assert s.runner.current() is None
    assert path.read_bytes() == before and not path.with_name("blueprint.json.bak").exists()
    assert s.runner.latest(PILOT_SLUG, "redesign")["state"] == "stopped"


def test_regenerate_a_draft_blueprint_starts_redesign_for_the_draft(make_studio):  # AC-6.2
    s, commands = make_studio(DesignCommands(slow=True))
    draft = new_draft(s, "ready", book=book_payload(8))
    write_atomic(DraftStore(s.paths.root).blueprint_path(draft["id"]), valid_blueprint())
    r = s.client.post(f"/drafts/{draft['id']}/blueprint/regenerate", data={"confirm": "yes"})
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{draft['id']}"
    assert commands.calls == [("redesign", f"draft:{draft['id']}")]
    page = text_of(s.client.get(f"/drafts/{draft['id']}").text)
    assert "blueprint design" in page  # the running job is named in the banner


def test_outdated_marks_name_the_command_and_change_nothing(make_studio):  # AC-6.4, 6.5, 6.6
    s, _ = make_studio()
    scripted(s)
    folder = pilot_folder(s.paths.root)
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    (folder.illustrations / "page_01.png").write_bytes(b"png")
    path = give_blueprint(s)
    old_sha = blueprint_sha256(path)
    record_use(folder, "art", old_sha)
    record_use(folder, "script", old_sha)
    book = s.book()
    assert "stale" not in text_of(s.client.get(f"/books/{PILOT_SLUG}").text).lower()
    assert sb.blueprint_status(s.paths, book)["stale"] == []
    status_before = sb.stage_status(s.paths, book, s.runner.latest)
    script_before = folder.script_path.read_bytes()
    write_atomic(path, valid_blueprint(**{"mood.summary": "Slow, hushed, and very dreamy."}))
    info = sb.blueprint_status(s.paths, book)
    assert [m["output"] for m in info["stale"]] == ["pictures", "episode script"]
    assert info["stale"][0]["command"].endswith(f"illustrate --book {PILOT_SLUG} --force")
    assert info["stale"][1]["command"].endswith(f"generate_script --book {PILOT_SLUG} --force")
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "stale" in page.lower()
    assert f"python -m video_pipeline.illustrate --book {PILOT_SLUG} --force" in page
    assert f"python -m video_pipeline.generate_script --book {PILOT_SLUG} --force" in page
    assert sb.stage_status(s.paths, book, s.runner.latest) == status_before  # AC-6.6
    assert folder.script_path.read_bytes() == script_before
    assert (folder.illustrations / "page_01.png").read_bytes() == b"png"


def test_nothing_is_marked_when_no_output_exists(make_studio):  # AC-6.4, AC-6.5, AC-7.2
    s, _ = make_studio()
    give_blueprint(s)
    info = sb.blueprint_status(s.paths, s.book())
    assert info["present"] and info["error"] is None and info["stale"] == []
    assert sb.blueprint_status(s.paths, s.book())["view"].mood.key == "sleepy-drift"


def test_blueprint_status_without_a_file_is_empty(make_studio):  # AC-7.2
    s, _ = make_studio()
    assert sb.blueprint_status(s.paths, s.book()) == {
        "present": False,
        "error": None,
        "view": None,
        "stale": [],
    }


def test_invalid_blueprint_is_refused_naming_file_and_field(make_studio):  # AC-6.7
    s, _ = make_studio()
    give_blueprint(s, valid_blueprint(**{"art.medium_key": "Bad Key"}))
    info = sb.blueprint_status(s.paths, s.book())
    assert info["present"] and info["view"] is None and info["stale"] == []
    assert "blueprint.json" in info["error"] and "medium_key" in info["error"]
    r = s.client.get(f"/books/{PILOT_SLUG}")
    page = text_of(r.text)
    assert r.status_code == 200
    assert "blueprint.json" in page and "medium_key" in page
    assert 'class="swatch"' not in r.text


def test_busy_runner_refuses_regenerate(make_studio):  # AC-6.8
    s, commands = make_studio(DesignCommands(slow=True))
    give_blueprint(s)
    s.runner.start(PILOT_SLUG, "script", [sys.executable, "-c", "import time; time.sleep(30)"])
    r = s.client.post(f"/books/{PILOT_SLUG}/blueprint/regenerate", data={"confirm": "yes"})
    assert r.status_code == 409 and "is running" in r.text
    assert commands.calls == []
    draft = new_draft(s, "design_failed", problems=["x"])
    assert s.client.post(f"/drafts/{draft['id']}/design-again").status_code == 409
    assert s.client.post(f"/drafts/{draft['id']}/write-without-blueprint").status_code == 409
    assert commands.calls == []
    assert DraftStore(s.paths.root).get(draft["id"])["state"] == "design_failed"


def test_rebuild_style_bible_button(make_studio):  # AC-3.4
    s, commands = make_studio()
    give_blueprint(s)
    folder = pilot_folder(s.paths.root)
    before = json.loads(folder.book_json.read_text(encoding="utf-8"))
    r = s.client.post(f"/books/{PILOT_SLUG}/blueprint/style-bible")
    assert r.status_code == 303
    after = json.loads(folder.book_json.read_text(encoding="utf-8"))
    assert (
        after["style_bible"]["style_prompt_prefix"]
        == "Soft chalk-pastel picture-book art on dark blue paper."
    )
    before["style_bible"]["style_prompt_prefix"] = after["style_bible"]["style_prompt_prefix"]
    assert after == before
    assert commands.calls == []  # no job: a plain file change


def test_rebuild_style_bible_refused_without_a_blueprint_or_when_busy(make_studio):  # AC-3.4, 6.8
    s, _ = make_studio(DesignCommands(slow=True))
    folder = pilot_folder(s.paths.root)
    original = folder.book_json.read_bytes()
    assert s.client.post(f"/books/{PILOT_SLUG}/blueprint/style-bible").status_code == 409
    give_blueprint(s)
    s.runner.start(PILOT_SLUG, "script", [sys.executable, "-c", "import time; time.sleep(30)"])
    assert s.client.post(f"/books/{PILOT_SLUG}/blueprint/style-bible").status_code == 409
    assert folder.book_json.read_bytes() == original
