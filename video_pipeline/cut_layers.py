"""Cut each planned layer out of a page picture and fill the background behind it (spec 009).

    python -m video_pipeline.cut_layers --book <slug> [--page N] [--force] [--root PATH]
    python -m video_pipeline.cut_layers download [--root PATH]

Segmentation is SAM 2.1 (Core ML, on CPU_AND_GPU: the Neural Engine overflows float16 in the
encoder); the fill is LaMa (ONNX, onnxruntime CPU provider). Every setting comes from the
`models` block of config/animation.json. Models load lazily, only when some page needs cutting,
from a local folder of real files (Core ML cannot load the symlinked Hugging Face cache), and
never touch the network. See docs/specs/009-animation-plan-layers/spike.md for the measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder
from video_pipeline.motion_plan import (
    LIMITS_FILE,
    Layer,
    MotionPlan,
    cut_fields_hash,
    load_limits,
    page_picture,
    plan_state,
)

DOWNLOAD_HINT = "python -m video_pipeline.cut_layers download"
MANIFEST = "layers.json"
MODELS_ENV = "KIDDO_MODELS_DIR"
SAFE_COMPUTE_UNITS = ("CPU_AND_GPU", "CPU_ONLY")
PROMPT_LABELS = {"exclude": 0, "include": 1, "box_origin": 2, "box_end": 3}
REGION_BLOCK_PX = 16  # grid used to find the separate parts of a hole


class ModelsMissing(RuntimeError):
    """The pinned models are not downloaded; the message names the download command."""


class ModelLoadError(RuntimeError):
    """A downloaded model file is there but Core ML / onnxruntime cannot load it."""


class SegmenterError(RuntimeError):
    """The segmenter is misconfigured or produced output that cannot be right."""


class Segmenter(Protocol):
    def masks(self, image: np.ndarray, layers: list[Layer]) -> list[np.ndarray]: ...


class Inpainter(Protocol):
    def fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray: ...


LoadModels = Callable[[Path, dict], tuple[Segmenter, Inpainter]]


@dataclass
class CutResult:
    page_number: int
    status: str  # "cut" | "cached" | "no plan" | "refused" | "failed"
    detail: str = ""
    empty: tuple[str, ...] = ()


# -- paths and identities -----------------------------------------------------------------------


def layers_dir(folder: BookFolder, page_number: int) -> Path:
    return folder.build_dir / "layers" / f"page_{page_number:02d}"


def models_dir() -> Path:
    """Where the downloaded models live: $KIDDO_MODELS_DIR, else ~/.cache/kiddo/models."""
    env = os.environ.get(MODELS_ENV)
    return Path(env) if env else Path.home() / ".cache" / "kiddo" / "models"


def model_path(base: Path, cfg: dict) -> Path:
    """`apple/coreml-sam2.1-small` at revision 883f5787eb0b… → base/apple__coreml-sam2.1-small@883f5787eb0b."""
    return Path(base) / f"{cfg['repo'].replace('/', '__')}@{cfg['revision'][:12]}"


def model_ids(models_cfg: dict) -> dict[str, str]:
    """Ids that decide the cut bytes: repo@revision:compute unit / provider, plus a hash of the
    pre/post-processing settings. Used by cut_book and the studio alike."""
    seg, fill = models_cfg["segmenter"], models_cfg["fill"]
    settings = {
        "segmenter": {k: seg.get(k) for k in ("size", "prompt", "preprocess", "postprocess")},
        "fill": {k: fill.get(k) for k in ("size", "tile")},
    }
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "segmenter": f"{seg['repo']}@{seg['revision']}:{seg['compute_units']}",
        "fill": f"{fill['repo']}@{fill['revision']}:{'+'.join(fill['providers'])}",
        "cut_settings": digest,
    }


def cut_hash(plan: MotionPlan, feather_px: int, ids: dict[str, str]) -> str:
    key = {
        "picture": plan.picture.sha256,
        "fields": cut_fields_hash(plan),
        "feather_px": feather_px,
        "models": dict(sorted(ids.items())),
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def read_config(root: Path) -> dict:
    return json.loads((Path(root) / "config" / LIMITS_FILE).read_text(encoding="utf-8"))


# -- pure mask maths ----------------------------------------------------------------------------


def _box_max(mask: np.ndarray, px: int) -> np.ndarray:
    """Max filter with a (2*px+1) square window, as separable shifted slices (exact)."""
    out = mask
    for axis in (0, 1):
        pad = [(px, px) if i == axis else (0, 0) for i in range(2)]
        padded = np.pad(out, pad)
        n = out.shape[axis]
        acc = np.zeros_like(out)
        for k in range(2 * px + 1):
            acc |= padded[k : k + n] if axis == 0 else padded[:, k : k + n]
        out = acc
    return out


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _crop_apply(mask: np.ndarray, px: int, fn: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
    """Run fn on the mask's bbox grown by px (fn must not reach further than px), paste back."""
    out = np.zeros_like(mask)
    bb = mask_bbox(mask)
    if bb is None:
        return out
    h, w = mask.shape
    x0, y0 = max(bb[0] - px, 0), max(bb[1] - px, 0)
    x1, y1 = min(bb[2] + px, w), min(bb[3] + px, h)
    out[y0:y1, x0:x1] = fn(mask[y0:y1, x0:x1])
    return out


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask.copy()
    return _crop_apply(mask, px, lambda m: _box_max(m, px))


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill every hole not connected to the border (flood the outside, keep the rest)."""

    def _fill(m: np.ndarray) -> np.ndarray:
        img = Image.fromarray(m.astype(np.uint8) * 255)
        padded = Image.new("L", (img.width + 2, img.height + 2), 0)
        padded.paste(img, (1, 1))
        ImageDraw.floodfill(padded, (0, 0), 128, thresh=0)
        return np.asarray(padded)[1:-1, 1:-1] != 128

    return _crop_apply(mask, 2, _fill)


def clean_mask(mask: np.ndarray, close_px: int, fill_interior: bool = True) -> np.ndarray:
    """Close small gaps (dilate then erode by close_px) and fill interior holes."""
    out = mask
    if close_px > 0:
        out = _crop_apply(mask, 2 * close_px, lambda m: ~_box_max(~_box_max(m, close_px), close_px))
    return fill_holes(out) if fill_interior else out


def occlude(masks: list[np.ndarray], zs: list[int]) -> list[np.ndarray]:
    """Each pixel belongs to the front-most layer: subtract every higher-z mask from a mask."""
    out = []
    for i, mask in enumerate(masks):
        front = np.zeros_like(mask)
        for j, other in enumerate(masks):
            if zs[j] > zs[i]:
                front |= other
        out.append(mask & ~front)
    return out


def select_mask(lows: np.ndarray, scores: np.ndarray, margin: float) -> int:
    """The largest mask among those scoring within `margin` of the best (box prompts often give
    a patchy part-mask a hair above the whole object)."""
    scores = np.asarray(scores, np.float32).reshape(-1)
    best = float(scores.max())
    ok = [i for i in range(len(scores)) if scores[i] >= best - margin]
    return max(ok, key=lambda i: (int((lows[i] > 0).sum()), -i))


def upsample_logits(low: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """256x256 logits → bool mask at size (width, height): bilinear, threshold at 0."""
    im = Image.fromarray(np.ascontiguousarray(low, dtype=np.float32))
    return np.asarray(im.resize(size, Image.Resampling.BILINEAR)) > 0.0


def check_embedding(embedding: np.ndarray, compute_unit: str, bound: float = 100.0) -> None:
    """Fail loudly when the encoder overflowed (the Neural Engine gives ~±5000, not ~±3)."""
    emb = np.asarray(embedding)
    peak = float(np.abs(emb).max()) if emb.size else 0.0
    if not np.isfinite(emb).all() or peak > bound:
        raise SegmenterError(
            f"segmenter encoder output is out of range (max |x| = {peak:.0f}, expected under "
            f"{bound:g}) with compute unit {compute_unit}; set compute_units to CPU_AND_GPU or "
            f"CPU_ONLY in config/{LIMITS_FILE}"
        )


def compute_unit_name(cfg: dict) -> str:
    unit = str(cfg.get("compute_units"))
    if unit not in SAFE_COMPUTE_UNITS:
        raise SegmenterError(
            f"compute_units {unit!r} is not allowed (the Neural Engine overflows the SAM encoder); "
            f"use one of {', '.join(SAFE_COMPUTE_UNITS)} in config/{LIMITS_FILE}"
        )
    return unit


def _point_count(layer: Layer) -> int:
    return 2 + len(layer.include_points) + len(layer.exclude_points)


def _points_problem(layer: Layer, max_points: int) -> str | None:
    count = _point_count(layer)
    if count <= max_points:
        return None
    return (
        f"layer {layer.name!r} has {count} prompt points (the box counts as 2), "
        f"the segmenter takes at most {max_points}; remove some include/exclude points"
    )


def prompt_points(
    layer: Layer,
    width: int,
    height: int,
    *,
    size: int,
    max_points: int,
    labels: dict[str, int] = PROMPT_LABELS,
) -> tuple[np.ndarray, np.ndarray]:
    """Box corners, include and exclude points in the stretched size x size space:
    points float32 [1,N,2] and labels int32 [1,N]."""
    problem = _points_problem(layer, max_points)
    if problem:
        raise ValueError(problem)
    sx, sy = size / width, size / height
    x0, y0, x1, y1 = layer.box
    pts = [(x0, y0), (x1, y1)]
    lab = [labels["box_origin"], labels["box_end"]]
    pts += list(layer.include_points)
    lab += [labels["include"]] * len(layer.include_points)
    pts += list(layer.exclude_points)
    lab += [labels["exclude"]] * len(layer.exclude_points)
    points = np.array([[(x * sx, y * sy) for x, y in pts]], dtype=np.float32)
    return points, np.array([lab], dtype=np.int32)


def hole_regions(hole: np.ndarray, block: int = REGION_BLOCK_PX) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x0,y0,x1,y1) of the separate parts of a hole, found on a coarse grid
    (8-connected blocks), each tightened to the hole pixels inside it."""
    h, w = hole.shape
    gh, gw = -(-h // block), -(-w // block)
    padded = np.zeros((gh * block, gw * block), bool)
    padded[:h, :w] = hole
    grid = padded.reshape(gh, block, gw, block).any(axis=(1, 3))
    seen = np.zeros_like(grid)
    regions = []
    for start in zip(*np.nonzero(grid), strict=True):
        if seen[start]:
            continue
        stack, cells = [start], []
        seen[start] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys, xs = zip(*cells, strict=True)
        bx0, by0 = min(xs) * block, min(ys) * block
        bx1, by1 = min((max(xs) + 1) * block, w), min((max(ys) + 1) * block, h)
        tight = mask_bbox(hole[by0:by1, bx0:bx1])
        if tight is not None:
            x0, y0 = int(bx0) + tight[0], int(by0) + tight[1]
            regions.append((x0, y0, int(bx0) + tight[2], int(by0) + tight[3]))
    return sorted(regions)


def merge_regions(
    boxes: list[tuple[int, int, int, int]], gap: int
) -> list[tuple[int, int, int, int]]:
    """Merge boxes that touch once grown by `gap`, until none do."""
    boxes = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (
                    a[0] - gap < b[2]
                    and b[0] - gap < a[2]
                    and a[1] - gap < b[3]
                    and b[1] - gap < a[3]
                ):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return sorted(boxes)


def limit_regions(
    boxes: list[tuple[int, int, int, int]], max_n: int
) -> list[tuple[int, int, int, int]]:
    """Merge the pair whose combined box is smallest until at most max_n boxes remain.
    Each LaMa tile costs ~2.3 s on the M1 Pro, so this bounds the fill time per page."""
    boxes = sorted(boxes)
    while len(boxes) > max(max_n, 1):
        best = None
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                box = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                area = (box[2] - box[0]) * (box[3] - box[1])
                if best is None or area < best[0]:
                    best = (area, i, j, box)
        _, i, j, box = best
        boxes = sorted([b for k, b in enumerate(boxes) if k not in (i, j)] + [box])
    return boxes


def crop_box(
    region: tuple[int, int, int, int], width: int, height: int, *, margin: int, min_crop: int
) -> tuple[int, int, int, int]:
    """The crop LaMa sees for a hole region: a square of the region's longer side plus a margin
    on each side, at least min_crop, never bigger than the picture, centred and clipped. When the
    region does not fit in a square the picture allows, the crop goes rectangular to cover it."""
    x0, y0, x1, y1 = region
    longest = max(x1 - x0, y1 - y0)
    side = max(longest + 2 * margin, min_crop)
    cw, ch = min(side, width), min(side, height)
    if cw != ch and longest <= min(cw, ch):
        cw = ch = min(cw, ch)

    def place(lo: int, hi: int, span: int, limit: int) -> int:
        return int(np.clip((lo + hi) // 2 - span // 2, 0, limit - span))

    cx0, cy0 = place(x0, x1, cw, width), place(y0, y1, ch, height)
    return cx0, cy0, cx0 + cw, cy0 + ch


# -- one page --------------------------------------------------------------------------------------


def _feather(mask: np.ndarray, px: int) -> np.ndarray:
    """Bool mask → float alpha: 1 inside the mask, a soft edge up to `px` outside it (so the
    layer over the filled background reproduces the picture exactly inside the mask)."""
    if px <= 0:
        return mask.astype(np.float32)
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(px / 2))
    blur = np.asarray(im, dtype=np.float32) / 255.0
    return np.where(mask, 1.0, np.where(dilate(mask, px), blur, 0.0)).astype(np.float32)


def cut_page(
    folder: BookFolder,
    page_number: int,
    picture: Path,
    plan: MotionPlan,
    seg: Segmenter,
    fill: Inpainter,
    feather_px: int,
    ids: dict[str, str],
) -> CutResult:
    out = layers_dir(folder, page_number)
    with Image.open(picture) as im:
        rgb = np.asarray(im.convert("RGB"))
    raw = seg.masks(rgb, plan.layers)
    masks = occlude([np.asarray(m, bool) for m in raw], [layer.z for layer in plan.layers])
    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).unlink(missing_ok=True)  # a missing manifest marks a half-written folder
    for old in out.glob("*.png"):
        old.unlink()
    entries, empty = [], []
    union = np.zeros(rgb.shape[:2], bool)
    for layer, mask in zip(plan.layers, masks, strict=True):
        alpha = _feather(mask, feather_px) if mask.any() else np.zeros(mask.shape, np.float32)
        box = mask_bbox(alpha > 0)
        if box is None:
            empty.append(layer.name)
            entries.append(
                {"name": layer.name, "file": None, "offset": None, "size": None, "empty": True}
            )
            continue
        x0, y0, x1, y1 = box
        a8 = (alpha[y0:y1, x0:x1] * 255).round().astype(np.uint8)
        rgba = np.dstack([rgb[y0:y1, x0:x1], a8])
        Image.fromarray(rgba, "RGBA").save(out / f"{layer.name}.png", optimize=False)
        entries.append(
            {
                "name": layer.name,
                "file": f"{layer.name}.png",
                "offset": [x0, y0],
                "size": [x1 - x0, y1 - y0],
                "empty": False,
            }
        )
        union |= mask
    hole = dilate(union, feather_px)
    background = rgb
    if hole.any():
        filled = np.asarray(fill.fill(rgb, hole))
        background = np.where(hole[..., None], filled, rgb).astype(np.uint8)
    Image.fromarray(background, "RGB").save(out / "background.png", optimize=False)
    write_atomic(
        out / MANIFEST,
        {
            "cut_hash": cut_hash(plan, feather_px, ids),
            "picture_sha256": plan.picture.sha256,
            "model_ids": ids,
            "feather_px": feather_px,
            "background": "background.png",
            "layers": entries,
        },
    )
    return CutResult(page_number, "cut", empty=tuple(empty))


def _cached(out: Path, key: str) -> bool:
    try:
        manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
        files = [e["file"] for e in manifest["layers"] if e["file"]] + [manifest["background"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return False
    return manifest.get("cut_hash") == key and all((out / f).is_file() for f in files)


def cut_book(
    folder: BookFolder,
    *,
    root: Path,
    pages: list[int] | None = None,
    force: bool = False,
    load_models: LoadModels | None = None,
    log: Callable[[str], None] = print,
) -> list[CutResult]:
    """Cut every planned page (or `pages`). Models load once, only if some page needs cutting;
    ModelsMissing and SegmenterError propagate."""
    config = read_config(root)
    limits = load_limits(Path(root) / "config")
    feather = int(config["cut"]["feather_px"])
    models_cfg = config["models"]
    ids = model_ids(models_cfg)
    max_points = int(models_cfg["segmenter"]["io"]["prompt_encoder"]["max_points"])
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    models = None
    results: list[CutResult] = []
    for page in sorted(book["pages"], key=lambda p: int(p["page_number"])):
        n = int(page["page_number"])
        if pages and n not in pages:
            continue
        st = plan_state(folder, page, limits)
        if st.state == "none":
            result = CutResult(n, "no plan")
        elif st.state == "stale":
            result = CutResult(
                n,
                "refused",
                f"stale plan – re-run python -m video_pipeline.motion_plan --book {folder.slug} "
                f"--page {n}",
            )
        elif st.state != "ok" or st.plan is None:
            result = CutResult(n, "failed", st.detail)
        elif problems := [
            p for layer in st.plan.layers if (p := _points_problem(layer, max_points))
        ]:
            result = CutResult(n, "failed", "; ".join(problems))
        elif not force and _cached(layers_dir(folder, n), cut_hash(st.plan, feather, ids)):
            result = CutResult(n, "cached")
        else:
            if models is None:
                models = (load_models or load_real_models)(Path(root), models_cfg)
            started = time.perf_counter()
            result = cut_page(
                folder, n, page_picture(folder, page), st.plan, models[0], models[1], feather, ids
            )
            result.detail = f"{time.perf_counter() - started:.1f} s"
        results.append(result)
        log(
            f"  page {n:02d}: {result.status}"
            + (f" – {result.detail}" if result.detail else "")
            + (f" (empty layers: {', '.join(result.empty)})" if result.empty else "")
        )
    return results


# -- real models -----------------------------------------------------------------------------------


def _segmenter_files(cfg: dict) -> list[str]:
    return [cfg[k] for k in ("encoder", "prompt_encoder", "decoder")]


MLPACKAGE_FILES = (
    "Manifest.json",
    "Data/com.apple.CoreML/model.mlmodel",
    "Data/com.apple.CoreML/weights/weight.bin",
)


def _required(base: Path, models_cfg: dict) -> list[Path]:
    """Every file a complete download has: each .mlpackage's manifest, spec and weights (a
    half-finished download can leave the manifest without the weights), and the LaMa file."""
    seg, fill = models_cfg["segmenter"], models_cfg["fill"]
    paths = [
        model_path(base, seg) / name / part
        for name in _segmenter_files(seg)
        for part in MLPACKAGE_FILES
    ]
    return [*paths, model_path(base, fill) / fill["file"]]


class SamCoreML:
    """SAM 2.1 small on Core ML: encoder once per page, prompt encoder + decoder per layer."""

    def __init__(self, base: Path, cfg: dict) -> None:
        import coremltools as ct

        self.cfg = cfg
        self.unit = compute_unit_name(cfg)
        units = ct.ComputeUnit[self.unit]
        folder = model_path(base, cfg)
        self.encoder = ct.models.MLModel(str(folder / cfg["encoder"]), compute_units=units)
        self.prompt = ct.models.MLModel(str(folder / cfg["prompt_encoder"]), compute_units=units)
        self.decoder = ct.models.MLModel(str(folder / cfg["decoder"]), compute_units=units)

    def masks(self, image: np.ndarray, layers: list[Layer]) -> list[np.ndarray]:
        cfg, io = self.cfg, self.cfg["io"]
        size = int(cfg["size"])
        post = cfg["postprocess"]
        h, w = image.shape[:2]
        stretched = Image.fromarray(image).resize((size, size), Image.Resampling.BILINEAR)
        enc = {
            k: np.asarray(v)
            for k, v in self.encoder.predict({io["encoder"]["input"]["name"]: stretched}).items()
        }
        bound = float(cfg.get("self_check", {}).get("embedding_max_abs", 100.0))
        check_embedding(enc["image_embedding"], self.unit, bound)
        out = []
        for layer in layers:
            points, labels = prompt_points(
                layer,
                w,
                h,
                size=size,
                max_points=int(io["prompt_encoder"]["max_points"]),
                labels=cfg["prompt"]["labels"],
            )
            penc = self.prompt.predict({"points": points, "labels": labels})
            dec = self.decoder.predict(
                {
                    "image_embedding": enc["image_embedding"],
                    "sparse_embedding": np.asarray(penc["sparse_embeddings"]),
                    "dense_embedding": np.asarray(penc["dense_embeddings"]),
                    "feats_s0": enc["feats_s0"],
                    "feats_s1": enc["feats_s1"],
                }
            )
            scores = np.asarray(dec["scores"]).reshape(-1).astype(np.float32)
            lows = np.asarray(dec["low_res_masks"])[0].astype(np.float32)
            best = select_mask(lows, scores, float(post["select_margin"]))
            mask = upsample_logits(lows[best], (w, h))
            clean = post.get("clean") or {}
            mask = clean_mask(
                mask, int(clean.get("close_px", 0)), bool(clean.get("fill_interior_holes"))
            )
            out.append(mask)
        return out


class LamaOnnx:
    """LaMa (512x512 ONNX) on onnxruntime's CPU provider, one tile per merged hole region."""

    def __init__(self, base: Path, cfg: dict) -> None:
        import onnxruntime as ort

        self.cfg = cfg
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(model_path(base, cfg) / cfg["file"]),
            sess_options=options,
            providers=list(cfg["providers"]),
        )

    def _tile(self, rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
        x = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        m = hole.astype(np.float32)[None, None]
        (out,) = self.session.run(["output"], {"image": x, "mask": m})
        return np.clip(out[0].transpose(1, 2, 0), 0, 255).round().astype(np.uint8)

    def fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        tile_cfg, size = self.cfg["tile"], int(self.cfg["size"])
        margin = int(tile_cfg["margin_px"])
        hole = dilate(mask, int(tile_cfg["dilate_px"]))
        cur = np.array(image, dtype=np.uint8, copy=True)
        h, w = hole.shape
        regions = merge_regions(hole_regions(hole), gap=margin)
        for region in limit_regions(regions, int(tile_cfg["max_tiles"])):
            x0, y0, x1, y1 = crop_box(
                region, w, h, margin=margin, min_crop=int(tile_cfg["min_crop_px"])
            )
            crop, cm = cur[y0:y1, x0:x1], hole[y0:y1, x0:x1]
            cw, ch = x1 - x0, y1 - y0
            if (cw, ch) != (size, size):
                crop_in = np.asarray(
                    Image.fromarray(crop).resize((size, size), Image.Resampling.LANCZOS)
                )
                small = Image.fromarray(cm.astype(np.uint8) * 255).resize(
                    (size, size), Image.Resampling.BILINEAR
                )
                cm_in = np.asarray(small) > 127
            else:
                crop_in, cm_in = crop, cm
            tile = self._tile(crop_in, cm_in)
            if (cw, ch) != (size, size):
                tile = np.asarray(Image.fromarray(tile).resize((cw, ch), Image.Resampling.LANCZOS))
            crop[cm] = tile[cm]  # paste only inside the hole
        return cur


def load_real_models(root: Path, models_cfg: dict) -> tuple[Segmenter, Inpainter]:
    """Load both models from the local models folder; never downloads (HF_HUB_OFFLINE=1)."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    base = models_dir()
    missing = [p for p in _required(base, models_cfg) if not p.is_file()]
    if missing:
        raise ModelsMissing(f"models not downloaded ({missing[0]} is missing): run {DOWNLOAD_HINT}")
    compute_unit_name(models_cfg["segmenter"])  # refuse the Neural Engine before loading
    loaded = []
    for role, cls in (("segmenter", SamCoreML), ("fill", LamaOnnx)):
        cfg = models_cfg[role]
        try:
            loaded.append(cls(base, cfg))
        except SegmenterError:
            raise
        except Exception as exc:  # noqa: BLE001 - coremltools/onnxruntime raise many kinds
            raise ModelLoadError(
                f"could not load the {role} model from {model_path(base, cfg)}: {exc} "
                f"(a damaged download? delete that folder and run {DOWNLOAD_HINT})"
            ) from exc
    return loaded[0], loaded[1]


def download(root: Path, *, snapshot: Callable[..., object] | None = None, log=print) -> list[str]:
    """Fetch the pinned model files once into the models folder as real files (FR-11)."""
    if snapshot is None:
        from huggingface_hub import snapshot_download as snapshot
    models_cfg = read_config(root)["models"]
    seg, fill = models_cfg["segmenter"], models_cfg["fill"]
    base = models_dir()
    stored = []
    for role, cfg, patterns in (
        ("segmenter", seg, [f"{name}/*" for name in _segmenter_files(seg)]),
        ("fill", fill, [fill["file"]]),
    ):
        dest = model_path(base, cfg)
        snapshot(
            repo_id=cfg["repo"],
            revision=cfg["revision"],
            allow_patterns=patterns,
            local_dir=str(dest),
        )
        stored.append(str(dest))
        log(
            f"{role}: {cfg['repo']} @ {cfg['revision']}\n"
            f"  licence: {cfg['license']}\n  source: {cfg['source']}\n  stored at: {dest}"
        )
    return stored


# -- CLI -------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None, load_models: LoadModels | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["download"]:
        parser = argparse.ArgumentParser(
            prog=f"{DOWNLOAD_HINT}", description="Download the pinned cut models (spec 009)."
        )
        parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
        args = parser.parse_args(argv[1:])
        try:
            download(args.root)
        except (OSError, KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.cut_layers",
        description="Cut planned layers out of page pictures and fill the background (spec 009).",
    )
    parser.add_argument("--book", required=True)
    parser.add_argument("--page", type=int, action="append", help="cut only this page (repeat)")
    parser.add_argument("--force", action="store_true", help="re-cut even when cached")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        results = cut_book(
            folder,
            root=Path(args.root),
            pages=args.page,
            force=args.force,
            load_models=load_models,
        )
    except (
        ModelsMissing,
        ModelLoadError,
        SegmenterError,
        LibraryError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    counts = {s: sum(r.status == s for r in results) for s in ("cut", "cached", "no plan")}
    refused = sum(r.status == "refused" for r in results)
    failed = sum(r.status == "failed" for r in results)
    print(
        f"{counts['cut']} cut, {counts['cached']} cached, {counts['no plan']} no plan, "
        f"{refused} refused, {failed} failed"
    )
    return 1 if refused or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
