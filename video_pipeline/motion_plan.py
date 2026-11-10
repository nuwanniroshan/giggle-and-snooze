"""Motion plan for one story page picture (spec 009): schema, limits, validation, stale check."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from video_pipeline.agents import (
    AgentResult,
    AgentUnavailable,
    build_codex_command,
    codex_status,
    run_codex,
)
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder

MOTIONS = ("sway", "bob", "rotate", "breathe", "shift")
EFFECTS = ("rain", "snow", "fireflies", "falling_leaves", "water_ripple", "smoke", "mist", "glow")
PLAN_DIR = "animation"
LIMITS_FILE = "animation.json"
RESERVED_LAYER_NAMES = frozenset({"background"})  # cut_layers writes background.png
MAX_SAVES = 3
SERVER_FIELDS = ("page_number", "picture", "provider", "written_at")


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
        if layer.name in RESERVED_LAYER_NAMES:
            out.append(f"layers[{i}].name: {layer.name!r} is reserved for the filled background")
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


# -- planning with a tool-using provider (Phase 2: FR-4, FR-5, FR-6) -----------------------------


@dataclass
class PlanTools:
    """The one planning tool. Page, picture identity and provider come from the server's own
    command line; the model only sends layers and effects."""

    limits: Limits
    picture: PictureId
    page_number: int
    out: Path
    provider: str = "codex"
    saves: int = 0

    def save_motion_plan(self, plan: object) -> dict:
        self.saves += 1
        if self.saves > MAX_SAVES:
            return {"saved": False, "errors": [f"save limit reached ({MAX_SAVES} saves); stop"]}
        if not isinstance(plan, dict):
            return {"saved": False, "errors": ["plan: must be an object {layers, effects}"]}
        data = {
            **{k: v for k, v in plan.items() if k not in SERVER_FIELDS},
            "page_number": self.page_number,
            "picture": self.picture.model_dump(),
            "provider": self.provider,
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        errors = plan_problems(data, self.limits)
        if errors:
            return {"saved": False, "errors": errors}
        save_plan(self.out, MotionPlan.model_validate(data))
        return {"saved": True, "errors": []}


PLAN_PROMPT = """You plan gentle loop animation for one children's picture-book illustration.
The picture is attached. It is {width}x{height} pixels; all coordinates are in these pixels.
Page text: {text}
What the picture shows: {prompt}
Characters: {characters}
Choose 1-{max_layers} layers worth moving (characters, their parts like hair or antennae, \
foreground leaves, water, clouds). For each: name (snake_case), description, a tight box \
[x0,y0,x1,y1], optional include_points/exclude_points, z (higher = in front), depth \
(0 far .. 1 near), pivot [x,y] where it would hinge, and motions from {motions} \
(amplitude: degrees for sway/rotate max {rot}; fraction of the shorter side for bob/shift \
max {shift}; scale fraction for breathe max {breathe}; period_s {pmin}-{pmax}; phase 0-1; \
shift needs direction [dx,dy]). Add 0-{max_effects} effects from {effects} that fit the scene, \
each with intensity 0-1, optional region {{"box": [...]}} or {{"layer": name}}, optional \
colour #rrggbb.
Keep everything subtle. Call save_motion_plan with {{"layers": [...], "effects": [...]}}; \
fix every error it returns and save again (at most {saves} saves). Finish with one short line."""


@dataclass(frozen=True)
class PlanOutcome:
    page_number: int
    status: Literal["written", "kept", "failed"]
    reason: str = ""


Provider = Callable[..., AgentResult]


def page_picture(folder: BookFolder, page: dict) -> Path:
    number = int(page["page_number"])
    return folder.illustrations / (page.get("illustration_file") or f"page_{number:02d}.png")


def read_planning(root: Path) -> tuple[int, float]:
    """(attempts, time limit in seconds) from config/animation.json."""
    data = json.loads((Path(root) / "config" / LIMITS_FILE).read_text(encoding="utf-8"))
    settings = data["planning"]
    return int(settings["attempts"]), float(settings["time_limit_s"])


def plan_prompt(page: dict, book: dict, picture: PictureId, limits: Limits) -> str:
    style = book.get("style_bible") or {}
    characters = " ".join(str(v) for k, v in style.items() if k.endswith("_description") and v)
    return PLAN_PROMPT.format(
        width=picture.width,
        height=picture.height,
        text=page.get("text", ""),
        prompt=page.get("prompt", ""),
        characters=characters or "(none described)",
        max_layers=limits.max_layers,
        motions=", ".join(MOTIONS),
        rot=limits.max_rotate_deg,
        shift=limits.max_shift,
        breathe=limits.max_breathe,
        pmin=limits.min_period_s,
        pmax=limits.max_period_s,
        max_effects=limits.max_effects,
        effects=", ".join(EFFECTS),
        saves=MAX_SAVES,
    )


def _refusals(result: AgentResult) -> list[str]:
    return [
        str(event.data["errors"][0])
        for event in result.events
        if event.kind == "tool-result" and isinstance(event.data, dict) and event.data.get("errors")
    ] or [result.reason or "the agent saved nothing"]


def plan_page(
    folder: BookFolder,
    page: dict,
    limits: Limits,
    *,
    binary: Path,
    book: dict,
    root: Path,
    force: bool = False,
    provider: Provider | None = None,
) -> PlanOutcome:
    """Plan one page: keep an existing plan (unless forced), else run the agent until it saves
    a valid plan or the attempts run out, feeding back the refusals."""
    number = int(page["page_number"])
    target, picture = plan_path(folder, number), page_picture(folder, page)
    if target.exists() and not force:
        return PlanOutcome(number, "kept")
    if not picture.is_file():
        return PlanOutcome(number, "failed", f"{picture.name} is missing")
    attempts, time_limit = read_planning(root)
    base = plan_prompt(page, book, picture_identity(picture), limits)
    feedback: list[str] = ["no attempts configured"]
    with tempfile.TemporaryDirectory(prefix="kiddo-plan-") as tmp:
        out = Path(tmp) / "plan.json"
        for attempt in range(1, attempts + 1):
            text = base
            if attempt > 1:
                text += "\nThe last attempt was refused. Fix these problems and save again:\n"
                text += "\n".join(f"- {problem}" for problem in feedback[:20])
            command = build_codex_command(
                binary,
                sys.executable,
                [
                    "-m",
                    "video_pipeline.plan_mcp",
                    "--picture",
                    str(picture),
                    "--page",
                    str(number),
                    "--out",
                    str(out),
                    "--root",
                    str(root),
                ],
                {"PYTHONPATH": str(PROJECT_ROOT)},
                prompt=text,
                images=[picture],
            )
            stamp = time.strftime("%Y%m%d-%H%M%S")
            log = folder.build_dir / "agent" / f"{stamp}-plan-p{number:02d}-{attempt}.jsonl"
            result = (provider or run_codex)(command, log, timeout_s=time_limit)
            if not out.exists():
                feedback = _refusals(result)
                continue
            try:
                plan = load_plan(out, limits)  # the tool validated it; check again on the way in
            except PlanError as exc:
                feedback = [str(exc)]
                out.unlink(missing_ok=True)
                continue
            if target.exists():
                shutil.copyfile(target, target.with_name(target.name + ".bak"))
            save_plan(target, plan)
            return PlanOutcome(number, "written")
    return PlanOutcome(number, "failed", feedback[0])


def plan_book(
    folder: BookFolder,
    limits: Limits,
    *,
    root: Path,
    pages: list[int] | None = None,
    force: bool = False,
    provider: Provider | None = None,
    binary: Path | None = None,
    log: Callable[[str], None] = print,
) -> list[PlanOutcome]:
    """Plan every story page (or only `pages`). Covers and maps are not pages and get no plan.
    A failing page never stops the others; AgentUnavailable is raised before any page."""
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    binary = binary or codex_status()
    outcomes: list[PlanOutcome] = []
    for page in sorted(book["pages"], key=lambda p: int(p["page_number"])):
        if pages and int(page["page_number"]) not in pages:
            continue
        outcome = plan_page(
            folder,
            page,
            limits,
            binary=binary,
            book=book,
            root=root,
            force=force,
            provider=provider,
        )
        reason = f" - {outcome.reason}" if outcome.reason else ""
        log(f"  page {outcome.page_number:02d}: {outcome.status}{reason}")
        outcomes.append(outcome)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.motion_plan",
        description="Write motion plans for a book's page pictures (spec 009).",
    )
    parser.add_argument("--book", required=True)
    parser.add_argument("--page", type=int, action="append", help="plan only this page (repeat)")
    parser.add_argument("--force", action="store_true", help="replace existing plans (.bak kept)")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        outcomes = plan_book(
            folder,
            load_limits(Path(args.root) / "config"),
            root=Path(args.root),
            pages=args.page,
            force=args.force,
        )
    except (LibraryError, AgentUnavailable, PlanError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    written = sum(o.status == "written" for o in outcomes)
    kept = sum(o.status == "kept" for o in outcomes)
    failed = [o for o in outcomes if o.status == "failed"]
    print(f"{written} written, {kept} kept, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
