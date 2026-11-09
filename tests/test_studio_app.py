"""Phase 3 of spec 004: the web app (pages, stage controls, media, start-up).

Every world lives under tmp_path (the studio root): `book_data.json` is a copy of the mini book
(the pilot), added books go to `books/<slug>/`. Stage commands are replaced by tiny `python -c`
programs run by a real JobRunner; FFmpeg and the voice model never run."""

import io
import json
import re
import shutil
import socket
import sys
import time

import pytest
from PIL import Image
from starlette.testclient import TestClient

from tests.helpers import MINI_BOOK, load_json
from tests.render_helpers import FakeRunner, cli_args, make_world, picture
from tests.voice_helpers import make_script, write_script
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import slugify
from video_pipeline.studio import app as sa
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

PILOT_SLUG = slugify(load_json(MINI_BOOK)["metadata"]["title"])
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
STATIC = sa.STUDIO_DIR / "static"


def png_bytes(colour=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), colour).save(buf, format="PNG")
    return buf.getvalue()


def new_book(title="The Moon Kite Race") -> dict:
    book = load_json(MINI_BOOK)
    book["metadata"]["title"] = title
    return book


def upload_parts(book: dict, *, skip: str | None = None, extra: dict | None = None) -> list:
    parts = [("book_data", ("book_data.json", json.dumps(book).encode(), "application/json"))]
    names = [f"page_{p['page_number']:02d}.png" for p in book["pages"]]
    for name in [*names, "cover_front.png", "cover_back.png"]:
        if name != skip:
            parts.append(("files", (name, png_bytes(), "image/png")))
    for name, data in (extra or {}).items():
        parts.append(("files", (name, data, "image/png")))
    return parts


class Commands:
    """Stands in for `stage_command`: records each call and returns a tiny Python program."""

    def __init__(self, code: str = "print('working'); print('finished')"):
        self.calls: list[tuple[str, str, bool]] = []
        self.code = code

    def __call__(self, stage, book, paths, force=False):
        self.calls.append((stage, book.slug, force))
        return [sys.executable, "-c", self.code]


class Studio:
    def __init__(self, root, commands: Commands | None = None):
        self.paths = sb.StudioPaths(root)
        self.runner = sj.JobRunner(self.paths.studio_dir, cwd=root, stop_grace=0.5)
        self.commands = commands or Commands()
        self.app = sa.create_app(self.paths, self.runner, self.commands, port=PORT)
        self.client = TestClient(self.app, base_url=ORIGIN, follow_redirects=False)

    def book(self, slug=PILOT_SLUG):
        return sb.get_book(self.paths, slug)

    def wait(self):
        assert self.runner.wait(10)


@pytest.fixture
def bare(tmp_path):
    """A pilot with no episode script yet."""
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    s = Studio(tmp_path)
    yield s
    if s.runner.current():
        s.runner.stop()


def with_world(tmp_path, monkeypatch, approve=True, commands=None):
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    world = make_world(tmp_path, monkeypatch, tmp_path / "book_data.json", approve=approve)
    return Studio(tmp_path, commands), world


@pytest.fixture
def approved(tmp_path, monkeypatch):
    s, world = with_world(tmp_path, monkeypatch)
    yield s, world
    if s.runner.current():
        s.runner.stop()


@pytest.fixture
def pending(tmp_path, monkeypatch):
    s, world = with_world(tmp_path, monkeypatch, approve=False)
    yield s, world
    if s.runner.current():
        s.runner.stop()


def scripted(s: Studio) -> None:
    write_script(make_script(s.paths.pilot_book), s.paths.episodes_dir, PILOT_SLUG)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- home (AC-1.2, AC-1.3) ----------------------------------------------------------------------


def test_home_lists_books_with_states_and_next_action(bare):
    r = bare.client.get("/")
    assert r.status_code == 200
    page = text_of(r.text)
    assert "The Lantern Bug Parade" in page
    assert "NOT STARTED" in page
    assert "Generate script" in page
    assert f'href="/books/{PILOT_SLUG}"' in r.text


def test_home_shows_an_added_book_and_escapes_its_title(bare):
    book = new_book("The <b>Bold</b> Kite")
    assert bare.client.post("/books", files=upload_parts(book)).status_code == 303
    r = bare.client.get("/")
    assert "The &lt;b&gt;Bold&lt;/b&gt; Kite" in r.text
    assert "<b>Bold</b>" not in r.text


def test_home_states_for_an_approved_book(approved):
    s, _world = approved
    page = text_of(s.client.get("/").text)
    assert "DONE" in page and "APPROVED" in page
    assert "Render video" in page


def test_pages_use_only_local_assets_and_the_black_and_white_palette(bare):
    html = bare.client.get("/").text
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    colours = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,6}\b", css)}
    assert colours <= {"#000", "#fff", "#6b6b6b", "#e5e5e5"}
    assert "rgb" not in css and "box-shadow" not in css and "gradient" not in css
    assert len(css.splitlines()) < 200
    assert bare.client.get("/static/studio.css").status_code == 200
    assert bare.client.get("/static/studio.js").status_code == 200


def test_unknown_book_is_404(bare):
    assert bare.client.get("/books/no-such-book").status_code == 404
    assert bare.client.get("/books/no-such-book/script").status_code == 404


# --- uploads (AC-2.1-2.5) -----------------------------------------------------------------------


def test_upload_form_page(bare):
    r = bare.client.get("/books/new")
    assert r.status_code == 200
    assert 'enctype="multipart/form-data"' in r.text
    assert 'name="book_data"' in r.text and 'name="files"' in r.text


def test_upload_valid_book_is_saved_and_listed(bare):
    book = new_book()
    nook = {"intro_nook.png": png_bytes((5, 5, 5))}
    r = bare.client.post("/books", files=upload_parts(book, extra=nook))
    slug = slugify(book["metadata"]["title"])
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{slug}"
    assert (bare.paths.books_dir / slug / "book_data.json").is_file()
    assert (bare.paths.books_dir / slug / "illustrations" / "page_01.png").is_file()
    assert (bare.paths.episodes_dir / slug / "intro_nook.png").is_file()
    page = text_of(bare.client.get(f"/books/{slug}").text)
    assert "The Moon Kite Race" in page and "Generate script" in page


def test_upload_invalid_json_saves_nothing(bare):
    parts = upload_parts(new_book())
    parts[0] = ("book_data", ("book_data.json", b"{not json", "application/json"))
    r = bare.client.post("/books", files=parts)
    assert r.status_code == 422
    assert "not valid JSON" in r.text
    assert not bare.paths.books_dir.exists()


def test_upload_lists_every_problem(bare):
    parts = upload_parts(new_book(), skip="page_03.png", extra={"../evil.png": png_bytes()})
    r = bare.client.post("/books", files=parts)
    assert r.status_code == 422
    assert "page_03.png" in r.text
    assert "evil.png" in r.text
    assert not bare.paths.books_dir.exists()
    assert not (bare.paths.root.parent / "evil.png").exists()


def test_upload_without_book_data_is_refused(bare):
    r = bare.client.post("/books", files=[("files", ("cover_front.png", png_bytes(), "image/png"))])
    assert r.status_code == 422
    assert "book data" in r.text


def test_duplicate_upload_refused_then_replaced_with_confirmation(bare):
    book = new_book()
    slug = slugify(book["metadata"]["title"])
    assert bare.client.post("/books", files=upload_parts(book)).status_code == 303
    marker = bare.paths.books_dir / slug / "illustrations" / "page_01.png"
    before = marker.read_bytes()
    book["metadata"]["author"] = "Someone Else"
    r = bare.client.post("/books", files=upload_parts(book))
    assert r.status_code == 409
    assert "already exists" in r.text and 'name="replace"' in r.text
    assert load_json(bare.paths.books_dir / slug / "book_data.json")["metadata"]["author"] != (
        "Someone Else"
    )
    r = bare.client.post("/books", data={"replace": "1"}, files=upload_parts(book))
    assert r.status_code == 303
    assert load_json(bare.paths.books_dir / slug / "book_data.json")["metadata"]["author"] == (
        "Someone Else"
    )
    assert marker.read_bytes() == before  # the same picture was uploaded again


def test_upload_of_the_pilot_title_is_refused(bare):
    r = bare.client.post(
        "/books",
        data={"replace": "1"},
        files=upload_parts(new_book(load_json(MINI_BOOK)["metadata"]["title"])),
    )
    assert r.status_code == 409
    assert "pilot" in r.text
    assert not (bare.paths.books_dir / PILOT_SLUG).exists()


# --- running stages (AC-3.1-3.6, AC-4.4) --------------------------------------------------------


def test_generate_script_runs_the_stage_command(bare):
    r = bare.client.post(f"/books/{PILOT_SLUG}/run/script")
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}"
    bare.wait()
    assert bare.commands.calls == [("script", PILOT_SLUG, False)]
    page = text_of(bare.client.get(f"/books/{PILOT_SLUG}").text)
    assert "finished" in page  # the log of the last run
    assert "DONE" in page


def test_failed_stage_shows_failure_and_the_error(tmp_path):
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    s = Studio(tmp_path, Commands("import sys; sys.exit('error: the provider said no')"))
    assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
    s.wait()
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "FAILED" in page
    assert "error: the provider said no" in page


def test_regenerating_a_script_requires_confirmation(bare):
    scripted(bare)
    r = bare.client.post(f"/books/{PILOT_SLUG}/run/script")
    assert r.status_code == 409
    assert "confirm" in r.text.lower()
    assert bare.commands.calls == []
    r = bare.client.post(f"/books/{PILOT_SLUG}/run/script", data={"confirm": "1"})
    assert r.status_code == 303
    bare.wait()
    assert bare.commands.calls == [("script", PILOT_SLUG, True)]


def test_book_page_offers_regenerate_with_confirm(bare):
    scripted(bare)
    html = bare.client.get(f"/books/{PILOT_SLUG}").text
    assert re.search(r'name="confirm"[^>]*required', html)  # the box must be ticked


def test_voices_blocked_until_the_script_is_reviewed(bare):
    r = bare.client.post(f"/books/{PILOT_SLUG}/run/voices")
    assert r.status_code == 409
    scripted(bare)
    r = bare.client.post(f"/books/{PILOT_SLUG}/run/voices")
    assert r.status_code == 409
    assert "review" in r.text.lower()
    html = bare.client.get(f"/books/{PILOT_SLUG}").text
    assert re.search(r"<button[^>]*disabled[^>]*>\s*Synthesize voices", html)
    sb.mark_reviewed(bare.paths, bare.book())
    assert bare.client.post(f"/books/{PILOT_SLUG}/run/voices").status_code == 303
    bare.wait()
    assert bare.commands.calls == [("voices", PILOT_SLUG, False)]


def test_voices_refused_when_the_script_changed_after_review(bare):
    scripted(bare)
    sb.mark_reviewed(bare.paths, bare.book())
    path = sb.script_path(bare.paths, bare.book())
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert bare.client.post(f"/books/{PILOT_SLUG}/run/voices").status_code == 409
    assert bare.commands.calls == []


def test_voices_allowed_when_audio_was_made_from_this_script(pending):
    s, _world = pending
    assert not sb.reviewed_ok(s.paths, s.book())
    r = s.client.post(f"/books/{PILOT_SLUG}/run/voices", data={"force": "1"})
    assert r.status_code == 303
    s.wait()
    assert s.commands.calls == [("voices", PILOT_SLUG, True)]


def test_render_blocked_until_audio_is_approved(pending):
    s, world = pending
    r = s.client.post(f"/books/{PILOT_SLUG}/run/render")
    assert r.status_code == 409
    assert "not approved" in r.text
    sv.main(["approve", *cli_args(world)[:8]])
    assert s.client.post(f"/books/{PILOT_SLUG}/run/render").status_code == 303
    s.wait()
    assert s.commands.calls == [("render", PILOT_SLUG, False)]


def test_unknown_stage_is_404(bare):
    assert bare.client.post(f"/books/{PILOT_SLUG}/run/upload").status_code == 404


def test_busy_refuses_other_stages_and_says_what_runs(tmp_path):
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    s = Studio(tmp_path, Commands("import time; print('busy', flush=True); time.sleep(30)"))
    try:
        assert s.client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
        r = s.client.post(f"/books/{PILOT_SLUG}/run/script")
        assert r.status_code == 409
        assert "script generation" in r.text and "running" in r.text.lower()
        home = text_of(s.client.get("/").text)
        assert "RUNNING" in home and "script generation" in home
        html = s.client.get(f"/books/{PILOT_SLUG}").text
        assert "data-job-id" in html
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Generate script", html)
        current = s.client.get("/api/jobs/current").json()
        assert current["job"]["stage"] == "script"
        assert current["job"]["book"] == PILOT_SLUG
        assert current["elapsed_seconds"] >= 0
        job_id = current["job"]["id"]
        text, offset = "", 0
        for _ in range(100):
            data = s.client.get(f"/api/jobs/{job_id}/log", params={"offset": offset}).json()
            text += data["text"]
            offset = data["offset"]
            if "busy" in text:
                break
            time.sleep(0.05)
        assert "busy" in text
        assert data["running"] is True
        again = s.client.get(f"/api/jobs/{job_id}/log", params={"offset": offset}).json()
        assert again["text"] == "" and again["offset"] == offset
        r = s.client.post("/jobs/stop")
        assert r.status_code == 303
        assert s.client.get("/api/jobs/current").json()["job"] is None
        assert "STOPPED" in text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    finally:
        if s.runner.current():
            s.runner.stop()


def test_log_of_unknown_job_is_404(bare):
    assert bare.client.get("/api/jobs/nope/log").status_code == 404
    assert bare.client.get("/api/jobs/..%2Fx/log").status_code == 404


# --- script review and edits (AC-4.1-4.3) -------------------------------------------------------


def test_script_page_shows_intro_pages_lines_and_outro(bare):
    scripted(bare)
    r = bare.client.get(f"/books/{PILOT_SLUG}/script")
    assert r.status_code == 200
    script = sb.load_script(bare.paths, bare.book())
    for line_id, _line in sb.script_lines(script):
        assert f'name="{line_id}"' in r.text
    page = text_of(r.text)
    assert "Intro" in page and "Outro" in page
    first_page = load_json(MINI_BOOK)["pages"][0]["text"]
    assert first_page.split()[0] in page
    assert f"/media/{PILOT_SLUG}/illustration/page_01.png" in r.text
    assert "storyteller" in page and "narration" in page


def test_script_page_without_a_script(bare):
    r = bare.client.get(f"/books/{PILOT_SLUG}/script")
    assert r.status_code == 200
    assert "Generate script" in text_of(r.text)


def test_save_valid_edit_writes_and_clears_review(bare):
    scripted(bare)
    sb.mark_reviewed(bare.paths, bare.book())
    script = sb.load_script(bare.paths, bare.book())
    line_id, line = sb.script_lines(script)[0]
    new_text = line["text"] + " Let us begin!"
    r = bare.client.post(f"/books/{PILOT_SLUG}/script", data={line_id: new_text})
    assert r.status_code == 303
    saved = dict(sb.script_lines(sb.load_script(bare.paths, bare.book())))
    assert saved[line_id]["text"] == new_text
    assert not sb.reviewed_ok(bare.paths, bare.book())


def test_save_invalid_edit_saves_nothing_and_shows_problem_by_the_line(bare):
    scripted(bare)
    path = sb.script_path(bare.paths, bare.book())
    before = path.read_bytes()
    r = bare.client.post(
        f"/books/{PILOT_SLUG}/script",
        data={"intro-02": "kept attempt <shout>", "page-01-01": "Wrong words."},
    )
    assert r.status_code == 422
    assert path.read_bytes() == before
    assert "kept attempt &lt;shout&gt;" in r.text  # textarea keeps what was typed
    html = r.text
    problems = re.findall(r'id="problems-([a-z0-9-]+)"', html)
    assert "intro-02" in problems
    assert "page-01-01" in problems or "page-01" in problems
    assert "narration does not match" in html


def test_mark_reviewed_button(bare):
    scripted(bare)
    html = bare.client.get(f"/books/{PILOT_SLUG}/script").text
    assert f'action="/books/{PILOT_SLUG}/script/reviewed"' in html
    r = bare.client.post(f"/books/{PILOT_SLUG}/script/reviewed")
    assert r.status_code == 303
    assert sb.reviewed_ok(bare.paths, bare.book())
    assert "REVIEWED" in text_of(bare.client.get(f"/books/{PILOT_SLUG}/script").text)


def test_mark_reviewed_without_a_script_is_refused(bare):
    r = bare.client.post(f"/books/{PILOT_SLUG}/script/reviewed")
    assert r.status_code == 409


# --- audio (AC-5.1-5.3) -------------------------------------------------------------------------


def test_audio_page_lists_preview_tracks_and_measurements(pending):
    s, world = pending
    r = s.client.get(f"/books/{PILOT_SLUG}/audio")
    assert r.status_code == 200
    assert f"/media/{PILOT_SLUG}/preview/preview.wav" in r.text
    tracks = sorted(p.stem for p in (world["audio"] / "tracks").glob("*.json"))
    for track in tracks:
        assert f"/media/{PILOT_SLUG}/track/{track}.wav" in r.text
    timing = load_json(world["audio"] / "tracks" / "intro.json")
    first = timing["lines"][0]
    page = text_of(r.text)
    assert first["line_id"] in page
    assert f"{first['quality']['peak']:.2f}" in page
    assert "PENDING REVIEW" in page
    assert r.text.index("track/intro.wav") < r.text.index("track/page-01.wav")
    assert r.text.index("track/page-01.wav") < r.text.index("track/outro.wav")


def test_approve_audio(pending):
    s, world = pending
    r = s.client.post(f"/books/{PILOT_SLUG}/audio/approve")
    assert r.status_code == 303
    status = load_json(world["audio"] / "status.json")
    assert status["status"] == "approved"
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}/audio").text)
    assert "APPROVED" in page and status["approved_at"] in page


def test_approve_refused_shows_the_reason(pending):
    s, world = pending
    path = world["script_path"]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    r = s.client.post(f"/books/{PILOT_SLUG}/audio/approve")
    assert r.status_code == 409
    assert "changed since the audio was synthesized" in r.text
    assert load_json(world["audio"] / "status.json")["status"] == "pending review"


def test_audio_page_without_audio(bare):
    r = bare.client.get(f"/books/{PILOT_SLUG}/audio")
    assert r.status_code == 200
    assert "<audio" not in r.text


# --- video (AC-6.1, AC-6.2) ---------------------------------------------------------------------


def test_video_page_plays_and_flags_out_of_date(approved):
    s, world = approved
    assert "<video" not in s.client.get(f"/books/{PILOT_SLUG}/video").text
    assert re_.main(cli_args(world, "--sfx-dir", str(s.paths.sfx_dir)), run=FakeRunner()) == 0
    r = s.client.get(f"/books/{PILOT_SLUG}/video")
    assert f'src="/media/{PILOT_SLUG}/video/{PILOT_SLUG}.mp4"' in r.text
    assert "controls" in r.text
    assert str(world["final"]) in r.text
    duration = load_json(world["video"] / f"{PILOT_SLUG}.json")["duration"]
    assert sa.format_duration(duration) in text_of(r.text)
    assert "OUT OF DATE" not in text_of(r.text)
    picture(world["assets"] / "illustrations" / "page_02.png", (9, 9, 9))
    assert "OUT OF DATE" in text_of(s.client.get(f"/books/{PILOT_SLUG}/video").text)


def test_format_duration():
    assert sa.format_duration(915.3) == "15:15"
    assert sa.format_duration(59.6) == "1:00"
    assert sa.format_duration(3725) == "1:02:05"


# --- media (FR-7) -------------------------------------------------------------------------------


def test_media_serves_whitelisted_files_only(approved):
    s, world = approved
    ok = [
        f"/media/{PILOT_SLUG}/illustration/page_01.png",
        f"/media/{PILOT_SLUG}/illustration/cover_front.png",
        f"/media/{PILOT_SLUG}/preview/preview.wav",
        f"/media/{PILOT_SLUG}/track/intro.wav",
    ]
    for url in ok:
        assert s.client.get(url).status_code == 200, url
    (world["audio"] / "secret.wav").write_bytes(b"x")
    (world["audio"] / "tracks" / "stray.wav").write_bytes(b"x")
    bad = [
        f"/media/{PILOT_SLUG}/illustration/book_data.json",
        f"/media/{PILOT_SLUG}/illustration/..%2F..%2Fbook_data.json",
        f"/media/{PILOT_SLUG}/preview/secret.wav",
        f"/media/{PILOT_SLUG}/track/stray.wav",
        f"/media/{PILOT_SLUG}/track/intro.json",
        f"/media/{PILOT_SLUG}/video/{PILOT_SLUG}.json",
        f"/media/{PILOT_SLUG}/video/{PILOT_SLUG}.mp4",  # not rendered yet
        f"/media/{PILOT_SLUG}/config/voices.json",
        "/media/no-such-book/illustration/page_01.png",
    ]
    for url in bad:
        assert s.client.get(url).status_code == 404, url


def test_media_nook_from_the_episode_folder(bare):
    nook = bare.paths.episodes_dir / PILOT_SLUG / "intro_nook.png"
    nook.parent.mkdir(parents=True)
    nook.write_bytes(png_bytes())
    assert bare.client.get(f"/media/{PILOT_SLUG}/illustration/intro_nook.png").status_code == 200
    assert bare.client.get(f"/media/{PILOT_SLUG}/illustration/outro_nook.png").status_code == 404


def test_media_range_request_is_206(approved):
    s, world = approved
    assert re_.main(cli_args(world, "--sfx-dir", str(s.paths.sfx_dir)), run=FakeRunner()) == 0
    url = f"/media/{PILOT_SLUG}/video/{PILOT_SLUG}.mp4"
    r = s.client.get(url, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-3/")
    assert r.content == world["final"].read_bytes()[:4]
    assert s.client.get(url).headers.get("accept-ranges") == "bytes"


# --- request safety (NFR Security) --------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Origin": "null"},
        {"Referer": "http://evil.example/page"},
        {"Referer": f"http://127.0.0.1:{PORT}.evil.example/"},
    ],
)
def test_cross_site_posts_are_refused(bare, headers):
    scripted(bare)
    r = bare.client.post(f"/books/{PILOT_SLUG}/script/reviewed", headers=headers)
    assert r.status_code == 403
    assert not sb.reviewed_ok(bare.paths, bare.book())


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": ORIGIN},
        {"Origin": f"http://localhost:{PORT}"},
        {"Referer": f"{ORIGIN}/books/{PILOT_SLUG}/script"},
    ],
)
def test_same_site_posts_are_allowed(bare, headers):
    scripted(bare)
    r = bare.client.post(f"/books/{PILOT_SLUG}/script/reviewed", headers=headers)
    assert r.status_code == 303
    assert sb.reviewed_ok(bare.paths, bare.book())


# --- start-up (AC-1.1) --------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_main_binds_loopback_only(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sa.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    port = free_port()
    assert sa.main(["--port", str(port)], paths=sb.StudioPaths(tmp_path)) == 0
    assert len(calls) == 1
    assert calls[0][1]["host"] == "127.0.0.1"
    assert calls[0][1]["port"] == port
    assert f"Studio running at http://127.0.0.1:{port}" in capsys.readouterr().out


def test_main_has_no_host_option(tmp_path):
    with pytest.raises(SystemExit):
        sa.main(["--host", "0.0.0.0"], paths=sb.StudioPaths(tmp_path))


def test_main_default_port(tmp_path, monkeypatch):
    assert sa.parse_args([]).port == 8765


def test_main_port_in_use(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sa.uvicorn, "run", lambda app, **kw: calls.append(kw))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert sa.main(["--port", str(port)], paths=sb.StudioPaths(tmp_path)) == 1
    assert calls == []
    assert f"error: port {port} is already in use" in capsys.readouterr().err


def test_module_entry_point_exists():
    import importlib

    module = importlib.import_module("video_pipeline.studio.__main__")
    assert module.main is sa.main


# --- review fixes (round 1) -----------------------------------------------------------------------


def test_upload_titled_new_is_refused_and_the_form_still_shows(bare):
    r = bare.client.post("/books", files=upload_parts(new_book("New")))
    assert r.status_code == 422
    assert "reserved" in r.text
    assert not (bare.paths.books_dir / "new").exists()
    assert 'name="book_data"' in bare.client.get("/books/new").text


def test_upload_with_a_page_named_like_a_nook_is_refused(bare):
    book = new_book("Nook Book")
    book["pages"][0]["illustration_file"] = "intro_nook.png"
    r = bare.client.post("/books", files=upload_parts(book, extra={"intro_nook.png": png_bytes()}))
    assert r.status_code == 422
    assert "reserved" in r.text
    assert not (bare.paths.books_dir / "nook-book").exists()


def test_oversize_request_is_refused_before_parsing(bare, monkeypatch):
    monkeypatch.setattr(sb, "validate_upload", lambda *a: pytest.fail("the form was parsed"))
    limit = (sb.MAX_UPLOAD_FILES + 1) * sb.MAX_UPLOAD_BYTES
    r = bare.client.post(
        "/books",
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=b",
            "content-length": str(limit + 1),
        },
    )
    assert r.status_code == 413
    assert "too large" in r.text


def test_oversize_book_data_upload_says_so(bare):
    parts = upload_parts(new_book())
    parts[0] = (
        "book_data",
        ("book_data.json", b" " * (sb.MAX_UPLOAD_BYTES + 1), "application/json"),
    )
    r = bare.client.post("/books", files=parts)
    assert r.status_code == 422
    assert "book data is larger than 25 MB" in r.text
    assert "not valid JSON" not in r.text


@pytest.mark.parametrize("host", ["attacker.example:8765", "attacker.example", "127.0.0.2:8765"])
def test_unknown_host_header_is_refused(bare, host):
    assert bare.client.get("/", headers={"host": host}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
def test_loopback_host_headers_are_allowed(bare, host):
    assert bare.client.get("/", headers={"host": host}).status_code == 200


def test_replace_upload_refused_while_that_book_runs(tmp_path):
    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    s = Studio(tmp_path, Commands("import time; time.sleep(30)"))
    book = new_book()
    slug = slugify(book["metadata"]["title"])
    assert s.client.post("/books", files=upload_parts(book)).status_code == 303
    try:
        assert s.client.post(f"/books/{slug}/run/script").status_code == 303
        book["metadata"]["author"] = "Someone Else"
        r = s.client.post("/books", data={"replace": "1"}, files=upload_parts(book))
        assert r.status_code == 409
        assert "running" in r.text
        assert load_json(s.paths.books_dir / slug / "book_data.json")["metadata"]["author"] != (
            "Someone Else"
        )
    finally:
        s.runner.stop()


def test_stop_does_not_freeze_other_requests(tmp_path):
    import threading

    shutil.copy(MINI_BOOK, tmp_path / "book_data.json")
    shutil.copytree(sv.CONFIG_DIR, tmp_path / "config")
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ignoring', flush=True)\n"
        "time.sleep(60)\n"
    )
    s = Studio(tmp_path, Commands(code))
    s.runner.stop_grace = 3
    # One shared event loop (as under uvicorn), unlike a TestClient used without `with`.
    with TestClient(s.app, base_url=ORIGIN, follow_redirects=False) as client:
        assert client.post(f"/books/{PILOT_SLUG}/run/script").status_code == 303
        job_id = s.runner.current()["id"]
        deadline = time.monotonic() + 10
        while "ignoring" not in s.runner.log(job_id)[0]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        stopper = threading.Thread(target=lambda: client.post("/jobs/stop"))
        stopper.start()
        time.sleep(0.3)  # the stop is now waiting out its grace period
        started = time.monotonic()
        r = client.get("/api/jobs/current")
        took = time.monotonic() - started
        stopper.join(10)
    assert r.status_code == 200
    assert took < 1.0, f"GET /api/jobs/current took {took:.2f}s while a stop was in progress"


def test_video_page_names_a_missing_picture_not_the_audio(approved):
    s, world = approved
    assert re_.main(cli_args(world), run=FakeRunner()) == 0
    (world["assets"] / "illustrations" / "page_02.png").unlink()
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}/video").text)
    assert "page_02.png" in page
    assert "no longer approved" not in page
