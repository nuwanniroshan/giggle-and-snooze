"""Phase 0 spike for spec 010 (ADR-010): animate one real pilot page section on the CPU.

Throwaway code, kept for reference. It measures the whole animated section path:

    layers + effects at picture resolution (float32) → lerp(original, composite, ease)
    → cv2.resize into the cached blurred canvas → camera via cv2.warpAffine (camera_at, with
    zoompan's integer/even crop emulated) → rgb24 piped to FFmpeg → caption overlays → ENCODE_ARGS

Run (from the project root):
    uv run python docs/specs/010-animate-pages/spike_animate.py animated --out DIR [--page 14]
        [--threads N] [--worst] [--no-writer-thread] [--fused] [--resize lanczos|cubic|linear]
    uv run python docs/specs/010-animate-pages/spike_animate.py still --out DIR [--page 14]
    uv run python docs/specs/010-animate-pages/spike_animate.py hash CLIP
    uv run python docs/specs/010-animate-pages/spike_animate.py sheet CLIP OUT.png
    uv run python docs/specs/010-animate-pages/spike_animate.py frame0 A.mp4 B.mp4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import resource
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from video_pipeline import render_episode as re_
from video_pipeline import timeline
from video_pipeline.episode import load_book
from video_pipeline.frames import compose_canvas, render_caption
from video_pipeline.library import PROJECT_ROOT, book_folder
from video_pipeline.synthesize_voices import AudioPaths

SLUG = "the-snail-mail-express"
FPS = 30

# Spike settings (Phase 1 config candidates).
EASE_IN_S = 1.0
FAST_BLEND = True  # --numpy-blend turns it off
WARP_INTERP = cv2.INTER_LINEAR  # --warp sets it
PARALLAX_MAX_SHIFT = 0.006  # fraction of the shorter side, at depth 1
PARALLAX_PERIOD_S = 9.0
EFFECT_DEFAULTS = {
    # count: particles at intensity 1; speed: picture-heights per second (rain/fireflies wander
    # radius is `size`); size: px at picture resolution; strength: opacity or px displacement.
    "rain": {"count": 260, "speed": 1.6, "size": 40.0, "strength": 0.6, "colour": "#dfe6f0"},
    "fireflies": {"count": 40, "speed": 0.25, "size": 7.0, "strength": 0.9, "colour": "#ffe08a"},
    "water_ripple": {"count": 0, "speed": 0.5, "size": 38.0, "strength": 5.0, "colour": "#ffffff"},
}


# --- small pure helpers -----------------------------------------------------------------------


def smoothstep(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def seed_for(slug: str, page: int, index: int, kind: str) -> int:
    digest = hashlib.sha256(f"{slug}:{page}:{index}:{kind}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def hex_rgb(colour: str) -> np.ndarray:
    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0


# --- layers ---------------------------------------------------------------------------------


@dataclass
class Layer:
    name: str
    z: int
    depth: float
    pivot: tuple[float, float]
    motions: list[dict]
    offset: tuple[int, int]
    rgba: np.ndarray  # premultiplied float32 HxWx4 in 0..1


def load_layers(plan: dict, cut_dir: Path) -> list[Layer]:
    manifest = json.loads((cut_dir / "layers.json").read_text())
    by_name = {m["name"]: m for m in manifest["layers"]}
    layers = []
    for _index, spec in sorted(enumerate(plan["layers"]), key=lambda p: (p[1]["z"], p[0])):
        meta = by_name[spec["name"]]
        if meta.get("empty"):
            continue
        with Image.open(cut_dir / meta["file"]) as im:
            arr = np.asarray(im.convert("RGBA"), np.float32) / 255.0
        arr[..., :3] *= arr[..., 3:4]
        layers.append(
            Layer(
                spec["name"],
                spec["z"],
                spec["depth"],
                tuple(spec["pivot"]),
                spec["motions"],
                tuple(meta["offset"]),
                np.ascontiguousarray(arr),
            )
        )
    return layers


def layer_matrix(layer: Layer, t: float, ease: float, side: int) -> np.ndarray:
    """2x3 forward affine: layer-local pixel → picture pixel at time t."""
    angle = 0.0
    scale = 1.0
    dx = dy = 0.0
    for m in layer.motions:
        w = math.sin(2 * math.pi * (t / m["period_s"] + m.get("phase", 0.0)))
        a = m["amplitude"] * ease
        if m["type"] == "sway":
            angle += a * w
        elif m["type"] == "rotate":
            angle += a * 0.5 * (1 + w)
        elif m["type"] == "bob":
            dy += a * side * w
        elif m["type"] == "breathe":
            scale *= 1 + a * w
        elif m["type"] == "shift":
            vx, vy = m["direction"]
            n = math.hypot(vx, vy) or 1.0
            dx += a * side * w * vx / n
            dy += a * side * w * vy / n
    dx += (
        layer.depth
        * PARALLAX_MAX_SHIFT
        * side
        * math.sin(2 * math.pi * t / PARALLAX_PERIOD_S)
        * ease
    )
    px, py = layer.pivot
    rot = cv2.getRotationMatrix2D((px, py), -angle, scale)  # cv2 angle is counter-clockwise
    ox, oy = layer.offset
    m = rot.copy()
    m[:, 2] += rot[:, 0] * ox + rot[:, 1] * oy + np.array([dx, dy])
    return m


def layer_bbox(layer: Layer, m: np.ndarray, side: int) -> tuple[int, int, int, int]:
    h, w = layer.rgba.shape[:2]
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64) @ m.T
    x0 = max(int(math.floor(corners[:, 0].min())) - 1, 0)
    y0 = max(int(math.floor(corners[:, 1].min())) - 1, 0)
    x1 = min(int(math.ceil(corners[:, 0].max())) + 1, side)
    y1 = min(int(math.ceil(corners[:, 1].max())) + 1, side)
    return x0, y0, x1, y1


def composite_layer(dst: np.ndarray, layer: Layer, m: np.ndarray) -> None:
    """Warp the premultiplied layer into its transformed bbox and draw it over dst (in place)."""
    x0, y0, x1, y1 = layer_bbox(layer, m, dst.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    mm = m.copy()
    mm[:, 2] -= (x0, y0)
    warped = cv2.warpAffine(
        layer.rgba, mm, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
    )
    region = dst[y0:y1, x0:x1]
    if FAST_BLEND:
        # cv2 arithmetic on contiguous planes is ~16x faster than numpy broadcasting on views
        b, g, r, a = cv2.split(warped)
        inv = 1.0 - a
        mixed = cv2.multiply(np.ascontiguousarray(region), cv2.merge((inv, inv, inv)))
        region[...] = cv2.add(mixed, cv2.merge((b, g, r)))
    else:
        region *= 1.0 - warped[..., 3:4]
        region += warped[..., :3]


# --- effects --------------------------------------------------------------------------------


def _box(effect: dict, side: int) -> tuple[int, int, int, int]:
    region = effect.get("region") or {}
    return tuple(region["box"]) if region.get("box") else (0, 0, side, side)


@dataclass
class Rain:
    box: tuple[int, int, int, int]
    colour: np.ndarray
    opacity: float
    x0: np.ndarray
    y0: np.ndarray
    speed: np.ndarray
    length: np.ndarray
    slant: float
    mask: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.box
        self.mask = np.zeros((y1 - y0, x1 - x0), np.uint8)

    @classmethod
    def make(cls, effect: dict, d: dict, seed: int, side: int) -> Rain:
        rng = np.random.default_rng(seed)
        n = max(1, round(d["count"] * effect["intensity"]))
        return cls(
            _box(effect, side),
            hex_rgb(effect.get("colour") or d["colour"]),
            d["strength"] * (0.5 + 0.5 * effect["intensity"]),
            rng.random(n),
            rng.random(n),
            d["speed"] * rng.uniform(0.8, 1.2, n),
            d["size"] * rng.uniform(0.6, 1.0, n),
            0.18,
        )

    def draw(self, pic: np.ndarray, t: float, ease: float) -> None:
        x0, y0, x1, y1 = self.box
        bw, bh = x1 - x0, y1 - y0
        span = bh + self.length.max()
        y = (self.y0 * span + self.speed * t * bh) % span - self.length  # top of each streak
        x = (self.x0 * (bw + self.slant * span) - self.slant * span) + self.slant * (
            y + self.length
        )
        ends = np.stack(
            [x, y, x + self.slant * self.length, y + self.length], axis=1
        )  # x,y → x+slant·L, y+L
        pts = np.round(ends * 16).astype(np.int32).reshape(-1, 2, 2)  # 4 fractional bits
        self.mask.fill(0)
        cv2.polylines(self.mask, list(pts), False, 255, 1, cv2.LINE_AA, shift=4)
        a = self.mask.astype(np.float32) * (self.opacity * ease / 255.0)
        region = pic[y0:y1, x0:x1]
        a3 = a[..., None]
        region += (self.colour - region) * a3

    def rescale(self, k: float) -> None:
        """Move the effect to a picture shown k times larger (drawn after the resize)."""
        self.box = tuple(round(v * k) for v in self.box)
        self.length = self.length * k
        self.__post_init__()

    def draw8(self, out8: np.ndarray, t: float, ease: float) -> None:
        """Same streaks on the uint8 picture, touching only the covered pixels."""
        if ease <= 0:
            return
        x0, y0, x1, y1 = self.box
        bw, bh = x1 - x0, y1 - y0
        span = bh + self.length.max()
        y = (self.y0 * span + self.speed * t * bh) % span - self.length
        x = (self.x0 * (bw + self.slant * span) - self.slant * span) + self.slant * (
            y + self.length
        )
        ends = np.stack([x, y, x + self.slant * self.length, y + self.length], axis=1)
        pts = np.round(ends * 16).astype(np.int32).reshape(-1, 2, 2)
        self.mask.fill(0)
        cv2.polylines(self.mask, list(pts), False, 255, 1, cv2.LINE_AA, shift=4)
        ys, xs = np.nonzero(self.mask)
        a = self.mask[ys, xs].astype(np.float32)[:, None] * (self.opacity * ease / 255.0)
        region = out8[y0:y1, x0:x1]
        v = region[ys, xs].astype(np.float32)
        region[ys, xs] = (v + (self.colour * 255.0 - v) * a + 0.5).astype(np.uint8)


@dataclass
class Fireflies:
    box: tuple[int, int, int, int]
    colour: np.ndarray
    strength: float
    sprite: np.ndarray
    cx: np.ndarray
    cy: np.ndarray
    rx: np.ndarray
    ry: np.ndarray
    fx: np.ndarray
    fy: np.ndarray
    ph: np.ndarray
    pulse_f: np.ndarray

    @classmethod
    def make(cls, effect: dict, d: dict, seed: int, side: int) -> Fireflies:
        rng = np.random.default_rng(seed)
        n = max(1, round(d["count"] * effect["intensity"]))
        x0, y0, x1, y1 = _box(effect, side)
        r = int(math.ceil(d["size"] * 2))
        g = np.arange(-r, r + 1, dtype=np.float32)
        sprite = np.exp(-(g[None, :] ** 2 + g[:, None] ** 2) / (2 * (d["size"] / 2) ** 2))
        wander = d["speed"] * 0.1 * side
        return cls(
            (x0, y0, x1, y1),
            hex_rgb(effect.get("colour") or d["colour"]),
            d["strength"],
            sprite.astype(np.float32),
            rng.uniform(x0, x1, n),
            rng.uniform(y0, y1, n),
            rng.uniform(0.3, 1.0, n) * wander,
            rng.uniform(0.3, 1.0, n) * wander * 0.6,
            rng.uniform(0.03, 0.09, n),
            rng.uniform(0.04, 0.11, n),
            rng.uniform(0, 2 * math.pi, n),
            rng.uniform(0.25, 0.6, n),
        )

    def draw(self, pic: np.ndarray, t: float, ease: float) -> None:
        x0, y0, x1, y1 = self.box
        xs = self.cx + self.rx * np.sin(2 * np.pi * self.fx * t + self.ph)
        ys = self.cy + self.ry * np.sin(2 * np.pi * self.fy * t + 1.7 * self.ph)
        xs = np.clip(xs, x0, x1 - 1)
        ys = np.clip(ys, y0, y1 - 1)
        glow = (0.5 + 0.5 * np.sin(2 * np.pi * self.pulse_f * t + self.ph)) ** 2
        glow = glow * self.strength * ease
        r = self.sprite.shape[0] // 2
        h, w = pic.shape[:2]
        for x, y, b in zip(np.round(xs).astype(int), np.round(ys).astype(int), glow, strict=True):
            if b <= 1e-4:
                continue
            sx0, sy0 = max(x - r, 0), max(y - r, 0)
            sx1, sy1 = min(x + r + 1, w), min(y + r + 1, h)
            s = self.sprite[sy0 - (y - r) : sy1 - (y - r), sx0 - (x - r) : sx1 - (x - r)]
            pic[sy0:sy1, sx0:sx1] += s[..., None] * (self.colour * b)

    def rescale(self, k: float) -> None:
        self.box = tuple(round(v * k) for v in self.box)
        self.cx, self.cy, self.rx, self.ry = self.cx * k, self.cy * k, self.rx * k, self.ry * k

    def draw8(self, out8: np.ndarray, t: float, ease: float) -> None:
        x0, y0, x1, y1 = self.box
        xs = np.clip(self.cx + self.rx * np.sin(2 * np.pi * self.fx * t + self.ph), x0, x1 - 1)
        ys = np.clip(
            self.cy + self.ry * np.sin(2 * np.pi * self.fy * t + 1.7 * self.ph), y0, y1 - 1
        )
        glow = (0.5 + 0.5 * np.sin(2 * np.pi * self.pulse_f * t + self.ph)) ** 2
        glow = glow * self.strength * ease
        r = self.sprite.shape[0] // 2
        h, w = out8.shape[:2]
        for x, y, b in zip(np.round(xs).astype(int), np.round(ys).astype(int), glow, strict=True):
            if b <= 1e-4:
                continue
            sx0, sy0 = max(x - r, 0), max(y - r, 0)
            sx1, sy1 = min(x + r + 1, w), min(y + r + 1, h)
            sp = self.sprite[sy0 - (y - r) : sy1 - (y - r), sx0 - (x - r) : sx1 - (x - r)]
            patch = out8[sy0:sy1, sx0:sx1]
            add = sp[..., None] * (self.colour * (255.0 * b))
            patch[...] = np.minimum(patch + add + 0.5, 255.0).astype(np.uint8)


@dataclass
class Ripple:
    box: tuple[int, int, int, int]
    amp: float
    wavelength: float
    speed: float
    gx: np.ndarray
    gy: np.ndarray
    taper: np.ndarray
    phase: float

    @classmethod
    def make(cls, effect: dict, d: dict, seed: int, side: int) -> Ripple:
        rng = np.random.default_rng(seed)
        x0, y0, x1, y1 = _box(effect, side)
        bw, bh = x1 - x0, y1 - y0
        gx, gy = np.meshgrid(np.arange(bw, dtype=np.float32), np.arange(bh, dtype=np.float32))
        edge = min(24.0, bw / 4, bh / 4)
        tx = np.clip(np.minimum(gx, bw - 1 - gx) / edge, 0, 1)
        ty = np.clip(np.minimum(gy, bh - 1 - gy) / edge, 0, 1)
        taper = (tx * ty).astype(np.float32)
        return cls(
            (x0, y0, x1, y1),
            d["strength"] * effect["intensity"],
            d["size"],
            d["speed"],
            gx,
            gy,
            taper,
            float(rng.uniform(0, 2 * math.pi)),
        )

    def draw(self, pic: np.ndarray, t: float, ease: float) -> None:
        a = self.amp * ease
        if a <= 1e-3:
            return
        x0, y0, x1, y1 = self.box
        k = 2 * math.pi / self.wavelength
        w = 2 * math.pi * self.speed * t + self.phase
        dx = a * self.taper * np.sin(k * self.gy + 0.35 * k * self.gx * 0.2 - w)
        dy = 0.5 * a * self.taper * np.sin(0.6 * k * self.gx + 0.8 * k * self.gy - 1.3 * w)
        region = pic[y0:y1, x0:x1]
        out = cv2.remap(
            region, self.gx + dx, self.gy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT
        )
        region[...] = out


# --- section --------------------------------------------------------------------------------


def load_section(page: int):
    folder = book_folder(SLUG, PROJECT_ROOT)
    book = load_book(folder.book_json)
    cfg = timeline.load_render_config(PROJECT_ROOT / "config" / "render.json")
    script = json.loads(folder.script_path.read_text(encoding="utf-8"))
    audio = AudioPaths(folder.build_dir / "audio")
    names = [
        "intro",
        *[timeline.page_section_name(int(p["page_number"])) for p in script["pages"]],
        "outro",
    ]
    timings = {n: json.loads(audio.track_timing(n).read_text(encoding="utf-8")) for n in names}
    pictures = re_.resolve_pictures(book, folder, PROJECT_ROOT / "assets" / "nook")
    sections = timeline.build_sections(script, timings, cfg, pictures.by_section)
    section = next(s for s in sections if s.name == timeline.page_section_name(page))
    return folder, cfg, section, pictures.files[section.picture]


def prepare_stills(section, picture: Path, cfg, work: Path) -> tuple[Path, list[Path]]:
    work.mkdir(parents=True, exist_ok=True)
    canvas = work / "canvas.png"
    compose_canvas(picture, cfg, canvas)
    captions = []
    for number, card in enumerate(section.captions):
        path = work / f"caption_{number:03d}.png"
        render_caption(card.text, cfg, path)
        captions.append(path)
    return canvas, captions


def animated_filtergraph(section, caption_count: int, cfg) -> str:
    """section_filtergraph with scale×4 + zoompan replaced by the BT.709 conversion of the piped
    camera-applied frames."""
    still = re_.section_filtergraph(section, caption_count, cfg)
    first, rest = still.split(";\n", 1) if caption_count or ";\n" in still else (still, "")
    head = f"[0:v]{re_._to_bt709_yuv(cfg.width, cfg.height)}[base]"
    return head + (";\n" + rest if rest else "")


def zoompan_crop(cam, frame: int, w: int, h: int, up: int) -> tuple[float, float, float, float]:
    """The crop zoompan takes (x, y, cw, ch) in 1x canvas pixels: FFmpeg evaluates z/x/y as
    doubles but crops with int w = iw/zoom, int x clipped to [0, iw-w] and rounded down to even
    (yuv420p chroma), at the ×`up` upscaled size."""
    z, cx, cy = timeline.camera_at(cam, frame)
    z = min(max(z, 1.0), 10.0)
    iw, ih = up * w, up * h
    cw, ch = int(iw * (1.0 / z)), int(ih * (1.0 / z))
    x = int(min(max(cx * iw - iw / z / 2, 0), max(iw - cw, 0))) & ~1
    y = int(min(max(cy * ih - ih / z / 2, 0), max(ih - ch, 0))) & ~1
    return x / up, y / up, cw / up, ch / up


def camera_matrix(crop, w: int, h: int) -> np.ndarray:
    """Inverse map (output pixel → canvas pixel), pixel centres aligned."""
    x, y, cw, ch = crop
    sx, sy = cw / w, ch / h
    return np.array([[sx, 0, x + 0.5 * sx - 0.5], [0, sy, y + 0.5 * sy - 0.5]], np.float64)


INTERP = {"lanczos": cv2.INTER_LANCZOS4, "cubic": cv2.INTER_CUBIC, "linear": cv2.INTER_LINEAR}


def _union_mask(rects, side: int) -> np.ndarray:
    mask = np.zeros((side, side), bool)
    for x0, y0, x1, y1 in rects:
        mask[y0:y1, x0:x1] = True
    return mask


def run_animated(args) -> dict:
    global FAST_BLEND, WARP_INTERP
    FAST_BLEND = not args.numpy_blend
    WARP_INTERP = INTERP[args.warp]
    if args.threads is not None:
        cv2.setNumThreads(args.threads)
    folder, cfg, section, picture = load_section(args.page)
    if args.max_frames:
        import dataclasses

        section = dataclasses.replace(section, frames=min(section.frames, args.max_frames))
    out = Path(args.out)
    work = out / "work"
    canvas_png, captions = prepare_stills(section, picture, cfg, work)
    plan = json.loads(
        (folder.book_json.parent / "animation" / f"page_{args.page:02d}.json").read_text()
    )
    cut_dir = folder.build_dir / "layers" / f"page_{args.page:02d}"

    t_load = time.perf_counter()
    with Image.open(picture) as im:
        original = np.asarray(im.convert("RGB"), np.float32) / 255.0
    side = original.shape[0]
    with Image.open(cut_dir / "background.png") as im:
        background = np.asarray(im.convert("RGB"), np.float32) / 255.0
    layers = load_layers(plan, cut_dir)
    effects_spec = [e for e in plan["effects"] if e["type"] in ("water_ripple", "fireflies")]
    effects_spec.append({"type": "rain", "region": None, "intensity": 0.5, "colour": None})
    if args.worst:
        effects_spec = [{**e, "intensity": 1.0} for e in effects_spec]
    makers = {"rain": Rain, "fireflies": Fireflies, "water_ripple": Ripple}
    effects = [
        makers[e["type"]].make(
            e, EFFECT_DEFAULTS[e["type"]], seed_for(SLUG, args.page, i, e["type"]), side
        )
        for i, e in enumerate(effects_spec)
    ]
    # water_ripple distorts what is under it; particles go on top.
    effects.sort(key=lambda e: 0 if isinstance(e, Ripple) else 1)
    with Image.open(canvas_png) as im:
        canvas = np.asarray(im.convert("RGB"), np.uint8).copy()
    W, H = cfg.width, cfg.height
    fw = round(side * min(W / side, H / side))
    px, py = (W - fw) // 2, (H - fw) // 2
    t_load = time.perf_counter() - t_load

    graph = work / "anim_graph.txt"
    graph.write_text(animated_filtergraph(section, len(captions), cfg), encoding="utf-8")
    clip = out / f"page_{args.page:02d}_animated.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-framerate",
        str(cfg.fps),
        "-i",
        "-",
    ]
    for c in captions:
        cmd += ["-framerate", str(cfg.fps), "-i", str(c)]
    cmd += [
        "-filter_complex_script",
        str(graph),
        "-map",
        "[vout]",
        "-frames:v",
        str(section.frames),
        *re_.ENCODE_ARGS(cfg),
        str(clip),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    stages = dict.fromkeys(("composite", "effects", "to_uint8", "resize", "warp", "write"), 0.0)
    frames_hash = hashlib.sha256()
    q: queue.Queue = queue.Queue(maxsize=3)
    write_s = [0.0]

    def writer() -> None:
        while (item := q.get()) is not None:
            t0 = time.perf_counter()
            proc.stdin.write(item)
            write_s[0] += time.perf_counter() - t0

    thread = None
    if not args.no_writer_thread:
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()

    interp = INTERP[args.resize]
    frame_buf = canvas.copy()
    pic = np.empty_like(background)
    pic8 = np.empty((side, side, 3), np.uint8)
    # static-layer cache: layers without motions and depth 0 are baked into the base once
    moving = [ly for ly in layers if ly.motions or ly.depth > 0]
    base = background.copy()
    for ly in layers:
        if ly not in moving:
            composite_layer(base, ly, layer_matrix(ly, 0.0, 0.0, side))
    ease_frames = EASE_IN_S * cfg.fps

    # dirty rects: each moving layer's envelope over the whole section, and distortion regions.
    # Outside them the cut background equals the original picture (checked), so pic8 starts as
    # the original and only the rects are recomputed in float.
    rects: list[tuple[int, int, int, int]] = []
    if args.dirty:
        for ly in moving:
            boxes = np.array(
                [
                    layer_bbox(
                        ly, layer_matrix(ly, f / cfg.fps, smoothstep(f / ease_frames), side), side
                    )
                    for f in range(section.frames)
                ]
            )
            rects.append(
                (boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max())
            )
        rects += [e.box for e in effects if isinstance(e, Ripple)]
        np.copyto(pic, base)
        pic8[...] = np.asarray(Image.open(picture).convert("RGB"))
        if not np.array_equal(
            np.round(base * 255).astype(np.uint8)[~_union_mask(rects, side)],
            pic8[~_union_mask(rects, side)],
        ):
            raise SystemExit("background differs from the original outside the dirty rects")
    distorters = [e for e in effects if isinstance(e, Ripple)]
    particles = [e for e in effects if not isinstance(e, Ripple)]
    out8 = np.empty_like(pic8)
    kf = fw / side
    front_static = canvas[py : py + fw, px : px + fw].copy()  # Pillow LANCZOS, as the still
    front_rects = [
        (
            max(int(math.floor(x0 * kf)) - 1, 0),
            max(int(math.floor(y0 * kf)) - 1, 0),
            min(int(math.ceil(x1 * kf)) + 1, fw),
            min(int(math.ceil(y1 * kf)) + 1, fw),
        )
        for x0, y0, x1, y1 in rects
    ]
    if args.front:
        if not args.dirty:
            raise SystemExit("--front needs --dirty")
        for e in particles:
            e.rescale(kf)

    wall0 = time.perf_counter()
    for f in range(section.frames):
        t = f / cfg.fps
        ease = smoothstep(f / ease_frames)
        s0 = time.perf_counter()
        if args.dirty:
            for x0, y0, x1, y1 in rects:
                pic[y0:y1, x0:x1] = base[y0:y1, x0:x1]
            for ly in moving:
                composite_layer(pic, ly, layer_matrix(ly, t, ease, side))
            s1 = time.perf_counter()
            for e in distorters:
                e.draw(pic, t, ease)
            sa = time.perf_counter()
            for x0, y0, x1, y1 in rects:
                src = pic[y0:y1, x0:x1]
                if ease < 1.0:
                    src = cv2.addWeighted(original[y0:y1, x0:x1], 1.0 - ease, src, ease, 0.0)
                pic8[y0:y1, x0:x1] = cv2.convertScaleAbs(src, alpha=255.0)
            if not args.front:
                np.copyto(out8, pic8)
            sb = time.perf_counter()
            if not args.front:
                for e in particles:
                    e.draw8(out8, t, ease)
            s3 = time.perf_counter()
            # report: effects = distortion + particles, to_uint8 = lerp/convert of the rects
            s2 = s1 + (sa - s1) + (s3 - sb)
            stages["to_uint8"] -= (s3 - s2) - (sb - sa)
            src8 = out8
        else:
            np.copyto(pic, base)
            for ly in moving:
                composite_layer(pic, ly, layer_matrix(ly, t, ease, side))
            s1 = time.perf_counter()
            for e in effects:
                e.draw(pic, t, ease)
            if ease < 1.0:
                cv2.addWeighted(original, 1.0 - ease, pic, ease, 0.0, dst=pic)
            s2 = time.perf_counter()
            cv2.convertScaleAbs(pic, pic8, alpha=255.0)  # rounds and saturates (values are ≥ 0)
            s3 = time.perf_counter()
            src8 = pic8
        crop = zoompan_crop(section.camera, f, W, H, cfg.upscale)
        if args.fused:
            # one warp straight from picture pixels: canvas warp for the backdrop, then the picture
            m = camera_matrix(crop, W, H)
            frame = cv2.warpAffine(
                canvas,
                m,
                (W, H),
                flags=WARP_INTERP | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )
            k = side / fw
            mp = np.array([[k, 0, -px * k + 0.5 * k - 0.5], [0, k, -py * k + 0.5 * k - 0.5]])
            mp = np.vstack([mp, [0, 0, 1]]) @ np.vstack([m, [0, 0, 1]])
            cv2.warpAffine(
                src8,
                mp[:2],
                (W, H),
                dst=frame,
                flags=WARP_INTERP | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_TRANSPARENT,
            )
            s4 = s3
        elif args.front:
            # resize only the dirty rects into the cached front picture, then draw particles at
            # front resolution (rain covers everything, so a full resize per frame is avoided)
            view = frame_buf[py : py + fw, px : px + fw]
            np.copyto(view, front_static)
            for dx0, dy0, dx1, dy1 in front_rects:
                m_inv = np.array(
                    [[1 / kf, 0, (dx0 + 0.5) / kf - 0.5], [0, 1 / kf, (dy0 + 0.5) / kf - 0.5]]
                )
                cv2.warpAffine(
                    pic8,
                    m_inv,
                    (dx1 - dx0, dy1 - dy0),
                    dst=view[dy0:dy1, dx0:dx1],
                    flags=interp | cv2.WARP_INVERSE_MAP,
                    borderMode=cv2.BORDER_REFLECT,
                )
            sp = time.perf_counter()
            for e in particles:
                e.draw8(view, t, ease)
            s4 = time.perf_counter()
            stages["effects"] += s4 - sp
            stages["resize"] -= s4 - sp
            frame = cv2.warpAffine(
                frame_buf,
                camera_matrix(crop, W, H),
                (W, H),
                flags=WARP_INTERP | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )
        else:
            front = cv2.resize(src8, (fw, fw), interpolation=interp)
            frame_buf[py : py + fw, px : px + fw] = front
            s4 = time.perf_counter()
            frame = cv2.warpAffine(
                frame_buf,
                camera_matrix(crop, W, H),
                (W, H),
                flags=WARP_INTERP | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            )
        s5 = time.perf_counter()
        data = frame.tobytes()
        if args.hash_input:
            frames_hash.update(data)
        if thread:
            q.put(data)
        else:
            proc.stdin.write(data)
        s6 = time.perf_counter()
        stages["composite"] += s1 - s0
        stages["effects"] += s2 - s1
        stages["to_uint8"] += s3 - s2
        stages["resize"] += s4 - s3
        stages["warp"] += s5 - s4
        stages["write"] += s6 - s5
    if thread:
        q.put(None)
        thread.join()
    proc.stdin.close()
    err = proc.stderr.read().decode()
    rc = proc.wait()
    wall = time.perf_counter() - wall0
    if rc != 0:
        raise SystemExit(f"ffmpeg failed: {err}")
    video_s = section.frames / cfg.fps
    result = {
        "mode": "animated",
        "page": args.page,
        "frames": section.frames,
        "video_s": round(video_s, 3),
        "wall_s": round(wall, 2),
        "s_per_s": round(wall / video_s, 3),
        "load_s": round(t_load, 2),
        "stages_s": {k: round(v, 2) for k, v in stages.items()},
        "writer_blocked_s": round(write_s[0], 2),
        "threads": cv2.getNumThreads(),
        "worst": args.worst,
        "fused": args.fused,
        "dirty": args.dirty,
        "front": args.front,
        "fast_blend": FAST_BLEND,
        "warp_interp": args.warp,
        "resize": args.resize,
        "layers_moving": len(moving),
        "effects": [type(e).__name__ for e in effects],
        "input_frames_sha256": frames_hash.hexdigest() if args.hash_input else None,
        "maxrss_self_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1),
        "maxrss_ffmpeg_mb": round(
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2**20, 1
        ),
        "clip": str(clip),
    }
    return result


def run_still(args) -> dict:
    folder, cfg, section, picture = load_section(args.page)
    out = Path(args.out)
    work = out / "work_still"
    canvas, captions = prepare_stills(section, picture, cfg, work)
    graph = work / "graph.txt"
    graph.write_text(re_.section_filtergraph(section, len(captions), cfg), encoding="utf-8")
    clip = out / f"page_{args.page:02d}_still.mp4"
    t0 = time.perf_counter()
    re_.run_ffmpeg(re_.section_command(section, canvas, captions, graph, clip, cfg), "still")
    wall = time.perf_counter() - t0
    video_s = section.frames / cfg.fps
    return {
        "mode": "still",
        "frames": section.frames,
        "video_s": round(video_s, 3),
        "wall_s": round(wall, 2),
        "s_per_s": round(wall / video_s, 3),
        "maxrss_self_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1),
        "maxrss_ffmpeg_mb": round(
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 2**20, 1
        ),
        "clip": str(clip),
    }


def decode(clip: str, extra: list[str] | None = None) -> subprocess.Popen:
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        clip,
        *(extra or []),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE)


def hash_clip(clip: str) -> str:
    h = hashlib.sha256()
    p = decode(clip)
    while chunk := p.stdout.read(1 << 22):
        h.update(chunk)
    p.wait()
    return h.hexdigest()


def first_frame(clip: str) -> np.ndarray:
    p = decode(clip, ["-frames:v", "1"])
    data = p.stdout.read()
    p.wait()
    return np.frombuffer(data, np.uint8).reshape(1080, 1920, 3)


def contact_sheet(clip: str, out_png: str, frames: int = 1682) -> None:
    times = [0.0, 2.0, 8.0, 20.0, 34.0, 50.0]
    tiles = []
    for t in times:
        p = subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-ss",
                str(t),
                "-i",
                clip,
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        im = Image.frombytes("RGB", (1920, 1080), p.stdout).resize(
            (960, 540), Image.Resampling.LANCZOS
        )
        tiles.append(im)
    sheet = Image.new("RGB", (960 * 2, 540 * 3))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % 2) * 960, (i // 2) * 540))
    sheet.save(out_png)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("animated")
    a.add_argument("--out", required=True)
    a.add_argument("--page", type=int, default=14)
    a.add_argument("--threads", type=int, default=None)
    a.add_argument("--worst", action="store_true")
    a.add_argument("--no-writer-thread", action="store_true")
    a.add_argument("--fused", action="store_true")
    a.add_argument("--dirty", action="store_true")
    a.add_argument("--front", action="store_true")
    a.add_argument("--numpy-blend", action="store_true")
    a.add_argument("--warp", choices=["linear", "cubic", "lanczos"], default="linear")
    a.add_argument("--max-frames", type=int, default=None)
    a.add_argument("--hash-input", action="store_true")
    a.add_argument("--resize", choices=list(INTERP), default="lanczos")
    s = sub.add_parser("still")
    s.add_argument("--out", required=True)
    s.add_argument("--page", type=int, default=14)
    h = sub.add_parser("hash")
    h.add_argument("clip")
    c = sub.add_parser("sheet")
    c.add_argument("clip")
    c.add_argument("png")
    f0 = sub.add_parser("frame0")
    f0.add_argument("a")
    f0.add_argument("b")
    args = ap.parse_args(argv)
    if args.cmd == "animated":
        print(json.dumps(run_animated(args), indent=2))
    elif args.cmd == "still":
        print(json.dumps(run_still(args), indent=2))
    elif args.cmd == "hash":
        print(hash_clip(args.clip))
    elif args.cmd == "sheet":
        contact_sheet(args.clip, args.png)
    elif args.cmd == "frame0":
        a0, b0 = first_frame(args.a).astype(np.int16), first_frame(args.b).astype(np.int16)
        diff = np.abs(a0 - b0)
        pic = diff[:, 420:1500]
        print(
            json.dumps(
                {
                    "mad_255": round(float(diff.mean()), 4),
                    "max": int(diff.max()),
                    "mad_picture_area": round(float(pic.mean()), 4),
                    "p99": float(np.percentile(diff, 99)),
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
