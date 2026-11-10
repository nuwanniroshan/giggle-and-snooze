"""Phase 0 spike (spec 009, ADR-009): cut layers with SAM 2.1 (Core ML) and fill with LaMa (ONNX).

Throwaway, kept for the record. Not part of the video_pipeline package, not imported by tests.

    uv run python docs/specs/009-animation-plan-layers/spike_cut.py \
        --models-dir DIR_WITH_MLPACKAGES --lama FILE.onnx --out OUT_DIR \
        [--units ALL|CPU_ONLY|CPU_AND_GPU|CPU_AND_NE] [--providers cpu|coreml] [--passes 2]

Each pass processes pages 1, 5 and 12 of the pilot book. Per page: encoder once, then
prompt encoder + decoder per hand-written box, then LaMa on the union of the masks.
Writes overlays, filled backgrounds and cutout sheets to OUT_DIR and a result.json with
timings, peak RSS and sha256 of every mask and background (to compare runs bit for bit).

NOTE: the .mlpackage files must be real copies, not the symlinks of the Hugging Face cache:
Core ML's compiler fails with "weight.bin doesn't exist" on symlinked packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from pathlib import Path

import coremltools as ct
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BOOK = ROOT / "books" / "the-snail-mail-express" / "illustrations"

ENCODER = "SAM2_1SmallImageEncoderFLOAT16.mlpackage"
PROMPT = "SAM2_1SmallPromptEncoderFLOAT16.mlpackage"
DECODER = "SAM2_1SmallMaskDecoderFLOAT16.mlpackage"
SAM_SIZE = 1024  # encoder input (stretched, no aspect preservation) and prompt coordinate space
LAMA_SIZE = 512
DILATE_PX = 6  # hole = mask dilated by this many px before filling
MARGIN_PX = 96  # context around a hole region when cropping for LaMa

# Hand-written boxes [x0, y0, x1, y1] in picture pixels, after looking at each picture.
BOXES: dict[int, dict[str, list[int]]] = {
    1: {
        "clara": [250, 105, 620, 605],
        "snail": [105, 230, 830, 985],
        "acorn_mailbox": [885, 335, 1015, 600],
        "front_mushroom": [745, 850, 880, 1000],
    },
    5: {
        "clara": [305, 120, 610, 530],
        "snail": [210, 195, 830, 860],
        "fern_left": [0, 200, 255, 560],
        "lily_pad": [590, 920, 805, 1010],
    },
    12: {
        "clara": [260, 85, 670, 600],
        "snail": [95, 215, 895, 950],
        "book": [265, 395, 410, 490],
        "leaf_sprig_right": [880, 385, 1010, 830],
    },
}

# Optional include / exclude points (picture pixels) for the layers a box alone cuts badly.
# Used with --points. These are what the plan's "include" and "exclude" fields would carry.
POINTS: dict[int, dict[str, dict[str, list[tuple[int, int]]]]] = {
    1: {
        "snail": {
            "include": [(300, 700), (740, 600), (250, 600), (400, 780)],
            "exclude": [(430, 420), (500, 520), (810, 920)],
        },
    },
    5: {
        "snail": {
            "include": [(300, 620), (560, 520), (740, 400), (780, 600), (430, 700)],
            "exclude": [(440, 400), (520, 330)],
        },
    },
    12: {
        "snail": {
            "include": [(250, 700), (600, 640), (500, 780), (820, 600), (810, 390)],
            "exclude": [(440, 420), (600, 560), (330, 440), (520, 160)],
        },
    },
}

COLORS = [(230, 30, 30), (30, 120, 240), (20, 180, 60), (240, 170, 0), (170, 40, 220)]


def sha(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)  # bytes on macOS


class Segmenter:
    def __init__(self, models_dir: Path, units: str) -> None:
        cu = getattr(ct.ComputeUnit, units)
        self.encoder = ct.models.MLModel(str(models_dir / ENCODER), compute_units=cu)
        self.prompt = ct.models.MLModel(str(models_dir / PROMPT), compute_units=cu)
        self.decoder = ct.models.MLModel(str(models_dir / DECODER), compute_units=cu)

    def encode(self, picture: Image.Image) -> dict[str, np.ndarray]:
        # The model's first ops scale and bias the pixels, so it takes a plain RGB image.
        img = picture.convert("RGB").resize((SAM_SIZE, SAM_SIZE), Image.Resampling.BILINEAR)
        out = self.encoder.predict({"image": img})
        return {k: np.asarray(v) for k, v in out.items()}

    def segment(
        self,
        enc: dict[str, np.ndarray],
        size: tuple[int, int],
        box: list[int],
        include: list[tuple[int, int]] = (),
        exclude: list[tuple[int, int]] = (),
        select: str = "score",
    ) -> tuple[np.ndarray, float, list[float]]:
        """Return (bool mask at picture size, best score, all three scores)."""
        w, h = size
        sx, sy = SAM_SIZE / w, SAM_SIZE / h
        pts = [(box[0] * sx, box[1] * sy), (box[2] * sx, box[3] * sy)]
        labels = [2, 3]  # box origin, box end
        for x, y in include:
            pts.append((x * sx, y * sy))
            labels.append(1)
        for x, y in exclude:
            pts.append((x * sx, y * sy))
            labels.append(0)
        penc = self.prompt.predict(
            {
                "points": np.array([pts], dtype=np.float32),
                "labels": np.array([labels], dtype=np.int32),
            }
        )
        out = self.decoder.predict(
            {
                "image_embedding": enc["image_embedding"],
                "sparse_embedding": np.asarray(penc["sparse_embeddings"]),
                "dense_embedding": np.asarray(penc["dense_embeddings"]),
                "feats_s0": enc["feats_s0"],
                "feats_s1": enc["feats_s1"],
            }
        )
        scores = np.asarray(out["scores"]).reshape(-1).astype(np.float32)
        lows = np.asarray(out["low_res_masks"])[0].astype(np.float32)  # 3 x 256x256 logits
        best = int(np.argmax(scores))
        if select == "large":
            # Largest mask among those scoring within 0.05 of the best: box prompts often give a
            # patchy "part" mask a hair above the whole-object mask.
            ok = [i for i in range(3) if scores[i] >= scores[best] - 0.05]
            best = max(ok, key=lambda i: int((lows[i] > 0).sum()))
        low = lows[best]
        up = Image.fromarray(low, mode="F").resize((w, h), Image.Resampling.BILINEAR)
        return np.asarray(up) > 0.0, float(scores[best]), [float(s) for s in scores]


class Filler:
    def __init__(self, path: Path, providers: str) -> None:
        provs = ["CPUExecutionProvider"]
        if providers == "coreml":
            provs = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.session = ort.InferenceSession(str(path), sess_options=so, providers=provs)

    def run_tile(self, rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
        """rgb uint8 HxWx3 and hole bool HxW, both 512x512 -> uint8 HxWx3."""
        x = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        m = hole.astype(np.float32)[None, None]
        (out,) = self.session.run(["output"], {"image": x, "mask": m})
        return np.clip(out[0].transpose(1, 2, 0), 0, 255).round().astype(np.uint8)

    def fill(
        self, picture: Image.Image, hole: np.ndarray, regions: list[tuple[int, int, int, int]]
    ):
        """Fill hole in picture. Each region is a bbox (x0,y0,x1,y1) around part of the hole."""
        cur = np.asarray(picture.convert("RGB")).copy()
        h, w = hole.shape
        for x0, y0, x1, y1 in regions:
            side = max(x1 - x0, y1 - y0) + 2 * MARGIN_PX
            side = min(max(side, LAMA_SIZE), min(w, h))  # never upscale a small hole
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            sx0 = int(np.clip(cx - side // 2, 0, w - side))
            sy0 = int(np.clip(cy - side // 2, 0, h - side))
            crop = Image.fromarray(cur[sy0 : sy0 + side, sx0 : sx0 + side])
            cm = hole[sy0 : sy0 + side, sx0 : sx0 + side]
            if side != LAMA_SIZE:
                crop_in = crop.resize((LAMA_SIZE, LAMA_SIZE), Image.Resampling.LANCZOS)
                cm_in = (
                    np.asarray(
                        Image.fromarray(cm.astype(np.uint8) * 255).resize(
                            (LAMA_SIZE, LAMA_SIZE), Image.Resampling.BILINEAR
                        )
                    )
                    > 127
                )
            else:
                crop_in, cm_in = crop, cm
            tile = self.run_tile(np.asarray(crop_in), cm_in)
            if side != LAMA_SIZE:
                tile = np.asarray(
                    Image.fromarray(tile).resize((side, side), Image.Resampling.LANCZOS)
                )
            region = cur[sy0 : sy0 + side, sx0 : sx0 + side]
            region[cm] = tile[cm]  # paste only inside the hole
        return Image.fromarray(cur)


def box_max(mask: np.ndarray, px: int) -> np.ndarray:
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


def crop_apply(mask: np.ndarray, px: int, fn) -> np.ndarray:
    """Run fn on the mask's bbox grown by px (fn must not reach further than px), paste back."""
    bb = mask_bbox(mask)
    out = np.zeros_like(mask)
    if bb is None:
        return out
    h, w = mask.shape
    x0, y0 = max(bb[0] - px, 0), max(bb[1] - px, 0)
    x1, y1 = min(bb[2] + px, w), min(bb[3] + px, h)
    out[y0:y1, x0:x1] = fn(mask[y0:y1, x0:x1])
    return out


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    return crop_apply(mask, px, lambda m: box_max(m, px))


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill every hole not connected to the border (flood the outside, keep the rest)."""

    def _fill(m: np.ndarray) -> np.ndarray:
        img = Image.fromarray(m.astype(np.uint8) * 255)
        padded = Image.new("L", (img.width + 2, img.height + 2), 0)
        padded.paste(img, (1, 1))
        ImageDraw.floodfill(padded, (0, 0), 128, thresh=0)
        return np.asarray(padded)[1:-1, 1:-1] != 128

    return crop_apply(mask, 2, _fill)


def clean_mask(mask: np.ndarray, close_px: int = 7) -> np.ndarray:
    """Close small gaps (dilate then erode) and fill interior holes."""
    closed = crop_apply(mask, 2 * close_px, lambda m: ~box_max(~box_max(m, close_px), close_px))
    return fill_holes(closed)


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def merge_regions(boxes: list[tuple[int, int, int, int]], gap: int):
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
    return boxes


def write_overlay(picture, masks, boxes, path: Path) -> None:
    base = picture.convert("RGBA")
    for i, m in enumerate(masks.values()):
        col = COLORS[i % len(COLORS)]
        tint = Image.new("RGBA", base.size, (*col, 0))
        alpha = Image.fromarray(m.astype(np.uint8) * 115)
        tint.putalpha(alpha)
        base = Image.alpha_composite(base, tint)
    d = ImageDraw.Draw(base)
    for i, (name, b) in enumerate(boxes.items()):
        col = COLORS[i % len(COLORS)]
        d.rectangle(b, outline=(*col, 255), width=3)
        d.text((b[0] + 6, b[1] + 4), name, fill=(*col, 255))
    base.convert("RGB").save(path)


def write_cutouts(picture, masks, path: Path) -> None:
    """One tile per layer: the cut layer over a grey checkerboard, cropped to its mask."""
    tiles = []
    for name, m in masks.items():
        bb = mask_bbox(m)
        if bb is None:
            continue
        x0, y0, x1, y1 = bb
        rgba = picture.convert("RGBA").crop(bb)
        rgba.putalpha(Image.fromarray(m[y0:y1, x0:x1].astype(np.uint8) * 255))
        scale = 300 / max(rgba.size)
        rgba = rgba.resize((max(1, int(rgba.width * scale)), max(1, int(rgba.height * scale))))
        board = Image.new("RGBA", (320, 320), (200, 200, 200, 255))
        bd = ImageDraw.Draw(board)
        for yy in range(0, 320, 20):
            for xx in range(0, 320, 20):
                if (xx // 20 + yy // 20) % 2:
                    bd.rectangle([xx, yy, xx + 19, yy + 19], fill=(150, 150, 150, 255))
        board.alpha_composite(rgba, (10, 10))
        bd.text((14, 300), name, fill=(255, 255, 255, 255))
        tiles.append(board)
    sheet = Image.new("RGB", (320 * len(tiles), 320), (255, 255, 255))
    for i, t in enumerate(tiles):
        sheet.paste(t.convert("RGB"), (320 * i, 0))
    sheet.save(path)


def process_page(
    seg, fill, page: int, out: Path, write: bool, select: str, use_points: bool, clean: bool
) -> dict:
    picture = Image.open(BOOK / f"page_{page:02d}.png").convert("RGB")
    size = picture.size
    t0 = time.perf_counter()
    enc = seg.encode(picture)
    t_enc = time.perf_counter() - t0
    masks, scores = {}, {}
    t1 = time.perf_counter()
    for name, box in BOXES[page].items():
        pt = POINTS.get(page, {}).get(name, {}) if use_points else {}
        masks[name], _, scores[name] = seg.segment(
            enc, size, box, pt.get("include", []), pt.get("exclude", []), select
        )
    if clean:
        masks = {k: clean_mask(m) for k, m in masks.items()}
    t_seg = time.perf_counter() - t1
    t2 = time.perf_counter()
    union = np.zeros(masks["clara"].shape, bool)
    for m in masks.values():
        union |= m
    hole = dilate(union, DILATE_PX)
    regions = merge_regions(
        [bb for m in masks.values() if (bb := mask_bbox(dilate(m, DILATE_PX)))], gap=MARGIN_PX
    )
    background = fill.fill(picture, hole, regions)
    t_fill = time.perf_counter() - t2
    if write:
        write_overlay(picture, masks, BOXES[page], out / f"overlay_page_{page:02d}.png")
        write_cutouts(picture, masks, out / f"cutouts_page_{page:02d}.png")
        background.save(out / f"background_page_{page:02d}.png")
        Image.fromarray(hole.astype(np.uint8) * 255).save(out / f"hole_page_{page:02d}.png")
    return {
        "page": page,
        "seconds": {
            "encoder": round(t_enc, 3),
            "prompt_decoder_total": round(t_seg, 3),
            "fill": round(t_fill, 3),
            "total": round(t_enc + t_seg + t_fill, 3),
        },
        "regions": regions,
        "scores": scores,
        "mask_area_pct": {k: round(100 * float(m.mean()), 2) for k, m in masks.items()},
        "mask_sha256": {k: sha(np.packbits(m)) for k, m in masks.items()},
        "background_sha256": sha(np.asarray(background)),
        "rss_mb_after": round(peak_rss_mb(), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", type=Path, required=True)
    ap.add_argument("--lama", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--units", default="ALL")
    ap.add_argument("--providers", default="cpu", choices=["cpu", "coreml"])
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--select", default="score", choices=["score", "large"])
    ap.add_argument("--clean", action="store_true", help="close gaps and fill interior holes")
    ap.add_argument("--points", action="store_true", help="also use the include/exclude points")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    t = time.perf_counter()
    seg = Segmenter(a.models_dir, a.units)
    fill = Filler(a.lama, a.providers)
    load_s = time.perf_counter() - t
    result = {
        "units": a.units,
        "providers": a.providers,
        "select": a.select,
        "points": a.points,
        "clean": a.clean,
        "load_seconds": round(load_s, 2),
        "rss_mb_after_load": round(peak_rss_mb(), 1),
        "passes": [],
    }
    for p in range(a.passes):
        pages = [
            process_page(seg, fill, n, a.out, p == a.passes - 1, a.select, a.points, a.clean)
            for n in BOXES
        ]
        result["passes"].append(
            {"pages": pages, "total_seconds": round(sum(x["seconds"]["total"] for x in pages), 3)}
        )
    result["peak_rss_mb"] = round(peak_rss_mb(), 1)
    # Bit-exact check inside this process: pass 0 against the last pass.
    first, last = result["passes"][0]["pages"], result["passes"][-1]["pages"]
    result["in_process_identical"] = all(
        x["mask_sha256"] == y["mask_sha256"] and x["background_sha256"] == y["background_sha256"]
        for x, y in zip(first, last, strict=True)
    )
    (a.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "passes"}, indent=2))
    for i, ps in enumerate(result["passes"]):
        print(
            f"pass {i}: total {ps['total_seconds']}s;",
            ", ".join(f"p{x['page']}={x['seconds']['total']}s" for x in ps["pages"]),
        )


if __name__ == "__main__":
    main()
