"""YouTube upload helpers (spec 024).

This phase is pure: settings, chapters from the render record, text rules and the small JSON
records kept per book. No network and no CLI yet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.contract import ConfigError, find_disallowed
from video_pipeline.episode_tools import write_atomic
from video_pipeline.file_hash import sha256_file
from video_pipeline.library import BookFolder

CONFIG_FILE = "youtube.json"
_PAGE_SECTION = re.compile(r"^page-(\d+)$")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LINK = re.compile(
    r"https?://|\bwww\.|\b[\w-]+\.(?:com|net|org|io|co|tv|app|ly|me)\b",
    re.IGNORECASE,
)


class YouTubeError(Exception):
    """A user-facing refusal. The message says what to fix."""


@dataclass(frozen=True)
class YouTubeSettings:
    series: str
    site_link: str
    site_line: str
    categories: dict[str, str]
    default_kind: str
    default_language: str
    chapter_labels: dict[str, str]
    min_chapter_s: float
    min_chapters: int
    title_max_chars: int
    description_max_bytes: int
    tags_max_chars: int
    scopes: tuple[str, ...]
    chunk_mb: int
    max_resumes: int
    consent_timeout_s: int
    codex_attempts: int
    codex_time_limit_s: int
    cta_terms: tuple[str, ...]
    thumbnail_size: tuple[int, int]
    thumbnail_max_bytes: int


@dataclass(frozen=True)
class Chapter:
    start: float
    label: str


# --- settings -------------------------------------------------------------------------------


def _bad(path: Path, field: str, why: str) -> ConfigError:
    return ConfigError(f"{path}: field {field!r} {why}")


def _get(data: dict, key: str, path: Path, kind: type | tuple[type, ...], what: str, where=""):
    name = f"{where}{key}"
    if key not in data:
        raise _bad(path, name, "is missing")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, kind):
        raise _bad(path, name, f"must be {what}")
    return value


def _str_list(data: dict, key: str, path: Path) -> tuple[str, ...]:
    value = _get(data, key, path, list, "a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise _bad(path, key, "must be a list of strings")
    return tuple(value)


def _str_map(data: dict, key: str, path: Path) -> dict[str, str]:
    value = _get(data, key, path, dict, "an object of strings")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise _bad(path, key, "must be an object of strings")
    return dict(value)


def load_settings(config_dir: Path) -> YouTubeSettings:
    path = Path(config_dir) / CONFIG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"YouTube configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"YouTube configuration is not valid JSON: {path} ({exc})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a JSON object")

    categories = _str_map(data, "categories", path)
    default_kind = _get(data, "default_kind", path, str, "a string")
    if default_kind not in categories:
        raise _bad(path, "default_kind", f"{default_kind!r} is not one of the categories")
    labels = _str_map(data, "chapter_labels", path)
    for key in ("intro", "outro"):
        if key not in labels:
            raise _bad(path, f"chapter_labels.{key}", "is missing")
    codex = _get(data, "codex", path, dict, "an object")
    thumb = _get(data, "thumbnail", path, dict, "an object")
    size = _get(thumb, "size", path, list, "a [width, height] list", "thumbnail.")
    if len(size) != 2 or not all(isinstance(n, int) and not isinstance(n, bool) for n in size):
        raise _bad(path, "thumbnail.size", "must be a [width, height] list of integers")

    return YouTubeSettings(
        series=_get(data, "series", path, str, "a string"),
        site_link=_get(data, "site_link", path, str, "a string"),
        site_line=_get(data, "site_line", path, str, "a string"),
        categories=categories,
        default_kind=default_kind,
        default_language=_get(data, "default_language", path, str, "a string"),
        chapter_labels=labels,
        min_chapter_s=_get(data, "min_chapter_s", path, (int, float), "a number"),
        min_chapters=_get(data, "min_chapters", path, int, "an integer"),
        title_max_chars=_get(data, "title_max_chars", path, int, "an integer"),
        description_max_bytes=_get(data, "description_max_bytes", path, int, "an integer"),
        tags_max_chars=_get(data, "tags_max_chars", path, int, "an integer"),
        scopes=_str_list(data, "scopes", path),
        chunk_mb=_get(data, "chunk_mb", path, int, "an integer"),
        max_resumes=_get(data, "max_resumes", path, int, "an integer"),
        consent_timeout_s=_get(data, "consent_timeout_s", path, int, "an integer"),
        codex_attempts=_get(codex, "attempts", path, int, "an integer", "codex."),
        codex_time_limit_s=_get(codex, "time_limit_s", path, int, "an integer", "codex."),
        cta_terms=_str_list(data, "cta_terms", path),
        thumbnail_size=(size[0], size[1]),
        thumbnail_max_bytes=_get(thumb, "max_bytes", path, int, "an integer", "thumbnail."),
    )


# --- chapters -------------------------------------------------------------------------------


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _page_label(book: dict, number: int) -> str:
    for page in book.get("pages", []):
        if page.get("page_number") == number:
            title = page.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
    return f"Page {number}"


def _merge_short(chapters: list[Chapter], duration: float, minimum: float) -> list[Chapter]:
    """Fold chapters shorter than `minimum` into their neighbour until none is left."""
    chapters = list(chapters)
    while len(chapters) > 1:
        ends = [c.start for c in chapters[1:]] + [duration]
        short = next(
            (i for i, c in enumerate(chapters) if ends[i] - c.start < minimum),
            None,
        )
        if short is None:
            break
        # a short chapter joins the one before it; a short first chapter absorbs the next
        del chapters[1 if short == 0 else short]
    return chapters


def chapters_from_record(record: dict, book: dict, settings: YouTubeSettings) -> list[Chapter]:
    raw = record.get("chapters")
    if not isinstance(raw, list) or not raw:
        raise YouTubeError(
            "the video record has no chapter times; re-render to record them "
            "(render_episode --book <slug> --force)"
        )
    chapters: list[Chapter] = []
    back_cover_start: float | None = None
    for item in raw:
        name, start = item["name"], float(item["start"])
        if name == "back-cover":
            back_cover_start = start
            continue
        if name == "intro":
            label = settings.chapter_labels["intro"]
        elif name == "outro":
            label = settings.chapter_labels["outro"]
            if back_cover_start is not None:
                start = back_cover_start
        elif match := _PAGE_SECTION.match(name):
            label = _page_label(book, int(match.group(1)))
        else:
            raise YouTubeError(f"the video record has an unknown section {name!r}")
        back_cover_start = None
        chapters.append(Chapter(start, label))
    if not chapters:
        raise YouTubeError("the video record has no chapter times to use")
    chapters[0] = Chapter(0.0, chapters[0].label)  # covers any channel clip before the intro
    duration = float(record.get("duration", 0.0))
    return _merge_short(chapters, duration, settings.min_chapter_s)


def chapter_problems(
    chapters: list[Chapter], duration: float, settings: YouTubeSettings
) -> list[str]:
    found: list[str] = []
    if len(chapters) < settings.min_chapters:
        found.append(
            f"chapters: need at least {settings.min_chapters} chapters for YouTube chapters "
            f"to show, have {len(chapters)}"
        )
    if chapters and chapters[0].start != 0:
        found.append("chapters: the first chapter must start at 0:00")
    starts = [c.start for c in chapters]
    if any(b <= a for a, b in zip(starts, starts[1:], strict=False)):
        found.append("chapters: start times must be strictly increasing")
    ends = starts[1:] + [duration]
    for chapter, end in zip(chapters, ends, strict=True):
        if end - chapter.start < settings.min_chapter_s:
            found.append(
                f"chapters: {chapter.label!r} at {format_timestamp(chapter.start)} is shorter "
                f"than {settings.min_chapter_s:g} seconds"
            )
    return found


# --- text -----------------------------------------------------------------------------------


def compose_description(body: str, chapters: list[Chapter], settings: YouTubeSettings) -> str:
    lines = [f"{format_timestamp(c.start)} {c.label}" for c in chapters]
    return body.strip() + "\n\n" + "\n".join(lines) + "\n\n" + settings.site_line


def tags_length(tags: list[str]) -> int:
    """YouTube's count: each tag, +2 when it has a space (it is quoted), plus the commas."""
    total = sum(len(tag) + (2 if " " in tag else 0) for tag in tags)
    return total + max(len(tags) - 1, 0)


def _field_problems(
    name: str, value: str, settings: YouTubeSettings, terms: list[str]
) -> list[str]:
    found: list[str] = []
    if "<" in value or ">" in value:
        found.append(f"{name}: must not contain '<' or '>'")
    for term in find_disallowed(value, terms):
        found.append(f"{name}: contains the disallowed term {term!r}")
    if _EMAIL.search(value):
        found.append(f"{name}: must not contain an email address")
    if _LINK.search(value):
        found.append(f"{name}: must not contain a link")
    for term in find_disallowed(value, list(settings.cta_terms)):
        found.append(f"{name}: contains the call to action {term!r}")
    return found


def text_problems(
    text: dict,
    book: dict,
    chapters: list[Chapter],
    duration: float,
    settings: YouTubeSettings,
    terms: list[str],
) -> list[str]:
    found: list[str] = []

    title = text.get("title")
    if not isinstance(title, str) or not title.strip():
        found.append("title: must be a non-empty string")
    else:
        if len(title) > settings.title_max_chars:
            found.append(
                f"title: {len(title)} characters is over the {settings.title_max_chars} limit"
            )
        book_title = str(book.get("metadata", {}).get("title", ""))
        if book_title and book_title.lower() not in title.lower():
            found.append(f"title: must contain the book title {book_title!r}")
        found += _field_problems("title", title, settings, terms)

    body = text.get("description")
    if not isinstance(body, str) or not body.strip():
        found.append("description: must be a non-empty string")
    else:
        size = len(compose_description(body, chapters, settings).encode("utf-8"))
        if size > settings.description_max_bytes:
            found.append(
                f"description: {size} bytes with chapters and site line is over the "
                f"{settings.description_max_bytes} byte limit"
            )
        found += _field_problems("description", body, settings, terms)

    tags = text.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        found.append("tags: must be a list of strings")
    else:
        if not tags:
            found.append("tags: must have at least one tag")
        if any(not t.strip() for t in tags):
            found.append("tags: a tag must not be empty")
        length = tags_length(tags)
        if length > settings.tags_max_chars:
            found.append(f"tags: {length} characters is over the {settings.tags_max_chars} limit")
        for tag in tags:
            found += _field_problems("tags", tag, settings, terms)

    return found + chapter_problems(chapters, duration, settings)


def category_for(book: dict, text: dict | None, settings: YouTubeSettings) -> str:
    if text and text.get("category_id"):
        return str(text["category_id"])
    kind = book.get("metadata", {}).get("content_kind") or settings.default_kind
    try:
        return settings.categories[kind]
    except KeyError:
        known = ", ".join(sorted(settings.categories))
        raise YouTubeError(f"unknown content_kind {kind!r}; use one of: {known}") from None


def language_for(book: dict, settings: YouTubeSettings) -> str:
    return book.get("metadata", {}).get("language") or settings.default_language


# --- records --------------------------------------------------------------------------------


@dataclass(frozen=True)
class YouTubePaths:
    folder: BookFolder

    @property
    def dir(self) -> Path:
        return self.folder.folder / "youtube"

    @property
    def text(self) -> Path:
        return self.dir / "text.json"

    @property
    def backup(self) -> Path:
        return self.dir / "text.json.bak"

    @property
    def uploads(self) -> Path:
        return self.dir / "uploads.json"

    @property
    def thumbnail(self) -> Path:
        return self.folder.build_dir / "youtube" / "thumbnail.jpg"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise YouTubeError(f"{path} is not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise YouTubeError(f"{path} must hold a JSON object")
    return data


def read_text(path: Path) -> dict | None:
    return _read_json(path)


def write_text(paths: YouTubePaths, text: dict, keep_backup: bool) -> None:
    if keep_backup and paths.text.is_file():
        paths.backup.parent.mkdir(parents=True, exist_ok=True)
        paths.backup.write_bytes(paths.text.read_bytes())
    write_atomic(paths.text, text)


def text_sha256(path: Path) -> str:
    return sha256_file(path)


def read_uploads(paths: YouTubePaths) -> list[dict]:
    data = _read_json(paths.uploads)
    return list(data.get("uploads", [])) if data else []


def append_upload(paths: YouTubePaths, entry: dict) -> None:
    write_atomic(paths.uploads, {"uploads": [*read_uploads(paths), entry]})


def last_upload(paths: YouTubePaths) -> dict | None:
    uploads = read_uploads(paths)
    return uploads[-1] if uploads else None


def mark_not_found(paths: YouTubePaths, video_id: str, checked_at: str) -> None:
    uploads = read_uploads(paths)
    hits = [u for u in uploads if u.get("video_id") == video_id]
    if not hits:
        raise YouTubeError(f"no upload record for video {video_id!r}")
    for entry in hits:
        entry["state"] = "not_found"
        entry["checked_at"] = checked_at
    write_atomic(paths.uploads, {"uploads": uploads})


def upload_state(paths: YouTubePaths, render_record: dict | None) -> str:
    last = last_upload(paths)
    if last is None:
        return "none"
    if last.get("state") == "not_found":
        return "not_found"
    if render_record is None or render_record.get("final_key") != last.get("final_key"):
        return "older_cut"
    return "current"
