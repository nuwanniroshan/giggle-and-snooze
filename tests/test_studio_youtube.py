"""Spec 024 Phase 6: the studio's YouTube settings page and the book's YouTube panel
(Stories 1-4, FR-1, FR-4, FR-6, Security NFR).

No network: the OAuth flow and the YouTube client are fakes injected on the Studio; the secrets
folder is a tmp dir (KIDDO_YOUTUBE_DIR). Stage commands are tiny `python -c` programs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.helpers import make_root
from tests.test_studio_app import PILOT_SLUG, Commands, Studio, text_of
from tests.test_youtube_mcp import good_text, write_record
from video_pipeline import youtube
from video_pipeline import youtube_api as api
from video_pipeline.library import BookFolder
from video_pipeline.studio import books as sb
from video_pipeline.studio import jobs as sj
from video_pipeline.youtube import YouTubePaths

CLIENT_JSON = {
    "installed": {
        "client_id": "fake-id.apps.googleusercontent.com",
        "client_secret": "placeholder-not-a-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}
TOKEN = "SECRET-TOKEN-XYZ"
CHANNEL = api.Channel(id="UC123", title="Giggle Channel", connected_at="2026-11-01T00:00:00+00:00")


# --- fakes and helpers ----------------------------------------------------------------------


class FakeCredentials:
    def to_json(self) -> str:
        return json.dumps({"token": TOKEN, "refresh_token": "SECRET-REFRESH"})


class FakeFlow:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.credentials = FakeCredentials()

    def fetch_token(self, code: str) -> None:
        if self.fail:
            raise self.fail


class FakeClient:
    def __init__(self, channel=CHANNEL, exists=True):
        self._channel = channel
        self.exists = exists
        self.checked: list[str] = []

    def channel(self):
        return self._channel

    def video_exists(self, video_id):
        self.checked.append(video_id)
        return self.exists


@pytest.fixture
def yt(tmp_path, monkeypatch):
    folder = tmp_path / "yt"
    monkeypatch.setenv("KIDDO_YOUTUBE_DIR", str(folder))
    return folder


@pytest.fixture
def s(tmp_path, yt):
    make_root(tmp_path)
    studio = Studio(tmp_path)
    studio.studio = studio.app.state.studio
    studio.studio.youtube_revoke = lambda token: None  # never contact Google
    yield studio
    if studio.runner.current():
        studio.runner.stop()


def with_client(yt: Path) -> None:
    yt.mkdir(parents=True, exist_ok=True)
    (yt / api.CLIENT_FILE).write_text(json.dumps(CLIENT_JSON), encoding="utf-8")


def connect(yt: Path) -> None:
    yt.mkdir(parents=True, exist_ok=True)
    (yt / api.TOKEN_FILE).write_text(json.dumps({"token": TOKEN}), encoding="utf-8")
    record = {"id": CHANNEL.id, "title": CHANNEL.title, "connected_at": CHANNEL.connected_at}
    (yt / api.CHANNEL_FILE).write_text(json.dumps(record), encoding="utf-8")


def folder_of(s) -> BookFolder:
    return BookFolder(PILOT_SLUG, s.paths.root)


def up_to_date(monkeypatch) -> None:
    monkeypatch.setattr(sb, "video_check", lambda paths, book: ("up_to_date", ""))


def rendered(s, monkeypatch) -> None:
    folder = folder_of(s)
    write_record(folder)
    (folder.build_dir / "video" / f"{folder.slug}.mp4").write_bytes(b"\0" * (2 * 1024 * 1024))
    up_to_date(monkeypatch)


def with_text(s) -> Path:
    paths = YouTubePaths(folder_of(s))
    paths.dir.mkdir(parents=True, exist_ok=True)
    text = {**good_text(), "language": "en", "category_id": "27", "written_by": "codex"}
    paths.text.write_text(json.dumps(text), encoding="utf-8")
    return paths.text


def ready(s, yt, monkeypatch) -> None:
    connect(yt)
    rendered(s, monkeypatch)
    with_text(s)


def uploaded(s, final_key="abc", state="uploaded") -> None:
    entry = {
        "video_id": "VID123",
        "url": youtube.video_url("VID123"),
        "channel_id": CHANNEL.id,
        "channel_title": CHANNEL.title,
        "uploaded_at": "2026-11-14T09:30:00+00:00",
        "final_key": final_key,
        "state": state,
        "warnings": [],
    }
    youtube.append_upload(YouTubePaths(folder_of(s)), entry)


def book_page(s) -> str:
    response = s.client.get(f"/books/{PILOT_SLUG}")
    assert response.status_code == 200
    return response.text


def start_consent(s) -> str:
    response = s.client.post("/youtube/connect")
    assert response.status_code == 303
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def callback(s, flow=None, client=None, **query):
    s.studio.youtube_flow_factory = lambda *a, **k: flow or FakeFlow()
    s.studio.youtube_client_factory = lambda creds: client or FakeClient()
    return s.client.get("/youtube/callback", params=query)


def stored(yt: Path) -> set[str]:
    return {p.name for p in yt.iterdir()} - {api.CLIENT_FILE} if yt.exists() else set()


# --- settings page and connection (Story 1) -------------------------------------------------


def test_settings_without_client_has_no_connect_button_and_names_path(s, yt):
    html = s.client.get("/youtube").text
    assert str(yt / api.CLIENT_FILE) in html
    assert 'action="/youtube/connect"' not in html
    assert "Not connected" in text_of(html)
    assert 'href="/youtube"' in book_page(s)  # the nav link


def test_connect_redirects_to_google_with_state(s, yt):
    with_client(yt)
    assert 'action="/youtube/connect"' in s.client.get("/youtube").text
    response = s.client.post("/youtube/connect")
    assert response.status_code == 303
    url = urlparse(response.headers["location"])
    assert url.hostname == "accounts.google.com"
    query = parse_qs(url.query)
    assert query["state"][0]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/youtube/callback"]
    assert query["code_challenge_method"] == ["S256"]


def test_connect_without_client_refused(s, yt):
    assert s.client.post("/youtube/connect").status_code == 409


def test_callback_without_pending_refused(s, yt):
    with_client(yt)
    response = callback(s, state="x", code="y")
    assert response.status_code == 400
    assert "No connection was started from this studio" in response.text
    assert stored(yt) == set()


def test_callback_wrong_state_refused_nothing_stored(s, yt):
    with_client(yt)
    start_consent(s)
    response = callback(s, state="WRONG", code="c")
    assert response.status_code == 400
    assert "Not connected" in response.text
    assert stored(yt) == set()
    # the pending connection is used up either way
    assert "No connection was started" in callback(s, state="WRONG", code="c").text


def test_callback_error_shows_not_connected(s, yt):
    with_client(yt)
    state = start_consent(s)
    response = callback(s, state=state, error="access_denied")
    assert response.status_code == 400
    assert "Not connected: Google said access_denied" in response.text
    assert stored(yt) == set()


def test_callback_no_channel_refused(s, yt):
    with_client(yt)
    state = start_consent(s)
    response = callback(s, client=FakeClient(channel=None), state=state, code="c")
    assert response.status_code == 400
    assert "This Google account has no YouTube channel" in response.text
    assert stored(yt) == set()


def test_callback_ok_shows_channel_on_settings_page(s, yt):
    with_client(yt)
    state = start_consent(s)
    response = callback(s, state=state, code="c")
    assert response.status_code == 303
    assert response.headers["location"] == "/youtube?msg=connected"
    html = s.client.get(response.headers["location"]).text
    assert CHANNEL.title in html and CHANNEL.id in html
    assert "Connected" in html


def test_callback_does_not_echo_query_values(s, yt):
    with_client(yt)
    start_consent(s)
    html = callback(s, state="EVIL-STATE", code="EVIL-CODE").text
    assert "EVIL" not in html
    state = start_consent(s)
    html = callback(s, state=state, error="EVIL<b>VALUE").text
    assert "EVIL" not in html and "Not connected" in html


def test_settings_flash_is_whitelisted(s, yt):
    html = s.client.get("/youtube", params={"msg": "INJECTED-TEXT"}).text
    assert "INJECTED-TEXT" not in html


def test_disconnect_needs_confirm(s, yt):
    connect(yt)
    response = s.client.post("/youtube/disconnect", data={})
    assert response.status_code == 409
    assert (yt / api.TOKEN_FILE).is_file()


def test_disconnect_deletes_connection(s, yt):
    connect(yt)
    revoked = []
    s.studio.youtube_revoke = revoked.append
    response = s.client.post("/youtube/disconnect", data={"confirm": "1"})
    assert response.status_code == 303
    assert response.headers["location"] == "/youtube?msg=disconnected"
    assert not (yt / api.TOKEN_FILE).exists() and not (yt / api.CHANNEL_FILE).exists()
    assert revoked == [TOKEN]
    assert "Not connected" in s.client.get("/youtube").text


# --- YouTube text (Story 2) -----------------------------------------------------------------


def test_write_text_starts_youtube_text_job(s, yt, monkeypatch):
    rendered(s, monkeypatch)
    html = book_page(s)
    assert 'id="youtube"' in html
    assert f'action="/books/{PILOT_SLUG}/run/youtube_text"' in html
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_text", data={})
    assert response.status_code == 303
    s.wait()
    assert s.commands.calls == [("youtube_text", PILOT_SLUG, False)]


def test_write_text_refused_when_video_not_up_to_date(s, yt):
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_text", data={})
    assert response.status_code == 409
    assert s.commands.calls == []


def test_rewrite_needs_confirm_and_passes_force(s, yt, monkeypatch):
    rendered(s, monkeypatch)
    with_text(s)
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_text", data={})
    assert response.status_code == 409
    assert "Confirm" in response.text
    assert s.commands.calls == []
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_text", data={"confirm": "1"})
    assert response.status_code == 303
    s.wait()
    assert s.commands.calls == [("youtube_text", PILOT_SLUG, True)]


def test_text_edit_saved_as_producer(s, yt, monkeypatch):
    rendered(s, monkeypatch)
    path = with_text(s)
    title = good_text()["title"] + " Again"
    response = s.client.post(
        f"/books/{PILOT_SLUG}/youtube/text",
        data={"title": title, "description": "A gentle new body.", "tags": " a, b ,, c "},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{PILOT_SLUG}#youtube"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["title"] == title
    assert saved["description"] == "A gentle new body."
    assert saved["tags"] == ["a", "b", "c"]
    assert saved["written_by"] == "producer" and saved["written_at"]
    assert saved["language"] == "en" and saved["category_id"] == "27"


def test_text_edit_refused_names_field_and_keeps_file(s, yt, monkeypatch):
    rendered(s, monkeypatch)
    path = with_text(s)
    before = path.read_bytes()
    response = s.client.post(
        f"/books/{PILOT_SLUG}/youtube/text",
        data={"title": "Something else", "description": "Please subscribe!", "tags": "a"},
    )
    assert response.status_code == 409
    text = text_of(response.text)
    assert "title: must contain the book title" in text
    assert "description: contains the call to action" in text
    assert path.read_bytes() == before


def test_composed_description_shown(s, yt, monkeypatch):
    rendered(s, monkeypatch)
    with_text(s)
    html = book_page(s)
    assert "0:00 Welcome" in html
    assert "More stories: http://giggleandsnooze.com/" in html


# --- upload (Story 3) -----------------------------------------------------------------------


def test_upload_disabled_reasons_in_order(s, yt, monkeypatch):
    monkeypatch.setattr(sb, "video_check", lambda paths, book: ("out_of_date", "page 3 changed"))

    def reason():
        return sb.youtube_panel(s.paths, s.book())["upload_disabled"]

    assert reason() == "Connect a channel on the YouTube page first"
    assert "Connect a channel on the YouTube page first" in book_page(s)
    connect(yt)
    assert "out of date" in reason() and "page 3 changed" in reason()
    rendered(s, monkeypatch)
    assert reason() == "Write YouTube text first"
    path = with_text(s)
    assert reason() is None
    path.write_text(json.dumps({**good_text(), "title": "Nope"}), encoding="utf-8")
    assert reason() == "Fix the YouTube text first"


def test_upload_refused_when_disabled(s, yt):
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_upload", data={"confirm": "1"})
    assert response.status_code == 409
    assert "Connect a channel" in response.text
    assert s.commands.calls == []


def test_upload_needs_confirm_and_starts_job(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    html = text_of(book_page(s))
    for line in ("Giggle Channel", good_text()["title"], "Made for kids: yes", "Privacy: Private"):
        assert line in html
    assert "2.0 MB" in html
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_upload", data={})
    assert response.status_code == 409
    assert "Confirm" in response.text
    assert s.commands.calls == []
    response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_upload", data={"confirm": "1"})
    assert response.status_code == 303
    s.wait()
    assert s.commands.calls == [("youtube_upload", PILOT_SLUG, False)]


def test_second_upload_summary_links_first(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s)
    html = text_of(book_page(s))
    assert "second video" in html and youtube.video_url("VID123") in html


def test_stage_command_upload_has_yes_and_text_force(tmp_path):
    make_root(tmp_path)
    paths = sb.StudioPaths(tmp_path)
    book = sb.get_book(paths, PILOT_SLUG)
    root = str(tmp_path)
    base = [sys.executable, "-m", "video_pipeline.youtube"]
    assert sj.stage_command("youtube_text", book, paths) == [
        *base, "text", "--book", PILOT_SLUG, "--root", root,
    ]  # fmt: skip
    assert sj.stage_command("youtube_text", book, paths, force=True) == [
        *base, "text", "--book", PILOT_SLUG, "--root", root, "--force",
    ]  # fmt: skip
    assert sj.stage_command("youtube_upload", book, paths) == [
        *base, "upload", "--book", PILOT_SLUG, "--root", root, "--yes",
    ]  # fmt: skip
    assert "youtube_text" in sj.BOOK_STAGES and "youtube_upload" in sj.BOOK_STAGES


def test_panel_shows_on_youtube_private_with_link(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s)
    html = book_page(s)
    assert "On YouTube (Private)" in html
    url = youtube.video_url("VID123")
    assert f'href="{url}" target="_blank" rel="noopener"' in html
    assert "2026-11-14T09:30:00+00:00" in html
    assert "YouTube has an older cut" not in html


def test_panel_shows_older_cut(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s, final_key="old")
    assert "YouTube has an older cut" in book_page(s)


def test_panel_shows_not_found(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s, state="not_found")
    assert "not found on YouTube" in book_page(s)


def test_panel_shows_upload_percent_from_log(tmp_path, yt, monkeypatch):
    make_root(tmp_path)
    code = (
        "import time; print('uploaded 10%', flush=True); print('uploaded 40%', flush=True); "
        "time.sleep(10)"
    )
    s = Studio(tmp_path, Commands(code))
    try:
        ready(s, yt, monkeypatch)
        response = s.client.post(f"/books/{PILOT_SLUG}/run/youtube_upload", data={"confirm": "1"})
        assert response.status_code == 303
        job = s.runner.current()
        deadline = time.monotonic() + 10
        while "40%" not in s.runner.log(job["id"], 0)[0] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "Uploading: 40%" in text_of(book_page(s))
        # a text edit is refused while a YouTube job runs for this book
        edit = s.client.post(
            f"/books/{PILOT_SLUG}/youtube/text",
            data={"title": good_text()["title"], "description": "x", "tags": "a"},
        )
        assert edit.status_code == 409
    finally:
        s.runner.stop()


# --- check (Story 4) ------------------------------------------------------------------------


def test_check_marks_not_found(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s)
    monkeypatch.setattr(api, "load_credentials", lambda folder, settings, request=None: object())
    client = FakeClient(exists=False)
    s.studio.youtube_client_factory = lambda creds: client
    response = s.client.post(f"/books/{PILOT_SLUG}/youtube/check")
    assert response.status_code == 303
    assert client.checked == ["VID123"]
    assert youtube.last_upload(YouTubePaths(folder_of(s)))["state"] == "not_found"
    assert len(youtube.read_uploads(YouTubePaths(folder_of(s)))) == 1  # kept, not deleted


def test_check_reconnect_needed(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    uploaded(s)

    def refuse(folder, settings, request=None):
        raise api.ReconnectNeeded("Reconnect needed: Google no longer accepts this connection")

    monkeypatch.setattr(api, "load_credentials", refuse)
    response = s.client.post(f"/books/{PILOT_SLUG}/youtube/check")
    assert response.status_code == 409
    assert "Reconnect needed" in response.text


# --- security -------------------------------------------------------------------------------


def test_cross_site_upload_post_refused(s, yt, monkeypatch):
    ready(s, yt, monkeypatch)
    for path in (
        f"/books/{PILOT_SLUG}/run/youtube_upload",
        "/youtube/connect",
        "/youtube/disconnect",
        f"/books/{PILOT_SLUG}/youtube/text",
    ):
        response = s.client.post(
            path, data={"confirm": "1"}, headers={"Origin": "http://evil.example"}
        )
        assert response.status_code == 403
    assert s.commands.calls == []
    assert (yt / api.TOKEN_FILE).is_file()


def test_autopilot_has_no_youtube_step():
    from video_pipeline import autopilot

    source = Path(autopilot.__file__).read_text(encoding="utf-8").lower()
    assert "youtube" not in source
    assert not any("youtube" in str(step).lower() for step in autopilot.STEPS)


def test_no_token_in_any_page(s, yt, monkeypatch):
    with_client(yt)
    ready(s, yt, monkeypatch)
    uploaded(s)
    for url in ("/youtube", f"/books/{PILOT_SLUG}", "/"):
        assert TOKEN not in s.client.get(url).text
