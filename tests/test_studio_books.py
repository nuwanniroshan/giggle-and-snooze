"""Phase 1 of spec 004: book registry, uploads, reviewed mark, script edits, render check, status.

Every world lives under tmp_path: the studio root holds `book_data.json` (the pilot, a copy of the
mini book), `assets/`, `books/`, `episodes/`, `build/` and `config/`. FFmpeg is never run."""

import io
import json
import shutil

import numpy as np
import pytest
from PIL import Image

from tests.helpers import MINI_BOOK, load_json
from tests.render_helpers import FakeRunner, cli_args, make_world, picture
from tests.voice_helpers import make_script, write_script
from video_pipeline import frames
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import slugify
from video_pipeline.episode_tools import ROOT
from video_pipeline.studio import books as sb

PILOT_SLUG = slugify(load_json(MINI_BOOK)["metadata"]["title"])


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
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    return sb.StudioPaths(tmp_path)


def tree(root):
    """Every file under root with its bytes, to prove a call wrote nothing."""
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- paths and the book registry (FR-1) -------------------------------------------------------


def test_studio_paths_layout(tmp_path):
    paths = sb.StudioPaths(tmp_path)
    assert paths.books_dir == tmp_path / "books"
    assert paths.episodes_dir == tmp_path / "episodes"
    assert paths.build_dir == tmp_path / "build"
    assert paths.config_dir == tmp_path / "config"
    assert paths.studio_dir == tmp_path / "build" / "studio"
    assert paths.pilot_book == tmp_path / "book_data.json"
    assert paths.pilot_assets == tmp_path / "assets"
    assert paths.sfx_dir == tmp_path / "assets" / "audio_sfx"


def test_list_books_pilot_added_and_broken(studio):  # AC-1.2, FR-1
    added = new_book("A Zebra Named Quill")
    sb.save_book(studio, added, upload_files(added))
    broken = studio.books_dir / "broken-book"
    broken.mkdir(parents=True)
    (broken / "book_data.json").write_text("{not json", encoding="utf-8")
    (studio.books_dir / ".half-written.tmp").mkdir()  # a temp folder is never a book

    listed = sb.list_books(studio)

    assert [b.slug for b in listed] == ["a-zebra-named-quill", "broken-book", PILOT_SLUG]
    zebra, bad, pilot = listed
    assert pilot.is_pilot and pilot.error is None
    assert pilot.book_path == studio.pilot_book and pilot.assets_dir == studio.pilot_assets
    assert pilot.title == "The Lantern Bug Parade"
    assert not zebra.is_pilot and zebra.error is None
    assert zebra.title == "A Zebra Named Quill"
    assert zebra.book_path == studio.books_dir / "a-zebra-named-quill" / "book_data.json"
    assert zebra.assets_dir == studio.books_dir / "a-zebra-named-quill"
    assert bad.error and "not valid JSON" in bad.error
    assert sb.get_book(studio, "a-zebra-named-quill") == zebra
    assert sb.get_book(studio, "nope") is None


def test_list_books_without_a_pilot(tmp_path):
    assert sb.list_books(sb.StudioPaths(tmp_path)) == []


def test_added_book_whose_folder_does_not_match_its_title_is_flagged(studio):
    folder = studio.books_dir / "wrong-name"
    folder.mkdir(parents=True)
    shutil.copy(MINI_BOOK, folder / "book_data.json")
    (only,) = [b for b in sb.list_books(studio) if not b.is_pilot]
    assert only.slug == "wrong-name"
    assert only.error and PILOT_SLUG in only.error


# --- upload validation (AC-2.1, AC-2.2, FR-6) -----------------------------------------------


def test_valid_upload_has_no_problems():
    book = new_book()
    parsed, problems = sb.validate_upload(as_bytes(book), upload_files(book, nooks=True))
    assert problems == []
    assert parsed == book


def test_upload_respects_illustration_file_names():
    book = new_book()
    book["pages"][0]["illustration_file"] = "opening.png"
    files = upload_files(book)
    assert "opening.png" in files and "page_01.png" not in files
    assert sb.validate_upload(as_bytes(book), files)[1] == []


def test_bad_json_is_one_problem():
    parsed, problems = sb.validate_upload(b"{nope", {})
    assert parsed is None
    assert len(problems) == 1 and "not valid JSON" in problems[0]


def test_non_object_json_is_rejected():
    parsed, problems = sb.validate_upload(b"[1, 2]", {})
    assert parsed is None and problems


def test_invalid_book_lists_every_problem():
    book = new_book()
    book["metadata"]["title"] = ""
    book["pages"][2]["text"] = " "
    book["pages"][4]["prompt"] = ""
    _parsed, problems = sb.validate_upload(as_bytes(book), upload_files(book))
    text = "\n".join(problems)
    assert "metadata.title is missing" in text
    assert "page 3: 'text' is missing or empty" in text
    assert "page 5: 'prompt' is missing or empty" in text


def test_missing_illustration_and_cover_are_listed():
    book = new_book()
    files = upload_files(book)
    del files["page_04.png"], files["cover_back.png"]
    _parsed, problems = sb.validate_upload(as_bytes(book), files)
    assert any("page 4" in p and "page_04.png" in p for p in problems)
    assert any("cover_back.png" in p for p in problems)
    assert len(problems) == 2


def test_missing_front_cover_is_listed():
    book = new_book()
    files = upload_files(book)
    del files["cover_front.png"]
    assert any("cover_front.png" in p for p in sb.validate_upload(as_bytes(book), files)[1])


def test_unreadable_image_is_rejected():
    book = new_book()
    files = upload_files(book)
    files["page_02.png"] = b"not a picture"
    problems = sb.validate_upload(as_bytes(book), files)[1]
    assert len(problems) == 1 and "page_02.png" in problems[0]


@pytest.mark.parametrize(
    "name", ["../evil.png", "sub/page_01.png", "..", "", "notes.txt", "page_99.png"]
)
def test_unexpected_or_path_like_names_are_rejected(name):
    book = new_book()
    files = upload_files(book)
    files[name] = png_bytes()
    problems = sb.validate_upload(as_bytes(book), files)[1]
    assert len(problems) == 1 and repr(name) in problems[0]


def test_illustration_file_with_a_path_in_the_book_data_is_rejected():
    book = new_book()
    book["pages"][0]["illustration_file"] = "../escape.png"
    files = upload_files(book)
    problems = sb.validate_upload(as_bytes(book), files)[1]
    assert any("page 1" in p and "illustration_file" in p for p in problems)


def test_oversize_file_is_rejected(monkeypatch):
    book = new_book()
    files = upload_files(book)
    monkeypatch.setattr(sb, "MAX_UPLOAD_BYTES", len(files["page_01.png"]) - 1)
    problems = sb.validate_upload(as_bytes(book), files)[1]
    assert problems and all("too large" in p for p in problems)


def test_default_upload_limit_is_25_mb():
    assert sb.MAX_UPLOAD_BYTES == 25 * 1024 * 1024


# --- saving a book (AC-2.3, AC-2.4, AC-2.5) ---------------------------------------------------


def test_save_new_book(studio):  # AC-2.3
    book = new_book()
    saved = sb.save_book(studio, book, upload_files(book))
    folder = studio.books_dir / "the-moon-kite-race"
    assert saved == sb.Book(
        "the-moon-kite-race", book["metadata"]["title"], folder / "book_data.json", folder, False
    )
    assert load_json(folder / "book_data.json") == book
    names = sorted(p.name for p in (folder / "illustrations").iterdir())
    assert names == sorted(
        [f"page_{n:02d}.png" for n in range(1, 9)] + ["cover_back.png", "cover_front.png"]
    )
    assert [p.name for p in studio.books_dir.iterdir()] == ["the-moon-kite-race"]
    # The saved layout is exactly what render's resolve_pictures expects.
    re_.resolve_pictures(book, saved.slug, studio.episodes_dir, saved.assets_dir)


def test_save_places_nooks_in_the_episode_folder(studio):  # AC-2.5
    book = new_book()
    files = upload_files(book, nooks=True)
    saved = sb.save_book(studio, book, files)
    episode = studio.episodes_dir / saved.slug
    assert (episode / "intro_nook.png").read_bytes() == files["intro_nook.png"]
    assert (episode / "outro_nook.png").read_bytes() == files["outro_nook.png"]
    assert not (saved.assets_dir / "illustrations" / "intro_nook.png").exists()
    pictures = re_.resolve_pictures(book, saved.slug, studio.episodes_dir, saved.assets_dir)
    assert pictures.by_section["intro"] == "intro_nook"


def test_duplicate_title_is_refused(studio):  # AC-2.4
    book = new_book()
    sb.save_book(studio, book, upload_files(book))
    before = tree(studio.books_dir)
    with pytest.raises(sb.StudioError, match="already exists"):
        sb.save_book(studio, book, upload_files(book))
    assert tree(studio.books_dir) == before


def test_replace_swaps_only_that_book(studio):  # AC-2.4
    first = new_book()
    first["pages"][0]["illustration_file"] = "old_name.png"
    sb.save_book(studio, first, upload_files(first))
    other = new_book("Another Tale Entirely")
    sb.save_book(studio, other, upload_files(other))
    other_before = tree(studio.books_dir / "another-tale-entirely")

    second = new_book()
    second["metadata"]["blurb"] = "replaced"
    sb.save_book(studio, second, upload_files(second), replace=True)

    folder = studio.books_dir / "the-moon-kite-race"
    assert load_json(folder / "book_data.json")["metadata"]["blurb"] == "replaced"
    assert not (folder / "illustrations" / "old_name.png").exists()
    assert (folder / "illustrations" / "page_01.png").exists()
    assert tree(studio.books_dir / "another-tale-entirely") == other_before
    assert sorted(p.name for p in studio.books_dir.iterdir()) == [
        "another-tale-entirely",
        "the-moon-kite-race",
    ]


def test_pilot_slug_is_refused_even_with_replace(studio):
    book = load_json(MINI_BOOK)
    with pytest.raises(sb.StudioError, match="pilot"):
        sb.save_book(studio, book, upload_files(book), replace=True)
    assert not studio.books_dir.exists() or not any(studio.books_dir.iterdir())


def test_save_refuses_an_invalid_upload(studio):
    book = new_book()
    files = upload_files(book)
    del files["cover_front.png"]
    with pytest.raises(sb.StudioError, match="cover_front.png"):
        sb.save_book(studio, book, files)
    assert not (studio.books_dir / "the-moon-kite-race").exists()


def test_failed_save_leaves_no_partial_book(studio, monkeypatch):
    book = new_book()
    real_replace = sb.os.replace

    def boom(src, dst):
        if str(dst).endswith("the-moon-kite-race"):
            raise OSError("disk trouble")
        return real_replace(src, dst)

    monkeypatch.setattr(sb.os, "replace", boom)
    with pytest.raises(OSError):
        sb.save_book(studio, book, upload_files(book))
    assert not studio.books_dir.exists() or list(studio.books_dir.iterdir()) == []


# --- the script, the reviewed mark and line edits (AC-4.2-4.4) ------------------------------


@pytest.fixture
def scripted(studio):
    pilot = sb.get_book(studio, PILOT_SLUG)
    write_script(make_script(studio.pilot_book), studio.episodes_dir, PILOT_SLUG)
    return pilot


def test_script_paths(studio, scripted):
    assert sb.script_path(studio, scripted) == (
        studio.episodes_dir / PILOT_SLUG / "episode_script.json"
    )
    assert sb.reviewed_path(studio, scripted) == studio.episodes_dir / PILOT_SLUG / "reviewed.json"
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
    book_path = tmp_path / "book_data.json"
    shutil.copy(MINI_BOOK, book_path)
    return make_world(tmp_path, monkeypatch, book_path)


def check(world, sfx_dir=None):
    return re_.render_check(
        world["book"],
        world["episodes"],
        world["build"],
        world["config"],
        world["assets"],
        sfx_dir if sfx_dir is not None else world["assets"] / "audio_sfx",
    )


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"a process was started: {args}")

    monkeypatch.setattr(re_.subprocess, "run", refuse)
    monkeypatch.setattr(frames.subprocess, "run", refuse)


def test_sfx_dir_defaults_to_the_shared_assets_folder():
    assert re_.parse_args([]).sfx_dir == ROOT / "assets" / "audio_sfx"


def test_render_check_not_rendered(world, no_subprocess):
    before = tree(world["book"].parent)
    assert check(world) == "not_rendered"
    assert tree(world["book"].parent) == before


def test_render_check_blocked_without_approval(tmp_path, monkeypatch, no_subprocess):
    book_path = tmp_path / "book_data.json"
    shutil.copy(MINI_BOOK, book_path)
    world = make_world(tmp_path, monkeypatch, book_path, approve=False)
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
    picture(world["assets"] / "illustrations" / "page_03.png", (1, 1, 1))
    assert check(world) == "out_of_date"


def test_render_check_with_a_fresh_build_writes_no_sound_cache(world, no_subprocess):
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    (world["build"] / "sfx" / "page_turn.wav").unlink()
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
        world["book"],
        world["episodes"],
        world["build"],
        world["config"],
        world["assets"],
        run=FakeRunner(),
    )
    assert seen == [ROOT / "assets" / "audio_sfx"]


# --- stage status and the next action (AC-1.2, AC-1.3, AC-5.3, AC-6.2) ----------------------


def no_jobs(book_slug, stage):
    return None


def status_of(paths, book, jobs=no_jobs):
    return sb.stage_status(paths, book, jobs)


def states(status):
    return (status["script"]["state"], status["voices"]["state"], status["video"]["state"])


def test_status_of_a_new_book(studio):
    book = new_book()
    saved = sb.save_book(studio, book, upload_files(book))
    status = status_of(studio, saved)
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
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    world = make_world(tmp_path, monkeypatch, tmp_path / "book_data.json", approve=approve)
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

    picture(world["assets"] / "illustrations" / "page_02.png", (9, 9, 9))
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
    (broken / "book_data.json").write_text("{", encoding="utf-8")
    (book,) = [b for b in sb.list_books(studio) if not b.is_pilot]
    status = status_of(studio, book)
    assert states(status) == ("not_started", "not_started", "not_started")
    assert status["script"]["detail"] == book.error
