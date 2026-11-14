"""Spec 024 Phase 5: CLI connect / disconnect / upload / check (Stories 1, 3, 4; FR-1, FR-4)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from tests.helpers import make_root
from tests.test_youtube_mcp import good_text, write_record
from video_pipeline import youtube
from video_pipeline import youtube_api as api
from video_pipeline.youtube import YouTubeError, YouTubePaths

SECRET = "ya29.SECRET-ACCESS-TOKEN"
CHANNEL = api.Channel(id="UC123", title="Giggle Channel", connected_at="2026-11-01T00:00:00+00:00")
NOW = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)


class FakeUpload:
    def __init__(self, steps):
        self.steps = list(steps)

    def next_chunk(self):
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class FakeYouTube:
    def __init__(self, steps=None, thumb_error: Exception | None = None, exists=True):
        self.steps = steps if steps is not None else [(0.5, None), (1.0, "VID123")]
        self.thumb_error = thumb_error
        self.exists = exists
        self.bodies: list[dict] = []
        self.thumbnails: list[tuple[str, Path]] = []
        self.checked: list[str] = []

    def channel(self):
        return CHANNEL

    def start_upload(self, path, body, chunk_bytes):
        self.bodies.append(body)
        return FakeUpload(self.steps)

    def set_thumbnail(self, video_id, path):
        if self.thumb_error:
            raise self.thumb_error
        self.thumbnails.append((video_id, Path(path)))

    def video_exists(self, video_id):
        self.checked.append(video_id)
        return self.exists


class FakeCreds:
    token = SECRET

    def __repr__(self):
        return f"FakeCreds({SECRET})"


def up_to_date(folder, root):
    return ("up_to_date", "")


def stale(folder, root):
    return ("out_of_date", "page 3 picture changed")


def _connect(secrets: Path) -> None:
    secrets.mkdir(parents=True, exist_ok=True)
    (secrets / api.TOKEN_FILE).write_text(json.dumps({"token": SECRET}), encoding="utf-8")
    record = {"id": CHANNEL.id, "title": CHANNEL.title, "connected_at": CHANNEL.connected_at}
    (secrets / api.CHANNEL_FILE).write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    path = tmp_path / "secrets"
    _connect(path)
    monkeypatch.setenv("KIDDO_YOUTUBE_DIR", str(path))
    monkeypatch.setattr(api, "load_credentials", lambda folder, settings, request=None: FakeCreds())
    return path


@pytest.fixture
def folder(tmp_path, secrets):
    folder = make_root(tmp_path / "root")
    write_record(folder)
    video = folder.build_dir / "video" / f"{folder.slug}.mp4"
    video.write_bytes(b"\0" * (3 * 1024 * 1024))
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (200, 120, 40)).save(folder.illustrations / "cover_front.png")
    paths = YouTubePaths(folder)
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.text.write_text(json.dumps({**good_text(), "language": "en"}), encoding="utf-8")
    return folder


def upload(folder, client=None, *, confirm=None, out=None, check=up_to_date, secrets=None):
    client = client or FakeYouTube()
    lines: list[str] = []
    prompts: list[str] = []

    def default_confirm(text):
        prompts.append(text)
        return True

    entry = youtube.upload_book(
        folder,
        folder.root,
        lambda creds: client,
        confirm=confirm or default_confirm,
        out=out or lines.append,
        check=check,
        now=lambda: NOW,
        secrets=secrets,
    )
    return entry, client, lines, prompts


def uploads(folder) -> list[dict]:
    return youtube.read_uploads(YouTubePaths(folder))


# --- gates ----------------------------------------------------------------------------------


def test_upload_refused_without_connection(folder, tmp_path):
    with pytest.raises(YouTubeError, match="Connect a channel first"):
        upload(folder, secrets=tmp_path / "empty")
    assert uploads(folder) == []


def test_upload_refused_when_reconnect_needed(folder, monkeypatch):
    def refuse(folder, settings, request=None):
        raise api.ReconnectNeeded("Reconnect needed: Google no longer accepts this connection")

    monkeypatch.setattr(api, "load_credentials", refuse)
    client = FakeYouTube()
    with pytest.raises(api.ReconnectNeeded):
        upload(folder, client)
    assert client.bodies == [] and uploads(folder) == []


def test_upload_refused_when_video_out_of_date_with_reason(folder):
    with pytest.raises(YouTubeError, match="out_of_date: page 3 picture changed; render it first"):
        upload(folder, check=stale)


def test_upload_refused_without_text(folder):
    YouTubePaths(folder).text.unlink()
    with pytest.raises(YouTubeError, match="Write YouTube text first"):
        upload(folder)


def test_upload_refused_with_text_problems_listed(folder):
    bad = {"title": "Nope", "description": "Please subscribe at www.example.com", "tags": []}
    YouTubePaths(folder).text.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(YouTubeError) as info:
        upload(folder)
    message = str(info.value)
    assert message.startswith("the YouTube text has problems:")
    assert "- title:" in message and "subscribe" in message and "- tags:" in message


# --- confirmation ---------------------------------------------------------------------------


def test_confirmation_lists_channel_title_private_made_for_kids_size(folder):
    _, _, _, prompts = upload(folder)
    text = prompts[0]
    assert "Giggle Channel" in text
    assert good_text()["title"] in text
    assert "Made for kids: yes" in text
    assert "Privacy: Private" in text
    assert "Category: 27" in text
    assert "3.0 MB" in text
    assert "second video" not in text


def test_declined_confirmation_sends_nothing(folder):
    client = FakeYouTube()
    with pytest.raises(YouTubeError, match="upload cancelled"):
        upload(folder, client, confirm=lambda text: False)
    assert client.bodies == [] and uploads(folder) == []


def test_second_upload_confirmation_links_first(folder):
    upload(folder)
    _, _, _, prompts = upload(folder, FakeYouTube(steps=[(1.0, "VID999")]))
    assert (
        "This makes a second video; the first is https://www.youtube.com/watch?v=VID123"
        in (prompts[0])
    )
    assert [u["video_id"] for u in uploads(folder)] == ["VID123", "VID999"]


# --- upload ---------------------------------------------------------------------------------


def test_progress_lines_printed(folder):
    _, _, lines, _ = upload(folder)
    progress = [line for line in lines if line.startswith("uploaded ")]
    assert progress == ["uploaded 0%", "uploaded 50%", "uploaded 100%"]
    assert lines[-1].endswith("https://www.youtube.com/watch?v=VID123")


def test_body_sent_is_private_made_for_kids_with_composed_description(folder):
    _, client, _, _ = upload(folder)
    body = client.bodies[0]
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": True}
    snippet = body["snippet"]
    assert snippet["title"] == good_text()["title"]
    assert snippet["tags"] == good_text()["tags"]
    assert snippet["categoryId"] == "27"
    assert snippet["description"].startswith(good_text()["description"])
    assert "0:00 Welcome" in snippet["description"]
    assert snippet["description"].endswith("More stories: http://giggleandsnooze.com/")
    assert client.thumbnails[0][0] == "VID123"
    assert client.thumbnails[0][1].is_file()


def test_record_has_all_ac_4_1_fields(folder):
    entry, _, _, _ = upload(folder)
    paths = YouTubePaths(folder)
    video = folder.build_dir / "video" / f"{folder.slug}.mp4"
    expected = {
        "video_id": "VID123",
        "url": "https://www.youtube.com/watch?v=VID123",
        "channel_id": "UC123",
        "channel_title": "Giggle Channel",
        "uploaded_at": "2026-11-14T09:30:00+00:00",
        "final_key": "abc",
        "video_sha256": youtube.sha256_file(video),
        "text_sha256": youtube.text_sha256(paths.text),
        "category_id": "27",
        "privacy": "private",
        "made_for_kids": True,
        "warnings": [],
        "state": "uploaded",
        "checked_at": None,
    }
    assert entry == expected
    assert uploads(folder) == [expected]


def test_thumbnail_refusal_is_warning_and_upload_recorded(folder):
    client = FakeYouTube(thumb_error=YouTubeError("custom thumbnails need a verified channel"))
    entry, _, lines, _ = upload(folder, client)
    assert entry["warnings"] == ["thumbnail not set: custom thumbnails need a verified channel"]
    assert uploads(folder)[0]["warnings"] == entry["warnings"]
    assert any("thumbnail not set" in line for line in lines)


@pytest.mark.parametrize(
    "error",
    [api.UploadError("YouTube refused the upload: bad title", "title"), api.QuotaExceeded()],
)
def test_failure_writes_no_record(folder, error):
    client = FakeYouTube(steps=[(0.5, None), error])
    with pytest.raises(type(error)):
        upload(folder, client)
    assert uploads(folder) == []
    assert not YouTubePaths(folder).uploads.exists()


# --- check ----------------------------------------------------------------------------------


def test_check_marks_not_found_and_keeps_record(folder):
    upload(folder)
    state = youtube.check_book(folder, FakeYouTube(exists=False), now=lambda: NOW)
    assert state == "not_found"
    [entry] = uploads(folder)
    assert entry["video_id"] == "VID123"
    assert entry["state"] == "not_found"
    assert entry["checked_at"] == "2026-11-14T09:30:00+00:00"


def test_check_sets_checked_at_when_found(folder):
    upload(folder)
    client = FakeYouTube(exists=True)
    assert youtube.check_book(folder, client, now=lambda: NOW) == "uploaded"
    assert client.checked == ["VID123"]
    [entry] = uploads(folder)
    assert entry["state"] == "uploaded" and entry["checked_at"] == "2026-11-14T09:30:00+00:00"


def test_check_without_upload_refused(folder):
    with pytest.raises(YouTubeError, match="nothing uploaded yet"):
        youtube.check_book(folder, FakeYouTube(), now=lambda: NOW)


# --- CLI ------------------------------------------------------------------------------------


def _client_file(secrets: Path) -> None:
    data = {"installed": {"client_id": "x", "client_secret": "SHHH"}}
    (secrets / api.CLIENT_FILE).write_text(json.dumps(data), encoding="utf-8")


def test_cli_connect_prints_channel_title_and_id(secrets, monkeypatch, capsys):
    _client_file(secrets)
    seen = {}

    def fake_connect(folder, settings, flow_factory=None, client_factory=None):
        seen["folder"] = folder
        return CHANNEL

    monkeypatch.setattr(api, "connect_cli", fake_connect)
    assert youtube.main(["connect"]) == 0
    assert seen["folder"] == secrets
    assert "Connected: Giggle Channel (UC123)" in capsys.readouterr().out


def test_cli_connect_without_client_exits_1(secrets, monkeypatch, capsys):
    monkeypatch.setattr(api, "connect_cli", lambda *a, **k: pytest.fail("must not connect"))
    assert youtube.main(["connect"]) == 1
    assert "no OAuth client" in capsys.readouterr().err


def test_cli_disconnect_yes(secrets, monkeypatch, capsys):
    revoked = []
    monkeypatch.setattr(api, "_post_revoke", revoked.append)
    assert youtube.main(["disconnect", "--yes"]) == 0
    assert revoked == [SECRET]
    assert not (secrets / api.TOKEN_FILE).exists()
    assert "Disconnected" in capsys.readouterr().out


def test_cli_disconnect_declined_keeps_connection(secrets, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(api, "_post_revoke", lambda token: pytest.fail("must not revoke"))
    assert youtube.main(["disconnect"]) == 1
    assert (secrets / api.TOKEN_FILE).exists()


def test_cli_status_not_connected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KIDDO_YOUTUBE_DIR", str(tmp_path / "none"))
    assert youtube.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "no OAuth client" in out and "Not connected" in out


def test_cli_status_connected(secrets, capsys):
    _client_file(secrets)
    assert youtube.main(["status"]) == 0
    assert "Giggle Channel (UC123)" in capsys.readouterr().out


def test_cli_upload_yes_exit_0_and_quota_exit_1(folder, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: FakeYouTube())
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("--yes must not ask"))
    argv = ["upload", "--book", folder.slug, "--root", str(folder.root), "--yes"]
    assert youtube.main(argv) == 0
    out = capsys.readouterr().out
    assert "uploaded 50%" in out and "https://www.youtube.com/watch?v=VID123" in out

    quota = FakeYouTube(steps=[api.QuotaExceeded()])
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: quota)
    assert youtube.main(argv) == 1
    assert "quota" in capsys.readouterr().err
    assert len(uploads(folder)) == 1


def test_cli_upload_prompt_declined_exit_1(folder, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    client = FakeYouTube()
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: client)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "")
    assert youtube.main(["upload", "--book", folder.slug, "--root", str(folder.root)]) == 1
    assert asked and asked[0].endswith("Upload? [y/N] ")
    assert client.bodies == [] and "upload cancelled" in capsys.readouterr().err


def test_cli_upload_interrupt_exit_130_no_record(folder, monkeypatch):
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    client = FakeYouTube(steps=[(0.5, None), KeyboardInterrupt()])
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: client)
    argv = ["upload", "--book", folder.slug, "--root", str(folder.root), "--yes"]
    assert youtube.main(argv) == 130
    assert uploads(folder) == []


def test_cli_check_not_found(folder, monkeypatch, capsys):
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: FakeYouTube())
    argv = ["--book", folder.slug, "--root", str(folder.root)]
    assert youtube.main(["upload", *argv, "--yes"]) == 0
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: FakeYouTube(exists=False))
    assert youtube.main(["check", *argv]) == 0
    assert "not found on YouTube (record kept)" in capsys.readouterr().out
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: FakeYouTube(exists=True))
    assert youtube.main(["check", *argv]) == 0
    assert "On YouTube" in capsys.readouterr().out


def test_no_secret_in_cli_output(folder, monkeypatch, capsys):
    _client_file(folder_secrets := Path(os.environ["KIDDO_YOUTUBE_DIR"]))
    monkeypatch.setattr(youtube, "_render_check", up_to_date)
    monkeypatch.setattr(youtube, "_client_factory", lambda creds: FakeYouTube())
    monkeypatch.setattr(api, "_post_revoke", lambda token: None)
    argv = ["--book", folder.slug, "--root", str(folder.root)]
    youtube.main(["status"])
    youtube.main(["upload", *argv, "--yes"])
    youtube.main(["check", *argv])
    monkeypatch.setattr(
        youtube, "_client_factory", lambda creds: FakeYouTube(steps=[api.QuotaExceeded()])
    )
    youtube.main(["upload", *argv, "--yes"])
    youtube.main(["disconnect", "--yes"])
    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        assert SECRET not in text and "SHHH" not in text
    assert not (folder_secrets / api.TOKEN_FILE).exists()
