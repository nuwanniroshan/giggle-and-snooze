# Implementation Plan: Per-book blueprint (spec 011 v1.1.0)

**Spec:** docs/specs/011-book-blueprint/spec.md (v1.1.0)
**Decisions:** docs/adr/011-llm-invented-blueprint.md (Accepted)
**Created:** 2026-11-11

## Global Constraints

- Python 3.12, `uv run pytest`, Ruff (line length 100, rules E,F,I,UP,B,SIM,PTH); no new dependency (pydantic, numpy, Pillow, mcp already used).
- `snake_case` keys; JSON written atomically with `indent=2`, `ensure_ascii=False` (`episode_tools.write_atomic`).
- Pure logic in pure functions with unit tests; no real Codex, FFmpeg or network in unit tests.
- A book without `blueprint.json` must produce identical requests, briefs, plans and cache keys (AC-3.2, AC-4.4, AC-5.3, AC-5.7, AC-7.1).
- Safety rules stay in code: kid-line length, allowed tags, disallowed/gentle terms, grounding (AC-4.3).
- `books/<slug>/blueprint.json` is committed; `drafts/` stays git-ignored.
- Every blueprint message names the file, the field and the reason (FR-3).
- One stage at a time in the studio (existing runner); attempts and per-attempt limit come from `config/story.json`.

## Pre-implementation gates

- **Simplicity (≤3 components):** (1) new `video_pipeline/blueprint.py` (pure core + config), (2) small edits to existing stages (story tools/CLI/drafts, illustrate, episode, llm, contract, generate_script), (3) studio. The writer reuses `story_mcp` (new mode `blueprint`), `StoryTools._submit` and `write_story.generate`; no new MCP server and no new dependency.
- **Anti-abstraction:** one `Blueprint` pydantic model is the single representation; no DTOs.
- **Integration-first:** Phase 0 writes the failing tests for the pure core and its interfaces before any code.

## Goal

A producer who picks an idea gets a validated, distinct `blueprint.json` first; the story, pictures, Q&A and ambience then follow it, edits are flagged stale, and books without a blueprint are untouched.

## Design notes (from reading the code)

- **Style today** lives in page prompts the story writer invents; `illustrate.plan_images` passes them with `STYLE_LINE`. So the blueprint's art text is prefixed to each image prompt in `plan_images` (signature of `paint` unchanged), and the story brief tells the writer the art direction and to name no medium.
- **Script writer output has no `ambience`** (`Banter` = intro, moments, outro). Spec v1.1.0 AC-5.5: `assemble_script` writes the lists, using `blueprint.assign_ambience`.
- **Q&A count today** falls out of `choose_qa_pages` + `qa_schedule_errors` bounds in `contract.py`. The blueprint range narrows the accepted counts (clamped to those bounds); with no range the RNG sequence is unchanged.
- **Staleness record:** a tiny `books/<slug>/blueprint_used.json` (`{"art": sha, "script": sha}`) written by `illustrate` (only when every picture was repainted) and `generate_script`. Not inside the script, so the voice hash (`file_hash.script_sha256`) is untouched.
- **Studio job chaining:** one subprocess `write_story blueprint --draft ID --then-book` designs then writes the story, so one job, log and Stop cover both (AC-1.1, AC-1.2, AC-1.7).
- **Draft states:** add `designing`, `design_failed`; a `phase` field (`design`|`story`) says what an `interrupted` draft was doing (AC-1.3). Drafts created before this spec have no `phase` and restart the story.
- **Job stages:** `blueprint` (draft, chained), `redesign` (subject `draft:<id>` or `book:<slug>`, not chained). Labels added to `STAGE_LABELS`.

## Files

New: `video_pipeline/blueprint.py`, `config/blueprint.json`, `tests/blueprint_helpers.py`, `tests/test_blueprint.py`, `tests/test_blueprint_job.py`, `tests/test_blueprint_consumers.py`, `tests/test_studio_blueprint.py`.
Edited: `video_pipeline/story_tools.py`, `story_mcp.py`, `write_story.py`, `drafts.py`, `library.py` (`BookFolder.blueprint_path`, `used_path`), `illustrate.py`, `episode.py`, `contract.py`, `llm.py`, `episode_tools.py`, `mcp_server.py`, `generate_script.py`, `studio/jobs.py`, `studio/books.py`, `studio/app.py`, `studio/templates/{book,draft}.html`, `CLAUDE.md`.

---

## Phase 0: Contracts and Tests First

**Implements:** FR-1, FR-3, FR-4 (pure core) | **Satisfies:** AC-2.1–2.4, 2.6–2.8, 5.4, 5.5 (function level), 6.7
**Files:** `tests/blueprint_helpers.py`, `tests/test_blueprint.py`
**Interfaces:** Consumes: nothing. Produces (tests pin these): `blueprint.Blueprint` (pydantic), `blueprint.Context(terms, gentle, sounds: dict[str,str], effects: frozenset[str])`, `blueprint.blueprint_errors(data, ctx, where) -> list[str]`, `blueprint.load_blueprint(path, ctx) -> Blueprint` (raises `BlueprintError`), `blueprint.load_config(config_dir) -> BlueprintConfig`, `blueprint.similarity_problems(candidate, others: dict[str, Blueprint], cfg) -> list[str]`, `blueprint.delta_e76(hex_a, hex_b) -> float`, `blueprint.assign_ambience(names, page_count) -> Ambience`, `blueprint.clamp_range(lo, hi, low, high) -> tuple[int,int]`.

Locks down the data shape and the distinctness/placement rules before any code exists.

- [ ] Write `tests/blueprint_helpers.py`:

```python
"""A valid blueprint dict and a context over the real sound library (spec 011)."""
from __future__ import annotations

import copy

from video_pipeline.blueprint import Context

VALID = {
    "version": 1,
    "mood": {
        "key": "sleepy-drift", "label": "Sleepy drift", "summary": "Slow, hushed, dreamy.",
        "roles": {
            "storyteller": {"pace": "slow", "energy": "hushed", "quirk": "Whispers the big moments."},
            "kid_older": {"pace": "steady", "energy": "calm", "quirk": "Yawns between guesses."},
            "kid_younger": {"pace": "slow", "energy": "calm", "quirk": "Hugs a pillow."},
        },
    },
    "art": {
        "medium_key": "chalk-pastel", "medium_label": "Chalk pastel",
        "palette": [
            {"name": "midnight", "hex": "#1b2a49"}, {"name": "moon", "hex": "#f4efd8"},
            {"name": "lantern", "hex": "#f2b441"}, {"name": "moss", "hex": "#5d8a66"},
            {"name": "rose", "hex": "#d98c9a"},
        ],
        "line": "soft smudged edges", "lighting": "moonlight and lantern glow",
        "composition": "low horizons, big skies",
        "style_prompt_prefix": "Soft chalk-pastel picture-book art on dark blue paper.",
        "avoid": ["photographic realism"],
    },
    "qa": {
        "format_key": "bedtime-wonderings", "format_label": "Bedtime wonderings",
        "question_types": ["what if", "who is sleepy"],
        "intro_hook": "The kids yawn and settle in.", "outro_hook": "The kids say goodnight.",
        "moments_min": 7, "moments_max": 8,
        "kid_notes": {"kid_older": "Whispers guesses.", "kid_younger": "Counts stars."},
    },
    "sound": {"ambience": ["crickets_night", "owl_hoot"], "sfx": ["bell_chime"],
              "music_direction": "slow music box lullaby"},
    "animation": {"intensity": 0.5, "effects": ["fireflies"]},
    "rationale": "Hushed night look unlike the sunny gouache pilot.",
}


def valid_blueprint(**changes) -> dict:
    """A deep copy of VALID; dotted keys change nested fields: valid_blueprint(**{"art.medium_key": "x"})."""
    data = copy.deepcopy(VALID)
    for dotted, value in changes.items():
        *path, last = dotted.split(".")
        node = data
        for part in path:
            node = node[part]
        node[last] = value
    return data


def context() -> Context:
    return Context(
        terms=["gun"], gentle=["monster"],
        sounds={"crickets_night": "ambience", "owl_hoot": "ambience", "rain_soft": "ambience",
                "bell_chime": "sfx", "music_box": "music"},
        effects=frozenset({"rain", "snow", "fireflies"}),
    )
```

- [ ] Write `tests/test_blueprint.py` (each test cites its AC):

```python
from __future__ import annotations

import json

import pytest

from tests.blueprint_helpers import context, valid_blueprint
from video_pipeline import blueprint as bp

CFG = bp.BlueprintConfig(
    distance=20.0,
    medium_synonyms={"gouache": "painted-water-media", "watercolor": "painted-water-media"},
    mood_synonyms={}, format_synonyms={},
)


def parsed(**changes) -> bp.Blueprint:
    return bp.Blueprint.model_validate(valid_blueprint(**changes))


def test_valid_blueprint_has_no_errors():
    assert bp.blueprint_errors(valid_blueprint(), context(), "blueprint.json") == []


@pytest.mark.parametrize("changes, needle", [
    ({"art.palette": [{"name": "a", "hex": "#000000"}] * 4}, "art.palette"),          # FR-1: exactly 5
    ({"art.palette": [{"name": "a", "hex": "red"}] * 5}, "art.palette.0.hex"),        # #rrggbb
    ({"mood.key": "Bad Key"}, "mood.key"),                                             # slug rule
    ({"qa.moments_min": 9, "qa.moments_max": 8}, "moments_min"),                       # min <= max
    ({"animation.intensity": 3.0}, "animation.intensity"),                             # 0.25..2.0
    ({"sound.ambience": []}, "sound.ambience"),                                        # 1..6
])
def test_field_rules_name_file_and_field(changes, needle):  # AC-6.7, FR-3
    errors = bp.blueprint_errors(valid_blueprint(**changes), context(), "books/x/blueprint.json")
    assert errors and all(e.startswith("books/x/blueprint.json: ") for e in errors)
    assert any(needle in e for e in errors)


def test_unknown_field_is_an_error():
    data = valid_blueprint()
    data["extra"] = 1
    assert any("extra" in e for e in bp.blueprint_errors(data, context(), "f"))


def test_disallowed_and_gentle_terms_are_refused():
    errors = bp.blueprint_errors(valid_blueprint(**{"rationale": "a gun and a monster"}), context(), "f")
    assert any("'gun'" in e for e in errors) and any("'monster'" in e for e in errors)


def test_unknown_sound_wrong_kind_and_unknown_effect_are_named():  # AC-5.4
    data = valid_blueprint(**{"sound.ambience": ["nope", "bell_chime"], "sound.sfx": ["rain_soft"],
                              "animation.effects": ["lava"]})
    text = "\n".join(bp.blueprint_errors(data, context(), "f"))
    assert "'nope'" in text and "'bell_chime'" in text and "'rain_soft'" in text and "'lava'" in text


def test_load_blueprint_refuses_bad_json_and_names_the_file(tmp_path):  # AC-6.7
    path = tmp_path / "blueprint.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(bp.BlueprintError, match="blueprint.json"):
        bp.load_blueprint(path, context())


# --- distinctness (FR-4) ---------------------------------------------------------------------

def test_same_medium_and_mood_is_rejected_naming_the_book():  # AC-2.1
    other = parsed()
    candidate = parsed(**{"qa.format_key": "other-format", "art.palette": [
        {"name": f"c{i}", "hex": h} for i, h in enumerate(
            ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#ff00ff"])]})
    problems = bp.similarity_problems(candidate, {"Moon Book": other}, CFG)
    assert len(problems) == 1 and "Moon Book" in problems[0]
    assert "art.medium_key" in problems[0] and "mood.key" in problems[0]


def test_same_format_key_is_rejected():  # AC-2.2
    other = parsed(**{"art.medium_key": "ink", "mood.key": "spooky-fun"})
    problems = bp.similarity_problems(parsed(), {"B": other}, CFG)
    assert any("qa.format_key" in p for p in problems)


def test_three_close_palette_colours_are_rejected_naming_them():  # AC-2.3
    other = parsed(**{"art.medium_key": "ink", "mood.key": "x1", "qa.format_key": "f1"})
    problems = bp.similarity_problems(parsed(**{"art.medium_key": "oil", "mood.key": "x2",
                                                 "qa.format_key": "f2"}), {"B": other}, CFG)
    assert any("palette" in p and "midnight" in p for p in problems)


def test_two_close_colours_pass():
    other = parsed(**{"art.medium_key": "ink", "mood.key": "x1", "qa.format_key": "f1"})
    cand = valid_blueprint(**{"art.medium_key": "oil", "mood.key": "x2", "qa.format_key": "f2"})
    for i in (2, 3, 4):
        cand["art"]["palette"][i]["hex"] = ["#ff0000", "#00ff00", "#0000ff"][i - 2]
    assert bp.similarity_problems(bp.Blueprint.model_validate(cand), {"B": other}, CFG) == []


def test_all_failed_checks_are_reported_together():
    problems = bp.similarity_problems(parsed(), {"B": parsed()}, CFG)
    assert len(problems) == 3


def test_synonym_keys_compare_as_their_canonical_key():  # AC-2.6
    a = parsed(**{"art.medium_key": "gouache", "qa.format_key": "f1"})
    b = parsed(**{"art.medium_key": "watercolor", "qa.format_key": "f2"})
    assert any("art.medium_key" in p for p in bp.similarity_problems(a, {"B": b}, CFG))


def test_unknown_but_valid_key_is_compared_as_itself():  # AC-2.7
    a = parsed(**{"art.medium_key": "felt-collage", "qa.format_key": "f1"})
    b = parsed(**{"art.medium_key": "felt-collage", "qa.format_key": "f2"})
    assert any("art.medium_key" in p for p in bp.similarity_problems(a, {"B": b}, CFG))


def test_delta_e76_known_values():
    assert bp.delta_e76("#336699", "#336699") == pytest.approx(0.0)
    assert bp.delta_e76("#000000", "#ffffff") == pytest.approx(100.0, abs=0.5)


# --- ambience placement (AC-5.5) and range clamp (AC-4.2) ------------------------------------

def test_assign_ambience_runs_in_palette_order_with_first_and_last_on_the_bookends():
    a = bp.assign_ambience(["rain", "wind", "owl"], 9)
    assert a.intro == ["rain"] and a.outro == ["owl"]
    assert [p[0] for p in a.pages] == ["rain"] * 3 + ["wind"] * 3 + ["owl"] * 3
    assert bp.assign_ambience(["rain", "wind", "owl"], 9) == a      # deterministic


def test_every_name_is_used_when_palette_is_not_longer_than_the_book():
    names = ["a", "b", "c", "d", "e", "f"]
    used = {p[0] for p in bp.assign_ambience(names, 8).pages}
    assert used == set(names)


@pytest.mark.parametrize("lo, hi, low, high, expected", [
    (7, 8, 7, 8, (7, 8)), (1, 12, 7, 8, (7, 8)), (8, 8, 7, 8, (8, 8)),
    (2, 3, 7, 8, (7, 7)), (11, 12, 7, 8, (8, 8)),
])
def test_clamp_range(lo, hi, low, high, expected):
    assert bp.clamp_range(lo, hi, low, high) == expected
```

- [ ] Run: `uv run pytest tests/test_blueprint.py -q` — expect: FAIL (`ImportError: cannot import name 'blueprint'`)
- [ ] Commit: `test(blueprint): contracts for the blueprint core (spec 011 Phase 0)`

---

## Phase 1: Blueprint core and config

**Implements:** FR-1, FR-3, FR-4, FR-8 (hashing), placement and clamp helpers | **Satisfies:** AC-2.1–2.4, 2.6–2.8, 5.4, 5.5 (function level), 6.7
**Files:** `video_pipeline/blueprint.py`, `config/blueprint.json`, `video_pipeline/library.py`, `tests/test_blueprint.py`
**Interfaces:** Consumes: `contract.find_disallowed`, `file_hash.sha256_file`, `episode_tools.write_atomic`, `sound_library.load_library`. Produces: everything pinned in Phase 0, plus `blueprint.BLUEPRINT_FILE`, `blueprint.load_context(root) -> Context`, `blueprint.others_for(root, exclude: tuple[str, str] | None) -> dict[str, Blueprint]`, `blueprint.blueprint_sha256(path) -> str`, `blueprint.record_use(folder, stage, sha)`, `blueprint.read_use(folder) -> dict`, `blueprint.style_text(bp) -> str`, `blueprint.rebuild_style_bible(folder) -> None`, `BookFolder.blueprint_path`, `BookFolder.used_path`, `Ambience(intro, pages, outro)`.

One pure module holds the model, validation, distinctness, hashing, and placement logic. `others_for` is the only function that reads other books and drafts.

- [ ] `config/blueprint.json`:

```json
{
  "distance": 20,
  "mediums": {
    "painted-water-media": ["gouache", "watercolor", "watercolour", "acrylic-wash"],
    "chalk-pastel": ["pastel", "soft-pastel"],
    "ink-and-wash": ["ink", "pen-and-wash"],
    "paper-collage": ["cut-paper", "felt-collage"],
    "digital-flat": ["vector", "flat-colour"]
  },
  "moods": {"sleepy-drift": ["sleepy", "drowsy"], "bouncy-bright": ["bouncy", "excited"]},
  "formats": {"guessing-game": ["guessing"], "detail-spotting": ["i-spy"], "what-would-you-do": ["choices"]}
}
```

- [ ] Add to `video_pipeline/library.py` `BookFolder`: `blueprint_path -> folder / "blueprint.json"` and `used_path -> folder / "blueprint_used.json"`.
- [ ] Implement `video_pipeline/blueprint.py`:

```python
"""Per-book blueprint (spec 011): schema, validation, distinctness, hashing, placement."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from video_pipeline.contract import find_disallowed
from video_pipeline.episode_tools import write_atomic
from video_pipeline.file_hash import sha256_file

BLUEPRINT_FILE = "blueprint.json"
USED_FILE = "blueprint_used.json"


class BlueprintError(Exception):
    """A blueprint file is missing, unreadable or invalid; the message names file and field."""


def _text(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=n)]


Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=40)]
Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    sounds: dict[str, str]          # library name -> kind
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


def _texts(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in _texts(v)]
    if isinstance(node, list):
        return [t for v in node for t in _texts(v)]
    return []


def blueprint_errors(data: object, ctx: Context, where: str) -> list[str]:
    """Every rule of FR-1 and FR-3; each message starts with `where` (the file) and the field."""
    try:
        model = Blueprint.model_validate(data)
    except ValidationError as exc:
        return [f"{where}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    for text in _texts(model.model_dump()):
        errors += [f"{where}: '{t}' is not allowed in a children's story" for t in find_disallowed(text, ctx.terms)]
        errors += [f"{where}: '{t}' is too frightening for this story" for t in find_disallowed(text, ctx.gentle)]
    for name in model.sound.ambience:
        if ctx.sounds.get(name) != "ambience":
            errors.append(f"{where}: sound.ambience: {name!r} is not an ambience sound in the library")
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
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise BlueprintError(f"{path}: blueprint settings missing or invalid ({exc})") from None

    def flip(table: dict) -> dict[str, str]:
        return {syn: canon for canon, syns in table.items() for syn in syns}

    return BlueprintConfig(float(raw["distance"]), flip(raw["mediums"]), flip(raw["moods"]), flip(raw["formats"]))


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


def similarity_problems(candidate: Blueprint, others: dict[str, Blueprint], cfg: BlueprintConfig) -> list[str]:
    """All failed distinctness checks (FR-4) against every other blueprint, in one list."""
    problems: list[str] = []
    cm = canonical(candidate.art.medium_key, cfg.medium_synonyms)
    cmood = canonical(candidate.mood.key, cfg.mood_synonyms)
    cf = canonical(candidate.qa.format_key, cfg.format_synonyms)
    for label, other in others.items():
        if cm == canonical(other.art.medium_key, cfg.medium_synonyms) and cmood == canonical(
            other.mood.key, cfg.mood_synonyms
        ):
            problems.append(f"too close to '{label}': same art.medium_key '{cm}' and mood.key '{cmood}'")
        if cf == canonical(other.qa.format_key, cfg.format_synonyms):
            problems.append(f"too close to '{label}': same qa.format_key '{cf}'")
        close = [
            s.name
            for s in candidate.art.palette
            if any(delta_e76(s.hex, o.hex) < cfg.distance for o in other.art.palette)
        ]
        if len(close) >= 3:
            problems.append(f"too close to '{label}': palette colours {', '.join(close)} are nearly the same")
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
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_use(folder, stage: str, sha: str) -> None:
    write_atomic(folder.used_path, {**read_use(folder), stage: sha})


def rebuild_style_bible(folder, bp: Blueprint) -> None:
    """AC-3.4: set only style_bible.style_prompt_prefix in book.json."""
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    book.setdefault("style_bible", {})["style_prompt_prefix"] = bp.art.style_prompt_prefix
    write_atomic(folder.book_json, book)
```

`load_context(root)` and `others_for(root, exclude)` (below) go in the same file:

```python
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


def others_for(root: Path, ctx: Context, exclude: tuple[str, str] | None = None) -> dict[str, Blueprint]:
    """Blueprints of every book and every draft, keyed by a display name; `exclude` is
    ("book", slug) or ("draft", id) (AC-2.8, AC-2.9). Unreadable files are skipped."""
    from video_pipeline.drafts import DraftStore
    from video_pipeline.library import BookFolder, list_book_slugs

    found: dict[str, Blueprint] = {}
    slugs, _ = list_book_slugs(root)
    for slug in slugs:
        if exclude == ("book", slug):
            continue
        folder = BookFolder(slug, Path(root))
        try:
            found[slug] = load_blueprint(folder.blueprint_path, ctx)
        except BlueprintError:
            continue
    store = DraftStore(root)
    for record in store.list():
        if exclude == ("draft", record["id"]):
            continue
        try:
            found[record["idea"]["title"]] = load_blueprint(store.draft_folder(record["id"]) / BLUEPRINT_FILE, ctx)
        except (BlueprintError, KeyError):
            continue
    return found
```

(`drafts`/`library` are imported inside the function to avoid import cycles: `drafts` imports `story_tools` which imports `blueprint`.)

- [ ] Run: `uv run pytest tests/test_blueprint.py -q` — expect: PASS
- [ ] Add tests (same file) for `others_for`: two books with blueprints, one draft with one, `exclude=("book","a")` omits only `a`; an unreadable file is skipped (AC-2.8, AC-2.9). Run — expect PASS after fixing any gap.
- [ ] Run: `uv run ruff check video_pipeline tests && uv run ruff format --check video_pipeline tests` — expect: clean
- [ ] Commit: `feat(blueprint): blueprint model, validation, distinctness and placement (spec 011 Phase 1)`

---

## Phase 2: Design job, drafts and story

**Implements:** FR-2, FR-5, FR-4 (in the job), part of FR-9 (CLI) | **Satisfies:** AC-1.1–1.7 (job/store level), AC-2.5, 2.8–2.10, AC-3.3
**Files:** `video_pipeline/story_tools.py`, `story_mcp.py`, `write_story.py`, `drafts.py`, `tests/test_blueprint_job.py`, `tests/test_write_story.py` (FakeCodex mode table), `tests/test_drafts.py`
**Interfaces:** Consumes: Phase 1 (`load_context`, `others_for`, `blueprint_errors`, `similarity_problems`, `load_config`, `Blueprint`). Produces: `StoryTools.submit_blueprint(payload) -> {saved, errors}`; `story_mcp --mode blueprint --subject draft:<id>|book:<slug>`; `write_story.run_blueprint(root, *, draft_id=None, slug=None, then_book=False, provider=None) -> int`; `write_story.blueprint_prompt(idea_or_book, others) -> str`; `DraftStore.STATES` += `designing`, `design_failed`; `DraftStore.new_draft(..., state="designing")`; draft field `phase`; `DraftStore.blueprint_path(draft_id)`; `create_book` copies the blueprint and sets `style_bible.style_prompt_prefix`.

The design job reuses the story agent loop (`generate`) with a new mode, so attempts, time limits, refusal feedback and the validating save step behave like the story stage.

- [ ] Write failing tests in `tests/test_blueprint_job.py` (fake Codex from `tests/test_write_story.FakeCodex`, extended with `"blueprint": tools.submit_blueprint`):

```python
def test_blueprint_job_saves_a_valid_blueprint_and_starts_the_story(root, monkeypatch):  # AC-1.1, AC-1.2
    store = DraftStore(root)
    draft = store.new_draft(idea(), 20, "A")                       # state designing
    fake = FakeCodex([valid_blueprint(), book()])                  # design, then story
    use(monkeypatch, fake)
    assert ws.run_blueprint(root, draft_id=draft["id"], then_book=True) == 0
    record = store.get(draft["id"])
    assert record["state"] == "ready" and record["book"]
    assert (store.draft_folder(draft["id"]) / "blueprint.json").is_file()
    assert "Sleepy drift" in prompt_of(fake.commands[1])           # AC-3.3: story brief has the mood
    assert "without naming a painting medium" in prompt_of(fake.commands[1])


def test_too_close_blueprint_is_refused_with_the_book_named_then_retried(root, monkeypatch):  # AC-2.5
    write_book_with_blueprint(root, "moon-book", valid_blueprint())
    close = valid_blueprint()                                       # same everything
    good = valid_blueprint(**{"art.medium_key": "ink-and-wash", "mood.key": "x-mood", "qa.format_key": "x-fmt",
                              "art.palette": DIFFERENT_PALETTE})
    fake = FakeCodex([close, good]); use(monkeypatch, fake)
    draft = DraftStore(root).new_draft(idea(), 20, "A")
    assert ws.run_blueprint(root, draft_id=draft["id"]) == 0
    assert "moon-book" in prompt_of(fake.commands[1])               # refusal reasons go back (AC-2.5)


def test_three_failed_attempts_mark_design_failed_with_reasons(root, monkeypatch):  # AC-1.4
    ...  # FakeCodex([close, close, close]) -> state "design_failed", problems mention "moon-book", return code 1


def test_brief_lists_every_other_blueprint(root, monkeypatch):  # AC-2.10
    ...  # prompt contains title, the 3 keys and the 5 hex colours of each other blueprint


def test_regenerating_a_book_excludes_its_own_blueprint(root, monkeypatch):  # AC-2.8
    ...  # run_blueprint(root, slug="moon-book") accepts the same blueprint it already has; old file kept as .bak


def test_other_drafts_blueprints_are_compared(root, monkeypatch):  # AC-2.9
    ...


def test_store_new_draft_starts_designing_and_interrupt_marks_it(root):  # AC-1.1, AC-1.3
    store = DraftStore(root)
    d = store.new_draft(idea(), 20, "A")
    assert d["state"] == "designing" and d["phase"] == "design"
    store.mark_interrupted_all()
    assert store.get(d["id"])["state"] == "interrupted"


def test_create_book_copies_blueprint_and_sets_style_bible(root):  # AC-1.6
    ...  # draft ready + blueprint.json -> books/<slug>/blueprint.json equal, book.json style_bible.style_prompt_prefix set, draft removed


def test_write_without_a_blueprint_makes_a_plain_book(root, monkeypatch):  # AC-1.5
    ...  # run_book on a design_failed draft with no blueprint -> ready; create_book -> no blueprint.json, style_bible == {}
```

(The `...` bodies follow the first two tests' pattern; the implementer writes each in full, one assertion per AC clause.)

- [ ] Run: `uv run pytest tests/test_blueprint_job.py -q` — expect: FAIL
- [ ] Implement `story_tools.py`:

```python
@dataclass
class StoryTools:
    ...
    blueprint_ctx: Context | None = None
    blueprint_others: dict | None = None
    blueprint_cfg: BlueprintConfig | None = None

    def submit_blueprint(self, payload: object) -> dict:
        def check(p: object) -> list[str]:
            errors = blueprint_errors(p, self.blueprint_ctx, "blueprint")
            if errors:
                return errors
            return similarity_problems(Blueprint.model_validate(p), self.blueprint_others, self.blueprint_cfg)
        return self._submit(check, payload)
```

(import `blueprint` at module top: `blueprint.py` must not import `story_tools` at module level; `load_context` imports it lazily.)
- [ ] Implement `story_mcp.py`: `MODES += ("blueprint",)`, `TOOL_FOR_MODE["blueprint"]="submit_blueprint"`, `--subject` argument, a `submit_blueprint(blueprint: dict) -> dict` tool with docstring listing the fields, and in `build_tools` for mode `blueprint`: `ctx = load_context(root)`, `exclude = tuple(subject.split(":", 1))`, `others = others_for(root, ctx, exclude)`, `cfg = load_config(root/"config")`.
- [ ] Implement `write_story.py`: `story_server_args(mode, out, pages, root, subject=None)` appends `--subject`; `validate_payload(mode, payload, pages, root, subject=None)` handles `"blueprint"` (same checks via `StoryTools`-free pure calls); `generate(..., subject=None)` passes it; add:

```python
BLUEPRINT_PROMPT = """You design the creative direction for a children's read-aloud picture book video.
Use only the story tools.
1. Call submit_blueprint with one object holding: "version" 1; "mood" (key, label, summary, roles for
   storyteller, kid_older, kid_younger each with pace slow|steady|brisk, energy hushed|calm|lively|bouncy
   and a one-sentence quirk); "art" (medium_key, medium_label, palette of exactly 5 {name, hex}, line,
   lighting, composition, style_prompt_prefix, avoid); "qa" (format_key, format_label, question_types
   2-4, intro_hook, outro_hook, moments_min, moments_max, kid_notes); "sound" (ambience: library names,
   sfx: library names, music_direction); "animation" (intensity 0.25-2.0, effects); "rationale".
2. Keys are lowercase slugs. Make it clearly different from every existing blueprint listed below:
   a different art medium or mood, a different Q&A format, and a different palette.
3. Library sounds you may use: {sounds}. Effects you may use: {effects}.
4. Gentle for young children. If submit_blueprint returns errors, fix every error and submit again.
5. Finish with one short line.
"""


def run_blueprint(root, *, draft_id=None, slug=None, then_book=False, provider=None) -> int:
    ...  # draft: phase "design", state designing; book: folder = book build dir; on success write
         # <target>/blueprint.json atomically (previous kept as blueprint.json.bak); draft + then_book -> run_book
         # failure on a draft that was designing -> state design_failed with problems; on a ready draft
         # (regenerate) -> state stays ready and problems go to "design_problems"
```

Add CLI `blueprint` sub-command: `--draft ID [--then-book]` | `--book SLUG` (mutually exclusive); `main` dispatches. In `run_book`: set `phase="story"`; if `store.draft_folder(id)/blueprint.json` exists append `story_direction(bp)` (mood label+summary, `art.style_prompt_prefix`, and "Describe each scene without naming a painting medium; the art direction is applied separately.") to the prompt.
- [ ] Implement `drafts.py`: `STATES += ("designing", "design_failed")`; `new_draft(..., state="designing")` with `"phase": "design"`; `mark_interrupted_all` covers `("writing", "designing")`; `blueprint_path`; `create_book` copies `blueprint.json` into the staging folder and sets `data["style_bible"] = {"style_prompt_prefix": bp.art.style_prompt_prefix}` when present.
- [ ] Run: `uv run pytest tests/test_blueprint_job.py tests/test_write_story.py tests/test_drafts.py tests/test_story_tools.py -q` — expect: PASS
- [ ] Commit: `feat(blueprint): design job, draft states and story brief (spec 011 Phase 2)`

---

## Phase 3: Pictures, Q&A and ambience follow the blueprint

**Implements:** FR-6, FR-7, FR-8 (recording), FR-10 | **Satisfies:** AC-3.1, 3.2, 3.4, AC-4.1–4.4, AC-5.1–5.3, 5.5–5.7, AC-7.1
**Files:** `video_pipeline/illustrate.py`, `contract.py`, `episode.py`, `llm.py`, `episode_tools.py`, `mcp_server.py`, `generate_script.py`, `tests/test_blueprint_consumers.py`
**Interfaces:** Consumes: Phase 1. Produces: `illustrate.plan_images(book, style=None)`; `contract.qa_bounds(page_count) -> tuple[int,int]`; `contract.validate_script(script, book, terms, allowed_ambience=None)`; `episode.choose_qa_pages(page_count, rng, window=None)`; `episode.plan_episode(book, seed, blueprint=None)`; `EpisodePlan.notes: tuple[str, ...] = ()`; `episode.assemble_script(book, cast, plan, banter, ambience=None)`; `llm.build_brief(book, cast, plan, blueprint=None)`; `EpisodeTools(blueprint=None)`.

- [ ] Write failing tests in `tests/test_blueprint_consumers.py`:

```python
def test_picture_request_has_blueprint_art_and_no_blueprint_is_unchanged():  # AC-3.1, AC-3.2
    book = load_json(MINI_BOOK)
    plain = plan_images(book)
    styled = plan_images(book, style=style_text(parsed_blueprint()))
    assert plain == plan_images(book)                       # identical to before this spec
    assert all("Soft chalk-pastel" in prompt and "#1b2a49" in prompt for _, prompt in styled)
    assert [s for s, _ in styled] == [s for s, _ in plain]  # same stems, covers included


def test_rebuild_style_bible_changes_only_that_field(tmp_path):  # AC-3.4
    ...  # book.json before/after differ only in style_bible.style_prompt_prefix


def test_no_blueprint_plan_and_brief_are_unchanged():  # AC-4.4
    book = load_json(MINI_BOOK)
    assert plan_episode(book, 7) == plan_episode(book, 7, None)
    assert build_brief(book, CAST, plan_episode(book, 7)) == build_brief(book, CAST, plan_episode(book, 7), None)


def test_blueprint_brief_uses_its_format_and_drops_the_fixed_rule():  # AC-4.1
    brief = build_brief(book, CAST, plan, parsed_blueprint())
    assert "Bedtime wonderings" in brief and "Whispers guesses." in brief and "The kids yawn and settle in." in brief
    assert "kid_older asks curious questions about the plot" not in brief
    assert "crickets_night" in brief                         # AC-5.1


def test_moment_count_stays_in_blueprint_range_inside_spec_001_bounds():  # AC-4.2
    bp = parsed_blueprint(**{"qa.moments_min": 7, "qa.moments_max": 7})
    for seed in range(20):
        assert len(plan_episode(book20, seed, bp).qa_speakers) == 7


def test_out_of_bounds_range_is_clamped_and_noted():
    bp = parsed_blueprint(**{"qa.moments_min": 2, "qa.moments_max": 3})
    plan = plan_episode(book20, 7, bp)
    assert len(plan.qa_speakers) == 7 and any("clamped" in n for n in plan.notes)


def test_generated_script_passes_every_spec_001_rule_with_a_blueprint():  # AC-4.3
    tools = EpisodeTools(book20, CAST, TERMS, 7, out, blueprint=parsed_blueprint())
    script, errors = tools.check(make_banter(book20, tools.plan))
    assert errors == [] and script is not None


def test_script_ambience_is_assigned_from_the_blueprint_and_absent_without_one():  # AC-5.5, AC-5.7
    ...  # intro == ["crickets_night"], outro == ["owl_hoot"], every page has one list; no key without blueprint


def test_ambience_outside_the_palette_is_refused_naming_sound_and_place():  # AC-5.2
    errors = validate_script(script, book, TERMS, allowed_ambience={"crickets_night"})
    assert any("owl_hoot" in e and "page" in e for e in errors)


def test_hand_edited_ambience_inside_the_palette_keeps_the_voice_hash():  # AC-5.6
    ...  # script_sha256 equal before/after editing a page's ambience to another palette name


def test_ambience_is_checked_against_the_library_only_without_a_blueprint():  # AC-5.3
    assert validate_script(script, book, TERMS) == validate_script(script, book, TERMS, None)
```

- [ ] Run: `uv run pytest tests/test_blueprint_consumers.py -q` — expect: FAIL
- [ ] `illustrate.py`: `plan_images(book, style=None)`: when `style` is set prefix every image prompt (pages and both covers) with `f"{style}\n\n"`; `main()` loads `folder.blueprint_path` (if present) with `load_blueprint(..., load_context(root))` and passes `style_text(bp)`; after a run that painted every planned image call `record_use(folder, "art", blueprint_sha256(folder.blueprint_path))`. `BlueprintError` joins the caught exceptions.
- [ ] `contract.py`: extract bounds:

```python
def qa_bounds(page_count: int) -> tuple[int, int]:
    return math.ceil(page_count / 3), math.floor(page_count / 2.5)

# qa_schedule_errors uses: low, high = qa_bounds(page_count)
```

and `validate_script(script, book, terms, allowed_ambience: set[str] | None = None)` passes it to `_bookend_errors`/`_page_errors`, whose `_ambience_errors(where, block, allowed)` adds `f"{where}: ambience {name!r} is not in the blueprint's sound palette"` for a name outside a non-None `allowed`.
- [ ] `episode.py`:

```python
@dataclass(frozen=True)
class EpisodePlan:
    qa_speakers: dict[int, tuple[str, ...]]
    motions: tuple[str, ...]
    notes: tuple[str, ...] = ()

def choose_qa_pages(page_count, rng, window=None):
    for _ in range(10_000):
        pages = [rng.randint(1, 3)]
        while (following := pages[-1] + rng.choice((2, 3))) <= page_count:
            pages.append(following)
        if not qa_schedule_errors(pages, page_count) and (window is None or window[0] <= len(pages) <= window[1]):
            return pages
    raise ValueError(...)

def plan_episode(book, seed, blueprint=None):
    rng = random.Random(seed)
    n = len(book["pages"])
    window, notes = None, ()
    if blueprint is not None:
        low, high = qa_bounds(n)
        window = clamp_range(blueprint.qa.moments_min, blueprint.qa.moments_max, low, high)
        if window != (blueprint.qa.moments_min, blueprint.qa.moments_max):
            notes = (f"blueprint moment range {blueprint.qa.moments_min}-{blueprint.qa.moments_max} clamped to {window[0]}-{window[1]} for {n} pages",)
    qa_pages = choose_qa_pages(n, rng, window)
    ...
    return EpisodePlan(dict(zip(qa_pages, speakers, strict=True)), motions, notes)
```

`assemble_script(..., ambience=None)`: when given (`Ambience`), set `intro["ambience"]`, each `page["ambience"] = ambience.pages[number-1]`, `outro["ambience"]`.
- [ ] `llm.py`: `build_brief(book, cast, plan, blueprint=None)`; with `None` the text is byte-identical. With a blueprint, replace the two fixed `kid_older asks… kid_younger reacts…` sentences and the intro/outro descriptions with a `DIRECTION` block (mood label/summary, per-role pace/energy/quirk, Q&A format label, question types, kid notes, intro hook, outro hook) and add `"Ambience palette (chosen automatically for the script): <names>"`. All RULES lines stay.
- [ ] `episode_tools.py`: field `blueprint: Blueprint | None = None`; `__post_init__` → `plan_episode(self.book, self.seed, self.blueprint)`; `load` reads `folder.blueprint_path` when it exists via `load_blueprint(path, load_context(root))` (root = `config_dir.parent`); `check` builds `assign_ambience(self.blueprint.sound.ambience, len(book["pages"]))` and passes it to `assemble_script`, and passes `allowed_ambience=set(self.blueprint.sound.ambience)` to `validate_script`; `get_episode_plan` passes the blueprint to `build_brief`.
- [ ] `mcp_server.py` / `generate_script.py`: catch `BlueprintError` with the other load errors; re-validation in `run_agent` passes `allowed_ambience`; print `plan.notes`; after a successful write call `record_use(folder, "script", blueprint_sha256(folder.blueprint_path))` when the blueprint exists.
- [ ] Studio script saving (`studio/books.py save_line_edits`) calls `validate_script(...)`: pass `allowed_ambience` from the book's blueprint when present (so a hand edit outside the palette is refused, AC-5.2).
- [ ] Run: `uv run pytest -q` (whole unit suite, skips real-provider tests) — expect: PASS, including every pre-existing test (AC-7.1)
- [ ] Commit: `feat(blueprint): pictures, Q&A and ambience follow the blueprint (spec 011 Phase 3)`

---

## Phase 4: Studio

**Implements:** FR-9, FR-8 (display) | **Satisfies:** AC-1.1, 1.3, 1.4, 1.5, 1.7, AC-6.1–6.9, AC-7.2, AC-3.4 (button)
**Files:** `video_pipeline/studio/jobs.py`, `studio/books.py`, `studio/app.py`, `studio/templates/book.html`, `studio/templates/draft.html`, `studio/static/studio.css` (swatch style), `tests/test_studio_blueprint.py`, `tests/test_studio_story.py`
**Interfaces:** Consumes: Phases 1–3. Produces: `jobs.story_command` handles `blueprint` and `redesign`; `books.blueprint_status(paths, book) -> dict` (`present`, `error`, `view`, `stale: list[{output, command}]`); studio routes `POST /drafts/{id}/design-again`, `POST /drafts/{id}/write-without-blueprint`, `POST /books/{slug}/blueprint/regenerate`, `POST /drafts/{id}/blueprint/regenerate`, `POST /books/{slug}/blueprint/style-bible`.

- [ ] Write failing tests in `tests/test_studio_blueprint.py` using the existing `make_studio` fixture (fake command builder like `test_studio_story.py`):

```python
def test_write_book_starts_the_blueprint_job_first(make_studio):  # AC-1.1
    s, runner = make_studio()
    ...  # POST /drafts -> draft state "designing", the started job's stage == "blueprint"

def test_draft_page_shows_designing_and_failed_actions(make_studio):  # AC-1.4
    ...  # design_failed -> "Try the design again" and "Write without a blueprint" buttons, reasons listed

def test_write_without_a_blueprint_starts_the_story_job(make_studio):  # AC-1.5
    ...

def test_interrupted_design_restarts_the_design(make_studio):  # AC-1.3
    ...  # interrupted + phase "design" -> write-again starts stage "blueprint"

def test_discard_stops_a_designing_draft(make_studio):  # AC-1.7
    ...

def test_book_page_shows_the_blueprint_panel_and_hides_regenerate_without_one(make_studio):  # AC-6.1, AC-6.3, AC-7.2
    ...  # swatches (5), mood, medium, format, sounds, music + animation notes; no panel errors without file

def test_regenerate_starts_redesign_and_keeps_the_old_file_on_stop(make_studio):  # AC-6.2, AC-6.9
    ...

def test_stale_marks_name_the_command_and_change_nothing(make_studio):  # AC-6.4–6.6
    ...  # edit blueprint after used-record -> pictures stale: "illustrate --force", script stale: "generate_script --force"

def test_invalid_blueprint_is_refused_naming_file_and_field(make_studio):  # AC-6.7
    ...

def test_busy_runner_refuses_regenerate(make_studio):  # AC-6.8
    ...

def test_rebuild_style_bible_button(make_studio):  # AC-3.4
    ...
```

- [ ] Run: `uv run pytest tests/test_studio_blueprint.py -q` — expect: FAIL
- [ ] `jobs.py`: `STORY_STAGES = ("ideas", "candidate", "book", "blueprint", "redesign")`; `story_command`:

```python
if stage == "blueprint":
    return [sys.executable, "-m", "video_pipeline.write_story", "blueprint", "--draft", subject,
            "--then-book", "--root", str(paths.root)]
if stage == "redesign":
    kind, _, ident = subject.partition(":")          # "draft:<id>" or "book:<slug>"
    flag = "--draft" if kind == "draft" else "--book"
    return [sys.executable, "-m", "video_pipeline.write_story", "blueprint", flag, ident,
            "--root", str(paths.root)]
```
- [ ] `books.py`: `STAGE_LABELS["blueprint"] = STAGE_LABELS["redesign"] = "blueprint design"`; add `blueprint_status`:

```python
def blueprint_status(paths: StudioPaths, book: Book) -> dict:
    folder = folder_of(paths, book)
    if not folder.blueprint_path.exists():
        return {"present": False, "error": None, "view": None, "stale": []}
    try:
        bp = load_blueprint(folder.blueprint_path, load_context(paths.root))
    except BlueprintError as exc:
        return {"present": True, "error": str(exc), "view": None, "stale": []}
    sha, used = blueprint_sha256(folder.blueprint_path), read_use(folder)
    stale = []
    if any(folder.illustrations.glob("*.png")) and used.get("art") != sha:
        stale.append({"output": "pictures", "command": f"python -m video_pipeline.illustrate --book {book.slug} --force"})
    if script_path(paths, book).exists() and used.get("script") != sha:
        stale.append({"output": "episode script", "command": f"python -m video_pipeline.generate_script --book {book.slug} --force"})
    return {"present": True, "error": None, "view": bp, "stale": stale}
```

  `stage_status` is unchanged (no blueprint effect on script/voices/video states, AC-6.6, FR-8: voice approval untouched).
- [ ] `app.py`: `running()` titles for `blueprint`/`redesign`; `write_book` creates the draft (state `designing`) and starts stage `blueprint`; `write_again` picks stage by `draft.get("phase") == "design"`; `mark_interrupted_all` already wired; new handlers for the five routes (all take `write_lock`, call `busy_reason()` first, and refuse with 409 and the reason, AC-6.8; "Regenerate" requires a `confirm` form field like Discard; the regenerate route refuses with 409 when the book/draft has no blueprint, AC-6.3); `book_page` passes `blueprint_status`; the "Rebuild style bible" handler loads the blueprint and calls `rebuild_style_bible`. Stopping uses the existing `/jobs/stop`; `run_blueprint` writes the new file only after validation, so a stopped job leaves the old file (AC-6.9). Discarding a `designing` draft first stops the running job through `runner.stop()` (AC-1.7).
- [ ] Templates: `draft.html` shows `designing` text, `design_failed` notice with reasons and two buttons, `design_problems` for a ready draft, and a Regenerate form; `book.html` gets a Blueprint section (mood label and summary, medium, five swatches `<span class="swatch" style="background:{{ s.hex }}">` with names, Q&A format and question types, sound palette, music direction and animation notes, stale list with commands, Regenerate and Rebuild style bible forms, load error text). Add `.swatch` CSS (black-and-white UI, only swatches coloured).
- [ ] Run: `uv run pytest tests/test_studio_blueprint.py tests/test_studio_story.py tests/test_studio_app.py tests/test_studio_books.py tests/test_studio_jobs.py -q` — expect: PASS
- [ ] Commit: `feat(blueprint): studio blueprint panel, design job and stale marks (spec 011 Phase 4)`

---

## Phase 5: Integration Verification

**Implements:** All FRs | **Satisfies:** All ACs
**Files:** `CLAUDE.md`, `docs/specs/011-book-blueprint/spec.md` (status), `tests/test_blueprint_integration.py`

- [ ] Write `tests/test_blueprint_integration.py`: a fixture root with the real `config/` and the real sound library; fake Codex designs a blueprint, writes the story, "Create book" → run `illustrate` with a fake painter and `generate_script` with a fake agent, assert: blueprint copied, style bible set, picture prompts carry the art text, script has ambience from the palette and every spec 001 rule passes, `blueprint_used.json` records both, then edit the blueprint and assert both outputs are reported stale and `script_sha256` is unchanged.
- [ ] Run: `uv run pytest -q` — expect: all PASS (real-provider tests skipped)
- [ ] Run: `uv run ruff check . && uv run ruff format --check .` — expect: clean
- [ ] Legacy check (AC-7.1): before Phase 1 starts, capture golden values on the current commit `0d4f00e` (`plan_episode(pilot, 7)`, a sha256 of `build_brief(...)`, `plan_images(pilot)` prompts, and `render_episode` section cache keys from `timeline.section_key` for the pilot book) and store them as constants in `tests/test_blueprint_integration.py`; the test asserts the same values after all phases with no blueprint.
- [ ] Update `CLAUDE.md` (commands: `write_story blueprint ...`, `blueprint.json`, `config/blueprint.json`, stale marks).
- [ ] Verify each AC against the table below manually in the studio (see Quickstart).
- [ ] Commit: `feat(blueprint): complete per-book blueprint (spec 011)`

---

## AC and FR coverage

| Requirement | Phase |
|---|---|
| FR-1, FR-3 (schema, safety, sounds, effects) | 0, 1 |
| FR-4 (distinctness, one rejection with all failures) | 0, 1, 2 |
| FR-2 (design job, attempts, auto-start story) | 2 |
| FR-5 (story brief) | 2 |
| FR-6 (picture requests, style bible) | 3, 4 |
| FR-7 (brief, range clamp, ambience lists) | 3 |
| FR-8 (recorded use, stale, voice approval untouched) | 3, 4 |
| FR-9 (studio) | 4 |
| FR-10 (legacy) | 3, 5 |
| AC-1.1–1.7 | 2 (store/job), 4 (studio) |
| AC-2.1–2.10 | 0, 1, 2 |
| AC-3.1, 3.2, 3.4 | 3, 4; AC-3.3 | 2 |
| AC-4.1–4.4 | 3 |
| AC-5.1–5.3, 5.5–5.7 | 3; AC-5.4 | 0, 1 |
| AC-6.1–6.9 | 4 (AC-6.7 also 1) |
| AC-7.1, 7.2 | 3, 5, 4 |

Traceability: every technical choice above is driven by a spec item (`blueprint_used.json` by FR-8 and AC-6.4–6.6; `phase` field by AC-1.3; stage `redesign` by AC-6.2; `clamp_range` by AC-4.2; `assign_ambience` by AC-5.5).

## Quickstart Validation

```bash
uv run pytest -q
uv run python -m video_pipeline.studio            # http://127.0.0.1:8765
```

1. Add book → suggest ideas → "Write this book": the draft shows `designing`, then `writing`, then pages; `drafts/<id>/blueprint.json` exists.
2. Create book: `books/<slug>/blueprint.json` is present and `book.json` has `style_bible.style_prompt_prefix`.
3. Add two more books from different ideas; compare the three blueprints (medium, mood, format keys and palettes differ).
4. Book page: swatches and fields show; run illustrate (pictures use the art direction), generate script (intro/pages/outro carry `ambience` from the palette).
5. Edit `blueprint.json` (change the palette): the book page marks pictures and script stale with the commands; nothing re-runs; voices stay approved.
6. Regenerate blueprint: confirm → `blueprint.json.bak` kept; Stop mid-run: the old file is unchanged.
7. Open `the-snail-mail-express`: no blueprint panel errors, no stale marks, no Regenerate button.
