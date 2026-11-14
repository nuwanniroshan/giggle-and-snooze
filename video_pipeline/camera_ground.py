"""Spec 025: grounded Q&A camera targets and caption bands, from picture masks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BookFolder
from video_pipeline.timeline import contain_size, page_section_name

CONFIG_FILE = "camera.json"
BANDS = ("top", "bottom")


@dataclass(frozen=True)
class CameraSettings:
    zoom_min: float
    zoom_max: float
    zoom_margin: float
    band_fraction: float
    min_mask_share: float
    max_subject_share: float
    attempts: int = 3
    time_limit_s: float = 600.0


def load_settings(config_dir: Path) -> CameraSettings:
    data = json.loads((Path(config_dir) / CONFIG_FILE).read_text(encoding="utf-8"))
    return CameraSettings(**data)


def camera_path(folder: BookFolder, page_number: int) -> Path:
    return folder.folder / "camera" / f"page_{page_number:02d}.json"


def target_for_box(
    box: tuple[int, int, int, int],
    picture_size: tuple[int, int],
    canvas: tuple[int, int],
    settings: CameraSettings,
) -> dict:
    """Centre of the subject box (picture fractions) and the largest zoom, within the limits,
    that keeps the box plus its margin in the crop. Zoom is 1 when the box is too large."""
    pw, ph = picture_size
    x0, y0, x1, y1 = box
    front_w, front_h = contain_size(picture_size, canvas)
    box_w = (x1 - x0) / pw * front_w * (1 + settings.zoom_margin)
    box_h = (y1 - y0) / ph * front_h * (1 + settings.zoom_margin)
    fit = min(canvas[0] / max(box_w, 1e-6), canvas[1] / max(box_h, 1e-6))
    zoom = round(min(settings.zoom_max, max(1.0, min(fit, settings.zoom_max))), 3)
    zoom = max(settings.zoom_min, zoom)
    return {
        "x": round((x0 + x1) / 2 / pw, 4),
        "y": round((y0 + y1) / 2 / ph, 4),
        "zoom": zoom,
    }


def band_for_rows(
    row_counts: np.ndarray,
    picture_size: tuple[int, int],
    canvas: tuple[int, int],
    settings: CameraSettings,
) -> str:
    """`top` or `bottom`: the caption band whose canvas rows hold fewer mask pixels. `row_counts`
    is mask pixels per picture row. Ties, no masks, or masks under `min_mask_share` -> bottom."""
    pw, ph = picture_size
    if len(row_counts) != ph or row_counts.sum() < settings.min_mask_share * pw * ph:
        return "bottom"
    _, front_h = contain_size(picture_size, canvas)
    offset = (canvas[1] - front_h) / 2
    band_h = settings.band_fraction * canvas[1]
    canvas_y = offset + (np.arange(ph) + 0.5) / ph * front_h
    top = int(row_counts[canvas_y < band_h].sum())
    bottom = int(row_counts[canvas_y >= canvas[1] - band_h].sum())
    return "top" if top < bottom else "bottom"


def problems(data: object) -> list[str]:
    """Every problem with a camera file's content (FR-3, AC-4.4); empty when usable."""
    if not isinstance(data, dict):
        return ["camera file is not an object"]
    out: list[str] = []
    pic = data.get("picture")
    if not isinstance(pic, dict) or not isinstance(pic.get("sha256"), str):
        out.append("picture.sha256 is missing")
    if data.get("band") not in BANDS:
        out.append("band must be top or bottom")
    target = data.get("target")
    if target is not None:
        ok = isinstance(target, dict) and all(
            isinstance(target.get(k), (int, float)) and not isinstance(target.get(k), bool)
            for k in ("x", "y", "zoom")
        )
        if not ok:
            out.append("target needs numeric x, y and zoom")
        else:
            if not (0 <= target["x"] <= 1 and 0 <= target["y"] <= 1):
                out.append("target x and y must be in [0, 1]")
            if not (1 <= target["zoom"] <= 2):
                out.append("target zoom must be in [1, 2]")
    return out


def load_current(folder: BookFolder, page_number: int, picture_sha: str) -> tuple[dict | None, str]:
    """(file, "") when present, valid and current; else (None, reason with the fix command)."""
    path = camera_path(folder, page_number)
    fix = f"run python -m video_pipeline.ground_camera --book {folder.slug} --page {page_number}"
    if not path.is_file():
        return None, f"no camera file - {fix}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable camera file ({exc}) - {fix} --force"
    if bad := problems(data):
        return None, f"invalid camera file ({'; '.join(bad)}) - {fix} --force"
    if data["picture"]["sha256"] != picture_sha:
        return None, f"stale camera file (picture changed) - {fix}"
    return data, ""


def load_groundings(
    folder: BookFolder,
    script: dict,
    by_section: dict[str, str],
    picture_sha: dict[str, str],
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """{section name: current camera file} for story pages, plus ("page NN", note) log lines for
    pages that fall back to the guessed target (AC-3.2). `by_section` maps a section name to its
    logical picture id and `picture_sha` is keyed by that id. A book with no `camera` folder
    renders as before and logs nothing."""
    found: dict[str, dict] = {}
    notes: list[tuple[str, str]] = []
    folder_exists = (folder.folder / "camera").is_dir()
    for page in script["pages"]:
        number = int(page["page_number"])
        name = page_section_name(number)
        sha = picture_sha.get(by_section.get(name, ""))
        if sha is None:
            continue
        data, reason = load_current(folder, number, sha)
        if data is None:
            if folder_exists:
                notes.append(
                    (
                        f"page {number:02d}",
                        f"camera: guessed target and bottom captions - {reason}",
                    )
                )
            continue
        found[name] = data
        if data.get("status", "ok") != "ok":  # failed grounding: page_section ignores its target
            notes.append(
                (
                    f"page {number:02d}",
                    f"camera: guessed target - grounding failed: {data.get('reason', '')}",
                )
            )
    return found, notes


MAX_PHRASE = 80
MAX_SAVES = 3


def subject_problems(
    subject: object, layer_names: list[str], picture_size: tuple[int, int]
) -> list[str]:
    """Every reason a named subject is refused (empty list: accepted)."""
    if not isinstance(subject, dict):
        return ["subject must be an object with `layer`, or `phrase` and `box`"]
    layer, phrase, box = subject.get("layer"), subject.get("phrase"), subject.get("box")
    if layer is not None:
        if phrase is not None or box is not None:
            return ["give either `layer`, or `phrase` and `box`, not both"]
        if layer in layer_names:
            return []
        return [f"layer {layer!r} is not one of: {', '.join(layer_names) or '(none)'}"]
    errors: list[str] = []
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > MAX_PHRASE:
        errors.append(f"phrase must be 1-{MAX_PHRASE} characters")
    w, h = picture_size
    ok = (
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
        and 0 <= box[0] < box[2] <= w
        and 0 <= box[1] < box[3] <= h
    )
    if not ok:
        errors.append(
            f"box must be [x0, y0, x1, y1] integers inside the {w}x{h} picture, x0<x1, y0<y1"
        )
    return errors


@dataclass
class SubjectTools:
    """The validating save tool Codex calls to name a page's Q&A subject."""

    layer_names: list[str]
    picture_size: tuple[int, int]
    out: Path
    saves: int = 0

    def save_camera_subject(self, subject: object) -> dict:
        self.saves += 1
        if self.saves > MAX_SAVES:
            return {"saved": False, "errors": [f"at most {MAX_SAVES} saves"]}
        errors = subject_problems(subject, self.layer_names, self.picture_size)
        if errors:
            return {"saved": False, "errors": errors}
        write_atomic(self.out, subject)  # type: ignore[arg-type]
        return {"saved": True, "errors": []}


def subject_prompt(
    page: dict,
    layer_names_with_descriptions: list[tuple[str, str]],
    picture_size: tuple[int, int],
) -> str:
    """The Codex request for one page's Q&A subject (pure)."""
    qa = [
        f"- {line.get('speaker', '')}: {line['text']}"
        for line in page.get("lines", [])
        if line.get("role") != "narration"
    ]
    layers = [f"- {name}: {desc}" for name, desc in layer_names_with_descriptions]
    w, h = picture_size
    grounding = page.get("qa_grounding") or "(none)"
    return "\n".join(
        [
            "The attached picture is a page of a children's book. Two kids ask questions about it.",
            "",
            "The kids' questions and answers:",
            *qa,
            "",
            f"What the question is about (from the script): {grounding}",
            "",
            f"Picture size: {w}x{h} pixels.",
            "Layers of this picture (name: description):",
            *(layers or ["- (none)"]),
            "",
            "Name the single thing the kids' question is about and save it with the "
            "save_camera_subject tool. If it is one of the listed layers, save "
            '{"layer": "<name>"}. Otherwise save {"phrase": "<2-6 words>", '
            '"box": [x0, y0, x1, y1]} with the box in picture pixels around that thing.',
            "Save at most 3 times. If a save is refused, fix every listed error and save again.",
        ]
    )
