"""Phase 1 of spec 004: book registry, uploads, reviewed mark, script edits, render check, status.

Every world lives under tmp_path: the studio root holds `book_data.json` (the pilot, a copy of the
mini book), `assets/`, `books/`, `episodes/`, `build/` and `config/`. FFmpeg is never run."""

import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import MINI_BOOK, add_book_from_draft, load_json, make_root
from tests.render_helpers import FakeRunner, cli_args, make_world, picture
from tests.voice_helpers import make_script, write_script
from video_pipeline import frames
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import slugify
from video_pipeline.library import BookFolder
from video_pipeline.studio import books as sb

PILOT_SLUG = slugify(load_json(MINI_BOOK)["metadata"]["title"])


def pilot_folder(root) -> BookFolder:
    """The pilot is an ordinary book now: books/<slug>/ under the studio root (spec 005)."""
    return BookFolder(PILOT_SLUG, Path(root))


def pilot_path(root) -> Path:
    return pilot_folder(root).book_json


def png_bytes(colour=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return buf.getvalue()


def new_book(title="The Moon Kite Race") -> dict:
    book = load_json(MINI_BOOK)
    book["metadata"]["title"] = title
    return book


def upload_files(book: dict, *, nooks: bool = False) -> dict[str, bytes]:
    files = {}
    for page in book["pages"]:
        name = page.get("illustration_file") or f"page_{page['page_number']:02d}.png"
        files[name] = png_bytes((page["page_number"] * 20, 40, 90))
    files["cover_front.png"] = png_bytes((200, 0, 0))
    files["cover_back.png"] = png_bytes((0, 200, 0))
    if nooks:
        files["intro_nook.png"] = png_bytes((1, 2, 3))
        files["outro_nook.png"] = png_bytes((3, 2, 1))
    return files


def as_bytes(book: dict) -> bytes:
    return json.dumps(book).encode("utf-8")


@pytest.fixture
def studio(tmp_path):
    make_root(tmp_path)
    return sb.StudioPaths(tmp_path)


def tree(root):
    """Every file under root with its bytes, to prove a call wrote nothing."""
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- paths and the book registry (FR-1) -------------------------------------------------------


def test_studio_paths_layout(tmp_path):  # spec 005 FR-3, FR-4
    paths = sb.StudioPaths(tmp_path)
    assert paths.books_dir == tmp_path / "books"
    assert paths.build_dir == tmp_path / "build"  # project-level output only
    assert paths.config_dir == tmp_path / "config"
    assert paths.studio_dir == tmp_path / "build" / "studio"
    assert paths.sfx_dir == tmp_path / "assets" / "audio_sfx"
    assert paths.shared_nooks == tmp_path / "assets" / "nook"


def test_list_books_pilot_added_and_broken(studio):  # AC-1.2, FR-1
    add_book_from_draft(studio.root, "A Zebra Named Quill")
    broken = studio.books_dir / "broken-book"
    broken.mkdir(parents=True)
    (broken / "book.json").write_text("{not json", encoding="utf-8")
    (studio.books_dir / ".half-written.tmp").mkdir()  # a temp folder is never a book

    listed = sb.list_books(studio)

    assert [b.slug for b in listed] == ["a-zebra-named-quill", "broken-book", PILOT_SLUG]
    zebra, bad, pilot = listed
    assert pilot.error is None and pilot.title == "The Lantern Bug Parade"
    assert pilot.book_path == pilot_path(studio.root)
    assert zebra.error is None and zebra.title == "A Zebra Named Quill"
    assert zebra.book_path == studio.books_dir / "a-zebra-named-quill" / "book.json"
    assert bad.title == "broken-book" and bad.error and "not valid JSON" in bad.error
    assert sb.get_book(studio, "a-zebra-named-quill") == zebra
    assert sb.get_book(studio, "nope") is None


def test_list_books_without_a_pilot(tmp_path):
    assert sb.list_books(sb.StudioPaths(tmp_path)) == []


def test_folder_name_need_not_match_the_title(studio):  # spec 005 FR-2: the folder is the slug
    folder = studio.books_dir / "wrong-name"
    folder.mkdir(parents=True)
    shutil.copy(MINI_BOOK, folder / "book.json")
    (only,) = [b for b in sb.list_books(studio) if b.slug == "wrong-name"]
    assert only.error is None and only.title == "The Lantern Bug Parade"


# --- the script, the reviewed mark and line edits (AC-4.2-4.4) ------------------------------


@pytest.fixture
def scripted(studio):
    pilot = sb.get_book(studio, PILOT_SLUG)
    write_script(make_script(pilot_path(studio.root)), BookFolder(PILOT_SLUG, studio.root))
    return pilot


def test_script_paths(studio, scripted):
    assert sb.script_path(studio, scripted) == (BookFolder(PILOT_SLUG, studio.root).script_path)
    assert sb.reviewed_path(studio, scripted) == BookFolder(PILOT_SLUG, studio.root).reviewed_path
    assert sb.load_script(studio, scripted)["title"] == scripted.title


def test_load_script_missing_is_none(studio):
    assert sb.load_script(studio, sb.get_book(studio, PILOT_SLUG)) is None


def test_mark_reviewed_records_the_exact_script(studio, scripted):  # AC-4.3
    assert not sb.reviewed_ok(studio, scripted)
    mark = sb.mark_reviewed(studio, scripted)
    assert mark["script_sha256"] == sv.sha256_file(sb.script_path(studio, scripted))
    assert mark["reviewed_at"]
    assert load_json(sb.reviewed_path(studio, scripted)) == mark
    assert sb.reviewed_ok(studio, scripted)


def test_reviewed_mark_goes_stale_when_script_changes_outside(studio, scripted):  # AC-4.4
    sb.mark_reviewed(studio, scripted)
    path = sb.script_path(studio, scripted)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not sb.reviewed_ok(studio, scripted)


def test_review_mark_survives_an_ambience_edit_but_not_a_text_edit(studio, scripted):  # spec 007
    from video_pipeline.episode_tools import write_atomic

    path = sb.script_path(studio, scripted)
    write_atomic(path, load_json(path))
    sb.mark_reviewed(studio, scripted)
    data = load_json(path)
    data["intro"]["ambience"] = ["rain"]
    data["pages"][0]["ambience"] = ["wind"]
    write_atomic(path, data)
    assert sb.reviewed_ok(studio, scripted)
    data["pages"][0]["lines"][0]["text"] = "Changed narration."
    write_atomic(path, data)
    assert not sb.reviewed_ok(studio, scripted)


def test_mark_reviewed_without_a_script_is_refused(studio):
    with pytest.raises(sb.StudioError, match="no episode script"):
        sb.mark_reviewed(studio, sb.get_book(studio, PILOT_SLUG))


def test_line_ids_match_voice_synthesis_numbering(studio, scripted):
    script = sb.load_script(studio, scripted)
    cfg = sv.load_voice_config(studio.config_dir / "voices.json")
    expected = [job.line_id for job in sv.plan_jobs(script, cfg, {})]
    ids = [line_id for line_id, _line in sb.script_lines(script)]
    assert ids == expected
    assert ids[0] == "intro-01" and "page-01-01" in ids and ids[-1].startswith("outro-")


def test_valid_edit_is_saved_and_clears_the_mark(studio, scripted):  # AC-4.2, AC-4.3
    sb.mark_reviewed(studio, scripted)
    problems = sb.save_line_edits(studio, scripted, {"intro-02": "Let's open this book together."})
    assert problems == []
    script = sb.load_script(studio, scripted)
    assert script["intro"]["lines"][1]["text"] == "Let's open this book together."
    assert not sb.reviewed_path(studio, scripted).exists()
    assert not list(sb.script_path(studio, scripted).parent.glob("*.tmp"))


def test_invalid_edit_saves_nothing(studio, scripted):  # AC-4.2
    sb.mark_reviewed(studio, scripted)
    before = sb.script_path(studio, scripted).read_bytes()
    problems = sb.save_line_edits(studio, scripted, {"intro-02": "<scream> Let's open the book."})
    assert problems and any("scream" in p for p in problems)
    assert sb.script_path(studio, scripted).read_bytes() == before
    assert sb.reviewed_ok(studio, scripted)


def test_unknown_line_id_is_a_problem(studio, scripted):
    before = sb.script_path(studio, scripted).read_bytes()
    problems = sb.save_line_edits(studio, scripted, {"page-99-01": "Hello.", "intro-02": "Hi."})
    assert problems == ["unknown line id: page-99-01"]
    assert sb.script_path(studio, scripted).read_bytes() == before


def test_unchanged_edit_keeps_the_mark(studio, scripted):
    sb.mark_reviewed(studio, scripted)
    text = sb.load_script(studio, scripted)["intro"]["lines"][1]["text"]
    assert sb.save_line_edits(studio, scripted, {"intro-02": text}) == []
    assert sb.reviewed_ok(studio, scripted)


def test_edit_without_a_script_is_a_problem(studio):
    problems = sb.save_line_edits(studio, sb.get_book(studio, PILOT_SLUG), {"intro-01": "x"})
    assert problems and "no episode script" in problems[0]


# --- render: --sfx-dir and render_check (AC-6.2) ----------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, MINI_BOOK)


def check(world, sfx_dir=None):
    return re_.render_check(
        world["folder"],
        world["root"],
        sfx_dir if sfx_dir is not None else world["root"] / "assets" / "audio_sfx",
    )


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"a process was started: {args}")

    monkeypatch.setattr(re_.subprocess, "run", refuse)
    monkeypatch.setattr(frames.subprocess, "run", refuse)


def test_render_check_not_rendered(world, no_subprocess):
    before = tree(world["book"].parent)
    assert check(world) == "not_rendered"
    assert tree(world["book"].parent) == before


def test_render_check_blocked_without_approval(tmp_path, monkeypatch, no_subprocess):
    world = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=False)
    assert check(world) == "blocked"


def test_render_check_blocked_when_audio_is_missing(world, no_subprocess):
    (world["audio"] / "tracks" / "outro.wav").unlink()
    assert check(world) == "blocked"


def test_render_check_up_to_date_then_out_of_date(world, monkeypatch):
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    monkeypatch.setattr(re_.subprocess, "run", lambda *a, **k: pytest.fail("process started"))
    monkeypatch.setattr(frames.subprocess, "run", lambda *a, **k: pytest.fail("process started"))
    before = tree(world["book"].parent)
    assert check(world) == "up_to_date"
    assert tree(world["book"].parent) == before
    picture(world["folder"].illustrations / "page_03.png", (1, 1, 1))
    assert check(world) == "out_of_date"


def test_render_check_with_a_fresh_build_writes_no_sound_cache(world, no_subprocess):
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    (world["root"] / "build" / "sfx" / "page_turn.wav").unlink()
    before = tree(world["book"].parent)
    assert check(world) == "up_to_date"
    assert tree(world["book"].parent) == before


def test_render_check_not_rendered_when_video_deleted(world):
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    world["final"].unlink()
    assert check(world) == "not_rendered"


@pytest.fixture
def fake_decode(monkeypatch):
    calls = []

    def decode(path, sample_rate):
        calls.append(path)
        seed = int.from_bytes(path.read_bytes()[:4].ljust(4, b"\0"), "little")
        return np.random.default_rng(seed).uniform(-0.1, 0.1, sample_rate // 4).astype(np.float32)

    monkeypatch.setattr(frames, "_decode_with_ffmpeg", decode)
    return calls


def test_sfx_dir_supplies_the_page_turn_sound(world, tmp_path, fake_decode):
    custom = tmp_path / "shared_sfx"
    custom.mkdir()
    (custom / "page_turn.wav").write_bytes(b"AAAA")
    assert re_.main(cli_args(world, "--sfx-dir", str(custom)), run=FakeRunner()) == 0
    assert fake_decode == [custom / "page_turn.wav"]
    # The default folder (no page_turn.wav) gives a different sound, so a different video.
    assert check(world) == "out_of_date"


def test_render_check_with_a_supplied_sound_runs_no_ffmpeg(world, tmp_path, fake_decode):
    custom = tmp_path / "shared_sfx"
    custom.mkdir()
    (custom / "page_turn.wav").write_bytes(b"AAAA")
    assert re_.main(cli_args(world, "--sfx-dir", str(custom)), run=FakeRunner()) == 0
    fake_decode.clear()
    before = tree(world["book"].parent)
    assert check(world, custom) == "up_to_date"
    assert fake_decode == []
    assert tree(world["book"].parent) == before
    (custom / "page_turn.wav").write_bytes(b"BBBB")
    assert check(world, custom) == "out_of_date"
    assert fake_decode == []


def test_up_to_date_render_backfills_page_turn_fields_for_old_records(world, tmp_path, fake_decode):
    custom = tmp_path / "shared_sfx"
    custom.mkdir()
    (custom / "page_turn.wav").write_bytes(b"AAAA")
    assert re_.main(cli_args(world, "--sfx-dir", str(custom)), run=FakeRunner()) == 0
    record_path = world["video"] / f"{world['slug']}.json"
    record = load_json(record_path)
    old = {k: v for k, v in record.items() if not k.startswith("page_turn")}
    record_path.write_text(json.dumps(old), encoding="utf-8")
    assert check(world, custom) == "out_of_date"  # cannot tell without decoding
    runner = FakeRunner()
    assert re_.main(cli_args(world, "--sfx-dir", str(custom)), run=runner) == 0
    assert runner.calls == []  # still up to date, nothing re-rendered
    assert check(world, custom) == "up_to_date"


def test_render_video_programmatic_default_sfx_dir(world, monkeypatch):
    seen = []
    real = re_.load_page_turn

    def spy(folder, rate, cache):
        seen.append(folder)
        return real(folder, rate, cache)

    monkeypatch.setattr(re_, "load_page_turn", spy)
    re_.render_video(
        world["folder"],
        world["root"],
        run=FakeRunner(),
    )
    assert seen == [world["root"] / "assets" / "audio_sfx"]


# --- stage status and the next action (AC-1.2, AC-1.3, AC-5.3, AC-6.2) ----------------------


def no_jobs(book_slug, stage):
    return None


def status_of(paths, book, jobs=no_jobs):
    return sb.stage_status(paths, book, jobs)


def states(status):
    return (status["script"]["state"], status["voices"]["state"], status["video"]["state"])


def test_status_of_a_new_book(studio):
    slug = add_book_from_draft(studio.root)
    status = status_of(studio, sb.get_book(studio, slug))
    assert states(status) == ("not_started", "not_started", "not_started")
    assert status["next_action"] == "Generate script"  # AC-2.3


def test_status_script_needs_review_then_reviewed(studio, scripted):
    status = status_of(studio, scripted)
    assert states(status) == ("needs_review", "not_started", "not_started")
    assert status["next_action"] == "Review script"
    sb.mark_reviewed(studio, scripted)
    status = status_of(studio, scripted)
    assert states(status) == ("done", "not_started", "not_started")
    assert status["next_action"] == "Synthesize voices"


def pilot_world(tmp_path, monkeypatch, approve=True):
    world = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=approve)
    paths = sb.StudioPaths(tmp_path)
    return paths, sb.get_book(paths, PILOT_SLUG), world


def test_status_audio_pending_review(tmp_path, monkeypatch):
    paths, book, _world = pilot_world(tmp_path, monkeypatch, approve=False)
    status = status_of(paths, book)
    # Audio made from this exact script means the script was reviewed (the CLI requires it).
    assert states(status) == ("done", "needs_review", "not_started")
    assert status["next_action"] == "Listen and approve"


def test_status_approved_then_rendered_then_out_of_date(tmp_path, monkeypatch):
    paths, book, world = pilot_world(tmp_path, monkeypatch)
    status = status_of(paths, book)
    assert states(status) == ("done", "approved", "not_started")
    assert status["voices"]["detail"]  # AC-5.3: when it was approved
    approved_at = load_json(world["audio"] / "status.json")["approved_at"]
    assert approved_at in status["voices"]["detail"]
    assert status["next_action"] == "Render video"

    assert re_.main(cli_args(world, "--sfx-dir", str(paths.sfx_dir)), run=FakeRunner()) == 0
    status = status_of(paths, book)
    assert states(status) == ("done", "approved", "done")
    assert status["next_action"] == "Watch video"

    picture(world["folder"].illustrations / "page_02.png", (9, 9, 9))
    status = status_of(paths, book)
    assert status["video"]["state"] == "out_of_date"  # AC-6.2
    assert status["next_action"] == "Render video"


def test_status_audio_out_of_date_after_script_edit(tmp_path, monkeypatch):
    paths, book, world = pilot_world(tmp_path, monkeypatch)
    assert re_.main(cli_args(world, "--sfx-dir", str(paths.sfx_dir)), run=FakeRunner()) == 0
    assert sb.save_line_edits(paths, book, {"intro-02": "Let's open this book together."}) == []
    status = status_of(paths, book)
    assert states(status) == ("needs_review", "out_of_date", "out_of_date")
    assert status["next_action"] == "Review script"
    sb.mark_reviewed(paths, book)
    assert status_of(paths, book)["next_action"] == "Synthesize voices"


def jobs_with(**records):
    def latest(book_slug, stage):
        return records.get(stage)

    return latest


@pytest.mark.parametrize(
    ("stage", "key", "label"),
    [
        ("script", "script", "script generation"),
        ("voices", "voices", "voice synthesis"),
        ("render", "video", "video render"),
    ],
)
def test_running_job_overrides_and_says_wait(studio, scripted, stage, key, label):
    status = status_of(studio, scripted, jobs_with(**{stage: {"state": "running"}}))
    assert status[key]["state"] == "running"
    assert status["next_action"] == f"Wait for {label} to finish"


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_failed_or_interrupted_job_overrides(studio, scripted, state):
    sb.mark_reviewed(studio, scripted)
    status = status_of(studio, scripted, jobs_with(voices={"state": state, "exit_code": 1}))
    assert status["voices"]["state"] == state
    assert status["script"]["state"] == "done"
    assert status["next_action"] == "Synthesize voices"


@pytest.mark.parametrize("state", ["done", "stopped"])
def test_finished_job_does_not_override(studio, scripted, state):
    status = status_of(studio, scripted, jobs_with(script={"state": state}))
    assert status["script"]["state"] == "needs_review"


def test_failed_script_job_without_a_script_says_generate(studio):
    book = sb.get_book(studio, PILOT_SLUG)
    status = status_of(studio, book, jobs_with(script={"state": "failed", "exit_code": 2}))
    assert status["script"]["state"] == "failed"
    assert status["next_action"] == "Generate script"


def test_latest_job_is_asked_per_book_and_stage(studio, scripted):
    asked = []
    sb.stage_status(studio, scripted, lambda slug, stage: asked.append((slug, stage)))
    assert asked == [(PILOT_SLUG, "script"), (PILOT_SLUG, "voices"), (PILOT_SLUG, "render")]


def test_stage_names():
    assert sb.STAGES == ("script", "voices", "render")


def test_status_of_a_broken_book_is_not_started(studio):
    broken = studio.books_dir / "broken-book"
    broken.mkdir(parents=True)
    (broken / "book.json").write_text("{", encoding="utf-8")
    (book,) = [b for b in sb.list_books(studio) if b.slug == "broken-book"]
    status = status_of(studio, book)
    assert states(status) == ("not_started", "not_started", "not_started")
    assert status["script"]["detail"] == book.error


# --- review fixes (round 1) -------------------------------------------------------------------


def test_reviewed_mark_is_bound_to_the_book_file(studio):
    saved = sb.get_book(studio, add_book_from_draft(studio.root))
    write_script(make_script(saved.book_path), BookFolder(saved.slug, studio.root))
    mark = sb.mark_reviewed(studio, saved)
    assert mark["book_sha256"] == sv.sha256_file(saved.book_path)
    assert sb.reviewed_ok(studio, saved)
    data = load_json(saved.book_path)
    data["metadata"]["author"] = "Someone Else"
    saved.book_path.write_text(json.dumps(data), encoding="utf-8")
    assert not sb.reviewed_ok(studio, saved)
    assert sb.reviewed_mark(studio, saved) is None


def test_reviewed_mark_without_book_hash_is_not_reviewed(studio, scripted):
    mark = sb.mark_reviewed(studio, scripted)
    mark.pop("book_sha256")
    sb.reviewed_path(studio, scripted).write_text(json.dumps(mark), encoding="utf-8")
    assert not sb.reviewed_ok(studio, scripted)


def _iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat()


def test_stale_failed_job_does_not_override_newer_files(studio, scripted):
    sb.mark_reviewed(studio, scripted)
    script_mtime = sb.script_path(studio, scripted).stat().st_mtime
    old = {"state": "failed", "exit_code": 1, "ended_at": _iso(script_mtime - 60)}
    status = status_of(studio, scripted, jobs_with(script=old))
    assert status["script"]["state"] == "done"
    new = dict(old, ended_at=_iso(script_mtime + 60))
    assert status_of(studio, scripted, jobs_with(script=new))["script"]["state"] == "failed"
    interrupted = dict(old, state="interrupted")
    status = status_of(studio, scripted, jobs_with(script=interrupted))
    assert status["script"]["state"] == "done"


def test_stale_failed_voices_and_render_jobs_do_not_override(tmp_path, monkeypatch):
    paths, book, world = pilot_world(tmp_path, monkeypatch)
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    audio_mtime = (world["audio"] / "status.json").stat().st_mtime
    video_mtime = (world["video"] / f"{world['slug']}.json").stat().st_mtime
    old = {"state": "failed", "exit_code": 1, "ended_at": _iso(min(audio_mtime, video_mtime) - 60)}
    status = status_of(paths, book, jobs_with(voices=old, render=old))
    assert states(status) == ("done", "approved", "done")
    new = dict(old, ended_at=_iso(max(audio_mtime, video_mtime) + 60))
    status = status_of(paths, book, jobs_with(voices=new, render=new))
    assert (status["voices"]["state"], status["video"]["state"]) == ("failed", "failed")
    running = {"state": "running", "ended_at": None}
    assert status_of(paths, book, jobs_with(render=running))["video"]["state"] == "running"


def test_sfx_cli_default_is_the_assets_dir(world, monkeypatch):
    assert re_.parse_args(["--book", PILOT_SLUG]).sfx_dir is None
    seen = []
    real = re_.load_page_turn

    def spy(folder, rate, cache):
        seen.append(folder)
        return real(folder, rate, cache)

    monkeypatch.setattr(re_, "load_page_turn", spy)
    assert "--sfx-dir" not in cli_args(world)
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    assert seen == [world["root"] / "assets" / "audio_sfx"]


def test_render_check_detail_names_a_missing_picture(world):
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    (world["folder"].illustrations / "page_02.png").unlink()
    state, reason = re_.render_check_detail(
        world["folder"],
        world["root"],
        world["root"] / "assets" / "audio_sfx",
    )
    assert state == "blocked"
    assert "page_02.png" in reason
    paths = sb.StudioPaths(world["root"])
    video = status_of(paths, sb.get_book(paths, PILOT_SLUG))["video"]
    assert "audio" not in video["detail"]
    assert "page_02.png" in video["detail"]


# --- animation plans and cuts (spec 009 Phase 5: AC-5.1, AC-5.3, AC-5.5) ----------------------


def _anim_book(tmp_path):
    from tests import animation_helpers as ah

    folder = make_root(tmp_path)
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, folder.slug)
    for n in (1, 2, 3, 4):
        ah.write_picture(folder, n)
    for n in (1, 2, 3):
        ah.write_plan(folder, n)
    return paths, book, folder, ah


def _rows(paths, book):
    return {r["page_number"]: r for r in sb.page_animation(paths, book)}


def test_animation_stages_are_separate_from_the_episode_flow():
    assert sb.ANIMATION_STAGES == ("plan", "cut")
    assert not set(sb.ANIMATION_STAGES) & set(sb.STAGES)
    assert sb.STAGE_LABELS["plan"] == "motion planning"
    assert sb.STAGE_LABELS["cut"] == "layer cutting"


def test_page_animation_plan_states(tmp_path):  # AC-5.1
    from video_pipeline.motion_plan import plan_path

    paths, book, folder, ah = _anim_book(tmp_path)
    ah.write_picture(folder, 2, colour=(0, 200, 0))  # page 2's picture changed → stale
    plan_path(folder, 3).write_text('{"page_number": 3}', encoding="utf-8")  # invalid
    rows = _rows(paths, book)
    assert len(rows) == 8
    assert rows[1]["plan_state"] == "ok" and rows[1]["plan"] is not None
    assert rows[2]["plan_state"] == "stale" and "changed" in rows[2]["detail"]
    assert rows[3]["plan_state"] == "invalid" and "picture" in rows[3]["detail"]
    assert rows[4]["plan_state"] == "none" and rows[4]["plan"] is None
    assert [rows[n]["cut_state"] for n in (2, 3, 4)] == ["—", "—", "—"]


def test_page_animation_cut_state_follows_the_manifest(tmp_path):  # AC-5.3
    paths, book, folder, ah = _anim_book(tmp_path)
    assert _rows(paths, book)[1]["cut_state"] == "needs cut"
    ah.cut(folder, pages=[1])
    rows = _rows(paths, book)
    assert rows[1]["cut_state"] == "cut"
    assert rows[1]["empty_layers"] == []
    assert rows[2]["cut_state"] == "needs cut"
    ah.write_plan(folder, 1, layers=[ah.layer(box=[10, 10, 30, 30])])  # a box edit
    assert _rows(paths, book)[1]["cut_state"] == "needs cut"


def test_page_animation_motion_edit_keeps_the_cut(tmp_path):
    paths, book, folder, ah = _anim_book(tmp_path)
    ah.cut(folder, pages=[1])
    moved = ah.layer(motions=[{"type": "bob", "amplitude": 0.01, "period_s": 4.0}])
    ah.write_plan(folder, 1, layers=[moved])
    assert _rows(paths, book)[1]["cut_state"] == "cut"


def test_page_animation_lists_empty_layers(tmp_path):
    paths, book, folder, ah = _anim_book(tmp_path)
    ah.write_plan(folder, 1, layers=[ah.layer(), ah.layer("nothing", box=[0, 0, 8, 8], z=2)])
    ah.cut(folder, pages=[1])
    row = _rows(paths, book)[1]
    assert row["cut_state"] == "cut"
    assert row["empty_layers"] == ["nothing"]


def test_page_animation_missing_layer_file_needs_cut(tmp_path):
    from video_pipeline.cut_layers import layers_dir

    paths, book, folder, ah = _anim_book(tmp_path)
    ah.cut(folder, pages=[1])
    (layers_dir(folder, 1) / "square.png").unlink()
    assert _rows(paths, book)[1]["cut_state"] == "needs cut"


def test_page_animation_reads_plans_from_disk_each_time(tmp_path):  # AC-5.5
    paths, book, folder, ah = _anim_book(tmp_path)
    assert _rows(paths, book)[1]["plan"].layers[0].z == 1
    ah.write_plan(folder, 1, layers=[ah.layer(z=5)])
    assert _rows(paths, book)[1]["plan"].layers[0].z == 5


def test_page_animation_without_models_config_degrades(tmp_path):
    paths, book, _folder, _ah = _anim_book(tmp_path)
    cfg_path = paths.config_dir / "animation.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    del cfg["models"]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    rows = _rows(paths, book)
    assert rows[1]["plan_state"] == "ok"
    assert rows[1]["cut_state"] == "—"


def test_page_animation_without_animation_config_degrades(tmp_path):
    paths, book, _folder, _ah = _anim_book(tmp_path)
    (paths.config_dir / "animation.json").unlink()
    rows = _rows(paths, book)
    assert {r["cut_state"] for r in rows.values()} == {"—"}
    assert rows[1]["plan_state"] == "unavailable"
    assert "animation.json" in rows[1]["detail"]


def test_animation_summary(tmp_path):
    paths, book, folder, ah = _anim_book(tmp_path)
    ah.cut(folder, pages=[1])
    assert sb.animation_summary(sb.page_animation(paths, book)) == "3 of 8 plans, 1 cut"


def test_layer_files_whitelist(tmp_path):
    paths, book, folder, ah = _anim_book(tmp_path)
    assert sb.layer_files(paths, book, 1) == {}
    ah.cut(folder, pages=[1])
    files = sb.layer_files(paths, book, 1)
    assert set(files) == {"square.png", "background.png"}
    assert all(p.is_file() for p in files.values())


def test_mask_overlay_tints_each_layer(tmp_path):
    paths, book, folder, ah = _anim_book(tmp_path)
    assert sb.mask_overlay(paths, book, 1) is None
    two = [ah.layer(), ah.layer("left", box=[0, 30, 10, 40], z=2)]
    ah.write_plan(folder, 1, layers=two)
    ah.cut(folder, pages=[1])
    data = sb.mask_overlay(paths, book, 1)
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (64, 48)
        px = im.convert("RGB")
        inside_a, inside_b, outside = (
            px.getpixel((28, 20)),
            px.getpixel((5, 35)),
            px.getpixel((50, 5)),
        )
    assert inside_a != inside_b
    assert inside_a != outside and inside_b != outside


def test_mask_colours_follow_the_plan_order_not_the_manifest(tmp_path):
    # the boxes in the template use the plan's layer order; the masks must use the same colours
    from video_pipeline.cut_layers import cut_book

    paths, book, folder, ah = _anim_book(tmp_path)
    square, left = ah.layer(), ah.layer("left", box=[0, 30, 10, 40], z=2)
    ah.write_plan(folder, 1, layers=[square, left])
    ah.cut(folder, pages=[1])
    ah.write_plan(folder, 1, layers=[left, square])  # no overlap: the cut stays cached
    assert _rows(paths, book)[1]["cut_state"] == "cut"
    reordered = np.asarray(Image.open(io.BytesIO(sb.mask_overlay(paths, book, 1))))
    cut_book(
        folder,
        root=folder.root,
        pages=[1],
        force=True,
        load_models=ah.fake_models,
        log=lambda _: None,
    )  # now the manifest lists the layers in plan order too
    fresh = np.asarray(Image.open(io.BytesIO(sb.mask_overlay(paths, book, 1))))
    assert np.array_equal(reordered, fresh)
