"""Spec 024 phase 3: the channel connection and the Google client, all offline with fakes."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httplib2
import pytest
from googleapiclient.errors import HttpError
from PIL import Image

from tests.helpers import ROOT
from video_pipeline import youtube_api as api
from video_pipeline.timeline import load_render_config
from video_pipeline.youtube import YouTubeError, load_settings

SETTINGS = load_settings(ROOT / "config")
REDIRECT = "http://127.0.0.1:8765/youtube/oauth"
TOKEN = "SECRET-TOKEN-123"
CODE = "SECRET-CODE"
CLIENT_SECRET = "SECRET-CLIENT-456"
# Fake desktop client: placeholder values only (no real secrets in fixtures).
CLIENT_JSON = {
    "installed": {
        "client_id": "fake-id.apps.googleusercontent.com",
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}
SECRETS = (TOKEN, CODE, CLIENT_SECRET, "SECRET-REFRESH-789")


def _client_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "yt"
    folder.mkdir()
    (folder / "client_secret.json").write_text(json.dumps(CLIENT_JSON), encoding="utf-8")
    return folder


def _token_json(expiry: str = "2099-01-01T00:00:00Z") -> dict:
    return {
        "token": TOKEN,
        "refresh_token": "SECRET-REFRESH-789",
        "client_id": "fake-id.apps.googleusercontent.com",
        "client_secret": CLIENT_SECRET,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": list(SETTINGS.scopes),
        "expiry": expiry,
    }


class FakeCredentials:
    def to_json(self) -> str:
        return json.dumps(_token_json())


class FakeFlow:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.code: str | None = None
        self.credentials = FakeCredentials()

    def fetch_token(self, code: str) -> None:
        if self.fail:
            raise self.fail
        self.code = code


class FakeClient:
    def __init__(self, channel: api.Channel | None):
        self._channel = channel

    def channel(self) -> api.Channel | None:
        return self._channel


CHANNEL = api.Channel(id="UC123", title="Giggle & Snooze", connected_at="")


def _pending(started: float = 1000.0) -> api.Pending:
    return api.Pending(
        auth_url="https://accounts.google.com/x",
        state="STATE-1",
        code_verifier="v" * 50,
        redirect_uri=REDIRECT,
        started=started,
    )


def _finish(folder, query, now=1010.0, flow=None, channel=CHANNEL):
    flow = flow or FakeFlow()
    return api.finish_consent(
        folder,
        _pending(),
        query,
        now,
        SETTINGS,
        flow_factory=lambda *a, **k: flow,
        client_factory=lambda creds: FakeClient(channel),
    )


def _stored(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} - {"client_secret.json"}


def _assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


# --- client file ----------------------------------------------------------------------------


def test_no_client_file_names_path(tmp_path):
    ok, why = api.client_status(tmp_path)
    assert not ok
    assert why == (
        f"no OAuth client: put the desktop client JSON at {tmp_path / 'client_secret.json'}"
    )


def test_client_file_must_be_installed_type(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"web": {}}), encoding="utf-8")
    assert api.client_status(tmp_path) == (
        False,
        f'{path}: not a desktop OAuth client (needs an "installed" entry)',
    )
    path.write_text("{nope", encoding="utf-8")
    ok, why = api.client_status(tmp_path)
    assert not ok and str(path) in why
    path.write_text(json.dumps(CLIENT_JSON), encoding="utf-8")
    assert api.client_status(tmp_path) == (True, "")


def test_secrets_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KIDDO_YOUTUBE_DIR", str(tmp_path))
    assert api.secrets_dir() == tmp_path
    monkeypatch.delenv("KIDDO_YOUTUBE_DIR")
    assert api.secrets_dir() == Path.home() / ".config" / "kiddo" / "youtube"


# --- consent --------------------------------------------------------------------------------


def test_consent_url_has_pkce_state_and_only_two_scopes(tmp_path):
    folder = _client_dir(tmp_path)
    pending = api.begin_consent(folder, REDIRECT, SETTINGS.scopes, now=5.0)
    query = parse_qs(urlparse(pending.auth_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["state"] == [pending.state] and pending.state
    assert query["redirect_uri"] == [REDIRECT]
    assert query["access_type"] == ["offline"]
    assert sorted(query["scope"][0].split()) == sorted(SETTINGS.scopes)
    assert len(SETTINGS.scopes) == 2
    assert pending.code_verifier and pending.started == 5.0
    assert _stored(folder) == set()


def test_state_mismatch_stores_nothing(tmp_path):
    folder = _client_dir(tmp_path)
    for query in ({"code": CODE, "state": "OTHER"}, {"code": CODE}):
        with pytest.raises(api.ConnectError, match="did not match this request"):
            _finish(folder, query)
    assert _stored(folder) == set()


def test_denied_consent_stores_nothing(tmp_path):
    folder = _client_dir(tmp_path)
    with pytest.raises(api.ConnectError, match="^Not connected: Google said access_denied$"):
        _finish(folder, {"error": "access_denied", "state": "STATE-1"})
    assert _stored(folder) == set()


def test_expired_consent_stores_nothing(tmp_path):
    folder = _client_dir(tmp_path)
    with pytest.raises(api.ConnectError, match="took longer than 5 minutes"):
        _finish(folder, {"code": CODE, "state": "STATE-1"}, now=1000.0 + 301)
    assert _stored(folder) == set()


def test_missing_code_stores_nothing(tmp_path):
    folder = _client_dir(tmp_path)
    with pytest.raises(api.ConnectError):
        _finish(folder, {"state": "STATE-1"})
    assert _stored(folder) == set()


def test_account_without_channel_refused_stores_nothing(tmp_path):
    folder = _client_dir(tmp_path)
    with pytest.raises(api.ConnectError, match="^This Google account has no YouTube channel$"):
        _finish(folder, {"code": CODE, "state": "STATE-1"}, channel=None)
    assert _stored(folder) == set()


def test_connected_writes_token_0600_and_channel(tmp_path):
    folder = tmp_path / "new" / "yt"
    folder.mkdir(parents=True, mode=0o755)
    (folder / "client_secret.json").write_text(json.dumps(CLIENT_JSON), encoding="utf-8")
    folder.chmod(0o755)
    flow = FakeFlow()
    channel = _finish(folder, {"code": CODE, "state": "STATE-1"}, flow=flow)
    assert flow.code == CODE
    assert (channel.id, channel.title) == ("UC123", "Giggle & Snooze")
    assert channel.connected_at.startswith("1970-01-01T00:16:50")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for name in ("token.json", "channel.json"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600
    assert json.loads((folder / "token.json").read_text())["token"] == TOKEN
    assert api.connected_channel(folder) == channel
    assert not list(folder.glob("*.tmp"))


def test_connect_cli_saves_like_finish(tmp_path):
    folder = _client_dir(tmp_path)
    seen = {}

    class CliFlow:
        def run_local_server(self, **kwargs):
            seen.update(kwargs)
            return FakeCredentials()

    channel = api.connect_cli(
        folder,
        SETTINGS,
        flow_factory=lambda *a, **k: CliFlow(),
        client_factory=lambda creds: FakeClient(CHANNEL),
    )
    assert seen["port"] == 0 and seen["timeout_seconds"] == 300
    assert channel.id == "UC123"
    assert api.connected_channel(folder) == channel


def test_connected_channel_none_without_token(tmp_path):
    folder = _client_dir(tmp_path)
    _finish(folder, {"code": CODE, "state": "STATE-1"})
    (folder / "token.json").unlink()
    assert api.connected_channel(folder) is None


def test_disconnect_revokes_and_deletes_even_if_revoke_fails(tmp_path):
    folder = _client_dir(tmp_path)
    _finish(folder, {"code": CODE, "state": "STATE-1"})
    revoked = []

    def revoke(token):
        revoked.append(token)
        raise OSError("network down")

    api.disconnect(folder, revoke=revoke)
    assert revoked == ["SECRET-REFRESH-789"]
    assert _stored(folder) == set()
    api.disconnect(folder, revoke=revoke)  # nothing connected: still fine


# --- stored credentials ---------------------------------------------------------------------


def test_missing_token_raises_connect_first(tmp_path):
    with pytest.raises(api.ReconnectNeeded, match="^Connect a channel first$"):
        api.load_credentials(tmp_path, SETTINGS)


class FakeResponse:
    def __init__(self, status: int, data: dict):
        self.status = status
        self.data = json.dumps(data).encode()
        self.headers = {}


def _expired_token(folder: Path) -> None:
    (folder / "token.json").write_text(json.dumps(_token_json("2000-01-01T00:00:00Z")))


def test_revoked_token_raises_reconnect_needed(tmp_path):
    _expired_token(tmp_path)

    def request(*args, **kwargs):
        return FakeResponse(400, {"error": "invalid_grant", "error_description": TOKEN})

    with pytest.raises(api.ReconnectNeeded) as info:
        api.load_credentials(tmp_path, SETTINGS, request=request)
    assert str(info.value) == "Reconnect needed: Google no longer accepts this connection"


def test_refresh_resaves_token_0600(tmp_path):
    _expired_token(tmp_path)

    def request(*args, **kwargs):
        return FakeResponse(200, {"access_token": "NEW-ACCESS", "expires_in": 3600})

    creds = api.load_credentials(tmp_path, SETTINGS, request=request)
    assert creds.token == "NEW-ACCESS"
    saved = tmp_path / "token.json"
    assert json.loads(saved.read_text())["token"] == "NEW-ACCESS"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


# --- video body -----------------------------------------------------------------------------


def test_video_body_private_made_for_kids_category_language():
    text = {"title": "The Snail", "tags": ["snail", "bedtime"], "language": "en"}
    body = api.video_body(text, "Body\n\n0:00 Welcome", "27", "en")
    assert body == {
        "snippet": {
            "title": "The Snail",
            "description": "Body\n\n0:00 Welcome",
            "tags": ["snail", "bedtime"],
            "categoryId": "27",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": True},
    }


# --- upload ---------------------------------------------------------------------------------


def _http_error(status: int, reason: str = "", message: str = "", location: str = ""):
    error = {"reason": reason, "message": message}
    if location:
        error["location"] = location
    content = json.dumps({"error": {"code": status, "message": message, "errors": [error]}})
    return HttpError(httplib2.Response({"status": status}), content.encode())


class FakeUpload:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class FakeUploader:
    def __init__(self, steps):
        self.upload = FakeUpload(steps)
        self.started = 0
        self.args = None

    def start_upload(self, path, body, chunk_bytes):
        self.started += 1
        self.args = (path, body, chunk_bytes)
        return self.upload


def _upload(steps, settings=SETTINGS):
    client = FakeUploader(steps)
    percents, sleeps = [], []
    result = api.upload_video(
        client, Path("v.mp4"), {"b": 1}, settings, percents.append, sleep=sleeps.append
    )
    return result, client, percents, sleeps


def test_upload_reports_whole_percents_once():
    steps = [(0.101, None), (0.104, None), (0.5, None), (None, None), (1.0, "VID1")]
    video_id, client, percents, sleeps = _upload(steps)
    assert video_id == "VID1"
    assert percents == [0, 10, 50, 100]
    assert client.args == (Path("v.mp4"), {"b": 1}, 8 * 1024 * 1024)
    assert sleeps == []


def test_upload_resumes_after_connection_error():
    steps = [(0.2, None), ConnectionError("reset"), TimeoutError("slow"), (None, "VID2")]
    video_id, client, percents, sleeps = _upload(steps)
    assert video_id == "VID2"
    assert client.started == 1  # resumed the same upload, never restarted
    assert sleeps == [1, 2]
    assert percents == [0, 20, 100]


def test_upload_resumes_after_503():
    steps = [_http_error(503, "backendError", "busy"), (None, "VID3")]
    video_id, client, _, sleeps = _upload(steps)
    assert video_id == "VID3" and client.started == 1 and sleeps == [1]


def test_upload_gives_up_after_5_resumes():
    steps = [OSError(f"drop {n}") for n in range(6)]
    with pytest.raises(api.UploadError, match=r"^upload failed after 5 retries: drop 5$") as info:
        _upload(steps)
    assert info.value.field is None


def test_upload_gives_up_sleeps_backoff():
    client = FakeUploader([OSError("x")] * 6)
    sleeps = []
    with pytest.raises(api.UploadError):
        api.upload_video(client, Path("v"), {}, SETTINGS, lambda p: None, sleep=sleeps.append)
    assert sleeps == [1, 2, 4, 8, 16]


def test_quota_error_message():
    for reason in ("quotaExceeded", "uploadLimitExceeded"):
        with pytest.raises(api.QuotaExceeded) as info:
            _upload([_http_error(403, reason, "The request cannot be completed")])
        assert str(info.value) == (
            "YouTube daily upload quota used up; try again after midnight Pacific time"
        )


def test_metadata_rejection_names_field():
    error = _http_error(400, "invalidTitle", "The video title is too long.", "body.snippet.title")
    with pytest.raises(api.UploadError) as info:
        _upload([error])
    assert "The video title is too long." in str(info.value)
    assert info.value.field == "body.snippet.title"


def test_other_403_is_upload_error():
    with pytest.raises(api.UploadError) as info:
        _upload([_http_error(403, "forbidden", "Not allowed")])
    assert not isinstance(info.value, api.QuotaExceeded)


# --- thumbnail ------------------------------------------------------------------------------


def _cover(tmp_path: Path) -> Path:
    import numpy as np

    rng = np.random.default_rng(1)
    pixels = rng.integers(0, 255, (900, 1200, 3), dtype=np.uint8)
    path = tmp_path / "cover_front.png"
    Image.fromarray(pixels).save(path)
    return path


def test_thumbnail_jpeg_under_2mb_1280x720(tmp_path):
    render_cfg = load_render_config(ROOT / "config" / "render.json")
    out = tmp_path / "build" / "youtube" / "thumb.jpg"
    assert api.make_thumbnail(_cover(tmp_path), out, render_cfg, SETTINGS) == out
    assert out.stat().st_size <= 2_000_000
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.size == (1280, 720)
    assert not list(out.parent.glob("*.tmp"))


def test_thumbnail_lowers_quality_then_refuses(tmp_path):
    render_cfg = load_render_config(ROOT / "config" / "render.json")
    cover = _cover(tmp_path)
    out = tmp_path / "thumb.jpg"
    api.make_thumbnail(cover, out, render_cfg, replace(SETTINGS, thumbnail_max_bytes=250_000))
    assert out.stat().st_size <= 250_000
    out.unlink()
    with pytest.raises(YouTubeError):
        api.make_thumbnail(cover, out, render_cfg, replace(SETTINGS, thumbnail_max_bytes=1000))
    assert not out.exists()


# --- secrets --------------------------------------------------------------------------------


def test_no_secret_in_exception_text(tmp_path):
    folder = _client_dir(tmp_path)
    messages = []
    flow = FakeFlow(fail=ValueError(f"bad code {CODE} for token {TOKEN} {CLIENT_SECRET}"))
    with pytest.raises(api.ConnectError) as info:
        _finish(folder, {"code": CODE, "state": "STATE-1"}, flow=flow)
    messages.append(str(info.value))
    assert _stored(folder) == set()

    class BadCli:
        def run_local_server(self, **kwargs):
            raise ValueError(f"oops {CODE} {TOKEN}")

    with pytest.raises(api.ConnectError) as info:
        api.connect_cli(folder, SETTINGS, flow_factory=lambda *a, **k: BadCli())
    messages.append(str(info.value))

    _expired_token(tmp_path)

    def request(*args, **kwargs):
        return FakeResponse(400, {"error": "invalid_grant", "error_description": TOKEN})

    with pytest.raises(api.ReconnectNeeded) as info:
        api.load_credentials(tmp_path, SETTINGS, request=request)
    messages.append(str(info.value))

    (tmp_path / "token.json").write_text(f"not json {TOKEN}")
    with pytest.raises(api.ReconnectNeeded) as info:
        api.load_credentials(tmp_path, SETTINGS)
    messages.append(str(info.value))

    for message in messages:
        _assert_no_secret(message)


# --- real upload (opt-in) -------------------------------------------------------------------


@pytest.mark.youtube
def test_real_upload_private_clip(tmp_path):
    folder = api.secrets_dir()
    if api.connected_channel(folder) is None:
        pytest.skip("no connected YouTube channel")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "3", "-pix_fmt", "yuv420p", str(clip)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    client = api.GoogleYouTube(api.load_credentials(folder, SETTINGS))
    text = {"title": "kiddo test clip (delete me)", "tags": ["test"]}
    body = api.video_body(text, "Automated test upload.", "27", "en")
    video_id = api.upload_video(client, clip, body, SETTINGS, lambda p: None)
    try:
        assert client.video_exists(video_id)
    finally:
        client.service.videos().delete(id=video_id).execute()
