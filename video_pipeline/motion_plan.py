"""Motion plan for one story page picture (spec 009): schema, limits, validation, stale check."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BookFolder

MOTIONS = ("sway", "bob", "rotate", "breathe", "shift")
EFFECTS = ("rain", "snow", "fireflies", "falling_leaves", "water_ripple", "smoke", "mist", "glow")
PLAN_DIR = "animation"
LIMITS_FILE = "animation.json"


class PlanError(ValueError):
    """A plan file is missing, unreadable or breaks a rule; the message names file and field."""


@dataclass(frozen=True)
class Limits:
    """Hard caps from config/animation.json. `max_shift` and `max_breathe` are fractions."""

    max_layers: int
    max_effects: int
    max_rotate_deg: float
    max_shift: float
    min_period_s: float
    max_period_s: float
    max_breathe: float


def load_limits(config_dir: Path) -> Limits:
    data = json.loads((Path(config_dir) / LIMITS_FILE).read_text(encoding="utf-8"))
    return Limits(**data["limits"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Motion(_Strict):
    type: Literal["sway", "bob", "rotate", "breathe", "shift"]
    amplitude: float = Field(ge=0)
    period_s: float = Field(gt=0)
    phase: float = Field(default=0.0, ge=0, le=1)
    direction: tuple[float, float] | None = None  # shift only


class Layer(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    box: tuple[int, int, int, int]
    include_points: list[tuple[int, int]] = []
    exclude_points: list[tuple[int, int]] = []
    z: int
    depth: float = Field(ge=0, le=1)
    pivot: tuple[int, int]
    motions: list[Motion] = []


class Region(_Strict):
    box: tuple[int, int, int, int] | None = None
    layer: str | None = None


class Effect(_Strict):
    type: Literal[
        "rain", "snow", "fireflies", "falling_leaves", "water_ripple", "smoke", "mist", "glow"
    ]
    region: Region | None = None
    intensity: float = Field(ge=0, le=1)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class PictureId(_Strict):
    file: str
    sha256: str
    width: int
    height: int


class MotionPlan(_Strict):
    page_number: int = Field(ge=1)
    picture: PictureId
    provider: str = ""
    written_at: str = ""
    layers: list[Layer] = Field(min_length=1)
    effects: list[Effect] = []


def _loc(err: dict) -> str:
    out = ""
    for part in err["loc"]:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _inside(xy: tuple[int, int], width: int, height: int) -> bool:
    return 0 <= xy[0] <= width and 0 <= xy[1] <= height


def rule_problems(
    plan: MotionPlan, limits: Limits, size: tuple[int, int] | None = None
) -> list[str]:
    """Rules beyond the schema: picture bounds, limits, unique names, effect regions.

    Bounds are checked against `size` (width, height) when given, else the plan's own picture.
    """
    w, h = size if size is not None else (plan.picture.width, plan.picture.height)
    caps = {
        "rotate": limits.max_rotate_deg,
        "sway": limits.max_rotate_deg,
        "bob": limits.max_shift,
        "shift": limits.max_shift,
        "breathe": limits.max_breathe,
    }
    out: list[str] = []
    if len(plan.layers) > limits.max_layers:
        out.append(f"layers: {len(plan.layers)} layers, max {limits.max_layers}")
    if len(plan.effects) > limits.max_effects:
        out.append(f"effects: {len(plan.effects)} effects, max {limits.max_effects}")
    names = [layer.name for layer in plan.layers]
    for name in sorted({n for n in names if names.count(n) > 1}):
        out.append(f"layers: duplicate layer name {name!r}")
    for i, layer in enumerate(plan.layers):
        x0, y0, x1, y1 = layer.box
        if not (x0 < x1 and y0 < y1 and _inside((x0, y0), w, h) and _inside((x1, y1), w, h)):
            out.append(f"layers[{i}].box: {list(layer.box)} is outside the picture {w}x{h}")
        for key in ("include_points", "exclude_points"):
            for j, point in enumerate(getattr(layer, key)):
                if not _inside(point, w, h):
                    out.append(
                        f"layers[{i}].{key}[{j}]: {list(point)} is outside the picture {w}x{h}"
                    )
        if not _inside(layer.pivot, w, h):
            out.append(f"layers[{i}].pivot: {list(layer.pivot)} is outside the picture {w}x{h}")
        for j, motion in enumerate(layer.motions):
            where = f"layers[{i}].motions[{j}]"
            if not limits.min_period_s <= motion.period_s <= limits.max_period_s:
                out.append(
                    f"{where}.period_s: {motion.period_s} not in "
                    f"{limits.min_period_s}-{limits.max_period_s} s"
                )
            cap = caps[motion.type]
            if motion.amplitude > cap:
                out.append(f"{where}.amplitude: {motion.amplitude} over the limit (max {cap})")
            if motion.type == "shift" and motion.direction is None:
                out.append(f"{where}.direction: shift needs a direction")
    for k, effect in enumerate(plan.effects):
        region = effect.region
        if region is None:
            continue
        if region.layer and region.layer not in names:
            out.append(f"effects[{k}].region.layer: no layer named {region.layer!r}")
        if region.box:
            x0, y0, x1, y1 = region.box
            if not (_inside((x0, y0), w, h) and _inside((x1, y1), w, h)):
                out.append(
                    f"effects[{k}].region.box: {list(region.box)} is outside the picture {w}x{h}"
                )
    return out


def plan_problems(data: object, limits: Limits, size: tuple[int, int] | None = None) -> list[str]:
    """Every problem in a plan dict (schema first, then the rules); an empty list means valid."""
    try:
        plan = MotionPlan.model_validate(data)
    except ValidationError as exc:
        return [f"{_loc(e)}: {e['msg']} (got {e.get('input')!r})" for e in exc.errors()]
    return rule_problems(plan, limits, size)


def load_plan(path: Path, limits: Limits) -> MotionPlan:
    """Read and fully validate a plan file. Values are never changed, only accepted or refused."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"{path}: {exc}") from None
    problems = plan_problems(data, limits)
    if problems:
        raise PlanError(f"{path}: " + "; ".join(problems))
    return MotionPlan.model_validate(data)


def save_plan(path: Path, plan: MotionPlan) -> None:
    write_atomic(Path(path), plan.model_dump(mode="json"))


def plan_path(folder: BookFolder, page_number: int) -> Path:
    return folder.folder / PLAN_DIR / f"page_{page_number:02d}.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def picture_identity(path: Path) -> PictureId:
    with Image.open(path) as im:
        width, height = im.size
    return PictureId(file=Path(path).name, sha256=sha256_file(path), width=width, height=height)


@dataclass(frozen=True)
class PlanState:
    state: Literal["none", "ok", "stale", "invalid", "no_picture"]
    detail: str = ""
    plan: MotionPlan | None = None


def plan_state(folder: BookFolder, page: dict, limits: Limits) -> PlanState:
    """Where a page's plan stands: none, ok, stale (picture changed), invalid or no_picture."""
    number = int(page["page_number"])
    path = plan_path(folder, number)
    picture = folder.illustrations / (page.get("illustration_file") or f"page_{number:02d}.png")
    if not path.exists():
        return PlanState("none")
    try:
        plan = load_plan(path, limits)
    except PlanError as exc:
        return PlanState("invalid", str(exc))
    if not picture.is_file():
        return PlanState("no_picture", f"{picture.name} is missing", plan)
    if sha256_file(picture) != plan.picture.sha256:
        return PlanState("stale", f"{picture.name} changed since the plan was written", plan)
    return PlanState("ok", "", plan)


def cut_fields_hash(plan: MotionPlan) -> str:
    """Hash of what the cut depends on: layer names, boxes, points (sorted by name)."""
    fields = sorted(
        (
            layer.name,
            list(layer.box),
            sorted(map(list, layer.include_points)),
            sorted(map(list, layer.exclude_points)),
        )
        for layer in plan.layers
    )
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
