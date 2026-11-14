"""Spec 026: remove AI artifacts from a story page with Codex flags, SAM masks and LaMa fills."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from video_pipeline.agents import build_codex_command, run_codex
from video_pipeline.cut_layers import dilate, load_real_models, read_config
from video_pipeline.library import PROJECT_ROOT, BookFolder
from video_pipeline.motion_plan import Layer, _refusals

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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def region_problems(data: object, size: tuple[int, int], settings: CleanupSettings) -> list[str]:
    """Every problem with Codex's `{"regions": [...]}`; empty means usable (an empty list is
    valid and means nothing to clean)."""
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
        ints = isinstance(box, list) and len(box) == 4 and all(_is_int(v) for v in box)
        if not ints or not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            out.append(
                f"{tag}: box must be [x0, y0, x1, y1] integers inside the {w}x{h} picture, "
                "x0<x1, y0<y1"
            )
        points = r.get("include_points", [])
        if not isinstance(points, list) or len(points) > settings.max_points:
            out.append(
                f"{tag}: include_points must be a list of at most {settings.max_points} [x, y]"
            )
        else:
            for p in points:
                ok = (
                    isinstance(p, list)
                    and len(p) == 2
                    and all(_is_int(v) for v in p)
                    and 0 <= p[0] < w
                    and 0 <= p[1] < h
                )
                if not ok:
                    out.append(f"{tag}: point {p!r} must be [x, y] inside the picture")
        reason = r.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > settings.max_reason_chars
        ):
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
    applied: list[dict] = []
    skipped: list[dict] = []
    for i, (region, mask) in enumerate(zip(regions, masks, strict=True)):
        mask = np.asarray(mask, bool)
        share = float(mask.sum()) / total
        base = {"index": i, "box": region["box"], "reason": region["reason"]}
        if not mask.any():
            skipped.append({**base, "why": "empty mask"})
        elif share > settings.max_region_share:
            skipped.append(
                {
                    **base,
                    "why": f"mask covers {share:.1%} of the picture "
                    f"(limit {settings.max_region_share:.1%})",
                }
            )
        else:
            hole |= mask
            applied.append({**base, "share": round(share, 5)})
    if not applied:
        return Applied(rgb, np.zeros(rgb.shape[:2], bool), [], skipped)
    grown = grow_mask(hole, settings.margin_px)
    try:
        filled = np.asarray(fill.fill(rgb, grown))
    except Exception as exc:  # noqa: BLE001 - onnxruntime raises many kinds
        skipped.extend({**a, "why": f"fill failed: {exc}"} for a in applied)
        return Applied(rgb, np.zeros(rgb.shape[:2], bool), [], skipped)
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
    tmp_bak.replace(bak)
    tmp = picture.with_name(picture.stem + ".tmp.png")
    try:
        Image.fromarray(image, "RGB").save(tmp, format="PNG")
        tmp.replace(picture)
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
    tmp.replace(picture)
    bak.unlink()
    record_path(picture).unlink(missing_ok=True)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


MAX_SAVES = 3


@dataclass
class RegionTools:
    """The save tool Codex calls: validates every payload and writes only a usable one."""

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


@dataclass
class CleanupOutcome:
    status: str  # "cleaned" | "nothing" | "failed"
    applied: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    reason: str = ""


def _page_entry(book: dict, page_number: int) -> dict:
    for page in book["pages"]:
        if int(page["page_number"]) == page_number:
            return page
    raise CleanupError(f"page {page_number} is not in the book")


def _ask_codex(
    folder: BookFolder,
    page: dict,
    picture: Path,
    size: tuple[int, int],
    settings: CleanupSettings,
    *,
    binary: Path,
    root: Path,
    provider: Callable | None,
) -> tuple[list[dict] | None, str]:
    """Regions Codex saved, or (None, the first refusal) when every attempt was refused."""
    base = cleanup_prompt(page, size, settings)
    feedback = ["no attempts configured"]
    number = int(page["page_number"])
    with tempfile.TemporaryDirectory(prefix="kiddo-cleanup-") as tmp:
        out = Path(tmp) / "regions.json"
        for attempt in range(1, settings.attempts + 1):
            text = base
            if attempt > 1:
                text += "\nThe last attempt was refused. Fix these problems and save again:\n"
                text += "\n".join(f"- {problem}" for problem in feedback[:20])
            command = build_codex_command(
                binary,
                sys.executable,
                [
                    "-m",
                    "video_pipeline.cleanup_mcp",
                    "--size",
                    f"{size[0]}x{size[1]}",
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
            log_path = folder.build_dir / "agent" / f"{stamp}-cleanup-p{number:02d}-{attempt}.jsonl"
            result = (provider or run_codex)(command, log_path, timeout_s=settings.time_limit_s)
            if not out.exists():
                feedback = _refusals(result)
                continue
            data = json.loads(out.read_text(encoding="utf-8"))
            problems = region_problems(data, size, settings)  # the tool validated; check again
            if problems:
                feedback = problems
                out.unlink(missing_ok=True)
                continue
            return data["regions"], ""
    return None, feedback[0]


def cleanup_page(
    folder: BookFolder,
    book: dict,
    page_number: int,
    *,
    root: Path = PROJECT_ROOT,
    binary: Path,
    load_models: Callable | None = None,
    provider: Callable | None = None,
    yes: bool = False,
    log: Callable[[str], None] = print,
) -> CleanupOutcome:
    """Flag artifacts with Codex, mask them with SAM, fill them with LaMa and replace the page
    picture (old bytes kept as `.bak`). Only a story page; nothing is written unless at least one
    region is applied (AC-1.1, AC-1.6). Episode files are never touched (AC-3.2)."""
    page = _page_entry(book, page_number)
    name = page.get("illustration_file") or f"page_{page_number:02d}.png"
    picture = folder.illustrations / name
    if Path(name).stem in COVER_STEMS:
        raise CleanupError("only story pages are supported")
    if not picture.is_file():
        raise CleanupError(f"{picture.name} is missing")
    root = Path(root)
    settings = load_settings(root / "config")
    before = picture.read_bytes()
    with Image.open(picture) as im:
        rgb = np.asarray(im.convert("RGB"))
    size = (rgb.shape[1], rgb.shape[0])
    regions, why = _ask_codex(
        folder, page, picture, size, settings, binary=binary, root=root, provider=provider
    )
    if regions is None:
        return CleanupOutcome("failed", reason=why)
    if not regions:
        log(f"page {page_number}: no artifacts found")
        return CleanupOutcome("nothing")
    models_cfg = read_config(root)
    seg, fill = (load_models or load_real_models)(root, models_cfg)
    probes = []
    for i, r in enumerate(regions):
        x0, y0, x1, y1 = r["box"]
        probes.append(
            Layer(
                name=f"artifact_{i}",
                box=(x0, y0, x1, y1),
                include_points=[tuple(p) for p in r.get("include_points", [])],
                z=1,
                depth=0.5,
                pivot=((x0 + x1) // 2, (y0 + y1) // 2),
            )
        )
    result = apply_regions(rgb, regions, seg.masks(rgb, probes), fill, settings)
    for a in result.applied:
        log(f"filled region {a['index']}: {a['reason']}")
    for s in result.skipped:
        log(f"skipped region {s['index']}: {s['why']} ({s['reason']})")
    if not result.applied:
        return CleanupOutcome("failed", skipped=result.skipped, reason=result.skipped[0]["why"])
    write_picture(result.image, picture, yes=yes)
    _write_json(
        record_path(picture),
        {
            "page_number": page_number,
            "picture_sha256_before": hashlib.sha256(before).hexdigest(),
            "picture_sha256_after": hashlib.sha256(picture.read_bytes()).hexdigest(),
            "applied": result.applied,
            "skipped": result.skipped,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "provider": "codex",
            "reviewed": False,
        },
    )
    return CleanupOutcome("cleaned", result.applied, result.skipped)
