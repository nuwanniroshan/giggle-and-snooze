"""The YouTube channel connection and the Google client (spec 024, phase 3).

Secrets (the OAuth client file, the token and the channel record) live outside the repository
in `secrets_dir()`, a 0700 folder whose token and channel files are 0600. No exception raised
here carries a token, refresh token, client secret or authorization code.

Resume design: the client hands out one resumable upload (`YouTubeClient.start_upload`) whose
`next_chunk()` sends the next chunk. `upload_video` owns retries, backoff and progress, and on a
dropped connection simply calls `next_chunk()` again on the same upload, which Google's
resumable request continues from the last confirmed byte. `GoogleYouTube` stays a thin adapter.
"""

from __future__ import annotations

import io
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httplib2
from googleapiclient.errors import HttpError
from PIL import Image

from video_pipeline.frames import canvas_parts
from video_pipeline.timeline import RenderConfig
from video_pipeline.youtube import YouTubeError, YouTubeSettings

CLIENT_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
CHANNEL_FILE = "channel.json"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
QUOTA_MESSAGE = "YouTube daily upload quota used up; try again after midnight Pacific time"
_QUOTA_REASONS = frozenset({"quotaExceeded", "uploadLimitExceeded"})
_RESUME_STATUSES = frozenset({500, 502, 503, 504})
_TRANSPORT_ERRORS = (ConnectionError, TimeoutError, OSError, httplib2.HttpLib2Error)
# Privacy and made-for-kids are fixed by the spec (FR-4: always), never parameters.
_PRIVACY = "private"
_MADE_FOR_KIDS = True
_THUMB_QUALITIES = range(90, 29, -10)


class ConnectError(YouTubeError):
    """Connecting the channel failed; nothing was stored."""


class ReconnectNeeded(YouTubeError):
    """No usable stored connection."""


class QuotaExceeded(YouTubeError):
    def __init__(self) -> None:
        super().__init__(QUOTA_MESSAGE)


class UploadError(YouTubeError):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    connected_at: str


@dataclass(frozen=True)
class Pending:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str
    started: float


class Upload(Protocol):
    def next_chunk(self) -> tuple[float | None, str | None]:
        """Send the next chunk: (fraction sent or None, the video id once finished)."""
        ...


class YouTubeClient(Protocol):
    def channel(self) -> Channel | None: ...

    def start_upload(self, path: Path, body: dict, chunk_bytes: int) -> Upload: ...

    def set_thumbnail(self, video_id: str, path: Path) -> None: ...

    def video_exists(self, video_id: str) -> bool: ...


# --- secrets folder -------------------------------------------------------------------------


def secrets_dir() -> Path:
    env = os.environ.get("KIDDO_YOUTUBE_DIR")
    return Path(env) if env else Path.home() / ".config" / "kiddo" / "youtube"


def client_status(folder: Path) -> tuple[bool, str]:
    path = Path(folder) / CLIENT_FILE
    if not path.is_file():
        return False, f"no OAuth client: put the desktop client JSON at {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, f"{path}: not readable as JSON"
    if not isinstance(data, dict) or not isinstance(data.get("installed"), dict):
        return False, f'{path}: not a desktop OAuth client (needs an "installed" entry)'
    return True, ""


def _private_dir(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)


def _write_private(path: Path, text: str) -> None:
    """Write `text` atomically with mode 0600 from the first byte."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _iso(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds")


def _read_channel(client: YouTubeClient) -> Channel:
    try:
        channel = client.channel()
    except HttpError as exc:
        raise ConnectError(
            f"Not connected: could not read the channel (HTTP {exc.resp.status})"
        ) from None
    except _TRANSPORT_ERRORS:
        raise ConnectError("Not connected: could not reach YouTube") from None
    if channel is None:
        raise ConnectError("This Google account has no YouTube channel")
    return channel


def _save_connection(folder: Path, credentials: Any, channel: Channel, now: float) -> Channel:
    """Store the token and channel (0600 in a 0700 folder) once every check has passed."""
    saved = replace(channel, connected_at=_iso(now))
    _private_dir(folder)
    _write_private(folder / TOKEN_FILE, credentials.to_json())
    record = {"id": saved.id, "title": saved.title, "connected_at": saved.connected_at}
    _write_private(folder / CHANNEL_FILE, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return saved


def _oauth_reason(exc: Exception) -> str:
    """A safe reason: oauthlib's fixed error code, never the exception text."""
    error = getattr(exc, "error", None)
    if isinstance(error, str) and error.replace("_", "").isalnum():
        return f"Google said {error}"
    return "Google did not accept the sign-in"


def _default_client(credentials: Any) -> YouTubeClient:
    return GoogleYouTube(credentials)


# --- consent --------------------------------------------------------------------------------


def begin_consent(
    folder: Path, redirect_uri: str, scopes: tuple[str, ...] | list[str], now: float
) -> Pending:
    from google_auth_oauthlib.flow import Flow

    ok, why = client_status(folder)
    if not ok:
        raise ConnectError(why)
    flow = Flow.from_client_secrets_file(
        str(Path(folder) / CLIENT_FILE),
        scopes=list(scopes),
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="false"
    )
    return Pending(auth_url, state, flow.code_verifier, redirect_uri, now)


def _default_flow(client_file: Path, scopes: tuple[str, ...], pending: Pending) -> Any:
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(
        str(client_file),
        scopes=list(scopes),
        state=pending.state,
        redirect_uri=pending.redirect_uri,
        code_verifier=pending.code_verifier,
        autogenerate_code_verifier=False,
    )


def finish_consent(
    folder: Path,
    pending: Pending,
    query: dict[str, str],
    now: float,
    settings: YouTubeSettings,
    flow_factory: Callable[..., Any] | None = None,
    client_factory: Callable[[Any], YouTubeClient] | None = None,
) -> Channel:
    folder = Path(folder)
    if query.get("error"):
        raise ConnectError(f"Not connected: Google said {query['error']}")
    if not query.get("state") or query["state"] != pending.state:
        raise ConnectError("Not connected: the reply did not match this request")
    if now - pending.started > settings.consent_timeout_s:
        minutes = f"{settings.consent_timeout_s / 60:g}"
        raise ConnectError(f"Not connected: the consent took longer than {minutes} minutes")
    code = query.get("code")
    if not code:
        raise ConnectError("Not connected: Google sent no authorization code")
    flow = (flow_factory or _default_flow)(folder / CLIENT_FILE, settings.scopes, pending)
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
    except Exception as exc:  # never echo: oauthlib messages can carry the code
        raise ConnectError(f"Not connected: {_oauth_reason(exc)}") from None
    channel = _read_channel((client_factory or _default_client)(credentials))
    return _save_connection(folder, credentials, channel, now)


def _default_cli_flow(client_file: Path, scopes: tuple[str, ...]) -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_secrets_file(
        str(client_file), scopes=list(scopes), autogenerate_code_verifier=True
    )


def connect_cli(
    folder: Path,
    settings: YouTubeSettings,
    flow_factory: Callable[..., Any] | None = None,
    client_factory: Callable[[Any], YouTubeClient] | None = None,
) -> Channel:
    folder = Path(folder)
    ok, why = client_status(folder)
    if not ok:
        raise ConnectError(why)
    flow = (flow_factory or _default_cli_flow)(folder / CLIENT_FILE, settings.scopes)
    try:
        credentials = flow.run_local_server(
            port=0, timeout_seconds=settings.consent_timeout_s, open_browser=True
        )
    except Exception as exc:  # never echo: may carry the code
        raise ConnectError(f"Not connected: {_oauth_reason(exc)}") from None
    if credentials is None:
        raise ConnectError("Not connected: the consent did not complete")
    channel = _read_channel((client_factory or _default_client)(credentials))
    return _save_connection(folder, credentials, channel, time.time())


# --- stored connection ----------------------------------------------------------------------


def load_credentials(folder: Path, settings: YouTubeSettings, request: Any = None) -> Any:
    from google.auth.exceptions import RefreshError, TransportError
    from google.oauth2.credentials import Credentials

    path = Path(folder) / TOKEN_FILE
    if not path.is_file():
        raise ReconnectNeeded("Connect a channel first")
    try:
        creds = Credentials.from_authorized_user_file(str(path), list(settings.scopes))
    except (OSError, ValueError, KeyError, TypeError):
        raise ReconnectNeeded("Reconnect needed: the stored connection is unreadable") from None
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise ReconnectNeeded("Reconnect needed: Google no longer accepts this connection")
    if request is None:
        from google.auth.transport.requests import Request

        request = Request()
    try:
        creds.refresh(request)
    except RefreshError:
        raise ReconnectNeeded(
            "Reconnect needed: Google no longer accepts this connection"
        ) from None
    except TransportError:
        raise YouTubeError("Could not reach Google to check the connection") from None
    _write_private(path, creds.to_json())
    return creds


def connected_channel(folder: Path) -> Channel | None:
    folder = Path(folder)
    if not (folder / TOKEN_FILE).is_file():
        return None
    try:
        data = json.loads((folder / CHANNEL_FILE).read_text(encoding="utf-8"))
        return Channel(id=data["id"], title=data["title"], connected_at=data["connected_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _post_revoke(token: str) -> None:
    import requests

    requests.post(REVOKE_URL, data={"token": token}, timeout=10)


def disconnect(folder: Path, revoke: Callable[[str], None] | None = None) -> None:
    """Revoke the stored token with Google (best effort), then delete it and the channel."""
    folder = Path(folder)
    token_path = folder / TOKEN_FILE
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
        token = data.get("refresh_token") or data.get("token")
        if token:
            (revoke or _post_revoke)(token)
    except Exception:  # noqa: S110 - best effort; the files are deleted regardless
        pass
    token_path.unlink(missing_ok=True)
    (folder / CHANNEL_FILE).unlink(missing_ok=True)


# --- Google client --------------------------------------------------------------------------


class _GoogleUpload:
    def __init__(self, request: Any) -> None:
        self._request = request

    def next_chunk(self) -> tuple[float | None, str | None]:
        status, response = self._request.next_chunk()
        if response is not None:
            return 1.0, response["id"]
        return (status.progress() if status else None), None


class GoogleYouTube:
    """The real YouTube Data API v3 behind `YouTubeClient`."""

    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self.service = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    def channel(self) -> Channel | None:
        reply = self.service.channels().list(part="snippet", mine=True).execute()
        items = reply.get("items") or []
        if not items:
            return None
        item = items[0]
        return Channel(id=item["id"], title=item["snippet"]["title"], connected_at="")

    def start_upload(self, path: Path, body: dict, chunk_bytes: int) -> Upload:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            str(path), chunksize=chunk_bytes, resumable=True, mimetype="video/mp4"
        )
        request = self.service.videos().insert(part="snippet,status", body=body, media_body=media)
        return _GoogleUpload(request)

    def set_thumbnail(self, video_id: str, path: Path) -> None:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(path), mimetype="image/jpeg")
        self.service.thumbnails().set(videoId=video_id, media_body=media).execute()

    def video_exists(self, video_id: str) -> bool:
        reply = self.service.videos().list(part="id", id=video_id).execute()
        return bool(reply.get("items"))


# --- upload ---------------------------------------------------------------------------------


def video_body(text: dict, full_description: str, category_id: str, language: str) -> dict:
    return {
        "snippet": {
            "title": text["title"],
            "description": full_description,
            "tags": list(text.get("tags", [])),
            "categoryId": category_id,
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {"privacyStatus": _PRIVACY, "selfDeclaredMadeForKids": _MADE_FOR_KIDS},
    }


def _http_parts(exc: HttpError) -> tuple[str, str, str | None]:
    """(message, reason, location) from YouTube's error JSON."""
    message, reason, location = exc.reason or f"HTTP {exc.resp.status}", "", None
    try:
        error = json.loads(exc.content.decode("utf-8"))["error"]
        message = error.get("message") or message
        first = (error.get("errors") or [{}])[0]
        reason = first.get("reason", "")
        location = first.get("location") or None
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        pass
    return message, reason, location


def _refusal(exc: HttpError) -> YouTubeError:
    message, reason, location = _http_parts(exc)
    if exc.resp.status == 403 and reason in _QUOTA_REASONS:
        return QuotaExceeded()
    return UploadError(f"YouTube refused the upload: {message}", field=location)


def upload_video(
    client: YouTubeClient,
    path: Path,
    body: dict,
    settings: YouTubeSettings,
    on_progress: Callable[[int], None],
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Send the video in resumable chunks and return its id; a dropped connection or a 5xx
    resumes the same upload up to `settings.max_resumes` times (backoff 1, 2, 4… s)."""
    upload = client.start_upload(path, body, settings.chunk_mb * 1024 * 1024)
    resumes = 0
    shown = 0
    on_progress(0)
    while True:
        try:
            fraction, video_id = upload.next_chunk()
        except HttpError as exc:
            if exc.resp.status not in _RESUME_STATUSES:
                raise _refusal(exc) from None
            message, _, _ = _http_parts(exc)
            last = f"HTTP {exc.resp.status} {message}"
        except _TRANSPORT_ERRORS as exc:
            last = str(exc) or type(exc).__name__
        else:
            percent = 100 if video_id else (None if fraction is None else int(fraction * 100))
            if percent is not None:
                percent = max(0, min(percent, 100))
                if percent != shown:
                    shown = percent
                    on_progress(percent)
            if video_id:
                return video_id
            continue
        if resumes >= settings.max_resumes:
            raise UploadError(f"upload failed after {resumes} retries: {last}")
        sleep(2**resumes)
        resumes += 1


# --- thumbnail ------------------------------------------------------------------------------


def make_thumbnail(
    cover_png: Path, out_jpg: Path, render_cfg: RenderConfig, settings: YouTubeSettings
) -> Path:
    """The video's look (whole cover over a blurred darkened copy) as a JPEG thumbnail, lowering
    quality from 90 until it fits `settings.thumbnail_max_bytes`."""
    out_jpg = Path(out_jpg)
    with Image.open(cover_png) as im:
        picture = im.convert("RGB")
    backdrop, size, offset = canvas_parts(picture, render_cfg)
    backdrop.paste(picture.resize(size, Image.Resampling.LANCZOS), offset)
    thumb = backdrop.resize(settings.thumbnail_size, Image.Resampling.LANCZOS)
    for quality in _THUMB_QUALITIES:
        buffer = io.BytesIO()
        thumb.save(buffer, "JPEG", quality=quality, optimize=True)
        if buffer.tell() <= settings.thumbnail_max_bytes:
            out_jpg.parent.mkdir(parents=True, exist_ok=True)
            tmp = out_jpg.with_name(out_jpg.name + ".tmp")
            try:
                tmp.write_bytes(buffer.getvalue())
                tmp.replace(out_jpg)
            finally:
                tmp.unlink(missing_ok=True)
            return out_jpg
    raise YouTubeError(
        f"{cover_png}: thumbnail stays over {settings.thumbnail_max_bytes} bytes "
        f"even at JPEG quality {_THUMB_QUALITIES[-1]}"
    )
