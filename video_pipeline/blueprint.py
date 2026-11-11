"""Per-book blueprint (spec 011): schema, validation, distinctness, hashing, placement."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from video_pipeline.contract import find_disallowed
from video_pipeline.episode_tools import write_atomic
from video_pipeline.file_hash import sha256_file

BLUEPRINT_FILE = "blueprint.json"
USED_FILE = "blueprint_used.json"


class BlueprintError(Exception):
    """A blueprint file is missing, unreadable or invalid; the message names file and field."""


def _text(n: int) -> type[str]:
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=n)]  # type: ignore[return-value]


Key = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=40)
]
Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Role(_M):
    pace: Literal["slow", "steady", "brisk"]
    energy: Literal["hushed", "calm", "lively", "bouncy"]
    quirk: _text(200)


class Roles(_M):
    storyteller: Role
    kid_older: Role
    kid_younger: Role


class Mood(_M):
    key: Key
    label: _text(40)
    summary: _text(300)
    roles: Roles


class Swatch(_M):
    name: _text(30)
    hex: Hex


class Art(_M):
    medium_key: Key
    medium_label: _text(40)
    palette: Annotated[list[Swatch], Field(min_length=5, max_length=5)]
    line: _text(200)
    lighting: _text(200)
    composition: _text(200)
    style_prompt_prefix: _text(600)
    avoid: Annotated[list[_text(80)], Field(max_length=6)] = []


class KidNotes(_M):
    kid_older: _text(200)
    kid_younger: _text(200)


class Qa(_M):
    format_key: Key
    format_label: _text(40)
    question_types: Annotated[list[_text(40)], Field(min_length=2, max_length=4)]
    intro_hook: _text(200)
    outro_hook: _text(200)
    moments_min: Annotated[int, Field(ge=1, le=12)]
    moments_max: Annotated[int, Field(ge=1, le=12)]
    kid_notes: KidNotes

    @model_validator(mode="after")
    def _ordered(self) -> Qa:
        if self.moments_min > self.moments_max:
            raise ValueError("moments_min must not be greater than moments_max")
        return self


class BpSound(_M):
    ambience: Annotated[list[str], Field(min_length=1, max_length=6)]
    sfx: Annotated[list[str], Field(max_length=6)] = []
    music_direction: _text(200)


class Animation(_M):
    intensity: Annotated[float, Field(ge=0.25, le=2.0)]
    effects: Annotated[list[str], Field(max_length=4)] = []


class Blueprint(_M):
    version: Literal[1]
    mood: Mood
    art: Art
    qa: Qa
    sound: BpSound
    animation: Animation
    rationale: _text(400)


@dataclass(frozen=True)
class Context:
    terms: list[str]
    gentle: list[str]
    sounds: dict[str, str]  # library name -> kind
    effects: frozenset[str]


@dataclass(frozen=True)
class BlueprintConfig:
    distance: float
    medium_synonyms: dict[str, str]
    mood_synonyms: dict[str, str]
    format_synonyms: dict[str, str]


@dataclass(frozen=True)
class Ambience:
    intro: list[str]
    pages: list[list[str]]
    outro: list[str]


def _texts(node: object, path: str = "") -> list[tuple[str, str]]:
    """Every string in the data with its dotted field path (`art.avoid.0`)."""
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in _texts(v, f"{path}.{k}" if path else str(k))]
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in _texts(v, f"{path}.{i}" if path else str(i))]
    return []


def blueprint_errors(data: object, ctx: Context, where: str) -> list[str]:
    """Every rule of FR-1 and FR-3; each message starts with `where` (the file) and the field."""
    try:
        model = Blueprint.model_validate(data)
    except ValidationError as exc:
        return [f"{where}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    for field, text in _texts(model.model_dump()):
        errors += [
            f"{where}: {field}: '{t}' is not allowed in a children's story"
            for t in find_disallowed(text, ctx.terms)
        ]
        errors += [
            f"{where}: {field}: '{t}' is too frightening for this story"
            for t in find_disallowed(text, ctx.gentle)
        ]
    for name in model.sound.ambience:
        if ctx.sounds.get(name) != "ambience":
            errors.append(
                f"{where}: sound.ambience: {name!r} is not an ambience sound in the library"
            )
    for name in model.sound.sfx:
        if ctx.sounds.get(name) != "sfx":
            errors.append(f"{where}: sound.sfx: {name!r} is not an sfx sound in the library")
    for name in model.animation.effects:
        if name not in ctx.effects:
            errors.append(f"{where}: animation.effects: {name!r} is not a known effect")
    return errors


def load_blueprint(path: Path, ctx: Context) -> Blueprint:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BlueprintError(f"{path}: blueprint not found") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BlueprintError(f"{path}: not valid JSON ({exc})") from None
    errors = blueprint_errors(data, ctx, str(path))
    if errors:
        raise BlueprintError(errors[0])
    return Blueprint.model_validate(data)


def load_config(config_dir: Path) -> BlueprintConfig:
    path = Path(config_dir) / "blueprint.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))

        def flip(table: dict) -> dict[str, str]:
            return {syn: canon for canon, syns in table.items() for syn in syns}

        return BlueprintConfig(
            float(raw["distance"]),
            flip(raw["mediums"]),
            flip(raw["moods"]),
            flip(raw["formats"]),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BlueprintError(f"{path}: blueprint settings missing or invalid ({exc})") from None


def canonical(key: str, synonyms: dict[str, str]) -> str:
    return synonyms.get(key, key)


def _lab(hex_colour: str) -> tuple[float, float, float]:
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e76(hex_a: str, hex_b: str) -> float:
    return math.dist(_lab(hex_a), _lab(hex_b))


def similarity_problems(
    candidate: Blueprint, others: dict[str, Blueprint], cfg: BlueprintConfig
) -> list[str]:
    """All failed distinctness checks (FR-4) against every other blueprint, in one list."""
    problems: list[str] = []
    cm = canonical(candidate.art.medium_key, cfg.medium_synonyms)
    cmood = canonical(candidate.mood.key, cfg.mood_synonyms)
    cf = canonical(candidate.qa.format_key, cfg.format_synonyms)
    for label, other in others.items():
        if cm == canonical(other.art.medium_key, cfg.medium_synonyms) and cmood == canonical(
            other.mood.key, cfg.mood_synonyms
        ):
            problems.append(
                f"too close to '{label}': same art.medium_key '{cm}' and mood.key '{cmood}'"
            )
        if cf == canonical(other.qa.format_key, cfg.format_synonyms):
            problems.append(f"too close to '{label}': same qa.format_key '{cf}'")
        close = [
            s.name
            for s in candidate.art.palette
            if any(delta_e76(s.hex, o.hex) <= cfg.distance for o in other.art.palette)
        ]
        if len(close) >= 3:
            problems.append(
                f"too close to '{label}': palette colours {', '.join(close)} are nearly the same"
            )
    return problems


def assign_ambience(names: list[str], page_count: int) -> Ambience:
    n = len(names)
    pages = [[names[i * n // page_count]] for i in range(page_count)]
    return Ambience([names[0]], pages, [names[-1]])


def clamp_range(lo: int, hi: int, low: int, high: int) -> tuple[int, int]:
    """The blueprint range [lo, hi] clamped into the spec 001 bounds [low, high]."""
    a, b = max(lo, low), min(hi, high)
    if a <= b:
        return a, b
    edge = low if hi < low else high
    return edge, edge


def style_text(bp: Blueprint) -> str:
    palette = ", ".join(f"{s.name} {s.hex}" for s in bp.art.palette)
    parts = [
        bp.art.style_prompt_prefix,
        f"Palette: {palette}.",
        f"Line: {bp.art.line}.",
        f"Lighting: {bp.art.lighting}.",
        f"Composition: {bp.art.composition}.",
    ]
    if bp.art.avoid:
        parts.append("Avoid: " + "; ".join(bp.art.avoid) + ".")
    return " ".join(parts)


def blueprint_sha256(path: Path) -> str:
    return sha256_file(path)


def read_use(folder) -> dict:
    try:
        data = json.loads(folder.used_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_use(folder, stage: str, sha: str) -> None:
    write_atomic(folder.used_path, {**read_use(folder), stage: sha})


def rebuild_style_bible(folder, bp: Blueprint | None = None) -> None:
    """AC-3.4: set only style_bible.style_prompt_prefix in book.json."""
    if bp is None:
        bp = load_blueprint(folder.blueprint_path, load_context(folder.root))
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    book.setdefault("style_bible", {})["style_prompt_prefix"] = bp.art.style_prompt_prefix
    write_atomic(folder.book_json, book)


def load_context(root: Path) -> Context:
    from video_pipeline.contract import load_disallowed_terms
    from video_pipeline.sound_library import load_library
    from video_pipeline.story_tools import load_gentle_terms

    root = Path(root)
    cfg = root / "config"
    render = json.loads((cfg / "render.json").read_text(encoding="utf-8"))
    library = load_library(root / "assets" / "sound_library")
    return Context(
        load_disallowed_terms(cfg / "disallowed_terms.txt"),
        load_gentle_terms(cfg / "gentle_terms.txt"),
        {name: sound.kind for name, sound in library.sounds.items()},
        frozenset(render["animation"]["effects"]),
    )


def _book_title(folder) -> str:
    try:
        data = json.loads(folder.book_json.read_text(encoding="utf-8"))
        title = data["metadata"]["title"]
    except (OSError, ValueError, KeyError, TypeError):
        return folder.slug
    return title.strip() if isinstance(title, str) and title.strip() else folder.slug


def others_for(
    root: Path, ctx: Context, exclude: tuple[str, str] | None = None
) -> dict[str, Blueprint]:
    """Blueprints of every book and every draft, keyed by the book's title (a draft's idea
    title); `exclude` is ("book", slug) or ("draft", id) (AC-2.8, AC-2.9). Titles shared by
    several blueprints get ` (<slug>)` or ` (draft <id start>)` added so none is lost.
    Unreadable files are skipped."""
    from video_pipeline.drafts import DraftStore
    from video_pipeline.library import BookFolder, list_book_slugs

    entries: list[tuple[str, str, Blueprint]] = []  # (title, suffix, blueprint)
    slugs, _ = list_book_slugs(root)
    for slug in slugs:
        if exclude == ("book", slug):
            continue
        folder = BookFolder(slug, Path(root))
        try:
            entries.append((_book_title(folder), slug, load_blueprint(folder.blueprint_path, ctx)))
        except BlueprintError:
            continue
    store = DraftStore(root)
    for record in store.list():
        draft_id = record.get("id")
        if not draft_id or exclude == ("draft", draft_id):
            continue
        title = (record.get("idea") or {}).get("title") or record.get("title") or draft_id
        try:
            loaded = load_blueprint(store.draft_folder(draft_id) / BLUEPRINT_FILE, ctx)
        except BlueprintError:
            continue
        entries.append((title, f"draft {draft_id[:6]}", loaded))
    counts: dict[str, int] = {}
    for title, _, _ in entries:
        counts[title] = counts.get(title, 0) + 1
    found: dict[str, Blueprint] = {}
    for title, suffix, loaded in entries:
        label = title if counts[title] == 1 else f"{title} ({suffix})"
        while label in found:  # a suffixed label can still collide with another title
            label += f" ({suffix})"
        found[label] = loaded
    return found
