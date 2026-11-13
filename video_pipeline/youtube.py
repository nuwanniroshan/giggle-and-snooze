"""YouTube upload helpers (spec 024).

Settings, chapters from the render record, text rules and the small JSON records kept per
book; Codex writing the text (phase 4) and the CLI:

    uv run python -m video_pipeline.youtube text --book <slug> [--root R] [--force]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from video_pipeline.agents import (
    AgentResult,
    AgentUnavailable,
    build_codex_command,
    codex_status,
    run_codex,
)
from video_pipeline.contract import ConfigError, find_disallowed, load_disallowed_terms
from video_pipeline.episode_tools import write_atomic
from video_pipeline.file_hash import sha256_file
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder

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


# --- Codex writes the text (phase 4: Story 2, FR-2, FR-3) ---------------------------------

TEXT_PROMPT = """You write the YouTube title, description and tags for one read-aloud video \
of a children's picture book in the {series} series.

Book title: {title}
Subtitle: {subtitle}
Blurb: {blurb}
Age range: {age_range}
{mood}
The story, page by page:
{pages}

Write, in your own words, for THIS book only:
- title: must contain the exact book title {title!r}; at most {title_max} characters.
- description: 2-4 short paragraphs, warm and written for parents, about this story and why it \
makes a calm bedtime read-aloud.
- tags: 8-15 short tags.
No links, no email addresses, no '<' or '>', and no calls to like, subscribe, comment or buy. \
Do not add chapters or the website: the pipeline adds them.
Save with save_youtube_text(title, description, tags); fix every error it returns and save \
again (at most 3 saves). Finish with one short line."""


@dataclass(frozen=True)
class TextOutcome:
    status: Literal["written", "failed"]
    problems: list[str] = field(default_factory=list)
    attempts: int = 0


def _mood_text(mood: dict | None) -> str:
    if not isinstance(mood, dict):
        return ""
    parts = [str(mood[k]).strip() for k in ("label", "summary") if mood.get(k)]
    return f"Mood: {'. '.join(p.rstrip('.') for p in parts)}.\n" if parts else ""


def build_prompt(book: dict, blueprint_mood: dict | None, settings: YouTubeSettings) -> str:
    meta = book.get("metadata", {})
    pages = []
    for page in book.get("pages", []):
        heading = f"Page {page.get('page_number')}"
        if page.get("title"):
            heading += f" - {page['title']}"
        pages.append(f"{heading}: {page.get('text', '')}")
    return TEXT_PROMPT.format(
        series=settings.series,
        title=meta.get("title", ""),
        subtitle=meta.get("subtitle") or "(none)",
        blurb=meta.get("blurb") or "(none)",
        age_range=meta.get("age_range") or "(not given)",
        mood=_mood_text(blueprint_mood),
        pages="\n".join(pages),
        title_max=settings.title_max_chars,
    )


def _read_mood(folder: BookFolder) -> dict | None:
    try:
        data = json.loads(folder.blueprint_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data.get("mood") if isinstance(data, dict) else None


def _render_check(folder: BookFolder, root: Path) -> tuple[str, str]:
    from video_pipeline.render_episode import render_check_detail  # heavy; load when used

    return render_check_detail(folder, root)


def _refusals(result: AgentResult) -> list[str]:
    errors = [
        event.data["errors"]
        for event in result.events
        if event.kind == "tool-result" and isinstance(event.data, dict) and event.data.get("errors")
    ]
    return [str(e) for e in errors[-1]] if errors else [result.reason or "the agent saved nothing"]


def _video_record(folder: BookFolder) -> Path:
    return folder.build_dir / "video" / f"{folder.slug}.json"


def write_youtube_text(
    folder: BookFolder,
    root: Path,
    *,
    force: bool,
    provider: Callable[..., AgentResult] | None = None,
    binary: Path | None = None,
    check: Callable[[BookFolder, Path], tuple[str, str]] | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> TextOutcome:
    """Have Codex write the book's YouTube text, refusing drafts with every FR-3 problem and
    feeding them back, until one is saved or the attempts run out (nothing written then)."""
    root = Path(root)
    state, reason = (check or _render_check)(folder, root)
    if state != "up_to_date":
        detail = f": {reason}" if reason else ""
        raise YouTubeError(
            f"the video is {state}{detail}; render it first (render_episode --book {folder.slug})"
        )
    paths = YouTubePaths(folder)
    if paths.text.exists() and not force:
        raise YouTubeError(
            "YouTube text exists; use --force to rewrite it (the old one is kept as text.json.bak)"
        )
    settings = load_settings(root / "config")
    terms = load_disallowed_terms(root / "config" / "disallowed_terms.txt")
    try:
        book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise YouTubeError(f"book data not found: {folder.book_json}") from None
    record_path = _video_record(folder)
    record = _read_json(record_path)
    if record is None:
        raise YouTubeError(f"no video record at {record_path}; render the video first")
    chapters = chapters_from_record(record, book, settings)
    duration = float(record.get("duration", 0.0))

    binary = binary or codex_status()
    base = build_prompt(book, _read_mood(folder), settings)
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kiddo-youtube-") as tmp:
        out = Path(tmp) / "text.json"
        for attempt in range(1, settings.codex_attempts + 1):
            out.unlink(missing_ok=True)
            prompt = base
            if problems:
                prompt += "\nThe last attempt was refused. Fix these problems and submit again:\n"
                prompt += "\n".join(f"- {problem}" for problem in problems[:20])
            command = build_codex_command(
                binary,
                sys.executable,
                [
                    "-m",
                    "video_pipeline.youtube_mcp",
                    "--book-json",
                    str(folder.book_json),
                    "--record",
                    str(record_path),
                    "--out",
                    str(out),
                    "--root",
                    str(root),
                ],
                {"PYTHONPATH": str(PROJECT_ROOT)},
                prompt=prompt,
            )
            stamp = now().strftime("%Y%m%d-%H%M%S")
            log = folder.build_dir / "agent" / f"{stamp}-youtube-{attempt}.jsonl"
            result = (provider or run_codex)(command, log, timeout_s=settings.codex_time_limit_s)
            if not out.exists():
                problems = _refusals(result)
                continue
            try:
                text = read_text(out)  # the tool validated it; check again on the way in
            except YouTubeError as exc:
                problems = [str(exc)]
                continue
            problems = text_problems(text or {}, book, chapters, duration, settings, terms)
            if not problems:
                write_text(paths, text, keep_backup=True)
                return TextOutcome("written", [], attempt)
    return TextOutcome("failed", problems, settings.codex_attempts)


# --- CLI ------------------------------------------------------------------------------------


def _cmd_text(args: argparse.Namespace) -> int:
    folder = book_folder(args.book, args.root)
    outcome = write_youtube_text(folder, Path(args.root), force=args.force)
    if outcome.status != "written":
        print(
            f"error: Codex did not write usable YouTube text in {outcome.attempts} attempts:",
            file=sys.stderr,
        )
        for problem in outcome.problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    text = read_text(YouTubePaths(folder).text) or {}
    print(f"Wrote books/{folder.slug}/youtube/text.json")
    print(f"Title: {text.get('title', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.youtube",
        description="Publish a book's video on YouTube (spec 024).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    text = commands.add_parser("text", help="have Codex write the title, description and tags")
    text.add_argument("--book", required=True)
    text.add_argument("--root", type=Path, default=PROJECT_ROOT)
    text.add_argument("--force", action="store_true", help="rewrite (the old one kept as .bak)")
    text.set_defaults(run=_cmd_text)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (YouTubeError, AgentUnavailable, LibraryError, ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
