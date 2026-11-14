# Implementation Plan: AI artifact cleanup with SAM and LaMa

**Spec:** docs/specs/026-artifact-cleanup/spec.md (Approved 2026-11-14)
**Decisions:** docs/adr/026-cleaned-picture-overwrite.md (Accepted)
**Created:** 2026-11-14

## Global Constraints

- Story pages only; covers are refused with "only story pages are supported" (AC-3.3). `--cleanup` needs `--page` (AC-4.1).
- The cleaned picture replaces `page_NN.png` immediately; the previous bytes are kept as `page_NN.png.bak`, written before the picture and only when at least one region applied (ADR-026, FR-6, AC-1.6, AC-2.1).
- Pixels outside the grown masks are bit-identical to the original; size and format unchanged (AC-1.3, NFR-4).
- A region that is empty, over `max_region_share` of the picture, or fails to segment or fill is skipped, never partly applied; each skip is logged with its reason; other regions still apply (AC-1.4).
- Codex output: validating save tool, 3 attempts, each refusal lists every problem (AC-1.5, FR-2). An empty region list is valid and means "nothing to clean" (AC-1.1).
- `episode_script.json`, its hash, review mark and voice approval are never touched (AC-3.2). Plan, cuts and rendered sections go stale through the picture sha256 check only (AC-3.1).
- Overwriting an existing `.bak` needs confirmation: studio asks, CLI needs `--yes` (AC-2.2). Accept keeps the `.bak`; Revert restores the exact bytes and removes the `.bak` (AC-2.4, AC-2.5).
- Models load only inside the cleanup job (`cut_layers.load_real_models`); Codex text never reaches a shell or file name (NFR-1, NFR-3).
- Python 3.12, Ruff (line 100), pathlib, atomic writes (`*.tmp` then `os.replace`), pure logic unit-tested with fakes, run only tests for changed files (project memory). Commits `type(scope): message` with the Co-Authored-By trailer, staged by explicit path (the working tree has unrelated uncommitted edits).

## Goal

`illustrate --book SLUG --page N --cleanup` flags artifact regions with Codex, masks them with SAM, fills them with LaMa, keeps a `.bak`, and the studio shows before and after with Accept and Revert.

---

## Phase 0: Contracts and tests first

**Implements:** FR-2, FR-3, FR-4, FR-5, FR-6 | **Satisfies:** AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-2.1, AC-2.4, AC-2.5, AC-3.3, AC-4.1
**Files:** `tests/test_cleanup.py` (new), `tests/test_cleanup_mcp.py` (new), additions to `tests/test_illustrate.py`
**Interfaces:** Consumes nothing. Produces the failing tests that Phases 1–3 satisfy: `cleanup.region_problems`, `cleanup.grow_mask`, `cleanup.composite`, `cleanup.apply_regions`, `cleanup.cleanup_page`, `cleanup.accept_page`, `cleanup.revert_page`, `cleanup.RegionTools`, `illustrate.parse_args` flag rules.

- [ ] Write `tests/test_cleanup.py` (pure tests; helpers build a 160×90 RGB picture, a fake segmenter returning a box mask for each `Layer` it is given, and a fake inpainter returning solid green):

```python
import numpy as np
import pytest

from video_pipeline import cleanup as cl

SIZE = (160, 90)
SETTINGS = cl.CleanupSettings(
    max_regions=6, max_points=4, max_reason_chars=120, margin_px=2,
    max_region_share=0.05, attempts=3, time_limit_s=600.0,
)


def region(box=(10, 10, 30, 30), points=(), reason="extra finger"):
    return {"box": list(box), "include_points": [list(p) for p in points], "reason": reason}


def test_region_problems_accepts_good_and_empty():
    assert cl.region_problems({"regions": []}, SIZE, SETTINGS) == []
    assert cl.region_problems({"regions": [region()]}, SIZE, SETTINGS) == []


@pytest.mark.parametrize("bad", [
    region(box=(10, 10, 5, 30)), region(box=(0, 0, 500, 30)), region(reason=""),
    region(reason="x" * 500), region(points=[(1, 1)] * 9), region(points=[(999, 1)]),
])
def test_region_problems_lists_every_problem(bad):
    assert cl.region_problems({"regions": [bad]}, SIZE, SETTINGS)


def test_too_many_regions_refused():
    assert cl.region_problems({"regions": [region()] * 7}, SIZE, SETTINGS)


def test_composite_changes_only_inside_hole():
    rgb = np.full((90, 160, 3), 100, np.uint8)
    filled = np.full_like(rgb, 200)
    hole = np.zeros((90, 160), bool)
    hole[10:20, 10:20] = True
    out = cl.composite(rgb, filled, hole)
    assert (out[hole] == 200).all()
    assert (out[~hole] == 100).all()


def test_apply_regions_skips_oversized_and_empty_but_applies_rest():
    rgb = np.full((90, 160, 3), 100, np.uint8)
    regions = [region((10, 10, 30, 30)), region((0, 0, 160, 90)), region((50, 50, 60, 60))]
    masks = [_box_mask((10, 10, 30, 30)), _box_mask((0, 0, 160, 90)), np.zeros((90, 160), bool)]
    result = cl.apply_regions(rgb, regions, masks, FakeFill(), SETTINGS)
    assert [a["index"] for a in result.applied] == [0]
    assert {s["index"]: s["why"] for s in result.skipped}.keys() == {1, 2}
    assert (result.image[~result.hole] == 100).all()


def test_cleanup_page_writes_bak_then_picture_and_record(tmp_path, book):
    ...  # FakeCodex({"regions":[region()]}); picture changed only inside grown mask; .bak == old bytes;
         # record lists applied; script bytes unchanged


def test_cleanup_page_nothing_to_clean_changes_nothing(tmp_path, book): ...   # regions [] -> no .bak, bytes same, status "nothing"
def test_cleanup_page_all_skipped_fails_without_bak(tmp_path, book): ...      # oversized only -> status "failed", no .bak
def test_cleanup_refuses_covers(book): ...                                    # stem cover_front -> CleanupError
def test_existing_bak_needs_yes(book): ...                                    # .bak present, yes=False -> CleanupError; yes=True overwrites
def test_accept_keeps_bak_and_marks_reviewed(book): ...
def test_revert_restores_bytes_and_removes_bak_and_record(book): ...
def test_revert_without_bak_is_refused(book): ...
def test_codex_refusals_exhaust_attempts(book): ...                           # provider never saves -> failed after 3, nothing changed
```

(Every `...` test is written in full in the file; they follow `test_apply_regions…` setup. `FakeFill`, `FakeSeg`, `FakeCodex` and the `book` fixture live at the top of the file.)

- [ ] Write `tests/test_cleanup_mcp.py`:

```python
from video_pipeline import cleanup as cl

SIZE = (160, 90)


def test_save_tool_writes_only_valid(tmp_path):
    out = tmp_path / "r.json"
    tools = cl.RegionTools(SIZE, SETTINGS, out)
    assert tools.save_artifact_regions({"regions": [{"box": [5, 5, 1, 9], "reason": "x"}]})["saved"] is False
    assert not out.exists()
    ok = {"regions": [{"box": [5, 5, 20, 20], "include_points": [], "reason": "blob"}]}
    assert tools.save_artifact_regions(ok)["saved"] is True and out.exists()


def test_fourth_save_refused(tmp_path): ...
def test_empty_list_saves(tmp_path): ...
def test_cleanup_prompt_mentions_size_and_tool(): ...
```

(`SETTINGS` imported from the same fixture module as above.)

- [ ] Add to `tests/test_illustrate.py`:

```python
def test_cleanup_flag_needs_page():
    with pytest.raises(SystemExit):
        illustrate.parse_args(["--book", "x", "--cleanup"])


def test_cleanup_refuses_covers():
    with pytest.raises(SystemExit):
        illustrate.parse_args(["--book", "x", "--cover", "front", "--cleanup"])


def test_cleanup_with_page_and_yes_parses():
    args = illustrate.parse_args(["--book", "x", "--page", "3", "--cleanup", "--yes"])
    assert args.cleanup and args.yes and args.page == 3
```

- [ ] Run: `uv run pytest tests/test_cleanup.py tests/test_cleanup_mcp.py tests/test_illustrate.py -q` — expect FAIL (names missing)
- [ ] Commit: `test(cleanup): contract tests for artifact cleanup (spec 026)`

---

## Phase 1: Pure cleanup logic and config

**Implements:** FR-3, FR-4, FR-6 (picture and `.bak` writes), FR-5 (record) | **Satisfies:** AC-1.2, AC-1.3, AC-1.4, AC-1.6, AC-2.1, AC-2.4, AC-2.5
**Files:** `config/cleanup.json` (new), `video_pipeline/cleanup.py` (new), `tests/test_cleanup.py`
**Interfaces:** Consumes `cut_layers.dilate`, `motion_plan.Layer`. Produces `CleanupSettings`, `load_settings(config_dir)`, `region_problems(data, size, settings) -> list[str]`, `grow_mask(mask, px) -> ndarray`, `composite(rgb, filled, hole) -> ndarray`, `apply_regions(rgb, regions, masks, fill, settings) -> Applied(image, hole, applied, skipped)`, `bak_path(picture)`, `record_path(picture)`, `write_picture(image, picture, *, yes) -> None`, `accept_page(picture)`, `revert_page(picture)`, `CleanupError`.

- [ ] Create `config/cleanup.json`:

```json
{
  "max_regions": 6,
  "max_points": 4,
  "max_reason_chars": 120,
  "margin_px": 6,
  "max_region_share": 0.05,
  "attempts": 3,
  "time_limit_s": 600
}
```

- [ ] Implement `video_pipeline/cleanup.py` pure half:

```python
"""Spec 026: remove AI artifacts from a story page with Codex flags, SAM masks and LaMa fills."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from video_pipeline.cut_layers import dilate

CONFIG_FILE = "cleanup.json"
COVER_STEMS = ("cover_front", "cover_front_print", "cover_back")


class CleanupError(Exception):
    """Cleanup refused or failed; the message says why."""


@dataclass(frozen=True)
class CleanupSettings:
    max_regions: int
    max_points: int
    max_reason_chars: int
    margin_px: int
    max_region_share: float
    attempts: int = 3
    time_limit_s: float = 600.0


def load_settings(config_dir: Path) -> CleanupSettings:
    data = json.loads((Path(config_dir) / CONFIG_FILE).read_text(encoding="utf-8"))
    return CleanupSettings(**data)


def region_problems(data: object, size: tuple[int, int], settings: CleanupSettings) -> list[str]:
    """Every problem with Codex's `{"regions": [...]}`; empty means usable (an empty list is valid)."""
    if not isinstance(data, dict) or not isinstance(data.get("regions"), list):
        return ['expected {"regions": [{"box", "include_points", "reason"}, ...]}']
    regions = data["regions"]
    out: list[str] = []
    if len(regions) > settings.max_regions:
        out.append(f"at most {settings.max_regions} regions, got {len(regions)}")
    w, h = size
    for i, r in enumerate(regions):
        tag = f"region {i}"
        if not isinstance(r, dict):
            out.append(f"{tag}: must be an object")
            continue
        box = r.get("box")
        ints = isinstance(box, list) and len(box) == 4 and all(
            isinstance(v, int) and not isinstance(v, bool) for v in box
        )
        if not ints or not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            out.append(f"{tag}: box must be [x0, y0, x1, y1] integers inside the {w}x{h} picture, x0<x1, y0<y1")
        points = r.get("include_points", [])
        if not isinstance(points, list) or len(points) > settings.max_points:
            out.append(f"{tag}: include_points must be a list of at most {settings.max_points} [x, y]")
        else:
            for p in points:
                if not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, int) for v in p)
                        and 0 <= p[0] < w and 0 <= p[1] < h):
                    out.append(f"{tag}: point {p!r} must be [x, y] inside the picture")
        reason = r.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > settings.max_reason_chars:
            out.append(f"{tag}: reason must be 1-{settings.max_reason_chars} characters")
    return out


def grow_mask(mask: np.ndarray, px: int) -> np.ndarray:
    return dilate(np.asarray(mask, bool), px)


def composite(rgb: np.ndarray, filled: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """`filled` inside `hole`, `rgb` untouched everywhere else (AC-1.3)."""
    return np.where(hole[..., None], filled, rgb).astype(np.uint8)


@dataclass
class Applied:
    image: np.ndarray
    hole: np.ndarray
    applied: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def apply_regions(rgb, regions, masks, fill, settings: CleanupSettings) -> Applied:
    """Fill every region whose mask is non-empty and at most `max_region_share` of the picture;
    skip the rest with a reason (AC-1.4). One fill call for all kept masks."""
    total = rgb.shape[0] * rgb.shape[1]
    hole = np.zeros(rgb.shape[:2], bool)
    applied, skipped = [], []
    for i, (region, mask) in enumerate(zip(regions, masks, strict=True)):
        mask = np.asarray(mask, bool)
        share = float(mask.sum()) / total
        base = {"index": i, "box": region["box"], "reason": region["reason"]}
        if not mask.any():
            skipped.append({**base, "why": "empty mask"})
        elif share > settings.max_region_share:
            skipped.append({**base, "why": f"mask covers {share:.1%} of the picture (limit {settings.max_region_share:.1%})"})
        else:
            hole |= mask
            applied.append({**base, "share": round(share, 5)})
    if not applied:
        return Applied(rgb, hole, applied, skipped)
    grown = grow_mask(hole, settings.margin_px)
    try:
        filled = np.asarray(fill.fill(rgb, grown))
    except Exception as exc:  # noqa: BLE001 - onnxruntime raises many kinds
        skipped.extend({**a, "why": f"fill failed: {exc}"} for a in applied)
        return Applied(rgb, hole & False, [], skipped)
    return Applied(composite(rgb, filled, grown), grown, applied, skipped)


def bak_path(picture: Path) -> Path:
    return picture.with_name(picture.name + ".bak")


def record_path(picture: Path) -> Path:
    return picture.with_name(picture.stem + ".cleanup.json")


def write_picture(image: np.ndarray, picture: Path, *, yes: bool) -> None:
    """`.bak` first (refused when one exists and not `yes`), then the picture, both atomically."""
    bak = bak_path(picture)
    if bak.exists() and not yes:
        raise CleanupError(f"{bak.name} already exists and would be overwritten; confirm (--yes)")
    tmp_bak = bak.with_name(bak.name + ".tmp")
    tmp_bak.write_bytes(picture.read_bytes())
    os.replace(tmp_bak, bak)
    tmp = picture.with_name(picture.stem + ".tmp.png")
    try:
        Image.fromarray(image, "RGB").save(tmp, format="PNG")
        os.replace(tmp, picture)
    finally:
        tmp.unlink(missing_ok=True)


def accept_page(picture: Path) -> None:
    """Dismiss the review: mark the record reviewed; the `.bak` stays (AC-2.4)."""
    record = record_path(picture)
    if not record.is_file():
        raise CleanupError("there is no cleanup to accept for this picture")
    data = json.loads(record.read_text(encoding="utf-8"))
    data["reviewed"] = True
    _write_json(record, data)


def revert_page(picture: Path) -> None:
    """Restore the exact previous bytes, remove the `.bak` and the record (AC-2.5)."""
    bak = bak_path(picture)
    if not bak.is_file():
        raise CleanupError(f"there is no {bak.name} to restore")
    tmp = picture.with_name(picture.stem + ".revert.tmp")
    tmp.write_bytes(bak.read_bytes())
    os.replace(tmp, picture)
    bak.unlink()
    record_path(picture).unlink(missing_ok=True)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] Run: `uv run pytest tests/test_cleanup.py -q -k "region_problems or composite or apply_regions or accept or revert or existing_bak or too_many"` — expect PASS for the pure tests
- [ ] Commit: `feat(cleanup): pure region, mask and picture logic (spec 026)`

---

## Phase 2: Codex region save tool

**Implements:** FR-2 | **Satisfies:** AC-1.1 (empty list), AC-1.5
**Files:** `video_pipeline/cleanup.py` (RegionTools, prompt), `video_pipeline/cleanup_mcp.py` (new), `tests/test_cleanup_mcp.py`
**Interfaces:** Consumes Phase 1 `region_problems`, `plan_mcp.py` pattern. Produces `RegionTools(size, settings, out).save_artifact_regions(data) -> {"saved": bool, "errors": list[str]}`, `cleanup_prompt(page, size, settings) -> str`, MCP server `cleanup_mcp` with tool `save_artifact_regions`.

- [ ] Run Phase 0's `tests/test_cleanup_mcp.py` — expect FAIL
- [ ] Implement in `cleanup.py`:

```python
MAX_SAVES = 3


@dataclass
class RegionTools:
    size: tuple[int, int]
    settings: CleanupSettings
    out: Path
    saves: int = 0

    def save_artifact_regions(self, data: object) -> dict:
        self.saves += 1
        if self.saves > MAX_SAVES:
            return {"saved": False, "errors": [f"at most {MAX_SAVES} saves"]}
        errors = region_problems(data, self.size, self.settings)
        if errors:
            return {"saved": False, "errors": errors}
        _write_json(self.out, data)  # type: ignore[arg-type]
        return {"saved": True, "errors": []}


def cleanup_prompt(page: dict, size: tuple[int, int], settings: CleanupSettings) -> str:
    w, h = size
    return (
        f"Look at the attached children's-book illustration ({w}x{h} pixels). Find only clear AI "
        "painting defects: extra, missing or fused fingers or limbs, stray blobs, smudges, "
        "garbled letters or marks, duplicated objects. Do not flag style choices, soft edges or "
        f"anything intended. Page text for context: {page.get('text', '')!r}.\n"
        "Save your findings once with the save_artifact_regions tool as "
        '{"regions": [{"box": [x0, y0, x1, y1], "include_points": [[x, y], ...], "reason": "..."}]} '
        f"in picture pixels: at most {settings.max_regions} regions, at most {settings.max_points} "
        f"include_points each (a point on the defect), reason up to {settings.max_reason_chars} "
        'characters. If the picture has no defects save {"regions": []}. Fix every listed error '
        f"and save again; at most {MAX_SAVES} saves."
    )
```

- [ ] `cleanup_mcp.py` mirrors `plan_mcp.py`: args `--size WxH`, `--out`, `--root`; `build_tools(args)` uses `load_settings(root/config)`; `create_server` registers `save_artifact_regions(data: dict) -> dict` whose docstring repeats the schema.
- [ ] Run: `uv run pytest tests/test_cleanup_mcp.py -q` — expect PASS
- [ ] Commit: `feat(cleanup): Codex region save tool (spec 026)`

---

## Phase 3: cleanup_page stage and illustrate CLI

**Implements:** FR-1, FR-2, FR-3, FR-4, FR-5, FR-6 | **Satisfies:** AC-1.1–1.6, AC-2.1, AC-2.2, AC-3.2, AC-3.3, AC-4.1
**Files:** `video_pipeline/cleanup.py` (stage half), `video_pipeline/illustrate.py` (flags, dispatch), `tests/test_cleanup.py`, `tests/test_illustrate.py`
**Interfaces:** Consumes Phases 1–2, `cut_layers.load_real_models`/`Segmenter`/`Inpainter`, `motion_plan.Layer`, `agents.build_codex_command`/`run_codex`/`codex_status`, `motion_plan._refusals`, `illustrate.page_stem`. Produces `cleanup_page(folder, book, page_number, *, root, binary, load_models, provider, yes, log) -> CleanupOutcome(status, applied, skipped, reason)` with status `"cleaned" | "nothing" | "failed"`; `illustrate --cleanup [--yes]`.

- [ ] Run Phase 0's cleanup_page tests and illustrate flag tests — expect FAIL
- [ ] Implement `cleanup_page` in `cleanup.py`: refuse a cover stem (`CleanupError("only story pages are supported")`) and a missing picture; ask Codex (`images=[picture]`, `cleanup_prompt`, up to `settings.attempts` tries with the refusals fed back like `motion_plan.plan_page`) through `python -m video_pipeline.cleanup_mcp --size WxH --out TMP --root R`; zero regions → `CleanupOutcome("nothing")`, no files touched; else load models once (`load_models or load_real_models`), build probe `Layer(name=f"artifact_{i}", box=box, include_points=points, z=1, depth=0.5, pivot=(cx, cy))` for each region, `seg.masks(rgb, layers)`, `apply_regions(...)`; no applied region → `CleanupOutcome("failed", skipped=..., reason=first skip why)` and nothing written; else `write_picture(result.image, picture, yes=yes)` and write the record `{page_number, picture_sha256_before, picture_sha256_after, applied, skipped, written_at, provider, reviewed: false}` to `record_path(picture)`. Log each applied and skipped region.
- [ ] `illustrate.py`: add `--cleanup` and `--yes` to `parse_args` (after the mutually exclusive group): `--cleanup` requires `--page` (`parser.error("--cleanup needs --page")`) and refuses `--cover` (`parser.error("--cleanup applies to story pages only")`); `main` dispatches `if args.cleanup:` before the repaint branch to `_cleanup_main(args, book, folder, binary)` which calls `cleanup.cleanup_page`, prints `cleaned page N: A region(s) filled, S skipped (old picture kept as page_NN.png.bak)` / `nothing to clean` / error, returns 0 for cleaned and nothing, 1 for failed; `CleanupError`, `ModelsMissing`, `ModelLoadError` print `error: ...` and return 1.
- [ ] Run: `uv run pytest tests/test_cleanup.py tests/test_cleanup_mcp.py tests/test_illustrate.py tests/test_cut_layers.py -q` — expect PASS; `uv run ruff check` / `ruff format --check` on touched files
- [ ] Commit: `feat(cleanup): cleanup stage and illustrate --cleanup (spec 026)`

---

## Phase 4: Studio — start cleanup, before and after, Accept and Revert

**Implements:** FR-5 | **Satisfies:** AC-2.2 (studio confirmation), AC-2.3, AC-2.4, AC-2.5, AC-3.1, AC-4.2
**Files:** `video_pipeline/studio/jobs.py`, `books.py`, `app.py`, `templates/animation_page.html`, `tests/test_studio_jobs.py`, `tests/test_studio_books.py`, `tests/test_studio_app.py`
**Interfaces:** Consumes `cleanup.bak_path/record_path/accept_page/revert_page`, the studio's repaint wiring (`REPAINT_STAGE`, `_start_stage`, `repaint_page` route, media whitelist for `illustration`). Produces stage `"cleanup"` (module `video_pipeline.illustrate`, command `... --book SLUG --page N --cleanup [--yes] --root R`), `books.cleanup_row(folder, page) -> {"state": "none"|"review"|"accepted", "applied", "skipped", "has_bak"}` on page rows, media kind for the `.bak` picture, routes `POST /books/{slug}/animation/{number}/cleanup` (start, confirm when `.bak` exists), `.../cleanup/accept`, `.../cleanup/revert`.

- [ ] Tests (fail first): `stage_command("cleanup", book, paths, page=3, force=True)` contains `--page 3 --cleanup --yes`, and without `force` omits `--yes`; `cleanup_row` is `review` with a fresh record, `accepted` when `reviewed`, `none` without a record; POST start without `confirm=1` while a `.bak` exists answers 409 "Confirm"; POST accept flips the record to reviewed and keeps the `.bak`; POST revert restores bytes and removes `.bak` and record; both refuse (409) while a job runs; the page view shows before and after images and the skipped-region list; media route serves the `.bak` picture only for a story page and never any other path.
- [ ] Implement following the repaint pattern exactly: `STAGE_MODULES["cleanup"]`, `STAGE_LABELS["cleanup"] = "artifact cleanup"`, add to the page-level stages, gate = repaint's gate plus "paint the picture first" when the picture is missing; `_start_stage` asks confirmation when `cleanup.bak_path(picture).exists()` (unless `form.get("confirm") == "1"`) and passes `force=True` (→ `--yes`) after confirmation; accept and revert routes are synchronous handlers (under `write_lock`, refused when `busy_reason()`), redirect back to the page view; `animation_page.html` gets a Cleanup panel: Clean artifacts button, before (`.bak`) and after pictures side by side when a record exists, applied and skipped region lists with reasons, Accept and Revert buttons; colours only from `static/tokens.css`.
- [ ] Run: `uv run pytest tests/test_studio_jobs.py tests/test_studio_books.py tests/test_studio_app.py tests/test_cleanup.py -q` — expect PASS; ruff clean
- [ ] Commit: `feat(studio): artifact cleanup stage with before/after, Accept and Revert (spec 026)`

---

## Phase 5: Integration verification and docs

**Implements:** All FRs | **Satisfies:** All ACs

- [ ] Run changed-area tests: `uv run pytest tests/test_cleanup.py tests/test_cleanup_mcp.py tests/test_illustrate.py tests/test_cut_layers.py tests/test_studio_jobs.py tests/test_studio_books.py tests/test_studio_app.py tests/test_studio_repaint.py -q` — all PASS; `uv run ruff check video_pipeline tests` and `ruff format --check` — clean
- [ ] Opt-in real test `tests/test_cleanup_real.py` (`@pytest.mark.models`, skips when models are missing): paint a defect (a coloured blob) onto a copy of a committed picture in a tmp dir, run `apply_regions` with the real segmenter and LaMa for a box around it, assert pixels outside the grown mask are identical and the blob area changed; no Codex.
- [ ] Add a test that `cleanup_page` leaves `episode/episode_script.json`, `episode/reviewed.json` and `build/audio/status.json` byte-identical (AC-3.2) if not already covered, and that after a cleanup `motion_plan.plan_state` reports `stale` for that page (AC-3.1).
- [ ] Update `CLAUDE.md`: command line `uv run python -m video_pipeline.illustrate --book <slug> --page N --cleanup [--yes]   # spec 026: Codex flags artifacts, SAM masks, LaMa fills; old picture kept as .bak` and one Architecture bullet "Artifact cleanup (spec 026)" (module `cleanup.py`, `cleanup_mcp.py`, `config/cleanup.json`, record `page_NN.cleanup.json`, ADR-026 overwrite + `.bak`, studio Cleanup panel with Accept/Revert, plan/cuts go stale by picture hash, covers excluded).
- [ ] Commit: `docs(cleanup): complete artifact cleanup (spec 026)`

---

## Quickstart Validation

```bash
uv run python -m video_pipeline.illustrate --book <slug> --page 3 --cleanup   # flags, masks, fills; keeps page_03.png.bak
git diff --stat -- books/<slug>/episode                                       # empty: script untouched
uv run python -m video_pipeline.motion_plan --book <slug> --page 3            # plan was stale after the cleanup; refresh it
uv run python -m video_pipeline.cut_layers --book <slug> --page 3
```

In the studio page view the Cleanup panel shows before and after with Accept and Revert; Revert restores the exact previous picture.
