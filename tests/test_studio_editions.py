"""Spec 019, Phases 3-4: the Build book stage (editions) and serving its files and the flipbook.

Stage commands are replaced by tiny `python -c` programs run by a real JobRunner (the `Commands`
fake); the real book builder never runs."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import make_root
from tests.render_helpers import picture
from tests.test_studio_app import PILOT_SLUG, Commands, Studio, pilot_folder, text_of
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

PAGES = [f"page_{n:02d}.png" for n in range(1, 9)]
COVERS = ["cover_front.png", "cover_back.png"]
EDITIONS = {
    "X_10Page_Digital_Edition.pdf": b"%PDF digital",
    "X_KDP_24Page_Print_Interior.pdf": b"%PDF interior, a little longer",
    "X.epub": b"PK epub",
    "X_Front_Cover.png": b"png front",
    "index.html": b'<img src="X_Front_Cover.png"><img src="../illustrations/page_01.png">',
}
SLOW = "import time; print('building', flush=True); time.sleep(30)"


@pytest.fixture
def studio(tmp_path):
    made = []

    def build(commands: Commands | None = None, pictures: list[str] | None = None) -> Studio:
        make_root(tmp_path)
        for name in [*PAGES, *COVERS] if pictures is None else pictures:
            picture(pilot_folder(tmp_path).illustrations / name)
        s = Studio(tmp_path, commands)
        made.append(s)
        return s

    yield build
    for s in made:
        if s.runner.current():
            s.runner.stop()


def build_dir(s: Studio):
    return pilot_folder(s.paths.root).build_dir


def write_editions(s: Studio, files: dict[str, bytes] = EDITIONS, age: float = 0) -> None:
    """The builder's output; every file `age` seconds newer than now (so sources look older)."""
    out = build_dir(s)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.time() + age
    for name, data in files.items():
        (out / name).write_bytes(data)
        os.utime(out / name, (stamp, stamp))


def editions(s: Studio) -> dict:
    return sb.editions_state(s.paths, s.book())


def no_jobs(book_slug, stage):
    return None


# --- Phase 3: the stage, its command and its state (FR-3; AC-2.1-2.3) --------------------------


def test_build_stage_contract():
    assert sb.BUILD_STAGE == "build"
    assert sb.STAGE_LABELS["build"] == "book build"
    assert sb.STATUS_KEYS["build"] == "editions"


def test_build_command_runs_the_book_builder(tmp_path):  # AC-2.1
    paths = sb.StudioPaths(tmp_path)
    folder = make_root(tmp_path)
    book = sb.get_book(paths, folder.slug)
    cmd = sj.stage_command("build", book, paths)
    assert cmd == [
        sys.executable,
        str(tmp_path / "scripts" / "build_book.py"),
        "--book",
        folder.slug,
        "--root",
        str(tmp_path),
    ]


def test_editions_blocked_names_missing_pictures(studio):  # AC-2.2
    s = studio(pictures=PAGES[:7])
    state = editions(s)
    assert state["state"] == "blocked"
    assert state["missing"] == ["page_08.png", *COVERS]
    assert "page_08.png" in state["detail"] and "cover_back.png" in state["detail"]
    html = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "paint the missing pictures first" in html and "page_08.png" in html


def test_build_refused_while_blocked(studio):  # AC-2.2
    s = studio(pictures=PAGES)
    r = s.client.post(f"/books/{PILOT_SLUG}/run/build")
    assert r.status_code == 409
    assert "paint the missing pictures first: cover_front.png, cover_back.png" in text_of(r.text)
    assert s.commands.calls == [] and s.runner.current() is None


def test_no_edition_files_is_not_built(studio):
    s = studio()
    state = editions(s)
    assert state["state"] == "not_started" and state["files"] == []
    assert state["detail"] == "not built"


def test_edition_files_are_listed_whatever_their_names(studio):  # FR-4, spec 020 names
    s = studio()
    write_editions(s)
    out = build_dir(s)
    (out / "_tmp_1.jpg").write_bytes(b"temp")
    (out / "_tmp_cover.png").write_bytes(b"temp")
    (out / ".DS_Store").write_bytes(b"x")
    (out / "notes.md").write_bytes(b"x")
    (out / "audio").mkdir()
    (out / "audio" / "status.json").write_text("{}")
    (out / "trap.pdf").mkdir()
    files = sb.edition_files(s.paths, s.book())
    assert [f["name"] for f in files] == sorted(EDITIONS)
    sizes = {f["name"]: f["size"] for f in files}
    assert sizes == {name: len(data) for name, data in EDITIONS.items()}
    assert all(isinstance(f["mtime"], float) for f in files)


def test_built_editions_are_done(studio):
    s = studio()
    write_editions(s, age=5)
    state = editions(s)
    assert state["state"] == "done" and state["detail"] == "built"
    assert len(state["files"]) == len(EDITIONS)


@pytest.mark.parametrize("source", ["book.json", "illustrations/page_03.png", "blueprint.json"])
def test_changed_book_json_or_picture_marks_editions_out_of_date(studio, source):  # AC-2.3
    s = studio()
    folder = pilot_folder(s.paths.root)
    if source == "blueprint.json":
        shutil.copytree(
            PROJECT_ROOT / "assets" / "sound_library", s.paths.root / "assets" / "sound_library"
        )  # blueprints are checked against the sound library
        write_atomic(folder.blueprint_path, valid_blueprint())
    write_editions(s, age=-60)  # built a minute ago, after every source
    for path in [folder.book_json, folder.blueprint_path, *folder.illustrations.glob("*.png")]:
        if path.is_file():
            os.utime(path, (time.time() - 120, time.time() - 120))
    assert editions(s)["state"] == "done"
    now = time.time()
    os.utime(folder.folder / source, (now, now))
    state = editions(s)
    assert state["state"] == "out_of_date"
    assert Path(source).name in state["detail"]
    assert state["files"]  # still listed for download
    assert "OUT OF DATE" in text_of(s.client.get(f"/books/{PILOT_SLUG}").text)


def test_editions_older_than_sources_but_rebuilt_are_done(studio):
    s = studio()
    folder = pilot_folder(s.paths.root)
    past = time.time() - 120
    for path in [folder.book_json, *folder.illustrations.glob("*.png")]:
        os.utime(path, (past, past))
    write_editions(s)
    assert editions(s)["state"] == "done"


def test_status_has_editions_and_next_action_builds_after_the_video(studio, monkeypatch):
    s = studio()
    status = sb.stage_status(s.paths, s.book(), no_jobs)
    assert status["editions"]["state"] == "not_started"
    assert status["next_action"] == "Generate script"  # the flow order is unchanged
    done = {"state": "done", "detail": ""}
    monkeypatch.setattr(sb, "_script_state", lambda *_a: done)
    monkeypatch.setattr(sb, "_voices_state", lambda *_a: {"state": "approved", "detail": ""})
    monkeypatch.setattr(sb, "_video_state", lambda *_a: done)
    assert sb.stage_status(s.paths, s.book(), no_jobs)["next_action"] == "Build book"
    write_editions(s, age=5)
    assert sb.stage_status(s.paths, s.book(), no_jobs)["next_action"] == "Watch video"


def test_build_runs_as_a_job_and_lists_files(studio):  # AC-2.1
    s = studio()
    r = s.client.post(f"/books/{PILOT_SLUG}/run/build")
    assert r.status_code == 303 and r.headers["location"] == f"/books/{PILOT_SLUG}"
    s.wait()
    assert s.commands.calls == [("build", PILOT_SLUG, False)]
    assert s.runner.latest(PILOT_SLUG, "build")["state"] == "done"


def test_build_job_running_shows_running(studio):
    s = studio(Commands(SLOW))
    assert s.client.post(f"/books/{PILOT_SLUG}/run/build").status_code == 303
    status = sb.stage_status(s.paths, s.book(), s.runner.latest)
    assert status["editions"]["state"] == "running"
    assert status["next_action"] == "Wait for book build to finish"


def test_failed_build_job_shows_failed_and_keeps_earlier_files(studio):  # error scenario
    s = studio(Commands("import sys; print('build stopped: no fonts'); sys.exit(2)"))
    write_editions(s, age=-60)
    assert s.client.post(f"/books/{PILOT_SLUG}/run/build").status_code == 303
    s.wait()
    status = sb.stage_status(s.paths, s.book(), s.runner.latest)
    assert status["editions"]["state"] == "failed"
    assert [f["name"] for f in status["editions"]["files"]] == sorted(EDITIONS)
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "build stopped: no fonts" in page
    assert "X_10Page_Digital_Edition.pdf" in page


def test_build_refused_while_any_job_runs(studio):
    s = studio(Commands(SLOW))
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    r = s.client.post(f"/books/{PILOT_SLUG}/run/build")
    assert r.status_code == 409
    assert "script generation is running for The Lantern Bug Parade" in text_of(r.text)
    assert [c[0] for c in s.commands.calls] == ["script"]


def test_book_page_lists_editions_with_sizes(studio):  # AC-2.1
    s = studio()
    write_editions(s, {**EDITIONS, "X_Complete_Reader.pdf": b"x" * 2048})
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert 'id="stage-build"' in html
    assert "Build book" in text_of(html)
    for name in EDITIONS:
        assert f'href="/books/{PILOT_SLUG}/editions/{name}"' in html
    assert "2.0 KB" in text_of(html)
    pdf = f'href="/books/{PILOT_SLUG}/editions/X_10Page_Digital_Edition.pdf" download'
    assert pdf in html
    assert f'href="/books/{PILOT_SLUG}/editions/index.html" target="_blank"' in html


def test_flow_strip_has_an_editions_step(studio):
    s = studio()
    steps = s.app.state.studio.flow_steps(s.book())
    assert [st["key"] for st in steps][-2:] == ["video", "editions"]
    assert steps[-1]["href"] == f"/books/{PILOT_SLUG}#stage-build"


# --- Phase 4: downloads and the flipbook (FR-4; AC-2.1, AC-2.4) -------------------------------


def test_edition_download_serves_a_listed_file(studio):
    s = studio()
    write_editions(s)
    for name, data in EDITIONS.items():
        r = s.client.get(f"/books/{PILOT_SLUG}/editions/{name}")
        assert r.status_code == 200, name
        assert r.content == data
    assert "pdf" in s.client.get(
        f"/books/{PILOT_SLUG}/editions/X_10Page_Digital_Edition.pdf"
    ).headers["content-type"]


@pytest.mark.parametrize(
    "name",
    [
        "..%2Fbook.json",
        "../book.json",
        "..%2F..%2F..%2Fconfig%2Frender.json",
        "audio",
        "audio%2Fstatus.json",
        "status.json",
        "_tmp_1.jpg",
        "notes.md",
        "missing.pdf",
        "%2Fetc%2Fpasswd",
    ],
)
def test_edition_route_refuses_unlisted_names(studio, name):  # FR-4, path traversal
    s = studio()
    write_editions(s)
    out = build_dir(s)
    (out / "_tmp_1.jpg").write_bytes(b"temp")
    (out / "notes.md").write_bytes(b"x")
    (out / "status.json").write_text("{}")
    (out / "audio").mkdir()
    (out / "audio" / "status.json").write_text("{}")
    r = s.client.get(f"/books/{PILOT_SLUG}/editions/{name}")
    assert r.status_code == 404
    assert b'"metadata"' not in r.content


def test_edition_route_for_an_unknown_book_is_404(studio):
    s = studio()
    write_editions(s)
    assert s.client.get("/books/nope/editions/index.html").status_code == 404


def test_flipbook_picture_paths_resolve_to_book_illustrations(studio):  # AC-2.4
    from urllib.parse import urljoin

    s = studio()
    write_editions(s)
    flipbook = f"http://127.0.0.1:8765/books/{PILOT_SLUG}/editions/index.html"
    assert s.client.get(flipbook).status_code == 200
    page = urljoin(flipbook, "../illustrations/page_01.png")
    assert page.endswith(f"/books/{PILOT_SLUG}/illustrations/page_01.png")
    r = s.client.get(page)
    assert r.status_code == 200
    assert r.content == (pilot_folder(s.paths.root).illustrations / "page_01.png").read_bytes()
    cover = s.client.get(urljoin(flipbook, "X_Front_Cover.png"))
    assert cover.status_code == 200 and cover.content == EDITIONS["X_Front_Cover.png"]
    assert s.client.get(urljoin(flipbook, "../illustrations/cover_back.png")).status_code == 200


@pytest.mark.parametrize(
    "name",
    ["stray.png", "..%2Fbook.json", "../book.json", "page_01.png.bak", "page_99.png", "intro.png"],
)
def test_flipbook_picture_route_refuses_unknown_files(studio, name):
    s = studio()
    folder = pilot_folder(s.paths.root)
    picture(folder.illustrations / "stray.png")
    (folder.illustrations / "page_01.png.bak").write_bytes(b"old")
    r = s.client.get(f"/books/{PILOT_SLUG}/illustrations/{name}")
    assert r.status_code == 404


# --- spec 023 Phase 3: the web reader's own files and voice tracks --------------------------------


def write_reader(s: Studio) -> Path:
    out = build_dir(s) / "reader"
    (out / "fonts").mkdir(parents=True, exist_ok=True)
    for name in ("tokens.css", "reader.css", "reader.js", "logo.png"):
        (out / name).write_bytes(f"/* {name} */".encode())
    for name in ("Andika-Regular.ttf", "OFL-Andika.txt"):
        (out / "fonts" / name).write_bytes(name.encode())
    return out


def test_reader_files_resolve_from_the_reader_page(studio):  # spec 023 FR-5
    from urllib.parse import urljoin

    s = studio()
    write_editions(s)
    write_reader(s)
    page = f"http://127.0.0.1:8765/books/{PILOT_SLUG}/editions/index.html"
    for rel in ("reader/tokens.css", "reader/reader.css", "reader/reader.js", "reader/logo.png"):
        r = s.client.get(urljoin(page, rel))
        assert r.status_code == 200, rel
        assert r.content == f"/* {rel.split('/')[-1]} */".encode()
    assert "css" in s.client.get(urljoin(page, "reader/reader.css")).headers["content-type"]
    css = urljoin(page, "reader/tokens.css")
    font = s.client.get(urljoin(css, "fonts/Andika-Regular.ttf"))
    assert font.status_code == 200 and font.content == b"Andika-Regular.ttf"
    assert s.client.get(urljoin(css, "fonts/OFL-Andika.txt")).status_code == 200


@pytest.mark.parametrize(
    "rel",
    [
        "reader/notes.md",
        "reader/..%2Fbook.json",
        "reader/fonts/..%2F..%2Fbook.json",
        "reader/fonts/evil.js",
        "reader/missing.css",
        "reader/fonts/Missing.ttf",
        "reader/link.css",
    ],
)
def test_reader_route_refuses_other_files(studio, rel):
    s = studio()
    write_editions(s)
    out = write_reader(s)
    (out / "notes.md").write_text("x")
    (out / "fonts" / "evil.js").write_text("x")
    (out / "link.css").symlink_to(pilot_folder(s.paths.root).book_json)
    r = s.client.get(f"/books/{PILOT_SLUG}/editions/{rel}")
    assert r.status_code == 404
    assert b'"metadata"' not in r.content


def test_reader_voice_tracks_resolve(studio):  # spec 023 AC-5.3
    from urllib.parse import urljoin

    s = studio()
    write_editions(s)
    tracks = build_dir(s) / "audio" / "tracks"
    tracks.mkdir(parents=True)
    (tracks / "page-01.wav").write_bytes(b"RIFF one")
    (tracks / "page-01.json").write_text("{}")
    page = f"http://127.0.0.1:8765/books/{PILOT_SLUG}/editions/index.html"
    r = s.client.get(urljoin(page, "audio/tracks/page-01.wav"))
    assert r.status_code == 200 and r.content == b"RIFF one"
    assert s.client.get(urljoin(page, "audio/tracks/page-01.json")).status_code == 404
    assert s.client.get(urljoin(page, "audio/tracks/page-02.wav")).status_code == 404
