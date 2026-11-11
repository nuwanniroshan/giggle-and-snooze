"""Pure page animator for spec 010: layer motions, parallax, effects, the compositor and the
camera warp. No FFmpeg and no files written; everything here is unit-tested on tiny arrays.

The frame path is the one measured in the ADR-010 spike (docs/specs/010-animate-pages/spike.md):

- Only *dirty rects* are recomputed in float32: each moving layer's section envelope (the union
  of its transformed bbox over every frame) and each distortion region. Outside them the cut
  background equals the original picture (checked on load, else the whole picture is one rect),
  so a persistent uint8 picture that starts as the original keeps everything else.
- Layers are warped into their transformed bbox only and drawn "over" with cv2 arithmetic on
  contiguous planes (numpy broadcasting on strided views is ~16x slower).
- Inside each rect the composite is eased in from the original with `addWeighted(original, 1-e,
  composite, e)` and rounded into the uint8 picture, so frame 0 is the original picture exactly.
- Overlay effects (particles, haze, glow) are drawn on a uint8 copy after the ease, with opacity
  × ease, touching only the pixels they cover. `water_ripple` distorts the float composite first.
- Every random value comes from `seed_for(slug, page, effect index, effect type)`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from video_pipeline.cut_layers import (
    MANIFEST,
    cut_state,
    layers_dir,
    manifest_files,
    read_config,
)
from video_pipeline.file_hash import sha256_file
from video_pipeline.library import BookFolder
from video_pipeline.motion_plan import (
    EFFECTS,
    LIMITS_FILE,
    Effect,
    Layer,
    MotionPlan,
    load_limits,
    plan_path,
    plan_state,
)
from video_pipeline.timeline import AnimationSettings, Camera, EffectDefaults, _hash, camera_at

ANIMATION_VERSION = 2  # 2: speed follows intensity; rest frames match the still canvas
REFERENCE_SIDE = 1024  # effect sizes in the config are px on a picture with this shorter side
EDGE_TAPER_PX = 24.0  # haze and ripple fade to nothing over this many px at a box edge (at 1024)
ZOOM_MAX = 10.0  # zoompan's zoom range

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (x1/y1 exclusive), picture pixels


class AnimationError(ValueError):
    """A page can't be animated from these files (missing, unreadable or mismatched)."""


# --- small pure helpers -------------------------------------------------------------------


def ease(t: float, ease_in_s: float) -> float:
    """Smoothstep ease-in from 0 at t = 0 to 1 at t ≥ ease_in_s; 1 throughout when ease_in_s ≤ 0."""
    if ease_in_s <= 0:
        return 1.0
    p = min(max(t / ease_in_s, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def seed_for(slug: str, page: int, index: int, kind: str) -> int:
    """The seed of one effect: the first 8 bytes of sha256("slug:page:index:type"), big-endian."""
    digest = hashlib.sha256(f"{slug}:{page}:{index}:{kind}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rgb255(colour: str) -> np.ndarray:
    return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], np.float32)


def _smooth(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _taper(width: int, height: int, edge: float) -> np.ndarray:
    """1 inside, falling linearly to 0 at the outermost pixels over `edge` px."""
    edge = max(min(edge, width / 4, height / 4), 1e-6)
    gx = np.arange(width, dtype=np.float32)
    gy = np.arange(height, dtype=np.float32)
    tx = np.clip(np.minimum(gx, width - 1 - gx) / edge, 0, 1)
    ty = np.clip(np.minimum(gy, height - 1 - gy) / edge, 0, 1)
    return np.ascontiguousarray(ty[:, None] * tx[None, :], dtype=np.float32)


def _gaussian_sprite(sigma: float) -> np.ndarray:
    r = max(1, math.ceil(sigma * 3))
    g = np.arange(-r, r + 1, dtype=np.float32)
    return np.exp(-(g[None, :] ** 2 + g[:, None] ** 2) / (2 * sigma * sigma)).astype(np.float32)


# --- layer geometry -----------------------------------------------------------------------


def layer_matrix(
    layer: Layer,
    t: float,
    short_side: int,
    e: float,
    scale: float,
    parallax: tuple[float, float],
) -> np.ndarray:
    """2x3 forward affine in picture pixels: where a point of the layer at rest is at time t.

    sway: rotation ±A·sin; rotate: A·(1−cos)/2, one-sided 0..A; bob: dy = A·short·sin; shift: the
    normalised direction × A·short·sin; breathe: scale 1 + A·sin. Rotation and scale are around
    the pivot (positive angles turn clockwise on screen). Parallax adds dx = depth · max_shift ·
    short · sin(2πt/period). Every amplitude is multiplied by the ease `e` and the page `scale`.
    """
    k = e * scale
    angle = 0.0
    zoom = 1.0
    dx = dy = 0.0
    for m in layer.motions:
        w = 2 * math.pi * (t / m.period_s + m.phase)
        a = m.amplitude * k
        if m.type == "sway":
            angle += a * math.sin(w)
        elif m.type == "rotate":
            angle += a * (1 - math.cos(w)) / 2
        elif m.type == "bob":
            dy += a * short_side * math.sin(w)
        elif m.type == "shift" and m.direction is not None:
            vx, vy = m.direction
            n = math.hypot(vx, vy) or 1.0
            dx += a * short_side * math.sin(w) * vx / n
            dy += a * short_side * math.sin(w) * vy / n
        elif m.type == "breathe":
            zoom *= 1 + a * math.sin(w)
    max_shift, period_s = parallax
    dx += layer.depth * max_shift * short_side * math.sin(2 * math.pi * t / period_s) * k
    px, py = layer.pivot
    rad = math.radians(angle)
    c, s = zoom * math.cos(rad), zoom * math.sin(rad)
    return np.array(
        [[c, -s, px - c * px + s * py + dx], [s, c, py - s * px - c * py + dy]], np.float64
    )


def _with_offset(m: np.ndarray, offset: tuple[int, int]) -> np.ndarray:
    """Layer-image pixel → picture pixel: the picture-space matrix after the cut's offset."""
    out = m.copy()
    out[:, 2] += m[:, 0] * offset[0] + m[:, 1] * offset[1]
    return out


def _bbox(m: np.ndarray, w: int, h: int, width: int, height: int) -> Rect | None:
    """The picture rect a w×h image covers under m, one pixel of margin, clipped (None if empty)."""
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64) @ m.T
    x0 = max(math.floor(corners[:, 0].min()) - 1, 0)
    y0 = max(math.floor(corners[:, 1].min()) - 1, 0)
    x1 = min(math.ceil(corners[:, 0].max()) + 1, width)
    y1 = min(math.ceil(corners[:, 1].max()) + 1, height)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _union(rects: Iterable[Rect | None]) -> Rect | None:
    got = [r for r in rects if r is not None]
    if not got:
        return None
    return (
        min(r[0] for r in got),
        min(r[1] for r in got),
        max(r[2] for r in got),
        max(r[3] for r in got),
    )


@dataclass
class LayerState:
    layer: Layer  # the plan layer (geometry)
    rgba: np.ndarray  # premultiplied float32 h×w×4, 0..1
    offset: tuple[int, int]  # top-left in picture pixels (from the manifest)
    moving: bool  # has motions or depth; static layers are baked into the base once

    @property
    def size(self) -> tuple[int, int]:
        return self.rgba.shape[1], self.rgba.shape[0]


def _warp(ls: LayerState, m: np.ndarray, width: int, height: int):
    """(rect, warped premultiplied RGBA) of a layer under its local matrix m, or None."""
    w, h = ls.size
    rect = _bbox(m, w, h, width, height)
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    mm = m.copy()
    mm[:, 2] -= (x0, y0)
    warped = cv2.warpAffine(
        ls.rgba, mm, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
    )
    return rect, warped


def _over(dst: np.ndarray, rect: Rect, warped: np.ndarray) -> None:
    """Draw premultiplied `warped` over dst[rect] in place, with cv2 arithmetic."""
    x0, y0, x1, y1 = rect
    region = dst[y0:y1, x0:x1]
    b, g, r, a = cv2.split(warped)
    inv = 1.0 - a
    mixed = cv2.multiply(np.ascontiguousarray(region), cv2.merge((inv, inv, inv)))
    region[...] = cv2.add(mixed, cv2.merge((b, g, r)))


# --- effect areas and blends --------------------------------------------------------------


@dataclass
class Area:
    """Where an effect draws on one frame. Effects work in region-local coordinates (0..w,
    0..h of the box, layer image or picture); `to_picture` maps them to picture pixels. `rect` is
    the clipped picture rect the effect may change; `mask` (layer regions) is the layer's warped
    alpha over `rect` and bounds the effect to the layer's area."""

    rect: Rect
    to_picture: np.ndarray
    local_size: tuple[int, int]
    mask: np.ndarray | None = None

    @property
    def shape(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.rect
        return y1 - y0, x1 - x0

    def to_rect(self) -> np.ndarray:
        m = self.to_picture.copy()
        m[:, 2] -= self.rect[:2]
        return m

    def points(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        m = self.to_rect()
        return m[0, 0] * xs + m[0, 1] * ys + m[0, 2], m[1, 0] * xs + m[1, 1] * ys + m[1, 2]

    def place(self, local: np.ndarray) -> np.ndarray:
        """A local float32 field resampled onto `rect` (unchanged for an axis-aligned box)."""
        h, w = self.shape
        m = self.to_rect()
        if local.shape == (h, w) and np.array_equal(m, [[1, 0, 0], [0, 1, 0]]):
            return local
        return cv2.warpAffine(
            local, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
        )


def _blend_cover(
    out8: np.ndarray, area: Area, cover: np.ndarray, colour: np.ndarray, opacity: float
) -> None:
    """Blend `colour` over the pixels a uint8 coverage mask (rect-shaped) touches."""
    ys, xs = np.nonzero(cover)
    if ys.size == 0:
        return
    a = cover[ys, xs].astype(np.float32) * (opacity / 255.0)
    if area.mask is not None:
        a *= area.mask[ys, xs]
    x0, y0, x1, y1 = area.rect
    region = out8[y0:y1, x0:x1]
    v = region[ys, xs].astype(np.float32)
    region[ys, xs] = np.clip(v + (colour - v) * a[:, None] + 0.5, 0, 255).astype(np.uint8)


def _blend_dense(
    out8: np.ndarray, area: Area, alpha: np.ndarray, colour: np.ndarray, additive: bool
) -> None:
    """Blend (or add) `colour` with a rect-shaped float32 alpha over the area's rect."""
    if area.mask is not None:
        alpha = alpha * area.mask
    x0, y0, x1, y1 = area.rect
    region = out8[y0:y1, x0:x1]
    f = region.astype(np.float32)
    a3 = cv2.merge((alpha, alpha, alpha))
    f += (colour * a3) if additive else (colour - f) * a3
    region[...] = cv2.convertScaleAbs(f)


def _blend_sprite(
    out8: np.ndarray,
    area: Area,
    x: float,
    y: float,
    sprite: np.ndarray,
    amount: float,
    colour: np.ndarray,
    additive: bool,
) -> None:
    """One sprite centred at rect coordinates (x, y), clipped to the rect."""
    rh, rw = area.shape
    r = sprite.shape[0] // 2
    xi, yi = round(x), round(y)
    sx0, sy0 = max(xi - r, 0), max(yi - r, 0)
    sx1, sy1 = min(xi + r + 1, rw), min(yi + r + 1, rh)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    a = sprite[sy0 - (yi - r) : sy1 - (yi - r), sx0 - (xi - r) : sx1 - (xi - r)] * amount
    if area.mask is not None:
        a = a * area.mask[sy0:sy1, sx0:sx1]
    x0, y0 = area.rect[:2]
    patch = out8[y0 + sy0 : y0 + sy1, x0 + sx0 : x0 + sx1]
    v = patch.astype(np.float32)
    v += a[..., None] * colour if additive else (colour - v) * a[..., None]
    patch[...] = np.clip(v + 0.5, 0, 255).astype(np.uint8)


# --- effects ------------------------------------------------------------------------------


@dataclass
class EffectState:
    """One plan effect, ready to draw. Subclasses set `distorts` and implement `setup` plus
    `draw` (overlays, on the uint8 picture) or `distort` (on the float composite)."""

    spec: Effect
    defaults: EffectDefaults
    intensity: float  # min(1, plan intensity × page scale)
    colour: np.ndarray  # RGB 0..255 float32 (the plan's hint, else the default)
    k: float  # picture short side / 1024: multiplies every px size in the defaults
    short: int
    local_size: tuple[int, int]
    region_layer: str | None
    region_box: Rect | None
    rng: np.random.Generator
    duration_s: float
    distorts = False

    def __post_init__(self) -> None:
        self.setup()

    def setup(self) -> None:  # pragma: no cover - overridden
        pass

    def draw(self, out8: np.ndarray, area: Area, t: float, e: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def distort(self, pic: np.ndarray, area: Area, t: float, e: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def count(self) -> int:
        return max(1, round(self.defaults.count * self.intensity))

    def speed_factor(self) -> float:
        """Moving particles go faster with the intensity: × (0.5 + 0.5 × intensity) (AC-1.3)."""
        return 0.5 + 0.5 * self.intensity

    def particle_opacity(self, e: float) -> float:
        return self.defaults.strength * (0.5 + 0.5 * self.intensity) * e


class Rain(EffectState):
    """Slanted anti-aliased 1 px streaks falling at speed × (0.5 + 0.5 × intensity) × short side
    per second, wrapping."""

    SLANT = 0.18

    def setup(self) -> None:
        d, n = self.defaults, self.count()
        self.x0 = self.rng.random(n)
        self.y0 = self.rng.random(n)
        self.speed = d.speed * self.speed_factor() * self.short * self.rng.uniform(0.8, 1.2, n)
        self.length = max(d.size * self.k, 1.0) * self.rng.uniform(0.6, 1.0, n)

    def draw(self, out8, area, t, e):
        bw, bh = self.local_size
        span = bh + self.length.max()
        y = (self.y0 * span + self.speed * t) % span - self.length
        x = self.x0 * (bw + self.SLANT * span) - self.SLANT * span + self.SLANT * (y + self.length)
        ax, ay = area.points(x, y)
        bx, by = area.points(x + self.SLANT * self.length, y + self.length)
        pts = np.round(np.stack([ax, ay, bx, by], axis=1) * 16).astype(np.int32).reshape(-1, 2, 2)
        cover = np.zeros(area.shape, np.uint8)
        cv2.polylines(cover, list(pts), False, 255, 1, cv2.LINE_AA, shift=4)
        _blend_cover(out8, area, cover, self.colour, self.particle_opacity(e))


class Snow(EffectState):
    """Soft round flakes drifting down with a sideways wobble."""

    def setup(self) -> None:
        d, n, rng = self.defaults, self.count(), self.rng
        self.x0 = rng.random(n)
        self.y0 = rng.random(n)
        self.speed = d.speed * self.speed_factor() * self.short * rng.uniform(0.7, 1.3, n)
        self.radius = np.maximum(d.size * self.k * rng.uniform(0.6, 1.4, n) / 2, 0.75)
        self.wobble = 8.0 * self.k
        self.freq = rng.uniform(0.3, 0.6, n)
        self.phase = rng.uniform(0, 2 * math.pi, n)

    def draw(self, out8, area, t, e):
        bw, bh = self.local_size
        span = bh + 4 * self.radius.max()
        y = (self.y0 * span + self.speed * t) % span - 2 * self.radius
        x = self.x0 * bw + self.wobble * np.sin(2 * math.pi * self.freq * t + self.phase)
        cx, cy = area.points(x, y)
        cover = np.zeros(area.shape, np.uint8)
        for px, py, r in zip(cx, cy, self.radius, strict=True):
            centre = (round(px * 16), round(py * 16))
            cv2.circle(cover, centre, max(round(r * 16), 1), 255, -1, cv2.LINE_AA, shift=4)
        _blend_cover(out8, area, cover, self.colour, self.particle_opacity(e))


class FallingLeaves(EffectState):
    """Small tinted ellipses tumbling down (±40°/s) with a sideways sway."""

    def setup(self) -> None:
        d, n, rng = self.defaults, self.count(), self.rng
        size = max(d.size * self.k, 2.0)
        self.x0 = rng.random(n)
        self.y0 = rng.random(n)
        self.speed = d.speed * self.speed_factor() * self.short * rng.uniform(0.7, 1.3, n)
        self.major = size / 2 * rng.uniform(0.7, 1.3, n)
        self.minor = np.maximum(self.major * rng.uniform(0.35, 0.55, n), 0.75)
        self.angle0 = rng.uniform(0, 360, n)
        self.spin = rng.uniform(-40, 40, n)
        self.sway = 20.0 * self.k
        self.freq = rng.uniform(0.2, 0.4, n)
        self.phase = rng.uniform(0, 2 * math.pi, n)
        self.shade = rng.integers(170, 256, n)

    def draw(self, out8, area, t, e):
        bw, bh = self.local_size
        span = bh + 2 * self.major.max()
        y = (self.y0 * span + self.speed * t) % span - self.major
        x = self.x0 * bw + self.sway * np.sin(2 * math.pi * self.freq * t + self.phase)
        cx, cy = area.points(x, y)
        cover = np.zeros(area.shape, np.uint8)
        angles = self.angle0 + self.spin * t
        for i in range(cx.size):
            cv2.ellipse(
                cover,
                (round(cx[i] * 16), round(cy[i] * 16)),
                (max(round(self.major[i] * 16), 1), max(round(self.minor[i] * 16), 1)),
                float(angles[i]),
                0,
                360,
                int(self.shade[i]),
                -1,
                cv2.LINE_AA,
                shift=4,
            )
        _blend_cover(out8, area, cover, self.colour, self.particle_opacity(e))


class Fireflies(EffectState):
    """Additive Gaussian dots wandering slowly, each pulsing (squared sine, 0.25–0.6 Hz)."""

    def setup(self) -> None:
        d, n, rng = self.defaults, self.count(), self.rng
        bw, bh = self.local_size
        self.sprite = _gaussian_sprite(max(d.size * self.k / 2, 0.75))
        wander = d.speed * 0.1 * self.short
        self.cx = rng.uniform(0, bw, n)
        self.cy = rng.uniform(0, bh, n)
        self.rx = rng.uniform(0.3, 1.0, n) * wander
        self.ry = rng.uniform(0.3, 1.0, n) * wander * 0.6
        self.fx = rng.uniform(0.03, 0.09, n)
        self.fy = rng.uniform(0.04, 0.11, n)
        self.phase = rng.uniform(0, 2 * math.pi, n)
        self.pulse = rng.uniform(0.25, 0.6, n)

    def draw(self, out8, area, t, e):
        bw, bh = self.local_size
        xs = np.clip(self.cx + self.rx * np.sin(2 * np.pi * self.fx * t + self.phase), 0, bw - 1)
        ys = np.clip(
            self.cy + self.ry * np.sin(2 * np.pi * self.fy * t + 1.7 * self.phase), 0, bh - 1
        )
        glow = (0.5 + 0.5 * np.sin(2 * np.pi * self.pulse * t + self.phase)) ** 2
        glow *= self.defaults.strength * e
        px, py = area.points(xs, ys)
        for x, y, b in zip(px, py, glow, strict=True):
            if b > 1e-4:
                _blend_sprite(out8, area, x, y, self.sprite, float(b), self.colour, True)


class Smoke(EffectState):
    """Soft puffs rising, growing ×1 → ×2 and fading in and out over their life."""

    def setup(self) -> None:
        d, rng = self.defaults, self.rng
        n = max(1, d.count)
        bw, bh = self.local_size
        self.x = rng.uniform(0.2, 0.8, n) * bw
        self.y = rng.uniform(0.85, 1.0, n) * bh
        self.life = rng.uniform(4.0, 8.0, n)
        self.phase = rng.random(n)
        self.drift = rng.uniform(4.0, 12.0, n) * self.k
        self.freq = rng.uniform(0.1, 0.25, n)
        self.rate = d.speed * self.speed_factor() * self.short
        self.radius = max(d.size * self.k / 2, 1.0)

    def draw(self, out8, area, t, e):
        alpha = self.defaults.strength * self.intensity * e
        for i in range(self.x.size):
            age = (t / self.life[i] + self.phase[i]) % 1.0
            amount = alpha * math.sin(math.pi * age)
            if amount <= 1e-4:
                continue
            lx = self.x[i] + self.drift[i] * math.sin(2 * math.pi * self.freq[i] * t + i)
            ly = self.y[i] - self.rate * self.life[i] * age
            (px,), (py,) = area.points(np.array([lx]), np.array([ly]))
            sprite = _gaussian_sprite(self.radius * (1 + age) / 2)
            _blend_sprite(out8, area, px, py, sprite, amount, self.colour, False)


class Mist(EffectState):
    """A low-frequency noise band drifting sideways (speed × short side px/s), denser lower down,
    fading out at the region's edges. The noise is built once, wide enough for the section."""

    def setup(self) -> None:
        d = self.defaults
        bw, bh = self.local_size
        self.rate = d.speed * self.short
        width = bw + math.ceil(self.rate * self.duration_s) + 2
        cell = max(d.size * self.k, 4.0)
        field = self._octave(width, bh, cell) + 0.5 * self._octave(width, bh, cell / 2)
        lo, hi = float(field.min()), float(field.max())
        field = _smooth((field - lo) / (hi - lo or 1.0))
        self.field = np.ascontiguousarray(field, dtype=np.float32)
        profile = 0.6 + 0.4 * np.arange(bh, dtype=np.float32) / max(bh - 1, 1)
        self.shape = (profile[:, None] * _taper(bw, bh, EDGE_TAPER_PX * self.k)).astype(np.float32)

    def _octave(self, width: int, height: int, cell: float) -> np.ndarray:
        gw, gh = math.ceil(width / cell) + 2, math.ceil(height / cell) + 2
        grid = self.rng.random((gh, gw)).astype(np.float32)
        big = cv2.resize(grid, (round(gw * cell), round(gh * cell)), interpolation=cv2.INTER_CUBIC)
        return big[:height, :width]

    def draw(self, out8, area, t, e):
        bw, bh = self.local_size
        shift = np.array([[1, 0, self.rate * t], [0, 1, 0]], np.float64)
        noise = cv2.warpAffine(
            self.field, shift, (bw, bh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
        )
        alpha = noise * self.shape * (self.defaults.strength * self.intensity * e)
        _blend_dense(out8, area, area.place(alpha), self.colour, additive=False)


class Glow(EffectState):
    """An additive soft ellipse filling the region, pulsing between 0.85 and 1.0 at speed Hz."""

    def setup(self) -> None:
        bw, bh = self.local_size
        gx = (np.arange(bw, dtype=np.float32) + 0.5 - bw / 2) / (bw / 2)
        gy = (np.arange(bh, dtype=np.float32) + 0.5 - bh / 2) / (bh / 2)
        rho = np.sqrt(gx[None, :] ** 2 + gy[:, None] ** 2)
        soft = min(max(self.defaults.size * self.k / (0.5 * min(bw, bh)), 0.05), 1.0)
        self.field = _smooth((1.0 - rho) / soft).astype(np.float32)
        self.phase = float(self.rng.uniform(0, 2 * math.pi))

    def draw(self, out8, area, t, e):
        pulse = 0.925 + 0.075 * math.sin(2 * math.pi * self.defaults.speed * t + self.phase)
        alpha = self.field * (self.defaults.strength * self.intensity * pulse * e)
        _blend_dense(out8, area, area.place(alpha), self.colour, additive=True)


class WaterRipple(EffectState):
    """A sine displacement of the composite (strength × intensity px at 1024), tapering to 0 at a
    box's edges, or following the layer's alpha in a layer region."""

    distorts = True

    def setup(self) -> None:
        d = self.defaults
        self.amp = d.strength * self.intensity * self.k
        self.wavelength = max(d.size * self.k, 2.0)
        self.phase = float(self.rng.uniform(0, 2 * math.pi))
        self._grids: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    def _grid(self, h: int, w: int):
        if (h, w) not in self._grids:
            gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            self._grids[(h, w)] = (gx, gy, _taper(w, h, EDGE_TAPER_PX * self.k))
        return self._grids[(h, w)]

    def distort(self, pic, area, t, e):
        a = self.amp * e
        if a <= 1e-3:
            return
        h, w = area.shape
        gx, gy, taper = self._grid(h, w)
        weight = taper if area.mask is None else area.mask
        k = 2 * math.pi / self.wavelength
        ph = 2 * math.pi * self.defaults.speed * t + self.phase
        dx = a * weight * np.sin(k * gy + 0.07 * k * gx - ph)
        dy = 0.5 * a * weight * np.sin(0.6 * k * gx + 0.8 * k * gy - 1.3 * ph)
        x0, y0, x1, y1 = area.rect
        region = pic[y0:y1, x0:x1]
        region[...] = cv2.remap(
            region, gx + dx, gy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT
        )


EFFECT_TYPES: dict[str, type[EffectState]] = {
    "rain": Rain,
    "snow": Snow,
    "fireflies": Fireflies,
    "falling_leaves": FallingLeaves,
    "water_ripple": WaterRipple,
    "smoke": Smoke,
    "mist": Mist,
    "glow": Glow,
}
assert set(EFFECT_TYPES) == set(EFFECTS), "one implementation per effect in the vocabulary"


# --- loading a page -----------------------------------------------------------------------


@dataclass
class PageAnimation:
    """Everything needed to draw one page section, loaded once."""

    original: np.ndarray  # H×W×3 float32 0..1
    original8: np.ndarray  # H×W×3 uint8 (the picture as shown when still)
    base: np.ndarray  # background with static layers baked in, H×W×3 float32
    layers: list[LayerState]  # ascending z, plan order for ties
    effects: list[EffectState]  # plan order (intensity 0 dropped)
    rects: list[Rect]  # dirty rects recomputed every frame
    settings: AnimationSettings
    scale: float
    fps: int
    frames: int
    static_rects: dict[str, Rect] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.original8.shape[1]

    @property
    def height(self) -> int:
        return self.original8.shape[0]

    @property
    def short(self) -> int:
        return min(self.width, self.height)

    @property
    def parallax(self) -> tuple[float, float]:
        return self.settings.parallax_max_shift, self.settings.parallax_period_s

    def local_matrix(self, ls: LayerState, t: float, e: float) -> np.ndarray:
        m = layer_matrix(ls.layer, t, self.short, e, self.scale, self.parallax)
        return _with_offset(m, ls.offset)


def _read_rgb(path: Path) -> np.ndarray:
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), np.uint8).copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise AnimationError(f"{path}: can't read the picture ({exc})") from None


def _read_layer(path: Path, size: object) -> np.ndarray:
    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGBA"), np.float32) / 255.0
    except (OSError, UnidentifiedImageError) as exc:
        raise AnimationError(f"{path}: can't read the layer ({exc})") from None
    if size is not None and list(size) != [arr.shape[1], arr.shape[0]]:
        raise AnimationError(f"{path}: size {arr.shape[1]}x{arr.shape[0]} ≠ manifest {size}")
    arr[..., :3] *= arr[..., 3:4]
    return np.ascontiguousarray(arr)


def _read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["layers"]  # noqa: B018 - shape check
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AnimationError(f"{path}: can't read the cut manifest ({exc})") from None
    return manifest


def _envelope(anim: PageAnimation, ls: LayerState) -> Rect | None:
    w, h = ls.size
    boxes = []
    for f in range(anim.frames):
        t = f / anim.fps
        m = anim.local_matrix(ls, t, ease(t, anim.settings.ease_in_s))
        boxes.append(_bbox(m, w, h, anim.width, anim.height))
    return _union(boxes)


def _outside_equal(a: np.ndarray, b: np.ndarray, rects: Iterable[Rect]) -> bool:
    keep = np.ones(a.shape[:2], bool)
    for x0, y0, x1, y1 in rects:
        keep[y0:y1, x0:x1] = False
    return bool(np.array_equal(a[keep], b[keep]))


def load_page_animation(
    plan: MotionPlan,
    layers_folder: Path,
    picture: Path,
    settings: AnimationSettings,
    scale: float,
    slug: str,
    page_number: int,
    fps: int,
    frames: int,
) -> PageAnimation:
    """Load one page's picture, cut and effects for a section of `frames` frames at `fps`.

    Geometry (z, depth, pivot, motions, effects) comes from the plan; files, offsets and sizes from
    the cut manifest. Raises AnimationError when the files can't be used."""
    if frames < 1 or fps < 1:
        raise ValueError(f"need frames ≥ 1 and fps ≥ 1, got {frames}, {fps}")
    layers_folder = Path(layers_folder)
    manifest = _read_manifest(layers_folder)
    original8 = _read_rgb(Path(picture))
    background8 = _read_rgb(layers_folder / manifest.get("background", "background.png"))
    if background8.shape != original8.shape:
        raise AnimationError(
            f"{layers_folder}: background {background8.shape[1]}x{background8.shape[0]} ≠ "
            f"picture {original8.shape[1]}x{original8.shape[0]}"
        )
    height, width = original8.shape[:2]
    by_name = {entry.get("name"): entry for entry in manifest["layers"]}
    states: list[LayerState] = []
    for _index, lay in sorted(enumerate(plan.layers), key=lambda p: (p[1].z, p[0])):
        entry = by_name.get(lay.name)
        if entry is None:
            raise AnimationError(f"{layers_folder / MANIFEST}: no layer {lay.name!r}")
        if entry.get("empty"):
            continue
        rgba = _read_layer(layers_folder / str(entry.get("file")), entry.get("size"))
        offset = entry.get("offset") or (0, 0)
        states.append(
            LayerState(lay, rgba, (int(offset[0]), int(offset[1])), bool(lay.motions or lay.depth))
        )

    anim = PageAnimation(
        original=original8.astype(np.float32) / 255.0,
        original8=original8,
        base=background8.astype(np.float32) / 255.0,
        layers=states,
        effects=[],
        rects=[],
        settings=settings,
        scale=scale,
        fps=fps,
        frames=frames,
    )
    for ls in states:
        if not ls.moving:
            got = _warp(ls, _with_offset(np.eye(2, 3), ls.offset), width, height)
            if got is not None:
                _over(anim.base, *got)
                anim.static_rects[ls.layer.name] = got[0]

    envelopes = {ls.layer.name: _envelope(anim, ls) for ls in states if ls.moving}
    rects = [r for r in envelopes.values() if r is not None]
    k = anim.short / REFERENCE_SIDE
    for index, spec in enumerate(plan.effects):
        intensity = min(1.0, spec.intensity * scale)
        if intensity <= 0:
            continue
        defaults = settings.effects.get(spec.type)
        if defaults is None:
            raise AnimationError(f"no defaults for effect {spec.type!r} in animation.effects")
        region = spec.region
        layer_name = region.layer if region is not None and region.layer else None
        box: Rect | None = None
        if layer_name is not None:
            ls = next((s for s in states if s.layer.name == layer_name), None)
            if ls is None:
                continue  # an empty (or absent) layer has no area to play in
            local_size = ls.size
            area_rect = envelopes.get(layer_name) or anim.static_rects.get(layer_name)
        else:
            has_box = region is not None and region.box is not None
            x0, y0, x1, y1 = region.box if has_box else (0, 0, width, height)
            box = (max(x0, 0), max(y0, 0), min(x1, width), min(y1, height))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            local_size = (box[2] - box[0], box[3] - box[1])
            area_rect = box
        cls = EFFECT_TYPES[spec.type]
        effect = cls(
            spec=spec,
            defaults=defaults,
            intensity=intensity,
            colour=_rgb255(spec.colour or defaults.colour),
            k=k,
            short=anim.short,
            local_size=local_size,
            region_layer=layer_name,
            region_box=box,
            rng=np.random.default_rng(seed_for(slug, page_number, index, spec.type)),
            duration_s=frames / fps,
        )
        if effect.distorts and area_rect is not None:
            rects.append(area_rect)
        anim.effects.append(effect)

    # Outside the dirty rects the persistent picture stays the original. That is right only when
    # the cut background equals the original there (static layers are at rest, so their boxes
    # are allowed to differ by the cut's feather); otherwise recompute the whole picture.
    if not _outside_equal(background8, original8, [*rects, *anim.static_rects.values()]):
        rects = [(0, 0, width, height)]
    anim.rects = rects
    return anim


# --- eligibility and cache keys (FR-11, FR-13) ------------------------------------------------


@dataclass(frozen=True)
class PageEligibility:
    """Whether a story page renders animated and, when it does, what its key is made from."""

    animated: bool
    reason: str = ""  # why the page renders still, with the command that fixes it
    plan: MotionPlan | None = None
    scale: float = 1.0
    plan_sha: str = ""
    manifest_sha: str = ""
    files_sha: tuple[tuple[str, str], ...] = ()  # (file name, sha256) of each listed cut file


def _is_real(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def page_eligibility(
    folder: BookFolder,
    page: dict,
    script_page: dict,
    settings: AnimationSettings,
    root: Path,
) -> PageEligibility:
    """Check a page in order: animation off, `animation: false` in the script, the plan (none,
    unreadable config, stale, invalid, no picture), then the cut. Reads files; never raises for
    a page that just can't animate (it is still, with the reason)."""
    if not settings.enabled:
        return PageEligibility(False, "animation off")
    override = script_page.get("animation")
    if override is False:
        return PageEligibility(False, "animation false in the script")
    if "animation" in script_page and not _is_real(override):
        return PageEligibility(
            False,
            f"invalid animation value in the script – {override!r} "
            "(use false or a number; the render refuses it)",
        )
    number = int(page["page_number"])
    plan_cmd = f"python -m video_pipeline.motion_plan --book {folder.slug} --page {number}"
    path = plan_path(folder, number)
    if not path.exists():
        return PageEligibility(False, f"no plan – run {plan_cmd}")
    try:
        config = read_config(root)
        limits = load_limits(Path(root) / "config")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        where = Path(root) / "config" / LIMITS_FILE
        return PageEligibility(False, f"animation config unreadable – {where}: {exc}")
    st = plan_state(folder, page, limits)
    if st.state == "none":  # removed since the check above
        return PageEligibility(False, f"no plan – run {plan_cmd}")
    if st.state == "stale":
        return PageEligibility(False, f"stale plan ({st.detail}) – re-run {plan_cmd}")
    if st.state == "invalid":
        return PageEligibility(False, f"invalid plan ({st.detail}) – re-run {plan_cmd}")
    if st.state == "no_picture" or st.plan is None:
        return PageEligibility(False, f"no picture ({st.detail})")
    try:
        cut, why = cut_state(folder, st.plan, config)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        where = Path(root) / "config" / LIMITS_FILE
        return PageEligibility(False, f"animation config unreadable – {where}: no usable {exc}")
    if cut != "cut":
        return PageEligibility(False, why)
    out = layers_dir(folder, number)
    try:
        plan_sha = sha256_file(path)
        manifest_sha = sha256_file(out / MANIFEST)
        manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
        files_sha = tuple((name, sha256_file(out / name)) for name in manifest_files(manifest))
    except (OSError, ValueError) as exc:  # changed between the checks and the hash
        return PageEligibility(False, f"files changed while checking ({exc})")
    scale = settings.intensity * (float(override) if override is not None else 1.0)
    return PageEligibility(True, "", st.plan, scale, plan_sha, manifest_sha, files_sha)


def animated_key(still_key: str, el: PageEligibility, settings: AnimationSettings) -> str:
    """An animated section's key: its still key plus the plan and cut identities (the manifest
    and the bytes of every layer and background file it lists), the animation
    settings, the page's scale and the animator version (Global constraints)."""
    return _hash(
        {
            "still": still_key,
            "plan": el.plan_sha,
            "manifest": el.manifest_sha,
            "files": [list(f) for f in el.files_sha],
            "settings": asdict(settings),
            "scale": el.scale,
            "animation_version": ANIMATION_VERSION,
        }
    )


# --- rendering ----------------------------------------------------------------------------


class PageRenderer:
    """Draws the frames of one page section. Holds the persistent uint8 picture and work buffers,
    so it is stateful, but every frame recomputes all dirty rects: frame n does not depend on
    which frames were drawn before. `frame` returns a buffer that the next call overwrites."""

    def __init__(self, anim: PageAnimation) -> None:
        self.anim = anim
        self.pic = anim.base.copy()
        self.pic8 = anim.original8.copy()
        self.out8 = np.empty_like(self.pic8)
        self.moving = [ls for ls in anim.layers if ls.moving]
        self.distorters = [fx for fx in anim.effects if fx.distorts]
        self.overlays = [fx for fx in anim.effects if not fx.distorts]
        self.region_layers = {fx.region_layer for fx in anim.effects if fx.region_layer}
        self.static_areas: dict[str, tuple[np.ndarray, Rect, np.ndarray]] = {}
        for ls in anim.layers:
            if not ls.moving and ls.layer.name in self.region_layers:
                m = _with_offset(np.eye(2, 3), ls.offset)
                got = _warp(ls, m, anim.width, anim.height)
                if got is not None:
                    self.static_areas[ls.layer.name] = (m, got[0], got[1][..., 3].copy())

    def _area(self, fx: EffectState, warps: dict) -> Area | None:
        if fx.region_layer is None:
            box = fx.region_box
            assert box is not None
            to_picture = np.array([[1, 0, box[0]], [0, 1, box[1]]], np.float64)
            return Area(box, to_picture, fx.local_size)
        got = warps.get(fx.region_layer) or self.static_areas.get(fx.region_layer)
        if got is None:
            return None
        m, rect, alpha = got
        return Area(rect, m, fx.local_size, np.ascontiguousarray(alpha))

    def frame(self, n: int) -> np.ndarray:
        anim = self.anim
        if not 0 <= n < anim.frames:
            raise ValueError(f"frame {n} outside the section's {anim.frames} frames")
        t = n / anim.fps
        e = ease(t, anim.settings.ease_in_s)
        if e <= 0:
            np.copyto(self.out8, anim.original8)  # at rest: exactly the still picture
            return self.out8
        pic = self.pic
        for x0, y0, x1, y1 in anim.rects:
            pic[y0:y1, x0:x1] = anim.base[y0:y1, x0:x1]
        warps: dict[str, tuple[np.ndarray, Rect, np.ndarray]] = {}
        for ls in self.moving:
            m = anim.local_matrix(ls, t, e)
            got = _warp(ls, m, anim.width, anim.height)
            if got is None:
                continue
            _over(pic, *got)
            if ls.layer.name in self.region_layers:
                warps[ls.layer.name] = (m, got[0], got[1][..., 3])
        for fx in self.distorters:
            area = self._area(fx, warps)
            if area is not None:
                fx.distort(pic, area, t, e)
        for x0, y0, x1, y1 in anim.rects:
            src = pic[y0:y1, x0:x1]
            if e < 1.0:
                src = cv2.addWeighted(anim.original[y0:y1, x0:x1], 1.0 - e, src, e, 0.0)
            self.pic8[y0:y1, x0:x1] = cv2.convertScaleAbs(src, alpha=255.0)
        np.copyto(self.out8, self.pic8)
        for fx in self.overlays:
            area = self._area(fx, warps)
            if area is not None:
                fx.draw(self.out8, area, t, e)
        return self.out8


def picture_frames(anim: PageAnimation, frames: int | None = None) -> Iterator[np.ndarray]:
    """The picture-area frames 0..frames-1 (uint8 H×W×3). Each yielded array is reused by the
    next one: copy it to keep it."""
    renderer = PageRenderer(anim)
    for n in range(anim.frames if frames is None else frames):
        yield renderer.frame(n)


def rest_frames(anim: PageAnimation) -> int:
    """How many leading frames are at rest (ease 0): each is exactly the original picture."""
    n = 0
    while n < anim.frames and ease(n / anim.fps, anim.settings.ease_in_s) <= 0:
        n += 1
    return n


def render_frame(anim: PageAnimation, n: int) -> np.ndarray:
    """One picture-area frame built from scratch (for tests and previews)."""
    return PageRenderer(anim).frame(n).copy()


# --- camera and canvas --------------------------------------------------------------------


def zoompan_crop(
    cam: Camera, frame: int, width: int, height: int, upscale: int
) -> tuple[float, float, float, float]:
    """The crop (x, y, w, h) in canvas pixels that zoompan takes at a frame: it works at the
    ×upscale size with int w = iw/zoom and an int x clipped to [0, iw−w] and rounded down to even
    (yuv420p chroma alignment)."""
    z, cx, cy = camera_at(cam, frame)
    z = min(max(z, 1.0), ZOOM_MAX)
    iw, ih = upscale * width, upscale * height
    cw, ch = int(iw * (1.0 / z)), int(ih * (1.0 / z))
    x = int(min(max(cx * iw - iw / z / 2, 0), max(iw - cw, 0))) & ~1
    y = int(min(max(cy * ih - ih / z / 2, 0), max(ih - ch, 0))) & ~1
    return x / upscale, y / upscale, cw / upscale, ch / upscale


def camera_matrix(cam: Camera, frame: int, width: int, height: int, upscale: int) -> np.ndarray:
    """Inverse map for `cv2.warpAffine(..., WARP_INVERSE_MAP)`: output pixel → canvas pixel,
    pixel centres aligned (src = x + (u + 0.5)·cw/W − 0.5)."""
    x, y, cw, ch = zoompan_crop(cam, frame, width, height, upscale)
    sx, sy = cw / width, ch / height
    return np.array([[sx, 0, x + 0.5 * sx - 0.5], [0, sy, y + 0.5 * sy - 0.5]], np.float64)


_IDENTITY = np.eye(2, 3)


def canvas_frames(
    pictures: Iterable[np.ndarray],
    backdrop: np.ndarray,
    size: tuple[int, int],
    offset: tuple[int, int],
    cam: Camera,
    frames: int,
    width: int,
    height: int,
    upscale: int,
    rest: np.ndarray | None = None,
    rest_count: int = 0,
) -> Iterator[bytes]:
    """rgb24 canvas frames: each picture resized (LANCZOS4) to `size` and pasted at `offset` into
    the backdrop (H×W×3 uint8), then the section's camera applied with a bilinear warp.

    The first `rest_count` frames paste `rest` instead (the picture already at `size`, resized
    as the still canvas is), so a section at rest starts exactly like the still one."""
    if rest_count and (rest is None or rest.shape[:2] != size[::-1]):
        raise ValueError(f"the rest picture must be {size[0]}×{size[1]}")
    if backdrop.shape != (height, width, 3):
        raise ValueError(f"backdrop is {backdrop.shape}, expected {(height, width, 3)}")
    buf = np.ascontiguousarray(backdrop, dtype=np.uint8).copy()
    fw, fh = size
    px, py = offset
    source = iter(pictures)
    for n in range(frames):
        pic = next(source, None)
        if pic is None:
            raise ValueError(f"picture frames ended after {n} of {frames} frames")
        if n < rest_count:
            pic = rest
        elif pic.shape[:2] != (fh, fw):
            pic = cv2.resize(pic, (fw, fh), interpolation=cv2.INTER_LANCZOS4)
        buf[py : py + fh, px : px + fw] = pic
        m = camera_matrix(cam, n, width, height, upscale)
        if np.array_equal(m, _IDENTITY):
            yield buf.tobytes()
        else:
            yield cv2.warpAffine(
                buf,
                m,
                (width, height),
                flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE,
            ).tobytes()
