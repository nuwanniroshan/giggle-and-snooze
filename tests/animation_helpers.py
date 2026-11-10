"""Small pictures, motion plans and fake cuts for the studio's animation tests (spec 009, Phase 5).

The fakes stand in for SAM and LaMa: each layer's mask is its box, the hole is filled with the
mean colour. No model is loaded."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from video_pipeline.cut_layers import cut_book
from video_pipeline.library import BookFolder
from video_pipeline.motion_plan import MotionPlan, picture_identity, plan_path, save_plan

W, H = 64, 48
SQUARE = (20, 12, 36, 28)


class FakeSegmenter:
    def masks(self, image: np.ndarray, layers) -> list[np.ndarray]:
        out = []
        for layer in layers:
            mask = np.zeros(image.shape[:2], bool)
            if layer.name != "nothing":
                x0, y0, x1, y1 = layer.box
                mask[y0:y1, x0:x1] = True
            out.append(mask)
        return out


class FakeInpainter:
    def fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        out = image.copy()
        out[mask] = image[~mask].mean(axis=0).round().astype(np.uint8)
        return out


def fake_models(root: Path, cfg: dict):
    return FakeSegmenter(), FakeInpainter()


def write_picture(folder: BookFolder, n: int = 1, colour=(30, 60, 200)) -> Path:
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[:] = colour
    x0, y0, x1, y1 = SQUARE
    rgb[y0:y1, x0:x1] = (220, 20, 20)
    path = folder.illustrations / f"page_{n:02d}.png"
    Image.fromarray(rgb).save(path)
    return path


def layer(name: str = "square", box=SQUARE, z: int = 1, **over) -> dict:
    data = {
        "name": name,
        "description": f"the {name}",
        "box": list(box),
        "include_points": [[28, 20]],
        "exclude_points": [[5, 5]],
        "z": z,
        "depth": 0.5,
        "pivot": [28, 28],
        "motions": [{"type": "sway", "amplitude": 2.0, "period_s": 6.0, "phase": 0.25}],
    }
    data.update(over)
    return data


def write_plan(
    folder: BookFolder, n: int = 1, layers: list[dict] | None = None, effects=None
) -> MotionPlan:
    picture = folder.illustrations / f"page_{n:02d}.png"
    plan = MotionPlan.model_validate(
        {
            "page_number": n,
            "picture": picture_identity(picture).model_dump(),
            "layers": layers or [layer()],
            "effects": effects or [],
        }
    )
    save_plan(plan_path(folder, n), plan)
    return plan


def cut(folder: BookFolder, pages: list[int] | None = None) -> None:
    cut_book(folder, root=folder.root, pages=pages, load_models=fake_models, log=lambda _: None)
