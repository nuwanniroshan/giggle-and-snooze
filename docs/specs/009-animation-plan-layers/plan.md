# Implementation Plan: Spec 009 – motion plan and layer cutting

**Spec:** docs/specs/009-animation-plan-layers/spec.md (Approved)
**Decisions:** docs/adr/009-layer-segmentation-runtime.md (Accepted); ADR-010 belongs to spec 010
**Created:** 2026-11-10
**Status:** Approved

## Context

Story pages in the video are still pictures. The producer chose AI-planned layers: once per picture, Codex writes a motion plan (layers + motions + effects). A local step then cuts the layers (SAM 2.1 Core ML) and fills the background behind them (LaMa ONNX). This plan covers spec 009 only, meaning the plan and the cut. Spec 010 animates the layers.

What the code already has:
- `agents.build_codex_command` / `run_codex` (headless Codex with one MCP server, `read-only` sandbox, `view_image` disabled, no `-i`).
- The `story_mcp.py` + `write_story.generate` pattern: a submit tool that validates and saves to an `--out` path, with retry attempts that feed back the errors.
- `episode_tools.write_atomic`.
- `library.BookFolder`.
- `illustrate.illustrate(..., paint=...)` with an injectable painter.
- The studio's `jobs.STAGE_MODULES` / `stage_command`, `books.STAGES`, and `app.media_path` whitelist.
- The `tts.download_model` pattern (pinned `snapshot_download`, then `HF_HUB_OFFLINE=1`).
- In the venv: numpy, onnxruntime 1.31.1, huggingface-hub 2.4.0. **Not** installed: coremltools.

Working-tree note: the branch `feat/007-sound-library` has uncommitted spec 006/008 work. sdd-execute must first decide (with the producer) where that work goes before cutting `feat/009-animation-plan-layers`.

## Global Constraints

- Every key is snake_case, and pages are identified by `page_number`. JSON is written atomically, with `indent=2, ensure_ascii=False` (`episode_tools.write_atomic`).
- Plans live in `books/<slug>/animation/page_NN.json`, committed and hand-editable. Cut output goes to `books/<slug>/build/layers/page_NN/` (git-ignored).
- Motions: `sway, bob, rotate, breathe, shift`. Effects: `rain, snow, fireflies, falling_leaves, water_ripple, smoke, mist, glow` (FR-1). One definition, shared with spec 010.
- Default limits (FR-3): 8 layers, 3 effects, 4° rotation, 2% shift/bob of the shorter side, period 2–12 s, 3% breathe. They live in `config/animation.json`.
- Plans cover page pictures only, never covers or maps. Values are never silently changed.
- Cutting runs offline after `download` and makes no network request. It holds one page in memory at a time and runs as its own process.
- Cutting takes ≤ 10 s per page after load, ≤ 4 GB peak, and gives bit-exact output on a re-run. A no-change re-run of the pilot book takes ≤ 5 s.
- Unit tests use no real Codex, Core ML or ONNX. Real-model tests carry the new pytest marker `models` (excluded by default, like `tts`).
- Ruff config as in `pyproject.toml`. `pathlib` throughout. Type hints on public functions.

## Goal

Every story page picture can get a validated, hand-editable motion plan (on demand, or automatically when `illustrate` paints it). `cut_layers` turns each plan into cached RGBA layers and a filled background, and the studio shows plans, masks and backgrounds per page.

Components (Simplicity Gate: 3):
1. `video_pipeline/motion_plan.py`: schema, vocabulary, limits, validation, stale check, cut-field hash, `PlanTools`, `plan` CLI. Thin MCP server: `video_pipeline/plan_mcp.py`.
2. `video_pipeline/cut_layers.py`: download, segmenter and inpainter adapters, cut, cache, manifest, CLI.
3. Studio additions in `video_pipeline/studio/`.

New dependency: `coremltools` (pinned; ADR-009). No wrappers around pydantic, onnxruntime or coremltools beyond one small adapter class each.

---

## Phase 0: Spike – models run on the M1 Pro (ADR-009 gate)

**Implements:** FR-11 (partly), NFR-2/3/4/6 | **Satisfies:** gate for AC-4.1–4.3, AC-4.9
**Files:** `pyproject.toml` (+`coremltools`, marker `models`), `config/animation.json` (`models` block), `docs/specs/009-animation-plan-layers/spike.md`, `docs/specs/009-animation-plan-layers/spike_cut.py` (throwaway, kept for the record)
**Interfaces:** Consumes: nothing. Produces: the pinned `models` config (repo, revision, files, Core ML / ONNX input and output names, sizes) that Phase 4 reads; the spike verdict.

Prove the ADR-009 choice before building on it. The Core ML and ONNX tensor names are not published, so the spike reads them from the models and writes them into config.

- [ ] `uv add "coremltools>=8.3,<9"`; add the marker `"models: runs the real segmentation/fill models (must be downloaded)"` and extend `addopts` to `-m 'not codex and not tts and not ffmpeg and not models'`.
- [ ] Download `apple/coreml-sam2.1-small` and `Carve/LaMa-ONNX` (`lama_fp32.onnx`) with `huggingface_hub.snapshot_download`, and record the resolved commit SHAs.
- [ ] Introspect the I/O names and shapes:
  ```python
  import coremltools as ct, onnxruntime as ort
  for p in sorted(Path(snap).glob("*.mlpackage")):
      spec = ct.models.MLModel(str(p), compute_units=ct.ComputeUnit.ALL).get_spec()
      print(p.name, [(i.name, i.type) for i in spec.description.input],
            [o.name for o in spec.description.output])
  s = ort.InferenceSession(str(lama), providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
  print([(i.name, i.shape, i.type) for i in s.get_inputs()], [o.name for o in s.get_outputs()])
  ```
- [ ] `spike_cut.py`:
  - On pilot pages 1, 5 and 12, draw 3–4 hand-written boxes per page, run encoder → prompt encoder (box as two points, labels 2 and 3) → decoder → masks, then LaMa on the union mask.
  - Write overlays and backgrounds into scratch.
  - Measure time per page (after load) and peak RSS (`resource.getrusage`), and check two runs are bit-exact.
- [ ] Record the results in `spike.md`: seconds per page, peak memory, determinism, the producer's verdict on the masks, and the licences with sources (NFR-6).
- [ ] Write `config/animation.json` with the limits (Phase 1) and the `models` block:
  ```json
  "models": {
    "segmenter": {"repo": "apple/coreml-sam2.1-small", "revision": "<sha from spike>",
                  "encoder": "<name>.mlpackage", "prompt_encoder": "<name>.mlpackage",
                  "decoder": "<name>.mlpackage", "size": 1024,
                  "io": {"image": "image", "points": "points", "labels": "labels",
                         "embedding": "image_embedding", "feats": ["feats_s0", "feats_s1"],
                         "sparse": "sparse_embeddings", "dense": "dense_embeddings",
                         "masks": "low_res_masks", "scores": "scores"},
                  "license": "apache-2.0", "source": "https://huggingface.co/apple/coreml-sam2.1-small"},
    "fill": {"repo": "Carve/LaMa-ONNX", "revision": "<sha>", "file": "lama_fp32.onnx", "size": 512,
             "io": {"image": "<name>", "mask": "<name>", "output": "<name>"},
             "license": "apache-2.0", "source": "https://huggingface.co/Carve/LaMa-ONNX"}
  }
  ```
  The `io` names above are the expected defaults; the spike replaces them with what introspection prints.
- [ ] **Gate:** pass means ≤ 10 s/page, ≤ 4 GB, bit-exact, and masks the producer accepts. On fail, try `mlx-sam`, then the official `sam2` (ADR-009 order), supersede ADR-009, and re-plan Phase 4's segmenter adapter only.
- [ ] Commit: `chore(layers): spike SAM 2.1 Core ML + LaMa ONNX on the M1 Pro (Phase 0)`

---

## Phase 1: Plan schema, limits and validation

**Implements:** FR-1, FR-2, FR-3, FR-10 | **Satisfies:** AC-1.4, AC-3.1, AC-3.2, AC-3.3 (load half), AC-3.4, AC-3.5, AC-3.6
**Files:** `video_pipeline/motion_plan.py`, `config/animation.json` (limits), `tests/test_motion_plan.py`
**Interfaces:** Consumes: `episode_tools.write_atomic`, `library.BookFolder`. Produces:
- `MOTIONS`, `EFFECTS`;
- `Limits`, `load_limits(config_dir) -> Limits`;
- `MotionPlan` (pydantic);
- `plan_problems(data, limits, size) -> list[str]`;
- `load_plan(path, limits) -> MotionPlan` (raises `PlanError`);
- `save_plan(path, plan)`;
- `plan_path(folder, page_number) -> Path`;
- `picture_identity(path) -> PictureId`;
- `plan_state(folder, page, limits) -> PlanState`;
- `cut_fields_hash(plan) -> str`.

Tests first (`tests/test_motion_plan.py`, fixture `make_plan(**over)` returns a valid dict for a 1024×1024 picture):
- A valid plan loads, and save → load gives an equal model. The file ends in `\n` with 2-space indent (AC-3.6).
- `"motions":[{"type":"wiggle",...}]` → an error containing `layers[0].motions[0].type` and `wiggle` (AC-3.1).
- A box `[900,10,1100,50]` on 1024×1024 → an error containing `layers[0].box`, `1100` and `1024x1024` (AC-3.2).
- `rotate` amplitude 9 → an error containing `max 4` (AC-3.3). The loaded value is never clamped.
- Duplicate layer names, and an effect region `"layer": "ghost"` → errors naming them (AC-3.4).
- 9 layers / 4 effects → errors naming the limit (AC-1.4).
- `plan_state` with a picture whose sha256 differs → `"stale"`; missing picture → `"no_picture"`; no file → `"none"`; bad JSON → `"invalid"` with the message (AC-3.5).
- `cut_fields_hash` changes when a box, point or name changes, and stays the same when a motion, effect, z, depth or pivot changes (FR-9 input, AC-4.5).

Code (core):
```python
# video_pipeline/motion_plan.py
"""Motion plan for one story page picture (spec 009): schema, limits, validation, stale check."""
from __future__ import annotations
import hashlib, json
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
    max_layers: int; max_effects: int; max_rotate_deg: float; max_shift: float
    min_period_s: float; max_period_s: float; max_breathe: float

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
    direction: tuple[float, float] | None = None   # shift only

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
    type: Literal["rain", "snow", "fireflies", "falling_leaves", "water_ripple", "smoke", "mist", "glow"]
    region: Region | None = None
    intensity: float = Field(ge=0, le=1)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

class PictureId(_Strict):
    file: str; sha256: str; width: int; height: int

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
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out

def _inside(xy, w, h) -> bool:
    return 0 <= xy[0] <= w and 0 <= xy[1] <= h

def rule_problems(plan: MotionPlan, limits: Limits) -> list[str]:
    """Rules beyond the schema: picture bounds, limits, unique names, effect regions."""
    w, h, short = plan.picture.width, plan.picture.height, min(plan.picture.width, plan.picture.height)
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
            for j, pt in enumerate(getattr(layer, key)):
                if not _inside(pt, w, h):
                    out.append(f"layers[{i}].{key}[{j}]: {list(pt)} is outside the picture {w}x{h}")
        if not _inside(layer.pivot, w, h):
            out.append(f"layers[{i}].pivot: {list(layer.pivot)} is outside the picture {w}x{h}")
        for j, m in enumerate(layer.motions):
            where = f"layers[{i}].motions[{j}]"
            if not limits.min_period_s <= m.period_s <= limits.max_period_s:
                out.append(f"{where}.period_s: {m.period_s} not in {limits.min_period_s}-{limits.max_period_s} s")
            cap = {"rotate": limits.max_rotate_deg, "sway": limits.max_rotate_deg,
                   "bob": limits.max_shift, "shift": limits.max_shift, "breathe": limits.max_breathe}[m.type]
            if m.amplitude > cap:
                out.append(f"{where}.amplitude: {m.amplitude} over the limit (max {cap})")
            if m.type == "shift" and m.direction is None:
                out.append(f"{where}.direction: shift needs a direction")
    for k, eff in enumerate(plan.effects):
        if eff.region and eff.region.layer and eff.region.layer not in names:
            out.append(f"effects[{k}].region.layer: no layer named {eff.region.layer!r}")
        if eff.region and eff.region.box:
            x0, y0, x1, y1 = eff.region.box
            if not (_inside((x0, y0), w, h) and _inside((x1, y1), w, h)):
                out.append(f"effects[{k}].region.box: {list(eff.region.box)} is outside the picture {w}x{h}")
    return out

def plan_problems(data: object, limits: Limits) -> list[str]:
    try:
        plan = MotionPlan.model_validate(data)
    except ValidationError as exc:
        return [f"{_loc(e)}: {e['msg']} (got {e.get('input')!r})" for e in exc.errors()]
    return rule_problems(plan, limits)

def load_plan(path: Path, limits: Limits) -> MotionPlan:
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
    path = plan_path(folder, int(page["page_number"]))
    picture = folder.illustrations / (page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png")
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
        (l.name, list(l.box), sorted(map(list, l.include_points)), sorted(map(list, l.exclude_points)))
        for l in plan.layers
    )
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
```
`config/animation.json` `limits`: `{"max_layers": 8, "max_effects": 3, "max_rotate_deg": 4.0, "max_shift": 0.02, "min_period_s": 2.0, "max_period_s": 12.0, "max_breathe": 0.03}`, plus `"planning": {"attempts": 3, "time_limit_s": 600}` and `"cut": {"feather_px": 2}`.

(`max_shift` and `max_breathe` are fractions of the picture's shorter side or scale, as FR-3 says. Spec 010 applies the units.)

- [ ] Run `uv run pytest tests/test_motion_plan.py -v`: FAIL, then implement, then PASS.
- [ ] Commit: `feat(plan): motion plan schema, limits and validation (Phase 1)`

---

## Phase 2: Planning with Codex – tools, MCP server, `plan` CLI

**Implements:** FR-4, FR-5, FR-6 | **Satisfies:** AC-1.1, AC-1.2, AC-1.3, AC-1.5, AC-1.6, AC-3.3 (save half)
**Files:** `video_pipeline/motion_plan.py` (+`PlanTools`, `plan_page`, `plan_book`, CLI), `video_pipeline/plan_mcp.py`, `video_pipeline/agents.py` (`images` option), `tests/test_motion_plan.py`, `tests/test_plan_mcp.py`, `tests/test_agents.py`
**Interfaces:** Consumes: the Phase 1 API, `agents.codex_status/build_codex_command/run_codex/AgentUnavailable`, `episode.load_book`. Produces:
- `PlanTools(limits, picture: PictureId, page_number, out).save_motion_plan(plan: dict) -> dict`;
- `plan_page(folder, page, limits, *, binary, provider=None, force=False) -> PlanOutcome`;
- `plan_book(folder, limits, *, pages=None, force=False, provider=None, log=print) -> list[PlanOutcome]`;
- `main(argv) -> int` (`python -m video_pipeline.motion_plan --book S [--page N] [--force] [--root R]`).

Design:
- The MCP tool receives the plan **without** `picture`, `provider` and `written_at`. The server fills these from its command line, so the model can't forge the picture identity.
- `build_codex_command` gains `images: Sequence[Path] = ()` and emits `-i <path>` for each, before the prompt. The sandbox stays read-only.
- Retry follows `write_story.generate`: up to `planning.attempts`, feeding back errors.

Tests first:
- `tests/test_agents.py::test_codex_command_attaches_images`: `-i /x/p.png` appears before the prompt, and there's no `-i` without images.
- `tests/test_plan_mcp.py`:
  - `PlanTools.save_motion_plan(valid)` → `{"saved": True}`, and the file has `picture.sha256` from the server args;
  - an invalid plan → `{"saved": False, "errors": [...]}` and no file is written;
  - an amplitude over the limit → an error naming the limit, and nothing is saved (AC-3.3 save half);
  - a fourth save is refused (`MAX_SAVES = 3`).
- `tests/test_motion_plan.py` (with a fake provider that writes the `out` file, as `tests/test_write_story.py` does):
  - `plan_book` writes a plan per page with a picture and none for covers or the map (AC-1.1);
  - it keeps existing plans byte-for-byte without force and logs `kept` (AC-1.2);
  - with force it writes `page_NN.json.bak` and a new plan (AC-1.3);
  - when one page's provider fails, the other pages are written, the outcome names the page and reason, and `main` exits 1 (AC-1.5);
  - `--page 3` touches only page 3 (AC-1.6).

Code:
```python
# video_pipeline/agents.py – build_codex_command signature + body addition
def build_codex_command(binary, server_command, server_args, server_env, model=None,
                        prompt=AGENT_PROMPT, images: Sequence[Path] = ()) -> list[str]:
    ...
    if model:
        command += ["--model", model]
    for image in images:
        command += ["-i", str(image)]
    command += ["--", prompt]   # "--" so a prompt can never be read as a flag after -i
    return command
```
(Check during implementation that existing callers and tests accept the `--` separator; `illustrate.build_image_command` already uses it. If a test pins the exact argv, update it.)

```python
# video_pipeline/plan_mcp.py
"""MCP server for the motion plan (spec 009): one save tool; picture identity comes from argv."""
from __future__ import annotations
import argparse
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from video_pipeline.library import PROJECT_ROOT
from video_pipeline.motion_plan import PlanTools, load_limits, picture_identity

SERVER_NAME = "kiddo"

def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m video_pipeline.plan_mcp")
    p.add_argument("--picture", type=Path, required=True)
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return p.parse_args(argv)

def create_server(tools: PlanTools) -> MCPServer:
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def save_motion_plan(plan: dict) -> dict:
        """Save the motion plan {layers, effects} for the attached picture. Coordinates are in
        the picture's own pixels (see the size in the prompt). Returns {saved, errors}; fix
        every error and save again. At most 3 saves."""
        return tools.save_motion_plan(plan)

    return server

def main(argv=None) -> int:
    a = parse_args(argv)
    tools = PlanTools(load_limits(a.root / "config"), picture_identity(a.picture), a.page, a.out)
    create_server(tools).run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```
```python
# video_pipeline/motion_plan.py – additions
MAX_SAVES = 3

@dataclass
class PlanTools:
    limits: Limits
    picture: PictureId
    page_number: int
    out: Path
    provider: str = "codex"
    saves: int = 0

    def save_motion_plan(self, plan: dict) -> dict:
        self.saves += 1
        if self.saves > MAX_SAVES:
            return {"saved": False, "errors": [f"save limit reached ({MAX_SAVES} saves); stop"]}
        data = {**(plan if isinstance(plan, dict) else {}), "page_number": self.page_number,
                "picture": self.picture.model_dump(), "provider": self.provider,
                "written_at": datetime.now(UTC).isoformat(timespec="seconds")}
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
Choose 1-{max_layers} layers worth moving (characters, their parts like hair or antennae,
foreground leaves, water, clouds). For each: name (snake_case), description, a tight box
[x0,y0,x1,y1], optional include_points/exclude_points, z (higher = in front), depth
(0 far .. 1 near), pivot [x,y] where it would hinge, and motions from {motions}
(amplitude: degrees for sway/rotate max {rot}; fraction of the shorter side for bob/shift
max {shift}; scale fraction for breathe max {breathe}; period_s {pmin}-{pmax}; phase 0-1;
shift needs direction [dx,dy]). Add 0-{max_effects} effects from {effects} that fit the scene,
each with intensity 0-1, optional region {{"box": [...]}} or {{"layer": name}}, optional colour #rrggbb.
Keep everything subtle. Call save_motion_plan with {{"layers": [...], "effects": [...]}};
fix every error it returns and save again. Finish with one short line."""

@dataclass
class PlanOutcome:
    page_number: int
    status: Literal["written", "kept", "failed"]
    reason: str = ""

def _picture(folder: BookFolder, page: dict) -> Path:
    return folder.illustrations / (page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png")

def plan_page(folder: BookFolder, page: dict, limits: Limits, *, binary: Path, book: dict,
              root: Path, force: bool = False, provider=None) -> PlanOutcome:
    number = int(page["page_number"])
    target, picture = plan_path(folder, number), _picture(folder, page)
    if target.exists() and not force:
        return PlanOutcome(number, "kept")
    if not picture.is_file():
        return PlanOutcome(number, "failed", f"{picture.name} is missing")
    settings = json.loads((root / "config" / LIMITS_FILE).read_text(encoding="utf-8"))["planning"]
    pid = picture_identity(picture)
    style = book.get("style_bible", {})
    characters = " ".join(v for k, v in style.items() if k.endswith("_description"))
    base = PLAN_PROMPT.format(width=pid.width, height=pid.height, text=page.get("text", ""),
        prompt=page.get("prompt", ""), characters=characters, max_layers=limits.max_layers,
        motions=", ".join(MOTIONS), rot=limits.max_rotate_deg, shift=limits.max_shift,
        breathe=limits.max_breathe, pmin=limits.min_period_s, pmax=limits.max_period_s,
        max_effects=limits.max_effects, effects=", ".join(EFFECTS))
    with tempfile.TemporaryDirectory(prefix="kiddo-plan-") as tmp:
        out, feedback = Path(tmp) / "plan.json", []
        for attempt in range(1, int(settings["attempts"]) + 1):
            text = base + ("\nThe last attempt was refused. Fix:\n" + "\n".join(f"- {p}" for p in feedback[:20]) if feedback else "")
            command = build_codex_command(binary, sys.executable,
                ["-m", "video_pipeline.plan_mcp", "--picture", str(picture), "--page", str(number),
                 "--out", str(out), "--root", str(root)],
                {"PYTHONPATH": str(PROJECT_ROOT)}, prompt=text, images=[picture])
            log = folder.build_dir / "agent" / f"{time.strftime('%Y%m%d-%H%M%S')}-plan-p{number:02d}-{attempt}.jsonl"
            result = (provider or run_codex)(command, log, timeout_s=float(settings["time_limit_s"]))
            if out.exists():
                if target.exists():
                    shutil.copyfile(target, target.with_name(target.name + ".bak"))
                plan = load_plan(out, limits)   # the tool already validated; this re-checks
                save_plan(target, plan)
                return PlanOutcome(number, "written")
            feedback = [str(e.data["errors"][0]) for e in result.events
                        if e.kind == "tool-result" and isinstance(e.data, dict) and e.data.get("errors")
                        ] or [result.reason or "the agent saved nothing"]
    return PlanOutcome(number, "failed", feedback[0])

def plan_book(folder: BookFolder, limits: Limits, *, root: Path, pages: list[int] | None = None,
              force: bool = False, provider=None, binary: Path | None = None, log=print) -> list[PlanOutcome]:
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    binary = binary or codex_status()          # AgentUnavailable propagates before any page
    outcomes = []
    for page in sorted(book["pages"], key=lambda p: p["page_number"]):
        if pages and int(page["page_number"]) not in pages:
            continue
        outcome = plan_page(folder, page, limits, binary=binary, book=book, root=root,
                            force=force, provider=provider)
        log(f"  page {outcome.page_number:02d}: {outcome.status}" + (f" – {outcome.reason}" if outcome.reason else ""))
        outcomes.append(outcome)
    return outcomes

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m video_pipeline.motion_plan", description="Write motion plans for page pictures.")
    p.add_argument("--book", required=True); p.add_argument("--page", type=int, action="append")
    p.add_argument("--force", action="store_true"); p.add_argument("--root", type=Path, default=PROJECT_ROOT)
    a = p.parse_args(argv)
    try:
        folder = book_folder(a.book, a.root)
        outcomes = plan_book(folder, load_limits(a.root / "config"), root=a.root, pages=a.page, force=a.force)
    except (LibraryError, AgentUnavailable, PlanError, OSError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    failed = [o for o in outcomes if o.status == "failed"]
    print(f"{sum(o.status == 'written' for o in outcomes)} written, "
          f"{sum(o.status == 'kept' for o in outcomes)} kept, {len(failed)} failed")
    return 1 if failed else 0
```
(Imports are added at the top: `argparse, shutil, sys, tempfile, time, datetime/UTC`; `agents.build_codex_command/run_codex/codex_status/AgentUnavailable`; `library.PROJECT_ROOT/book_folder/LibraryError`.)

- [ ] Run `uv run pytest tests/test_motion_plan.py tests/test_plan_mcp.py tests/test_agents.py -v`: FAIL, then implement, then PASS. The full suite still passes (`uv run pytest`).
- [ ] Real check (marker `codex`): `tests/test_motion_plan.py::test_real_plan_page_one` plans pilot page 1 and the plan validates.
- [ ] Commit: `feat(plan): plan pictures with Codex through a validating tool (Phase 2)`

---

## Phase 3: Plans written by `illustrate`

**Implements:** FR-13 | **Satisfies:** AC-2.1, AC-2.2, AC-2.3, AC-2.4, AC-2.5
**Files:** `video_pipeline/illustrate.py`, `tests/test_illustrate.py`
**Interfaces:** Consumes: `motion_plan.plan_page`, `load_limits`, `PlanOutcome`. Produces: `illustrate(book, folder, binary, force=False, paint=paint_one, log=print, plan=None) -> list[str]`, where `plan: Callable[[dict], PlanOutcome] | None` is called after each painted page.

Tests first (fake painter + fake planner recording calls):
- Painting pages 1–2 and both covers calls the planner for pages 1 and 2 only, each right after its paint and before the next paint (AC-2.1, AC-2.2).
- A planner returning `failed` → the picture still exists, the log has `page 01: plan failed – <reason>`, and painting continues (AC-2.3).
- With `force=True`, the planner is called with force, and an old plan gets a `.bak` (through the real `plan_page` with a fake provider) (AC-2.4).
- A kept picture means no planner call (AC-2.5).

Code:
```python
# illustrate.py – inside illustrate(); stems map to pages
pages = {Path(_page_file(p)).stem: p for p in book["pages"]}
...
        else:
            log(f"  {stem}: painting...")
            paint(binary, prompt, reference, target)
            painted.append(stem)
            if plan is not None and stem in pages:
                outcome = plan(pages[stem])
                log(f"  {stem}: plan {outcome.status}" + (f" – {outcome.reason}" if outcome.reason else ""))
# main(): build the planner once
limits = load_limits(args.root / "config")
planner = lambda page: plan_page(folder, page, limits, binary=binary, book=book,
                                 root=args.root, force=True)   # a fresh paint always gets a fresh plan
painted = illustrate(book, folder.illustrations, binary, force=args.force, plan=planner)
```
(`force=True` in the planner is correct: it only runs for a page that was just painted, so any existing plan is stale. The `.bak` keeps hand edits, per AC-2.4.)

- [ ] Tests FAIL, then implement, then PASS.
- [ ] Commit: `feat(plan): illustrate plans each painted page (Phase 3)`

---

## Phase 4: `cut_layers` – download, segment, fill, cache

**Implements:** FR-7, FR-8, FR-9, FR-11 | **Satisfies:** AC-4.1–AC-4.10
**Files:** `video_pipeline/cut_layers.py`, `tests/test_cut_layers.py`, `.gitignore` (unchanged: `build/` is already ignored)
**Interfaces:** Consumes: `motion_plan.load_limits/plan_state/cut_fields_hash/MotionPlan`, the `models` config from Phase 0, and `library.book_folder`. Produces:
- `Segmenter` protocol: `masks(image: np.ndarray, layers: list[Layer]) -> list[np.ndarray[bool]]`;
- `Inpainter` protocol: `fill(image: np.ndarray, mask: np.ndarray) -> np.ndarray`;
- `cut_hash(plan, feather_px, model_ids) -> str`;
- `cut_page(folder, page_number, picture, plan, seg, fill, feather_px, model_ids) -> CutResult`;
- `cut_book(folder, *, root, pages=None, force=False, load_models=None, log=print) -> list[CutResult]` (None → `_load_real_models(root, models_cfg) -> (Segmenter, Inpainter)`);
- `download(root) -> list[str]`;
- `layers_dir(folder, n) -> Path`;
- the manifest format `layers.json`: `{cut_hash, picture_sha256, model_ids, feather_px, background, layers:[{name, file, offset:[x,y], size:[w,h], empty}]}`.

Models load lazily, only when some page needs cutting (AC-4.4: no model work on a no-change run). Tests inject a fake segmenter (box → filled rectangle mask, or empty for a layer named `nothing`) and a fake inpainter (mean colour fill).

Tests first (`tests/test_cut_layers.py`; a 64×48 synthetic picture with a red square on blue, a plan with layer `square`):
- AC-4.1: the output folder has `square.png` (RGBA, cropped to the mask bounds), `background.png` (64×48 RGB) and `layers.json` with offset, size and `cut_hash`.
- AC-4.2: compositing `square.png` at its offset over `background.png` gives a mean absolute difference ≤ 2/255 against the original inside the mask.
- AC-4.3: `background.png` equals the original outside the mask dilated by `feather_px`, and inside it is opaque and not black.
- AC-4.4: a second `cut_book` with a `load_models` that raises if called → it passes, and the file mtimes and bytes are unchanged.
- AC-4.5: editing a motion amplitude in the plan → `load_models` is not called.
- AC-4.6: a stale plan → that page reports `refused: stale plan – re-run python -m video_pipeline.motion_plan`, other pages are cut, and `main` exits 1.
- AC-4.7: no plan → `no plan`, skipped.
- AC-4.8: `load_models` with a missing snapshot → `ModelsMissing` naming `python -m video_pipeline.cut_layers download`. The test asserts `HF_HUB_OFFLINE == "1"` during the load and that no socket is opened (monkeypatch `socket.socket` to raise).
- AC-4.10: a layer whose mask is empty → the page completes, the manifest has `empty: true`, and the log names the layer.
- Interrupted write: a folder with layer files but no `layers.json` → re-cut.
- FR-9: `cut_hash` changes with the picture sha, the boxes or points, `feather_px` or a model id, and not with a motion.

Code (core; the real adapters at the end):
```python
# video_pipeline/cut_layers.py
"""Cut each planned layer out of a page picture and fill the background behind it (spec 009).

    python -m video_pipeline.cut_layers --book <slug> [--page N] [--force] [--root PATH]
    python -m video_pipeline.cut_layers download [--root PATH]
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import numpy as np
from PIL import Image, ImageFilter
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder
from video_pipeline.motion_plan import Layer, LIMITS_FILE, MotionPlan, cut_fields_hash, load_limits, plan_state

DOWNLOAD_HINT = "python -m video_pipeline.cut_layers download"
MANIFEST = "layers.json"

class ModelsMissing(RuntimeError):
    """The pinned models are not downloaded; the message names the download command."""

class Segmenter(Protocol):
    def masks(self, image: np.ndarray, layers: list[Layer]) -> list[np.ndarray]: ...

class Inpainter(Protocol):
    def fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray: ...

@dataclass
class CutResult:
    page_number: int
    status: str            # "cut" | "cached" | "no plan" | "refused" | "failed"
    detail: str = ""
    empty: tuple[str, ...] = ()

def layers_dir(folder: BookFolder, page_number: int) -> Path:
    return folder.build_dir / "layers" / f"page_{page_number:02d}"

def cut_hash(plan: MotionPlan, feather_px: int, model_ids: dict[str, str]) -> str:
    key = {"picture": plan.picture.sha256, "fields": cut_fields_hash(plan),
           "feather_px": feather_px, "models": dict(sorted(model_ids.items()))}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()

def _feather(mask: np.ndarray, px: int) -> np.ndarray:
    """Bool mask → float alpha 0..1 with a `px`-wide soft edge (deterministic Pillow blur)."""
    im = Image.fromarray(mask.astype(np.uint8) * 255)
    if px > 0:
        im = im.filter(ImageFilter.GaussianBlur(px / 2))
    return np.asarray(im, dtype=np.float32) / 255.0

def _dilate(mask: np.ndarray, px: int) -> np.ndarray:
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(2 * px + 1))
    return np.asarray(im) > 0

def _bounds(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha > 0)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

def cut_page(folder, page_number, picture: Path, plan: MotionPlan, seg: Segmenter,
             fill: Inpainter, feather_px: int, model_ids: dict[str, str]) -> CutResult:
    out = layers_dir(folder, page_number)
    with Image.open(picture) as im:
        rgb = np.asarray(im.convert("RGB"))
    masks = seg.masks(rgb, plan.layers)
    entries, empty, union = [], [], np.zeros(rgb.shape[:2], bool)
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).unlink(missing_ok=True)        # a missing manifest marks a half-written folder
    for layer, mask in zip(plan.layers, masks, strict=True):
        alpha = _feather(mask, feather_px)
        box = _bounds(alpha)
        if box is None:
            empty.append(layer.name)
            entries.append({"name": layer.name, "file": None, "offset": None, "size": None, "empty": True})
            continue
        x0, y0, x1, y1 = box
        rgba = np.dstack([rgb[y0:y1, x0:x1], (alpha[y0:y1, x0:x1] * 255).round().astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(out / f"{layer.name}.png", optimize=False)
        entries.append({"name": layer.name, "file": f"{layer.name}.png", "offset": [x0, y0],
                        "size": [x1 - x0, y1 - y0], "empty": False})
        union |= mask
    hole = _dilate(union, feather_px)
    background = fill.fill(rgb, hole) if hole.any() else rgb
    background = np.where(hole[..., None], background, rgb).astype(np.uint8)
    Image.fromarray(background, "RGB").save(out / "background.png", optimize=False)
    write_atomic(out / MANIFEST, {
        "cut_hash": cut_hash(plan, feather_px, model_ids), "picture_sha256": plan.picture.sha256,
        "model_ids": model_ids, "feather_px": feather_px, "background": "background.png",
        "layers": entries})
    return CutResult(page_number, "cut", empty=tuple(empty))

def _cached(out: Path, key: str) -> bool:
    try:
        manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    files = [e["file"] for e in manifest["layers"] if e["file"]] + [manifest["background"]]
    return manifest.get("cut_hash") == key and all((out / f).is_file() for f in files)

def cut_book(folder: BookFolder, *, root: Path, pages: list[int] | None = None, force: bool = False,
             load_models=None, log=print) -> list[CutResult]:
    config = json.loads((root / "config" / LIMITS_FILE).read_text(encoding="utf-8"))
    limits, feather = load_limits(root / "config"), int(config["cut"]["feather_px"])
    model_ids = {k: f"{v['repo']}@{v['revision']}" for k, v in config["models"].items()}
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    models, results = None, []
    for page in sorted(book["pages"], key=lambda p: p["page_number"]):
        n = int(page["page_number"])
        if pages and n not in pages:
            continue
        st = plan_state(folder, page, limits)
        if st.state == "none":
            results.append(CutResult(n, "no plan"))
        elif st.state == "stale":
            results.append(CutResult(n, "refused", f"stale plan – re-run python -m video_pipeline.motion_plan --book {folder.slug} --page {n}"))
        elif st.state in ("invalid", "no_picture"):
            results.append(CutResult(n, "failed", st.detail))
        elif not force and _cached(layers_dir(folder, n), cut_hash(st.plan, feather, model_ids)):
            results.append(CutResult(n, "cached"))
        else:
            if models is None:
                models = (load_models or _load_real_models)(root, config["models"])  # ModelsMissing propagates
            started = time.perf_counter()
            picture = folder.illustrations / st.plan.picture.file
            result = cut_page(folder, n, picture, st.plan, models[0], models[1], feather, model_ids)
            result.detail = f"{time.perf_counter() - started:.1f} s"
            results.append(result)
        r = results[-1]
        log(f"  page {n:02d}: {r.status}" + (f" – {r.detail}" if r.detail else "")
            + (f" (empty layers: {', '.join(r.empty)})" if r.empty else ""))
    return results
```
Real adapters, filled in with the I/O names from the Phase 0 config:
- `SamCoreML(models_cfg)`:
  1. Load the three `.mlpackage` files once from the offline snapshot (`ct.models.MLModel(..., compute_units=ct.ComputeUnit.ALL)`).
  2. Resize the picture to `size` (1024; letterbox for 16:9, recording scale and offset) and run the encoder once per page.
  3. Per layer, build the points: the box corners with labels 2 and 3, `include_points` as 1 and `exclude_points` as 0, scaled. Run the prompt encoder, then the decoder.
  4. Take the best-score mask, upsample it to picture size, threshold at 0, return bool.
- `LamaOnnx(models_cfg)`:
  1. Open an `onnxruntime.InferenceSession` on `CPUExecutionProvider` (deterministic, NFR-4; CoreML EP only if the spike shows it bit-exact).
  2. Per connected hole region, crop a square around it with a 32 px margin, resize to 512, run, resize back and paste only inside the hole.
- `_load_real_models(root, cfg)`:
  - set `os.environ["HF_HUB_OFFLINE"] = "1"`;
  - resolve each snapshot with `snapshot_download(repo, revision=..., local_files_only=True)`;
  - on `LocalEntryNotFoundError` or `FileNotFoundError` raise `ModelsMissing(f"models not downloaded: run {DOWNLOAD_HINT}")`.
- `download(root)`:
  - `snapshot_download(repo, revision=pinned, allow_patterns=[...files])` for both models;
  - print the repo, revision, licence and local path (FR-11). Models live in the HF cache, outside `build/`.
- `main(argv)`:
  - `download` subcommand, or `--book/--page/--force/--root`;
  - exit 1 when any page is `refused` or `failed`, or on `ModelsMissing`, `LibraryError` or `OSError`;
  - the summary line counts cut / cached / no plan / refused / failed.

Real-model tests (marker `models`):
- `test_real_cut_pilot_page_one`: cut pilot page 1 from a checked-in fixture plan (`tests/fixtures/plan_page_01.json`). Every non-empty layer covers ≥ 50% of its box. A second run is bit-exact. Time is ≤ 10 s and peak RSS is ≤ 4 GB (NFR-2, NFR-3, NFR-4).

- [ ] Unit tests FAIL, then implement the core, then PASS. Then implement the adapters and run `uv run pytest -m models tests/test_cut_layers.py` (PASS).
- [ ] Commit: `feat(layers): cut planned layers and fill the background, cached (Phase 4)`

---

## Phase 5: Studio – plan state, animation view, Plan/Cut jobs

**Implements:** FR-12 | **Satisfies:** AC-5.1, AC-5.2, AC-5.3, AC-5.4, AC-5.5
**Files:** `video_pipeline/studio/books.py`, `video_pipeline/studio/jobs.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/book.html`, new `video_pipeline/studio/templates/animation.html`, `tests/test_studio_books.py`, `tests/test_studio_app.py`, `tests/test_studio_jobs.py`
**Interfaces:** Consumes: `motion_plan.plan_state/load_limits/plan_path`, `cut_layers.layers_dir/cut_hash/MANIFEST`. Produces:
- `books.ANIMATION_STAGES = ("plan", "cut")`;
- `books.page_animation(paths, book) -> list[dict]` (per page: `page_number, plan_state, detail, cut_state ∈ {"cut","needs cut","—"}`);
- routes `GET /books/{slug}/animation` and `GET /books/{slug}/animation/{number:int}`;
- media kinds `layer` and `mask` (whitelisted names from the manifest only).

Design:
- `plan` and `cut` join `jobs.STAGE_MODULES` (`video_pipeline.motion_plan`, `video_pipeline.cut_layers`) and `books.STAGE_LABELS`. They are **not** added to `books.STAGES`, so the script → voices → render next-action flow is unchanged.
- `app._run_stage` accepts `stage in sb.STAGES + sb.ANIMATION_STAGES`, and `gate()` has no extra gate for them (cut on a stale plan reports per page).
- Plans are read from disk on every request, so there is no cache and edits show on reload (AC-5.5).
- The masks overlay is a PNG made on request from the layer PNG alphas, each tinted with a fixed colour from a palette. It is cached in memory by `cut_hash` and served through `media_path(kind="mask", name=f"page_{n:02d}.png")`.
- Boxes, points and labels are drawn client-side with inline SVG over the picture, with no JS build.

Tests first:
- `tests/test_studio_books.py`: `page_animation` returns `none/ok/stale/invalid` (with message) per page and `cut`/`needs cut` against the manifest hash (AC-5.1, AC-5.3).
- `tests/test_studio_jobs.py`: `stage_command("plan", book, paths)` → `[python, -m, video_pipeline.motion_plan, --book, slug, --root, R]`, and `"cut"` → `cut_layers` (AC-5.4).
- `tests/test_studio_app.py` (Starlette `TestClient`, fake runner):
  - the book page links to the Animation view and shows the plan states;
  - `GET /books/s/animation/1` shows the layer names, z, depth, pivot, motions and effects, plus an `<svg>` with one `<rect>` per box (AC-5.2);
  - after a fake cut, the page shows the mask image and the background image and says `cut`; after a box edit it says `needs cut` (AC-5.3);
  - `POST /books/s/run/plan` and `/run/cut` start jobs and are refused when busy (AC-5.4);
  - editing the plan file then re-requesting shows the new value (AC-5.5);
  - `/media/.../layer/../../etc` → 404; only manifest names are served.

Code (key parts):
```python
# studio/books.py
from video_pipeline.motion_plan import load_limits, plan_state
from video_pipeline.cut_layers import MANIFEST, cut_hash, layers_dir
ANIMATION_STAGES = ("plan", "cut")
STAGE_LABELS |= {"plan": "motion planning", "cut": "layer cutting"}

def page_animation(paths: StudioPaths, book: Book) -> list[dict]:
    folder = folder_of(paths, book)
    limits = load_limits(paths.config_dir)
    config = json.loads((paths.config_dir / "animation.json").read_text(encoding="utf-8"))
    feather = int(config["cut"]["feather_px"])
    model_ids = {k: f"{v['repo']}@{v['revision']}" for k, v in config["models"].items()}
    rows = []
    for page in sorted(load_book(book.book_path)["pages"], key=lambda p: p["page_number"]):
        st = plan_state(folder, page, limits)
        cut = "—"
        if st.state == "ok":
            manifest = _read_dict(layers_dir(folder, int(page["page_number"])) / MANIFEST) or {}
            cut = "cut" if manifest.get("cut_hash") == cut_hash(st.plan, feather, model_ids) else "needs cut"
        rows.append({"page_number": int(page["page_number"]), "plan_state": st.state,
                     "detail": st.detail, "cut_state": cut, "plan": st.plan})
    return rows
# studio/jobs.py
STAGE_MODULES |= {"plan": "video_pipeline.motion_plan", "cut": "video_pipeline.cut_layers"}
```
The `animation.html` template extends `base.html`. It has a table of pages (state, cut state, link), and per page:
```html
<figure class="anim">
  <img src="{{ picture_url }}" alt="Page {{ n }} picture">
  <svg viewBox="0 0 {{ plan.picture.width }} {{ plan.picture.height }}" aria-label="layer boxes">
    {% for l in plan.layers %}
    <rect x="{{ l.box[0] }}" y="{{ l.box[1] }}" width="{{ l.box[2]-l.box[0] }}" height="{{ l.box[3]-l.box[1] }}"/>
    <text x="{{ l.box[0]+4 }}" y="{{ l.box[1]+20 }}">{{ l.name }}</text>
    {% for p in l.include_points %}<circle cx="{{ p[0] }}" cy="{{ p[1] }}" r="6" class="inc"/>{% endfor %}
    {% for p in l.exclude_points %}<circle cx="{{ p[0] }}" cy="{{ p[1] }}" r="6" class="exc"/>{% endfor %}
    {% endfor %}
  </svg>
</figure>
{% if cut_state == "cut" %}<img src="{{ mask_url }}" alt="masks"><img src="{{ background_url }}" alt="filled background">{% endif %}
```
It uses the black-and-white CSS already in `base.html`, plus `figure.anim { position: relative } figure.anim svg { position: absolute; inset: 0; width: 100%; height: 100% }`.

- [ ] Tests FAIL, then implement, then PASS. The full suite passes.
- [ ] Check in the browser: `preview_start {name: "studio"}`, open the pilot book's Animation view, and screenshot a cut page.
- [ ] Commit: `feat(studio): animation view with plans, masks and Plan/Cut jobs (Phase 5)`

---

## Phase 6: Integration Verification

**Implements:** All FRs | **Satisfies:** All ACs; NFR-1–7

- [ ] `uv run pytest` passes (unit), and `uv run ruff check video_pipeline tests` is clean.
- [ ] `uv run pytest -m "codex or models"` passes (real provider + real models).
- [ ] Pilot end to end:
  1. Run `motion_plan --book the-snail-mail-express`: 20 plans, 0 failed.
  2. Run `cut_layers download`.
  3. Run `cut_layers --book …` and record the time per page and the peak RSS (`/usr/bin/time -l`). Targets: ≤ 10 s per page, ≤ 4 GB.
  4. Run it again: everything cached, ≤ 5 s total.
- [ ] Run `cut_layers` with Wi-Fi off (or `HF_HUB_OFFLINE=1` and the network blocked): it works (NFR-1).
- [ ] Producer review in the studio: ≥ 80% of layers clean (NFR-7). Fix the weak ones by editing boxes or points and re-cutting, and record the counts in `spike.md`.
- [ ] Check that a book with no plans still runs `build_book.py`, `synthesize_voices` and `render_episode` unchanged (NFR-5); no existing tests change behaviour.
- [ ] Update `CLAUDE.md` (commands + architecture line for spec 009), `.claude/memory/steering/tech-stack.md` (coremltools, SAM 2.1 Core ML, LaMa ONNX), and `.claude/rules/` only if a new lesson came up.
- [ ] Run `sdd-superpowers:sdd-review` (implementation mode), then commit `feat: complete spec 009 motion plan and layer cutting`.

## Quickstart Validation

```bash
uv sync --inexact
uv run python -m video_pipeline.motion_plan --book the-snail-mail-express          # plans in books/<slug>/animation/
uv run python -m video_pipeline.cut_layers download                                # once
uv run python -m video_pipeline.cut_layers --book the-snail-mail-express           # build/layers/page_NN/
uv run python -m video_pipeline.studio                                             # open Animation view per page
```
Edit one box in `books/the-snail-mail-express/animation/page_05.json`. The studio shows "needs cut", and re-running `cut_layers` re-cuts only page 5.

## Coverage map

| FR / AC | Phase |
|---|---|
| FR-1, FR-2, FR-3, FR-10; AC-1.4, AC-3.1–3.6 | 1 |
| FR-4, FR-5, FR-6; AC-1.1–1.3, 1.5, 1.6, AC-3.3 (save) | 2 |
| FR-13; AC-2.1–2.5 | 3 |
| FR-7, FR-8, FR-9, FR-11; AC-4.1–4.10 | 0 (gate), 4 |
| FR-12; AC-5.1–5.5 | 5 |
| NFR-1–7 | 0, 4, 6 |

Gates:
- **Simplicity:** 3 components, plus one new dependency (coremltools, required by ADR-009).
- **Anti-abstraction:** pydantic models are the only plan representation; the adapters are thin.
- **Integration-first:** the schema and tool contracts (Phases 1–2) are tested before cutting and the studio.
