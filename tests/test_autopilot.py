"""Spec 021 Phase 2: the autopilot CLI (`video_pipeline/autopilot.py`).

No stage ever runs: the child runner is a fake that flips a `World` of stage states (the states
the autopilot reads through its probe functions), and records each child's argv. The marks
(review, approve) are faked too, except in the test that checks they are written by autopilot."""

from __future__ import annotations

import signal
import sys

import pytest

from tests.helpers import MINI_BOOK, load_json, make_root
from tests.render_helpers import make_world
from tests.test_story_tools import book as book_payload
from tests.test_story_tools import idea as idea_payload
from tests.test_studio_app import PILOT_SLUG
from video_pipeline import autopilot as ap
from video_pipeline import synthesize_voices as sv
from video_pipeline.drafts import DraftStore
from video_pipeline.studio import books as sb
from video_pipeline.studio.jobs import stage_command

ALL_STEPS = ["paint", "plan", "cut", "script", "review", "voices", "approve", "render", "build"]


def stage_of(cmd: list[str]) -> str:
    text = " ".join(cmd)
    for marker, stage in (
        ("video_pipeline.illustrate", "paint"),
        ("video_pipeline.motion_plan", "plan"),
        ("video_pipeline.cut_layers", "cut"),
        ("video_pipeline.generate_script", "script"),
        ("video_pipeline.synthesize_voices", "voices"),
        ("video_pipeline.render_episode", "render"),
        ("build_book.py", "build"),
        ("video_pipeline.write_story", "story"),
    ):
        if marker in text:
            return stage
    raise AssertionError(f"unknown child command: {cmd}")


class World:
    """Stage states for one book, as the autopilot's probes report them."""

    def __init__(self, **states):
        self.missing = ["page_01.png", "cover_back.png"]
        self.paint_stale = False
        self.unplanned = [1, 2]
        self.uncut = [1, 2]
        self.script = "missing"  # missing | ok | unreadable | stale_blueprint
        self.reviewed = False
        self.voices = "not_started"
        self.video = "not_started"
        self.editions = "not_started"
        self.__dict__.update(states)
        self.calls: list[str] = []
        self.cmds: list[list[str]] = []
        self.fail: dict[str, tuple[int, list[str]]] = {}
        self.no_effect: set[str] = set()
        self.on_child = None
        self.draft_effect = None

    @classmethod
    def finished(cls, **states):
        base = dict(
            missing=[], unplanned=[], uncut=[], script="ok", reviewed=True,
            voices="approved", video="done", editions="done",
        )  # fmt: skip
        return cls(**{**base, **states})

    # probes ---------------------------------------------------------------------------------
    def pictures_state(self, paths, book):
        if self.missing:
            return {"state": "not_started", "detail": "", "missing": list(self.missing)}
        if self.paint_stale:
            return {"state": "out_of_date", "detail": "older blueprint", "missing": []}
        return {"state": "done", "detail": "", "missing": []}

    def animation_rows(self, paths, book):
        return [
            {
                "page_number": n,
                "plan_state": "none" if n in self.unplanned else "ok",
                "detail": "",
                "cut_state": "needs cut" if n in self.uncut else "cut",
            }
            for n in (1, 2)
        ]

    def script_state(self, paths, book):
        return self.script

    def script_reviewed(self, paths, book):
        return self.reviewed

    def voices_state(self, paths, book):
        return {"state": self.voices, "detail": ""}

    def video_state(self, paths, book):
        return {"state": self.video, "detail": ""}

    def editions_state(self, paths, book):
        if self.missing:
            return {"state": "blocked", "detail": f"paint the missing pictures first: "
                    f"{', '.join(self.missing)}", "missing": self.missing}  # fmt: skip
        return {"state": self.editions, "detail": "", "missing": []}

    # acts -----------------------------------------------------------------------------------
    def mark_review(self, paths, book):
        self.calls.append("review")
        if "review" in self.fail:
            raise sb.StudioError("cannot mark it")
        self.reviewed = True

    def approve_audio(self, paths, book):
        self.calls.append("approve")
        if "approve" in self.fail:
            raise sv.ApproveError("the audio no longer matches the script")
        self.voices = "approved"

    def run_child(self, cmd):
        stage = stage_of(cmd)
        self.calls.append(stage)
        self.cmds.append(cmd)
        if self.on_child:
            self.on_child(stage)
        if stage in self.fail:
            return self.fail[stage]
        if stage in self.no_effect:
            return 0, []
        if stage == "story":
            self.draft_effect()
        elif stage == "paint":
            self.missing = []
        elif stage == "plan":
            self.unplanned = []
        elif stage == "cut":
            self.uncut = []
        elif stage == "script":
            self.script, self.reviewed = "ok", False
        elif stage == "voices":
            self.voices = "needs_review"
        elif stage == "render":
            self.video = "done"
        elif stage == "build":
            self.editions = "done"
        return 0, [f"{stage} ok"]

    def install(self, monkeypatch):
        for name in (
            "pictures_state", "animation_rows", "script_state", "script_reviewed",
            "voices_state", "video_state", "editions_state", "mark_review", "approve_audio",
        ):  # fmt: skip
            monkeypatch.setattr(ap, name, getattr(self, name))
        return self


@pytest.fixture
def paths(tmp_path):
    make_root(tmp_path)
    return sb.StudioPaths(tmp_path)


def run_file(paths, name=PILOT_SLUG) -> dict:
    return load_json(ap.run_path(paths, name))


def go(paths, world, **kw) -> int:
    return ap.run(paths, PILOT_SLUG, run_child=world.run_child, job_id="job-1", **kw)


# --- order and skipping (AC-2.1, AC-2.2) ------------------------------------------------------


def test_steps_run_in_spec_order_from_an_empty_book(paths, monkeypatch):  # AC-2.1
    world = World().install(monkeypatch)
    assert go(paths, world) == 0
    assert world.calls == ALL_STEPS
    record = run_file(paths)
    assert record["state"] == "finished" and record["finished_at"]
    assert [s["stage"] for s in record["steps"]] == ALL_STEPS
    assert {s["result"] for s in record["steps"]} == {"done"}


def test_no_editions_finishes_after_the_video(paths, monkeypatch):
    world = World().install(monkeypatch)
    assert go(paths, world, editions=False) == 0
    assert world.calls == ALL_STEPS[:-1]
    steps = {s["stage"]: s["result"] for s in run_file(paths)["steps"]}
    assert steps["build"] == "skipped"
    assert run_file(paths)["state"] == "finished"


def test_done_steps_are_skipped(paths, monkeypatch):  # AC-2.2
    world = World(missing=[], unplanned=[], uncut=[], script="ok").install(monkeypatch)
    assert go(paths, world) == 0
    assert world.calls == ["review", "voices", "approve", "render", "build"]
    steps = {s["stage"]: s["result"] for s in run_file(paths)["steps"]}
    assert steps["paint"] == steps["plan"] == steps["cut"] == steps["script"] == "skipped"


def test_finished_book_runs_nothing_and_finishes(paths, monkeypatch):  # AC-2.2, AC-2.3
    world = World.finished().install(monkeypatch)
    assert go(paths, world) == 0
    assert world.calls == []
    record = run_file(paths)
    assert record["state"] == "finished" and record["finished_at"]
    assert {s["result"] for s in record["steps"]} == {"skipped"}


def test_an_out_of_date_stage_runs_again(paths, monkeypatch):  # AC-2.2: only up to date skips
    world = World.finished(voices="out_of_date", video="out_of_date").install(monkeypatch)
    assert go(paths, world) == 0
    assert world.calls == ["voices", "approve", "render"]


# --- failures stop the run (AC-4.1) and resume (AC-4.2) -------------------------------------


def test_failed_child_stops_with_stage_and_reason_and_runs_nothing_later(paths, monkeypatch):
    world = World().install(monkeypatch)
    world.fail["script"] = (1, ["Generating...", "error: Codex is not available: log in"])
    assert go(paths, world) == 1
    assert world.calls == ["paint", "plan", "cut", "script"]  # AC-4.1: nothing later starts
    record = run_file(paths)
    assert record["state"] == "failed" and record["stage"] == "script"
    assert "script generation failed (exit code 1)" in record["reason"]
    assert "Codex is not available" in record["reason"]
    assert record["steps"][-1] == {**record["steps"][-1], "stage": "script", "result": "failed"}


def test_resume_starts_at_the_first_unfinished_stage(paths, monkeypatch):  # AC-4.2, AC-1.2
    world = World().install(monkeypatch)
    world.fail["render"] = (1, ["error: ffmpeg died"])
    assert go(paths, world) == 1
    world.calls.clear()
    del world.fail["render"]
    assert go(paths, world) == 0
    assert world.calls == ["render", "build"]
    assert run_file(paths)["state"] == "finished"


def test_review_and_approval_are_written_by_autopilot(tmp_path, monkeypatch):  # AC-3.1, 3.2
    w = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=False)
    paths = sb.StudioPaths(tmp_path)
    world = World.finished().install(monkeypatch)
    for name in ("script_state", "script_reviewed", "voices_state", "mark_review", "approve_audio"):
        monkeypatch.setattr(ap, name, getattr(ap, f"_{name}"))  # the real ones
    assert go(paths, world) == 0
    assert world.calls == []  # no child: the script and audio exist
    book = sb.get_book(paths, PILOT_SLUG)
    assert sb.reviewed_mark(paths, book)["by"] == "autopilot"
    status = load_json(sv.AudioPaths(w["audio"]).status)
    assert status["status"] == "approved" and status["by"] == "autopilot"
    steps = {s["stage"]: s["result"] for s in run_file(paths)["steps"]}
    assert steps["review"] == steps["approve"] == "done" and steps["voices"] == "skipped"


def test_voices_failure_never_approves(paths, monkeypatch):  # AC-3.3
    world = World(missing=[], unplanned=[], uncut=[], script="ok").install(monkeypatch)
    world.fail["voices"] = (
        1,
        [
            "error: 2 line(s) failed their checks after 3 attempts; nothing stitched:",
            "  - page-03-02 (kid_younger): clipping",
            "  - outro-01 (storyteller): too long",
        ],
    )
    assert go(paths, world) == 1
    assert "approve" not in world.calls and world.voices == "not_started"
    record = run_file(paths)
    assert record["stage"] == "voices" and record["state"] == "failed"
    assert "page-03-02" in record["reason"] and "outro-01" in record["reason"]


def test_voices_that_leave_unvalid_audio_never_approve(paths, monkeypatch):  # AC-3.3
    world = World(missing=[], unplanned=[], uncut=[], script="ok").install(monkeypatch)
    world.no_effect.add("voices")  # exit 0 but the audio is still not usable
    assert go(paths, world) == 1
    assert "approve" not in world.calls
    record = run_file(paths)
    assert record["stage"] == "voices" and "still not done" in record["reason"]


def test_approve_error_stops_at_approve(paths, monkeypatch):  # AC-3.3, AC-4.1
    world = World.finished(voices="needs_review").install(monkeypatch)
    world.fail["approve"] = (1, [])
    assert go(paths, world) == 1
    assert world.calls == ["approve"]
    record = run_file(paths)
    assert record["stage"] == "approve" and "no longer matches" in record["reason"]


def test_review_error_stops_at_review(paths, monkeypatch):
    world = World.finished(reviewed=False).install(monkeypatch)
    world.fail["review"] = (1, [])
    assert go(paths, world) == 1
    assert run_file(paths)["stage"] == "review"


def test_step_still_needed_after_success_fails(paths, monkeypatch):  # guards against loops
    world = World(missing=[]).install(monkeypatch)
    world.no_effect.add("plan")
    assert go(paths, world) == 1
    assert world.calls == ["plan"]
    record = run_file(paths)
    assert record["stage"] == "plan"
    assert "motion planning finished but is still not done" in record["reason"]


# --- stops that need a person (plan rulings) --------------------------------------------------


def test_stale_blueprint_pictures_stop_with_reason(paths, monkeypatch):
    world = World.finished(paint_stale=True).install(monkeypatch)
    assert go(paths, world) == 1
    assert world.calls == []  # never repaints by itself
    record = run_file(paths)
    assert record["stage"] == "paint" and record["state"] == "failed"
    assert "older blueprint" in record["reason"] and "Repaint all" in record["reason"]


def test_stale_blueprint_script_stops_with_reason(paths, monkeypatch):
    world = World.finished(script="stale_blueprint").install(monkeypatch)
    assert go(paths, world) == 1
    assert world.calls == []  # never overwrites the script by itself
    record = run_file(paths)
    assert record["stage"] == "script" and "older blueprint" in record["reason"]


def test_blocked_editions_stop_with_missing_pictures(paths, monkeypatch):
    world = World.finished(missing=["page_02.png"]).install(monkeypatch)
    world.no_effect.add("paint")
    world.pictures_state = lambda p, b: {"state": "done", "detail": "", "missing": []}
    monkeypatch.setattr(ap, "pictures_state", world.pictures_state)
    assert go(paths, world) == 1
    record = run_file(paths)
    assert record["stage"] == "build" and "page_02.png" in record["reason"]


def test_a_broken_book_stops_before_any_stage(paths, monkeypatch):
    world = World().install(monkeypatch)
    sb.get_book(paths, PILOT_SLUG).book_path.write_text("{", encoding="utf-8")
    assert go(paths, world) == 1
    assert world.calls == []
    assert run_file(paths)["state"] == "failed"


# --- from a draft (AC-1.1) --------------------------------------------------------------------


def _draft(paths) -> tuple[DraftStore, dict]:
    store = DraftStore(paths.root)
    return store, store.new_draft(idea_payload("The Moon Kite Race"), pages=8, author="A. Author")


def _write_story(store, draft_id, title="The Moon Kite Race"):
    def effect():
        payload = book_payload(8)
        payload["metadata"]["title"] = title
        store.update(draft_id, state="ready", book=payload, attempts=1, problems=[])

    return effect


def test_draft_run_writes_story_then_creates_book_then_paints(paths, monkeypatch):  # AC-1.1
    store, draft = _draft(paths)
    world = World().install(monkeypatch)
    world.draft_effect = _write_story(store, draft["id"])
    code = ap.run_draft(paths, draft["id"], run_child=world.run_child, job_id="job-9")
    assert code == 0
    assert world.calls == ["story", *ALL_STEPS]
    slug = "the-moon-kite-race"
    assert sb.get_book(paths, slug) is not None and store.get(draft["id"]) is None
    assert world.cmds[0] == stage_command("blueprint", draft["id"], paths)  # design + story
    assert all("--book" in c and slug in c for c in world.cmds[1:])
    assert not ap.run_path(paths, f"draft-{draft['id']}").exists()  # the run file moved
    record = run_file(paths, slug)
    assert record["book"] == slug and record["draft"] == draft["id"]
    assert record["job_id"] == "job-9" and record["state"] == "finished"
    assert [s["stage"] for s in record["steps"]][:2] == ["story", "create"]


def test_draft_not_ready_stops_at_story(paths, monkeypatch):
    store, draft = _draft(paths)
    world = World().install(monkeypatch)

    def failed():
        store.update(draft["id"], state="failed", problems=["page 3 is too long"])

    world.draft_effect = failed
    assert ap.run_draft(paths, draft["id"], run_child=world.run_child) == 1
    assert world.calls == ["story"]
    record = run_file(paths, f"draft-{draft['id']}")
    assert record["state"] == "failed" and record["stage"] == "story"
    assert "page 3 is too long" in record["reason"]


def test_draft_story_child_failure_stops_at_story(paths, monkeypatch):
    store, draft = _draft(paths)
    world = World().install(monkeypatch)
    world.fail["story"] = (1, ["error: Codex is not available"])
    assert ap.run_draft(paths, draft["id"], run_child=world.run_child) == 1
    record = run_file(paths, f"draft-{draft['id']}")
    assert record["stage"] == "story" and "Codex is not available" in record["reason"]


def test_draft_create_failure_stops_at_create(paths, monkeypatch):
    store, draft = _draft(paths)
    world = World().install(monkeypatch)
    title = "The Lantern Bug Parade"  # that book already exists
    world.draft_effect = _write_story(store, draft["id"], title)
    assert ap.run_draft(paths, draft["id"], run_child=world.run_child) == 1
    record = run_file(paths, f"draft-{draft['id']}")
    assert record["stage"] == "create" and "already exists" in record["reason"]
    assert store.get(draft["id"]) is not None  # the draft is kept


def test_ready_draft_skips_the_story(paths, monkeypatch):  # AC-4.2 for a draft run
    store, draft = _draft(paths)
    _write_story(store, draft["id"])()
    world = World.finished().install(monkeypatch)
    assert ap.run_draft(paths, draft["id"], run_child=world.run_child) == 0
    assert world.calls == []
    assert sb.get_book(paths, "the-moon-kite-race") is not None


# --- stop (AC-1.3), argv (FR-1) and the run file (FR-2) -------------------------------------


def test_sigterm_marks_the_run_stopped(paths, monkeypatch):  # AC-1.3
    world = World().install(monkeypatch)

    def stop_at(stage):
        if stage == "plan":
            ap._on_sigterm(signal.SIGTERM, None)

    world.on_child = stop_at
    assert go(paths, world) == 143
    assert world.calls == ["paint", "plan"]  # nothing after the stopped stage
    record = run_file(paths)
    assert record["state"] == "stopped" and record["stage"] == "plan"


def test_main_installs_the_sigterm_handler(paths, monkeypatch):
    seen = {}
    monkeypatch.setattr(ap, "run", lambda p, slug, **kw: seen.setdefault("slug", slug) and 0)
    monkeypatch.setattr(signal, "signal", lambda sig, handler: seen.setdefault(sig, handler))
    assert ap.main(["--book", PILOT_SLUG, "--root", str(paths.root)]) == 0
    assert seen[signal.SIGTERM] is ap._on_sigterm and seen["slug"] == PILOT_SLUG


def test_main_reads_the_job_id_from_the_environment(paths, monkeypatch):
    seen = {}
    monkeypatch.setenv("KIDDO_JOB_ID", "job-42")
    monkeypatch.setattr(ap, "run", lambda p, slug, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    ap.main(["--book", PILOT_SLUG, "--root", str(paths.root)])
    assert seen["job_id"] == "job-42"


def test_children_use_stage_command_argv(paths, monkeypatch):  # FR-1
    world = World().install(monkeypatch)
    assert go(paths, world) == 0
    book = sb.get_book(paths, PILOT_SLUG)
    expected = [
        stage_command(stage, book, paths)
        for stage in ("paint", "plan", "cut", "script", "voices", "render", "build")
    ]
    assert world.cmds == expected
    assert "--reviewed" in world.cmds[4]  # FR-4: the gate stays in the CLI


def test_run_state_file_is_written_atomically_with_by_autopilot(paths, monkeypatch):  # FR-2
    writes = []
    real = ap.write_atomic
    monkeypatch.setattr(ap, "write_atomic", lambda p, d: (writes.append(p), real(p, d)))
    world = World.finished(video="not_started").install(monkeypatch)
    assert go(paths, world) == 0
    assert writes and set(writes) == {ap.run_path(paths, PILOT_SLUG)}
    record = run_file(paths)
    assert ap.run_path(paths, PILOT_SLUG) == paths.studio_dir / "autopilot" / f"{PILOT_SLUG}.json"
    assert record["by"] == "autopilot" and record["book"] == PILOT_SLUG
    assert record["job_id"] == "job-1"
    for key in ("started_at", "updated_at", "finished_at", "stage", "reason", "steps", "draft"):
        assert key in record
    assert not list(ap.run_path(paths, PILOT_SLUG).parent.glob("*.tmp"))


def test_child_output_is_streamed_and_its_tail_kept(capfdbinary):
    code, tail = ap._run_child(
        [sys.executable, "-c", "import sys; print('one', flush=True); print('error: two', file=sys.stderr);"
         " sys.exit(3)"]
    )  # fmt: skip
    assert code == 3 and tail == ["one", "error: two"]
    assert b"one" in capfdbinary.readouterr().out


def test_cli_prints_a_header_per_stage(paths, monkeypatch, capsys):  # AC-4.1: the log
    world = World(missing=[]).install(monkeypatch)
    go(paths, world)
    out = capsys.readouterr().out
    assert "== autopilot: plan ==" in out and "== autopilot: build ==" in out
