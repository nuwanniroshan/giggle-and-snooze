"""Phase 3 of spec 004: the web app (pages, stage controls, media, start-up).

Every world lives under tmp_path (the studio root): `book_data.json` is a copy of the mini book
(the pilot), added books go to `books/<slug>/`. Stage commands are replaced by tiny `python -c`
programs run by a real JobRunner; FFmpeg and the voice model never run."""

import io
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from starlette.testclient import TestClient

from tests.helpers import MINI_BOOK, add_book_from_draft, load_json, make_root
from tests.render_helpers import FakeRunner, cli_args, make_world, picture
from tests.voice_helpers import make_script, write_script
from video_pipeline import render_episode as re_
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import slugify
from video_pipeline.library import BookFolder
from video_pipeline.studio import app as sa
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj

PILOT_SLUG = slugify(load_json(MINI_BOOK)["metadata"]["title"])


def pilot_folder(root) -> BookFolder:
    """The pilot is an ordinary book now: books/<slug>/ under the studio root (spec 005)."""
    return BookFolder(PILOT_SLUG, Path(root))


def pilot_path(root) -> Path:
    return pilot_folder(root).book_json


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

    def __call__(self, stage, book, paths, force=False, page=None, cover=None):
        if cover is not None:
            self.calls.append((stage, book.slug, f"cover:{cover}"))  # spec 019
        elif page is not None:
            self.calls.append((stage, book.slug, page))
        else:
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
    make_root(tmp_path)
    s = Studio(tmp_path)
    yield s
    if s.runner.current():
        s.runner.stop()


def with_world(tmp_path, monkeypatch, approve=True, commands=None):
    world = make_world(tmp_path, monkeypatch, MINI_BOOK, approve=approve)
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
    write_script(make_script(pilot_path(s.paths.root)), BookFolder(PILOT_SLUG, s.paths.root))


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- home (AC-1.2, AC-1.3) ----------------------------------------------------------------------


def test_home_lists_books_with_states_and_next_action(bare):
    r = bare.client.get("/")
    assert r.status_code == 200
    page = text_of(r.text)
    assert "The Lantern Bug Parade" in page
    assert "NOT STARTED" in page
    assert "Paint pictures" in page  # spec 016: a book without pictures is painted first
    assert f'href="/books/{PILOT_SLUG}"' in r.text


def test_home_shows_an_added_book_and_escapes_its_title(bare):
    add_book_from_draft(bare.paths.root, "The <b>Bold</b> Kite")
    r = bare.client.get("/")
    assert "The &lt;b&gt;Bold&lt;/b&gt; Kite" in r.text
    assert "<b>Bold</b>" not in r.text


def test_home_states_for_an_approved_book(approved):
    s, _world = approved
    page = text_of(s.client.get("/").text)
    assert "DONE" in page and "APPROVED" in page
    assert "Render video" in page


def test_pages_use_only_local_assets_and_the_bloom_tokens(bare):
    html = bare.client.get("/").text
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    head = (STATIC / "studio.css").read_text(encoding="utf-8").lstrip().splitlines()[:12]
    assert head[0].startswith("/* Hallmark")  # the design stamp comes first
    assert any(
        line.startswith("/* Episode studio (spec 014)") for line in head
    )  # every colour lives in tokens.css
    for name in ("studio.css", "add_book.css", "animation.css", "base.css"):
        css = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(", css), name
        assert "transition: all" not in css
        assert not re.search(r"font-family:(?!\s*var\()", css), name
    tokens = (STATIC / "tokens.css").read_text(encoding="utf-8")
    assert "--color-ink" in tokens and "--font-display" in tokens
    assert "https://" not in tokens
    assert bare.client.get("/static/tokens.css").status_code == 200
    assert bare.client.get("/static/studio.css").status_code == 200
    assert bare.client.get("/static/studio.js").status_code == 200


def test_unknown_book_is_404(bare):
    assert bare.client.get("/books/no-such-book").status_code == 404
    assert bare.client.get("/books/no-such-book/script").status_code == 404


# --- uploads (AC-2.1-2.5) -----------------------------------------------------------------------


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
    make_root(tmp_path)
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
    make_root(tmp_path)
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
    picture(world["folder"].illustrations / "page_02.png", (9, 9, 9))
    assert "OUT OF DATE" in text_of(s.client.get(f"/books/{PILOT_SLUG}/video").text)


def test_video_page_shows_the_grade_and_goes_stale_when_it_changes(approved):  # spec 013 AC-4.5
    s, world = approved
    cfg_path = world["config"] / "render.json"
    cfg = load_json(cfg_path)
    cfg["grade"] = {"enabled": True}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    loudnorm = json.dumps(
        {
            "input_i": "-19",
            "input_tp": "-3",
            "input_lra": "9",
            "input_thresh": "-29",
            "target_offset": "0",
        }
    )

    def measure(cmd, what):
        return (
            loudnorm
            if what == "measure loudness"
            else "Summary:\n I: -16.1 LUFS\n LRA: 5.0 LU\n Peak: -1.9 dBFS\n"
        )

    args = cli_args(world, "--sfx-dir", str(s.paths.sfx_dir))
    assert re_.main(args, run=FakeRunner(), measure=measure) == 0
    text = text_of(s.client.get(f"/books/{PILOT_SLUG}/video").text)
    assert "OUT OF DATE" not in text
    assert "contrast : 1.05" in text and "loudness_lufs : -16" in text
    assert "-16.1 LUFS" in text and "true peak -1.9 dBFS" in text
    cfg["grade"] = {"enabled": True, "contrast": 1.2}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
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
    nook = BookFolder(PILOT_SLUG, bare.paths.root).nooks / "intro_nook.png"
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


@pytest.mark.parametrize("host", ["attacker.example:8765", "attacker.example", "127.0.0.2:8765"])
def test_unknown_host_header_is_refused(bare, host):
    assert bare.client.get("/", headers={"host": host}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
def test_loopback_host_headers_are_allowed(bare, host):
    assert bare.client.get("/", headers={"host": host}).status_code == 200


def test_stop_does_not_freeze_other_requests(tmp_path):
    import threading

    make_root(tmp_path)
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
    (world["folder"].illustrations / "page_02.png").unlink()
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}/video").text)
    assert "page_02.png" in page
    assert "no longer approved" not in page


# --- review fixes (round 2) -----------------------------------------------------------------------


def test_main_refuses_a_second_studio_on_the_same_project(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sa.uvicorn, "run", lambda app, **kw: calls.append(kw))
    paths = sb.StudioPaths(tmp_path)
    holder = sj.JobRunner(paths.studio_dir, cwd=tmp_path)
    try:
        assert sa.main(["--port", str(free_port())], paths=paths) == 1
    finally:
        holder.close()
    assert calls == []
    assert "error: another studio is already running on this project" in capsys.readouterr().err


def test_main_releases_the_studio_lock_when_it_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(sa.uvicorn, "run", lambda app, **kw: None)
    paths = sb.StudioPaths(tmp_path)
    assert sa.main(["--port", str(free_port())], paths=paths) == 0
    sj.JobRunner(paths.studio_dir, cwd=tmp_path).close()  # free again


def test_app_shutdown_releases_the_studio_lock(bare):
    with TestClient(bare.app, base_url=ORIGIN), pytest.raises(sj.StudioLocked):
        sj.JobRunner(bare.paths.studio_dir, cwd=bare.paths.root)
    sj.JobRunner(bare.paths.studio_dir, cwd=bare.paths.root).close()


def _slow(events: list, name: str, real, seconds: float = 0.5, started=None):
    def wrapper(*args, **kwargs):
        events.append(f"{name}-begin")
        if started is not None:
            started.set()
        time.sleep(seconds)
        try:
            return real(*args, **kwargs)
        finally:
            events.append(f"{name}-end")

    return wrapper


def _concurrently(client, first: tuple, second: tuple, started) -> tuple:
    """POST `first` in a thread, wait until its slow part has begun, then POST `second`."""
    import threading

    results = {}
    thread = threading.Thread(
        target=lambda: results.update(first=client.post(*first[:1], **first[1]))
    )
    thread.start()
    assert started.wait(5)
    second_response = client.post(*second[:1], **second[1])
    thread.join(10)
    return results["first"], second_response


def test_a_stage_does_not_start_while_a_script_save_is_writing(bare, monkeypatch):
    import threading

    scripted(bare)
    line_id, line = sb.script_lines(sb.load_script(bare.paths, bare.book()))[0]
    events, started = [], threading.Event()
    monkeypatch.setattr(
        sb, "save_line_edits", _slow(events, "save", sb.save_line_edits, started=started)
    )
    real_start = bare.runner.start
    monkeypatch.setattr(
        bare.runner, "start", lambda *a, **k: (events.append("start"), real_start(*a, **k))[1]
    )
    with TestClient(bare.app, base_url=ORIGIN, follow_redirects=False) as client:
        saved, ran = _concurrently(
            client,
            (f"/books/{PILOT_SLUG}/script", {"data": {line_id: line["text"] + " Hello!"}}),
            (f"/books/{PILOT_SLUG}/run/script", {"data": {"confirm": "1"}}),
            started,
        )
    assert saved.status_code == 303 and ran.status_code == 303
    assert events == ["save-begin", "save-end", "start"]


def test_a_script_save_waits_for_a_starting_stage_and_is_refused(tmp_path, monkeypatch):
    import threading

    make_root(tmp_path)
    s = Studio(tmp_path, Commands("import time; time.sleep(30)"))
    scripted(s)
    line_id, line = sb.script_lines(sb.load_script(s.paths, s.book()))[0]
    before = sb.script_path(s.paths, s.book()).read_bytes()
    events, started = [], threading.Event()
    monkeypatch.setattr(s.runner, "start", _slow(events, "start", s.runner.start, started=started))
    with TestClient(s.app, base_url=ORIGIN, follow_redirects=False) as client:
        ran, saved = _concurrently(
            client,
            (f"/books/{PILOT_SLUG}/run/script", {"data": {"confirm": "1"}}),
            (f"/books/{PILOT_SLUG}/script", {"data": {line_id: line["text"] + " Hello!"}}),
            started,
        )
        reviewed = client.post(f"/books/{PILOT_SLUG}/script/reviewed")
    assert ran.status_code == 303
    assert saved.status_code == 409 and "running for this book" in saved.text
    assert reviewed.status_code == 409
    assert sb.script_path(s.paths, s.book()).read_bytes() == before


def test_mark_reviewed_waits_for_a_starting_stage_and_is_refused(tmp_path, monkeypatch):
    import threading

    make_root(tmp_path)
    s = Studio(tmp_path, Commands("import time; time.sleep(30)"))
    scripted(s)
    events, started = [], threading.Event()
    monkeypatch.setattr(s.runner, "start", _slow(events, "start", s.runner.start, started=started))
    with TestClient(s.app, base_url=ORIGIN, follow_redirects=False) as client:
        ran, reviewed = _concurrently(
            client,
            (f"/books/{PILOT_SLUG}/run/script", {"data": {"confirm": "1"}}),
            (f"/books/{PILOT_SLUG}/script/reviewed", {}),
            started,
        )
    assert ran.status_code == 303
    assert reviewed.status_code == 409
    assert not sb.reviewed_ok(s.paths, s.book())


def test_approve_waits_for_a_starting_voices_stage_and_is_refused(tmp_path, monkeypatch):
    import threading

    s, world = with_world(
        tmp_path, monkeypatch, approve=False, commands=Commands("import time; time.sleep(30)")
    )
    events, started = [], threading.Event()
    monkeypatch.setattr(s.runner, "start", _slow(events, "start", s.runner.start, started=started))
    with TestClient(s.app, base_url=ORIGIN, follow_redirects=False) as client:
        ran, approved = _concurrently(
            client,
            (f"/books/{PILOT_SLUG}/run/voices", {}),
            (f"/books/{PILOT_SLUG}/audio/approve", {}),
            started,
        )
    assert ran.status_code == 303, ran.text
    assert approved.status_code == 409 and "still running" in approved.text
    assert load_json(world["audio"] / "status.json")["status"] == "pending review"


def test_add_book_page_has_no_file_field(bare):  # AC-4.1
    html = bare.client.get("/books/new").text
    assert 'type="file"' not in html and "book_data" not in html
    assert 'action="/ideas"' in html and 'action="/ideas/improve"' in html


def test_posting_a_book_file_is_refused_and_saves_nothing(bare):  # AC-4.2
    book = load_json(MINI_BOOK)
    r = bare.client.post("/books", files=upload_parts(book))
    assert r.status_code == 410
    assert "no longer used" in r.text
    assert sorted(p.name for p in bare.paths.books_dir.iterdir()) == [PILOT_SLUG]


# --- animation view (spec 009 Phase 5: AC-5.1 – AC-5.5) ---------------------------------------


def _animated(tmp_path, commands=None):
    from tests import animation_helpers as ah

    folder = make_root(tmp_path)
    for n in (1, 2):
        ah.write_picture(folder, n)
    effect = {"type": "fireflies", "region": {"box": [0, 0, 32, 24]}, "intensity": 0.4}
    two = [ah.layer(), ah.layer("lamp", box=[2, 30, 12, 44], z=2, pivot=[7, 44])]
    ah.write_plan(folder, 1, layers=two, effects=[effect])
    return Studio(tmp_path, commands), folder, ah


@pytest.fixture
def animated(tmp_path):
    s, folder, ah = _animated(tmp_path)
    yield s, folder, ah
    if s.runner.current():
        s.runner.stop()


def test_book_page_links_to_animation_with_summary(animated):  # AC-5.1
    s, _folder, _ah = animated
    html = s.client.get(f"/books/{PILOT_SLUG}").text
    assert f'href="/books/{PILOT_SLUG}/animation"' in html
    assert "1 of 8 plans, 0 cut" in text_of(html)


def test_animation_view_lists_page_states(animated):  # AC-5.1
    s, folder, ah = animated
    from video_pipeline.motion_plan import plan_path

    plan_path(folder, 3).parent.mkdir(parents=True, exist_ok=True)
    plan_path(folder, 3).write_text("[]", encoding="utf-8")
    r = s.client.get(f"/books/{PILOT_SLUG}/animation")
    assert r.status_code == 200
    text = text_of(r.text)
    assert "ok" in text and "none" in text and "invalid" in text
    assert "needs cut" in text
    assert f'href="/books/{PILOT_SLUG}/animation/1"' in r.text


def test_animation_page_shows_the_plan(animated):  # AC-5.2
    s, _folder, _ah = animated
    r = s.client.get(f"/books/{PILOT_SLUG}/animation/1")
    assert r.status_code == 200
    html = r.text
    text = text_of(html)
    for word in ("square", "lamp", "the square", "sway", "6.0", "0.25", "fireflies", "0.4"):
        assert word in text, word
    assert "[28, 28]" in text and "[7, 44]" in text  # pivots
    assert f'src="/media/{PILOT_SLUG}/illustration/page_01.png"' in html
    svg = re.search(r"<svg[^>]*viewBox=\"0 0 64 48\".*?</svg>", html, re.S)
    assert svg is not None
    assert len(re.findall(r"<rect\b[^>]*class=\"layer", svg.group(0))) == 2
    assert len(re.findall(r"<circle\b", svg.group(0))) == 4  # 2 include + 2 exclude points
    assert "needs cut" in text


def test_animation_page_after_cut_shows_masks_and_background(animated):  # AC-5.3
    s, folder, ah = animated
    ah.cut(folder, pages=[1])
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/1").text
    assert f'src="/media/{PILOT_SLUG}/mask/page_01.png"' in html
    assert f'src="/media/{PILOT_SLUG}/layer/page_01_background.png"' in html
    assert re.search(r"\bcut\b", text_of(html)) and "needs cut" not in text_of(html)
    r = s.client.get(f"/media/{PILOT_SLUG}/mask/page_01.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    for name in ("page_01_background.png", "page_01_square.png", "page_01_lamp.png"):
        assert s.client.get(f"/media/{PILOT_SLUG}/layer/{name}").status_code == 200, name
    ah.write_plan(folder, 1, layers=[ah.layer(box=[10, 10, 30, 30])])  # a box edit
    assert "needs cut" in text_of(s.client.get(f"/books/{PILOT_SLUG}/animation/1").text)


def test_animation_page_reads_the_plan_from_disk(animated):  # AC-5.5
    s, folder, ah = animated
    assert "lamp" in s.client.get(f"/books/{PILOT_SLUG}/animation/1").text
    ah.write_plan(folder, 1, layers=[ah.layer("chimney")])
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/1").text
    assert "chimney" in html and "lamp" not in html


def test_animation_page_without_plan_and_unknown_page(animated):
    s, _folder, _ah = animated
    r = s.client.get(f"/books/{PILOT_SLUG}/animation/2")
    assert r.status_code == 200 and "no plan" in text_of(r.text).lower()
    assert s.client.get(f"/books/{PILOT_SLUG}/animation/99").status_code == 404
    assert s.client.get("/books/nope/animation").status_code == 404


def test_animation_page_shows_invalid_detail(animated):
    s, folder, _ah = animated
    from video_pipeline.motion_plan import plan_path

    plan_path(folder, 2).write_text('{"page_number": 2}', encoding="utf-8")
    text = text_of(s.client.get(f"/books/{PILOT_SLUG}/animation/2").text)
    assert "invalid" in text and "picture" in text


def test_layer_and_mask_media_are_whitelisted(animated):
    s, folder, ah = animated
    from video_pipeline.cut_layers import layers_dir

    assert s.client.get(f"/media/{PILOT_SLUG}/mask/page_01.png").status_code == 404  # no cut yet
    ah.cut(folder, pages=[1])
    (layers_dir(folder, 1) / "stray.png").write_bytes(png_bytes())
    bad = [
        f"/media/{PILOT_SLUG}/layer/page_01_stray.png",
        f"/media/{PILOT_SLUG}/layer/page_01_layers.json",
        f"/media/{PILOT_SLUG}/layer/../../etc",
        f"/media/{PILOT_SLUG}/layer/page_01_..%2F..%2Fbook.json",
        f"/media/{PILOT_SLUG}/layer/page_02_background.png",
        f"/media/{PILOT_SLUG}/layer/background.png",
        f"/media/{PILOT_SLUG}/mask/page_02.png",
        f"/media/{PILOT_SLUG}/mask/../page_01.png",
    ]
    for url in bad:
        assert s.client.get(url).status_code == 404, url


def test_run_plan_and_cut_start_jobs(animated):  # AC-5.4
    s, _folder, _ah = animated
    for stage in ("plan", "cut"):
        r = s.client.post(f"/books/{PILOT_SLUG}/run/{stage}")
        assert r.status_code == 303
        assert r.headers["location"] == f"/books/{PILOT_SLUG}/animation"
        s.wait()
    assert [c[:2] for c in s.commands.calls] == [("plan", PILOT_SLUG), ("cut", PILOT_SLUG)]
    html = s.client.get(f"/books/{PILOT_SLUG}/animation").text
    assert "layer cutting" in text_of(html).lower() and "finished" in html


def test_run_plan_and_cut_refused_when_busy(tmp_path):  # AC-5.4
    s, _folder, _ah = _animated(tmp_path, Commands("import time; time.sleep(30)"))
    try:
        assert s.client.post(f"/books/{PILOT_SLUG}/run/plan").status_code == 303
        for stage in ("plan", "cut", "script"):
            r = s.client.post(f"/books/{PILOT_SLUG}/run/{stage}")
            assert r.status_code == 409, stage
            assert "motion planning" in r.text
        html = s.client.get(f"/books/{PILOT_SLUG}/animation").text
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Plan pictures", html)
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Cut layers", html)
    finally:
        s.runner.stop()


def test_animation_runs_are_refused_cross_site(animated):
    s, _folder, _ah = animated
    r = s.client.post(f"/books/{PILOT_SLUG}/run/cut", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert s.commands.calls == []


def test_book_page_survives_missing_animation_config(animated):
    s, _folder, _ah = animated
    (s.paths.config_dir / "animation.json").unlink()
    assert s.client.get(f"/books/{PILOT_SLUG}").status_code == 200
    assert s.client.get(f"/books/{PILOT_SLUG}/animation").status_code == 200
    assert s.client.get(f"/books/{PILOT_SLUG}/animation/1").status_code == 200


def test_book_page_lists_pages_whose_plans_need_attention(animated):  # AC-5.1
    s, folder, ah = animated
    from video_pipeline.motion_plan import plan_path

    ah.write_plan(folder, 2)
    ah.write_picture(folder, 2, colour=(0, 200, 0))  # page 2 repainted → stale
    plan_path(folder, 3).write_text('{"page_number": 3}', encoding="utf-8")  # invalid
    text = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert "3 of 8 plans, 0 cut" in text
    assert "Page 2 stale" in text and "page_02.png changed since the plan was written" in text
    assert "Page 3 invalid" in text and "picture" in text
    assert "Page 1 " not in text  # ok pages are not listed one by one
    assert "No plan: pages 4, 5, 6, 7, 8" in text


def test_book_page_names_an_unreadable_animation_config_once(animated):
    s, _folder, _ah = animated
    (s.paths.config_dir / "animation.json").unlink()
    text = text_of(s.client.get(f"/books/{PILOT_SLUG}").text)
    assert text.count("Plans cannot be checked") == 1 and "animation.json" in text
    assert "unavailable" not in text  # one line for the config, not one per page


# --- page preview (spec 010 Phase 5: FR-16, AC-4.3, AC-6.2) -----------------------------------


def _voiced(tmp_path, monkeypatch, commands=None):
    """A book with voice tracks (audio not approved) and page 4 planned but not cut."""
    from tests import animation_helpers as ah

    s, world = with_world(tmp_path, monkeypatch, approve=False, commands=commands)
    ah.write_plan(world["folder"], 4, layers=[ah.layer()])
    return s, world, ah


@pytest.fixture
def voiced(tmp_path, monkeypatch):
    s, world, ah = _voiced(tmp_path, monkeypatch)
    yield s, world, ah
    if s.runner.current():
        s.runner.stop()


def test_preview_starts_a_job_for_the_page(voiced):  # AC-6.2
    s, _world, _ah = voiced
    r = s.client.post(f"/books/{PILOT_SLUG}/animation/4/preview")
    assert r.status_code == 303
    assert r.headers["location"] == f"/books/{PILOT_SLUG}/animation/4"
    s.wait()
    assert s.commands.calls == [("preview", PILOT_SLUG, 4)]
    job = s.runner.latest(PILOT_SLUG, "preview", page=4)
    assert job["state"] == "done" and job["page"] == 4
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/4").text
    assert "page preview" in text_of(html).lower() and "finished" in html
    assert "finished" not in s.client.get(f"/books/{PILOT_SLUG}/animation/5").text


def test_preview_command_names_the_page(voiced):  # FR-16
    s, _world, _ah = voiced
    cmd = sj.stage_command("preview", s.book(), s.paths, page=4)
    assert cmd[-2:] == ["--preview-page", "4"]


def test_preview_refused_when_busy(tmp_path, monkeypatch):  # AC-6.2
    s, _world, _ah = _voiced(tmp_path, monkeypatch, Commands("import time; time.sleep(30)"))
    try:
        assert s.client.post(f"/books/{PILOT_SLUG}/animation/4/preview").status_code == 303
        r = s.client.post(f"/books/{PILOT_SLUG}/animation/5/preview")
        assert r.status_code == 409 and "page preview" in r.text
        html = s.client.get(f"/books/{PILOT_SLUG}/animation/5").text
        assert re.search(r"<button[^>]*disabled[^>]*>\s*Preview", html)
    finally:
        s.runner.stop()
    assert len(s.commands.calls) == 1


def test_preview_refused_cross_site(voiced):
    s, _world, _ah = voiced
    r = s.client.post(
        f"/books/{PILOT_SLUG}/animation/4/preview", headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert s.commands.calls == []


def test_preview_needs_the_page_track_not_approval(voiced):  # AC-6.3
    s, world, _ah = voiced
    (world["audio"] / "tracks" / "page-04.wav").unlink()
    r = s.client.post(f"/books/{PILOT_SLUG}/animation/4/preview")
    assert r.status_code == 409 and "voice track" in r.text
    assert f"python -m video_pipeline.synthesize_voices --book {PILOT_SLUG} --reviewed" in r.text
    assert s.commands.calls == []
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/4").text
    assert re.search(r"<button[^>]*disabled[^>]*>\s*Preview", html)
    assert s.client.post(f"/books/{PILOT_SLUG}/animation/99/preview").status_code == 404


def test_animation_view_says_animated_or_still(voiced):  # AC-4.3
    s, world, ah = voiced
    ah.write_picture(world["folder"], 4, colour=(0, 200, 0))  # repainted → stale plan
    text = text_of(s.client.get(f"/books/{PILOT_SLUG}/animation").text)
    assert "still: stale plan" in text
    assert "still: no plan" in text
    page = text_of(s.client.get(f"/books/{PILOT_SLUG}/animation/4").text)
    assert "still: stale plan" in page


def test_animation_view_shows_an_animated_page(voiced):  # AC-4.3
    s, world, ah = voiced
    ah.cut(world["folder"], pages=[4])
    html = s.client.get(f"/books/{PILOT_SLUG}/animation").text
    row = re.search(r"Page 4</a>.*?</li>", html, re.S).group(0)
    assert "animated" in text_of(row) and "still" not in text_of(row)


def test_page_view_plays_an_existing_preview(voiced):  # AC-6.2
    s, world, _ah = voiced
    url = f"/media/{PILOT_SLUG}/preview_video/page_04.mp4"
    assert "<video" not in s.client.get(f"/books/{PILOT_SLUG}/animation/4").text
    assert s.client.get(url).status_code == 404
    args = cli_args(world, "--preview-page", "4")
    assert re_.main(args, run=FakeRunner()) == 0
    html = s.client.get(f"/books/{PILOT_SLUG}/animation/4").text
    assert re.search(r"<video[^>]*controls", html)
    assert f'src="{url}' in html
    r = s.client.get(url, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.content == (world["video"] / "preview" / "page_04.mp4").read_bytes()[:4]
    assert "<video" not in s.client.get(f"/books/{PILOT_SLUG}/animation/5").text


def test_preview_media_is_whitelisted(voiced):
    s, world, _ah = voiced
    assert re_.main(cli_args(world, "--preview-page", "4"), run=FakeRunner()) == 0
    preview = world["video"] / "preview"
    (preview / "stray.mp4").write_bytes(b"x")
    (preview / "page_04.json").write_bytes(b"x")
    bad = [
        f"/media/{PILOT_SLUG}/preview_video/stray.mp4",
        f"/media/{PILOT_SLUG}/preview_video/page_4.mp4",
        f"/media/{PILOT_SLUG}/preview_video/page_04.json",
        f"/media/{PILOT_SLUG}/preview_video/page_05.mp4",
        f"/media/{PILOT_SLUG}/preview_video/..%2Fsections%2Fpage-04.mp4",
        f"/media/{PILOT_SLUG}/preview_video/../sections/page-04.mp4",
        f"/media/{PILOT_SLUG}/preview/page_04.mp4",
    ]
    for url in bad:
        assert s.client.get(url).status_code == 404, url


# --- spec 014: thumbnails, covers and the book sidebar -----------------------------------------


def big_cover(s) -> Path:
    path = pilot_folder(s.paths.root).illustrations / "cover_front.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 900), (200, 120, 40)).save(path)
    return path


def test_thumb_serves_a_small_jpeg_of_a_listed_picture(bare):
    big_cover(bare)
    r = bare.client.get(f"/media/{PILOT_SLUG}/thumb/cover_front.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.width <= 640 and im.format == "JPEG"


def test_thumb_refuses_names_the_book_does_not_list(bare):
    for name in ("not_listed.png", "book_data.json", "..%2F..%2Fbook_data.json"):
        assert bare.client.get(f"/media/{PILOT_SLUG}/thumb/{name}").status_code == 404, name


def test_thumb_follows_a_changed_picture(bare):
    path = big_cover(bare)
    first = bare.client.get(f"/media/{PILOT_SLUG}/thumb/cover_front.png").content
    Image.new("RGB", (1200, 900), (10, 40, 200)).save(path)
    os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert bare.client.get(f"/media/{PILOT_SLUG}/thumb/cover_front.png").content != first


def test_home_shows_each_cover_as_a_thumb(bare):
    big_cover(bare)
    assert f"/media/{PILOT_SLUG}/thumb/cover_front.png" in bare.client.get("/").text


def test_book_screens_share_the_sidebar_with_the_current_place_marked(approved):
    s, _world = approved
    pages = {
        f"/books/{PILOT_SLUG}": f"/books/{PILOT_SLUG}",
        f"/books/{PILOT_SLUG}/script": f"/books/{PILOT_SLUG}/script",
        f"/books/{PILOT_SLUG}/audio": f"/books/{PILOT_SLUG}/audio",
        f"/books/{PILOT_SLUG}/video": f"/books/{PILOT_SLUG}/video",
    }
    for url, here in pages.items():
        html = s.client.get(url).text
        assert 'class="book-side"' in html, url
        assert re.search(rf'href="{re.escape(here)}"[^>]*aria-current="page"', html), url


# --- spec 017: feelings in the script view (AC-4.1, AC-4.2) ----------------------------------


def _selected(html: str, line_id: str) -> str | None:
    """The selected option of the feeling select for `line_id`, or None without a select."""
    m = re.search(rf'<select name="emotion:{line_id}"[^>]*>(.*?)</select>', html, re.S)
    if m is None:
        return None
    picked = re.search(r'<option value="([^"]+)" selected', m.group(1))
    return picked.group(1) if picked else None


def test_script_view_shows_a_feeling_per_spoken_line(bare):  # AC-4.1
    scripted(bare)
    script = sb.load_script(bare.paths, bare.book())
    html = bare.client.get(f"/books/{PILOT_SLUG}/script").text
    for line_id, line in sb.script_lines(script):
        if line.get("role") == "narration":
            assert _selected(html, line_id) is None
        else:
            assert _selected(html, line_id) == (line.get("emotion") or "neutral")
    assert _selected(html, "intro-01") == "neutral"  # missing means neutral
    for name in ("excited", "sleepy", "whisper", "surprised"):
        assert f'<option value="{name}"' in html


def test_saving_a_feeling_writes_it_and_clears_review(bare):  # AC-4.2
    scripted(bare)
    sb.mark_reviewed(bare.paths, bare.book())
    r = bare.client.post(f"/books/{PILOT_SLUG}/script", data={"emotion:intro-01": "excited"})
    assert r.status_code == 303
    saved = dict(sb.script_lines(sb.load_script(bare.paths, bare.book())))
    assert saved["intro-01"]["emotion"] == "excited"
    assert not sb.reviewed_ok(bare.paths, bare.book())
    html = bare.client.get(f"/books/{PILOT_SLUG}/script").text
    assert _selected(html, "intro-01") == "excited"


def test_choosing_neutral_removes_the_feeling(bare):  # AC-4.2
    scripted(bare)
    script = sb.load_script(bare.paths, bare.book())
    line_id = next(i for i, line in sb.script_lines(script) if line.get("emotion") == "happy")
    r = bare.client.post(f"/books/{PILOT_SLUG}/script", data={f"emotion:{line_id}": "neutral"})
    assert r.status_code == 303
    saved = dict(sb.script_lines(sb.load_script(bare.paths, bare.book())))
    assert "emotion" not in saved[line_id]


def test_unchanged_feelings_keep_the_review_mark(bare):
    scripted(bare)
    sb.mark_reviewed(bare.paths, bare.book())
    script = sb.load_script(bare.paths, bare.book())
    data = {
        f"emotion:{i}": line.get("emotion") or "neutral"
        for i, line in sb.script_lines(script)
        if line.get("role") != "narration"
    }
    assert bare.client.post(f"/books/{PILOT_SLUG}/script", data=data).status_code == 303
    assert sb.reviewed_ok(bare.paths, bare.book())


def test_unknown_feeling_is_refused_with_the_message(bare):  # AC-4.2
    scripted(bare)
    path = sb.script_path(bare.paths, bare.book())
    before = path.read_bytes()
    r = bare.client.post(f"/books/{PILOT_SLUG}/script", data={"emotion:outro-02": "grumpy"})
    assert r.status_code == 422
    assert path.read_bytes() == before
    assert "outro line 2" in r.text and "grumpy" in r.text and "sleepy" in r.text
    assert "intro-01" not in re.findall(r'id="problems-([a-z0-9-]+)"', r.text)
    assert "outro-02" in re.findall(r'id="problems-([a-z0-9-]+)"', r.text)
    assert _selected(r.text, "outro-02") == "grumpy"  # the select keeps what was picked


def test_feeling_for_an_unknown_line_is_refused(bare):
    scripted(bare)
    r = bare.client.post(f"/books/{PILOT_SLUG}/script", data={"emotion:intro-99": "happy"})
    assert r.status_code == 422
    assert "unknown line id: intro-99" in r.text


# --- character sheet (spec 018: AC-3.1) -------------------------------------------------------


def test_book_page_lists_the_character_sheet(bare):  # AC-3.1
    from tests.test_story_tools import OWL, TEAPOT

    slug = add_book_from_draft(bare.paths.root, "The Moon Kite Race")
    html = bare.client.get(f"/books/{slug}").text
    page = text_of(html)
    assert 'id="characters"' in html
    for entry in (TEAPOT, OWL):
        assert entry["name"] in page and entry["look"] in page
    assert f"books/{slug}/book.json" in page and "not repainted automatically" in page


def test_book_page_without_a_sheet_has_no_characters_panel(bare):  # AC-2.3
    html = bare.client.get(f"/books/{PILOT_SLUG}").text
    assert 'id="characters"' not in html


# --- spec 022: video and print covers (AC-5.1, AC-5.2) ------------------------------------------


def make_titled(s: Studio) -> None:
    path = pilot_path(s.paths.root)
    data = load_json(path)
    data["metadata"]["cover_style"] = "titled"
    path.write_text(json.dumps(data), encoding="utf-8")


def test_repaint_print_cover_runs_cover_print_job(bare):  # AC-5.2
    make_titled(bare)
    r = bare.client.post(
        f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "print", "confirm": "1"}
    )
    assert r.status_code == 303
    bare.wait()
    assert bare.commands.calls == [("repaint", PILOT_SLUG, "cover:print")]


def test_print_side_refused_for_old_book(bare):  # AC-5.2
    r = bare.client.post(
        f"/books/{PILOT_SLUG}/repaint-cover", data={"cover": "print", "confirm": "1"}
    )
    assert r.status_code == 422
    assert bare.commands.calls == [] and bare.runner.current() is None


def test_book_page_shows_repaint_buttons_per_cover(bare):  # AC-5.1
    old = text_of(bare.client.get(f"/books/{PILOT_SLUG}").text)
    assert "Repaint front cover" in old and "Repaint back cover" in old
    assert "print cover" not in old
    make_titled(bare)
    html = bare.client.get(f"/books/{PILOT_SLUG}").text
    page = text_of(html)
    assert "Repaint video cover" in page and "Repaint print cover" in page
    assert "Repaint back cover" in page and "Repaint front cover" not in page
    assert "Replace the print cover (one picture)" in page
    assert 'value="print"' in html
