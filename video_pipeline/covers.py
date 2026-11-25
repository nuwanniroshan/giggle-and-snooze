"""Titled Codex covers (spec 022): settings, the marker, requests, trimming and read-back.

Pure: no subprocess and no `illustrate` import, so render, print build and studio can all use it.
New books carry `metadata.cover_style = "titled"` (set from `config/book_defaults.json`); older
books lack it and keep their old covers.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from video_pipeline.characters import cover_characters_text

TITLED_STEMS = ("cover_front", "cover_front_print")
SETTINGS_FILE = "covers.json"


class CoverError(Exception):
    """A cover setting, shape or reading problem; the message names the file or field."""


@dataclass(frozen=True)
class CoverShape:
    label: str
    ratio: tuple[int, int]
    accept: tuple[float, float | None]  # allowed width/height of the painted picture (None = open)
    frame: str


@dataclass(frozen=True)
class CoverSettings:
    logo: Path  # relative to the project root
    logo_name: str
    logo_description: str  # how the logo looks, quoted in every cover request
    logo_width_pct: int
    safe_side_pct: int
    safe_top_bottom_pct: int
    tries: int
    shapes: dict[str, CoverShape]  # keyed by stem
    layout_rules: list[str]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CoverError(f"{where}: '{key}' must be a non-empty string")
    return value


def _int(data: dict, key: str, where: str, low: int, high: int) -> int:
    value = data.get(key)
    if not _is_int(value) or not low <= value <= high:
        raise CoverError(f"{where}: '{key}' must be a whole number from {low} to {high}")
    return value


def _shape(stem: str, data: object, where: str) -> CoverShape:
    here = f"{where}: shapes.{stem}"
    if not isinstance(data, dict):
        raise CoverError(f"{here} must be an object")
    ratio = data.get("ratio")
    if not (
        isinstance(ratio, list) and len(ratio) == 2 and all(_is_int(v) and v > 0 for v in ratio)
    ):
        raise CoverError(f"{here}.ratio must be two positive whole numbers")
    accept = data.get("accept")
    if not (
        isinstance(accept, list)
        and len(accept) == 2
        and _is_number(accept[0])
        and accept[0] > 0
        and (accept[1] is None or (_is_number(accept[1]) and accept[1] >= accept[0]))
    ):
        raise CoverError(f"{here}.accept must be [low, high] with high a number or null")
    return CoverShape(
        label=_text(data, "label", here),
        ratio=(ratio[0], ratio[1]),
        accept=(float(accept[0]), None if accept[1] is None else float(accept[1])),
        frame=_text(data, "frame", here),
    )


def load_cover_settings(config_dir: Path) -> CoverSettings:
    """Read `config_dir/covers.json`; a CoverError names the file and the bad or missing field."""
    path = Path(config_dir) / SETTINGS_FILE
    where = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CoverError(f"cover settings not found: {path}") from None
    except (OSError, ValueError) as exc:
        raise CoverError(f"cover settings not readable as JSON: {path} ({exc})") from None
    if not isinstance(data, dict):
        raise CoverError(f"{where}: must be a JSON object")
    shapes = data.get("shapes")
    if not isinstance(shapes, dict) or not shapes:
        raise CoverError(f"{where}: 'shapes' must be an object with at least one cover shape")
    rules = data.get("layout_rules")
    if not isinstance(rules, list) or not rules or not all(isinstance(r, str) for r in rules):
        raise CoverError(f"{where}: 'layout_rules' must be a list of strings")
    return CoverSettings(
        logo=Path(_text(data, "logo", where)),
        logo_name=_text(data, "logo_name", where),
        logo_description=_text(data, "logo_description", where),
        logo_width_pct=_int(data, "logo_width_pct", where, 1, 50),
        safe_side_pct=_int(data, "safe_side_pct", where, 0, 40),
        safe_top_bottom_pct=_int(data, "safe_top_bottom_pct", where, 0, 40),
        tries=_int(data, "tries", where, 1, 10),
        shapes={stem: _shape(stem, shape, where) for stem, shape in shapes.items()},
        layout_rules=list(rules),
    )


def titled_covers(book: dict) -> bool:
    """True for books marked `metadata.cover_style = "titled"` (new books only)."""
    return (book.get("metadata") or {}).get("cover_style") == "titled"


# --- Cover requests (FR-2, FR-3) ---------------------------------------------------------------

CODEX_FLAGS = (
    "exec",
    "--ephemeral",
    "--skip-git-repo-check",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox",
    "workspace-write",
    "-c",
    'web_search="disabled"',
)


def image_codex_flags() -> list[str]:
    flags = list(CODEX_FLAGS)
    # Ignoring user config also drops windows.sandbox. Without a backend,
    # Codex on Windows downgrades workspace-write to read-only.
    if sys.platform == "win32":
        flags += ["-c", 'windows.sandbox="elevated"']
    return flags


PAINT_TASK = (
    "Use the image generation tool to create this picture. Reply with only the absolute local "
    "path of the generated image. Do not use the shell to copy it to ./out.png; "
    "the calling pipeline will copy the image itself."
)


def _art_block(book: dict, art: dict | None) -> str:
    if art:
        palette = ", ".join(f"{c['name']} {c['hex']}" for c in art.get("palette") or [])
        avoid = ", ".join(art.get("avoid") or [])
        return (
            "Art style (the book's own direction, use it for the picture AND the lettering):\n"
            f"{art['style_prompt_prefix']}\n"
            f"Medium: {art['medium_label']}. Line: {art['line']} Lighting: {art['lighting']}\n"
            f"Palette: {palette}. Avoid: {avoid}."
        )
    prefix = (book.get("style_bible") or {}).get("style_prompt_prefix")
    if not prefix:
        return ""
    return f"Art style (use it for the picture AND the lettering):\n{prefix}"


def _logo_rule(settings: CoverSettings) -> str:
    return (
        f'- Do not add the "{settings.logo_name}" (Giggle and Snooze) logo, channel name, '
        "publisher mark, branding badge or watermark anywhere on the cover. "
        f"Keep the title, subtitle and main character at least {settings.safe_side_pct}% of the finished "
        "cover's width away from the left and right edges and at least "
        f"{settings.safe_top_bottom_pct}% from the top and bottom edges."
    )


def cover_request(book: dict, art: dict | None, stem: str, settings: CoverSettings) -> str:
    """The Codex request for one titled cover, built from this book's data only."""
    shape = settings.shapes.get(stem)
    if shape is None:
        raise CoverError(f"no cover shape for '{stem}' in {SETTINGS_FILE}")
    meta = dict(book.get("metadata") or {})
    if stem == "cover_front":
        meta["subtitle"] = ""
    parts = [
        "Create the finished FRONT COVER of a children's picture book, "
        f"{shape.frame}. Keep everything important inside the safe area described below.",
        f'Book: "{meta.get("title", "")}" — subtitle "{meta.get("subtitle", "")}".\n'
        f"Story: {meta.get('blurb', '')}",
    ]
    art_block = _art_block(book, art)
    if art_block:
        parts.append(art_block)
    parts.append(
        "Reference image 1, when supplied, is a page from this book: "
        "keep the characters' looks exactly as shown there."
    )
    layout_rules = settings.layout_rules
    if stem == "cover_front":
        layout_rules = [
            "Place only the exact book title in the middle of the frame, horizontally and vertically centered as a balanced multiline block. No subtitle or other text.",
            "Large highly legible hand-painted brush lettering with imperfect edges, visible bristle breaks and light pencil strokes, matching the illustration. Keep characters and props clear of the title.",
            "Frame the central title with distinctive characters and sparse scenery; use vibrant local colours and fresh paper breathing room.",
        ]
    rules = [f"- {rule}" for rule in layout_rules] + [_logo_rule(settings)]
    parts.append("Cover layout, like a real published picture book cover:\n" + "\n".join(rules))
    looks = cover_characters_text(book)
    if looks:
        parts.append(looks)
    return "\n\n".join(parts)


def build_cover_command(
    binary: Path, request: str, logo: Path, reference: Path | None
) -> list[str]:
    """Paint a cover with only the story page attached; logo is a legacy unused argument."""
    command = [str(binary), *image_codex_flags()]
    if reference is not None:
        command += ["-i", str(reference)]
    return [*command, "--", f"{PAINT_TASK}\n\n{request}"]


# --- Trimming (FR-3) ---------------------------------------------------------------------------


def trim_to_ratio(path: Path, shape: CoverShape) -> None:
    """Centre-crop a painted cover evenly to exactly `shape.ratio`, in place (PNG, RGB).

    A picture whose width/height lies outside `shape.accept` is refused (CoverError)."""
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        aspect = width / height
        low, high = shape.accept
        if aspect < low or (high is not None and aspect > high):
            raise CoverError(
                f"{path.name}: came back {width}x{height}, outside the accepted shape for the "
                f"{shape.label}"
            )
        rw, rh = shape.ratio
        if width * rh == height * rw and im.mode == "RGB":
            return
        unit = min(width // rw, height // rh)
        new_w, new_h = unit * rw, unit * rh
        left, top = (width - new_w) // 2, (height - new_h) // 2
        cropped = im.convert("RGB").crop((left, top, left + new_w, top + new_h))
    cropped.save(path, format="PNG")


# --- Read-back check (FR-4) --------------------------------------------------------------------

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "logo_absent": {"type": "boolean"},
    },
    "required": ["title", "subtitle", "logo_absent"],
    "additionalProperties": False,
}


def build_check_command(
    binary: Path, cover: Path, schema: Path, reply: Path, logo_name: str
) -> list[str]:
    """Codex command that reads a trimmed cover back. The expected title is never sent."""
    task = (
        "Read the lettering on this picture-book cover and reply with JSON only:\n"
        '- "title": the main title exactly as lettered (line breaks as spaces; keep the spelling '
        "and punctuation exactly as you see them).\n"
        '- "subtitle": the smaller subtitle exactly as lettered ("" if there is none).\n'
        f'- "logo_absent": true only if the "{logo_name}" (Giggle and Snooze) logo and '
        "channel branding are entirely absent. Any complete or partial logo, publisher mark "
        "or branding badge makes this false."
    )
    return [
        str(binary),
        *image_codex_flags(),
        "--output-schema",
        str(schema),
        "-o",
        str(reply),
        "-i",
        str(cover),
        "--",
        task,
    ]


@dataclass(frozen=True)
class CoverReading:
    title: str
    subtitle: str
    logo_absent: bool


def parse_reading(text: str) -> CoverReading:
    """The reader's JSON reply (per CHECK_SCHEMA); anything else is a CoverError."""
    try:
        data = json.loads(text.strip())
    except ValueError:
        data = None
    if (
        not isinstance(data, dict)
        or set(data) != set(CHECK_SCHEMA["required"])
        or not isinstance(data["title"], str)
        or not isinstance(data["subtitle"], str)
        or not isinstance(data["logo_absent"], bool)
    ):
        raise CoverError(f"cover check gave no readable answer: {text.strip()[:200]!r}")
    return CoverReading(data["title"], data["subtitle"], data["logo_absent"])


QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def _norm(text: str) -> str:
    return " ".join(text.translate(QUOTES).split()).casefold()


def words_match(expected: str, read: str) -> bool:
    """Same words: case, line breaks, runs of spaces and curly quotes are ignored."""
    return _norm(expected) == _norm(read)


def check_problem(reading: CoverReading, title: str, subtitle: str) -> str | None:
    """None when the cover passes; else the first problem found."""
    if not words_match(title, reading.title):
        return f"title read as {reading.title!r}"
    if not words_match(subtitle, reading.subtitle):
        return f"subtitle read as {reading.subtitle!r}"
    if not reading.logo_absent:
        return "unwanted cover logo or branding"
    return None
