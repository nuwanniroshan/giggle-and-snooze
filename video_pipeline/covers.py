"""Cover settings and the titled-cover marker (spec 022).

Pure: no model, image or `illustrate` imports, so render, print build and studio can all use it.
New books carry `metadata.cover_style = "titled"` (set from `config/book_defaults.json`); older
books lack it and keep their old covers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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
