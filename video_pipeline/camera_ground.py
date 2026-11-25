"""Narration focus boxes, grounded Q&A camera targets and caption bands."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

from video_pipeline import cut_layers
from video_pipeline.agents import AgentResult, build_codex_command, codex_status, run_codex
from video_pipeline.cut_layers import MANIFEST, Segmenter, SegmenterError, layers_dir, mask_bbox
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder
from video_pipeline.motion_plan import (
    Layer,
    _refusals,
    load_limits,
    page_picture,
    picture_identity,
    plan_state,
)
from video_pipeline.timeline import contain_size, load_render_config, page_section_name

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


def _subject_file_problems(subject: object) -> list[str]:
    if not isinstance(subject, dict):
        return ["subject must be an object"]
    out: list[str] = []
    name = subject.get("name")
    if not isinstance(name, str) or not name.strip():
        out.append("subject.name is missing")
    box = subject.get("box")
    ints = (
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
    )
    if not ints:
        out.append("subject.box must be 4 integers")
    elif not (box[0] >= 0 and box[1] >= 0 and box[0] < box[2] and box[1] < box[3]):
        out.append("subject.box must have 0 <= x0 < x1 and 0 <= y0 < y1")
    return out


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
    subject = data.get("subject")
    if subject is not None:
        out.extend(_subject_file_problems(subject))
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
    beats = data.get("narration", [])
    if not isinstance(beats, list) or len(beats) > 8:
        out.append("narration must be a list of at most 8 cues")
    else:
        previous = -1
        for beat in beats:
            if not isinstance(beat, dict):
                out.append("narration cue must be an object")
                continue
            index = beat.get("line")
            if not isinstance(index, int) or isinstance(index, bool) or index <= previous:
                out.append("narration line indices must be increasing nonnegative integers")
            else:
                previous = index
            if not isinstance(beat.get("target"), dict):
                out.append("narration cue requires a target")
            else:
                out.extend(problems({"picture": pic, "band": "bottom", "target": beat["target"]}))
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
        if data.get("narration_text") != page.get("lines", []):
            data = {**data, "narration": []}
        found[name] = data
        if data.get("status", "ok") == "failed":  # failed grounding: page_section ignores its target
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
    if "narration" in subject:
        beats = subject["narration"]
        if not isinstance(beats, list) or len(beats) > 8:
            return ["narration must be a list of at most 8 focus cues"]
        previous = -1
        for beat in beats:
            if not isinstance(beat, dict):
                return ["each narration cue must be an object"]
            line = beat.get("line")
            if not isinstance(line, int) or isinstance(line, bool) or line <= previous:
                return ["narration line indices must be increasing nonnegative integers"]
            previous = line
            errors = subject_problems(
                {"phrase": beat.get("phrase"), "box": beat.get("box")}, [], picture_size
            )
            if errors:
                return errors
        if not any(key in subject for key in ("layer", "phrase", "box")):
            return []
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
    narration = [
        f"{i}: {line['text']}"
        for i, line in enumerate(page.get("lines", []))
        if line.get("role") == "narration"
    ]
    layers = [f"- {name}: {desc}" for name, desc in layer_names_with_descriptions]
    w, h = picture_size
    grounding = page.get("qa_grounding") or "(none)"
    return "\n".join(
        [
            "The attached picture is a narrated children's book page, sometimes followed by kids' Q&A.",
            "",
            "Narration lines (zero-based indices in the script):",
            *narration,
            "Include a narration list in your save: [{line: index, phrase: subject, box: [x0,y0,x1,y1]}].",
            "Choose up to 8 meaningful focus changes matching these spoken lines. Focus on faces for feelings, reactions or speech; on visible objects and hands for actions or discoveries. Use picture pixels, never guessed coordinates from text alone. Frame the whole face or object with context. Omit cues when the wide view tells the story better or the subject is not visible. Do not alternate targets unnecessarily. Use an empty list if no focus is needed.",
            "For a page without Q&A, save only {narration: [...]}. For a page with Q&A, also include the Q&A layer or phrase and box as below.",
            "The kids' questions and answers:",
            *qa,
            "",
            f"What the question is about (from the script): {grounding}",
            "",
            f"Picture size: {w}x{h} pixels.",
            "Layers of this picture (name: description):",
            *(layers or ["- (none)"]),
            "",
            "If Q&A is present, name the single thing the question is about and include it with narration in the "
            "save_camera_subject tool. If it is one of the listed layers, save "
            '{"layer": "<name>"}. Otherwise save {"phrase": "<2-6 words>", '
            '"box": [x0, y0, x1, y1]} with the box in picture pixels around that thing.',
            "Save at most 3 times. If a save is refused, fix every listed error and save again.",
        ]
    )


# -- the stage: name the subject with Codex, find its mask, write the camera file ------------------

Provider = Callable[..., AgentResult]
LoadSegmenter = Callable[[Path, dict], Segmenter]
PROVIDER_NAME = "codex"


@dataclass(frozen=True)
class GroundOutcome:
    page_number: int
    status: Literal["written", "kept", "no_qa", "failed"]
    reason: str = ""


class _CutMissing(Exception):
    """The page's cut layers are not on disk."""


def _manifest(folder: BookFolder, page_number: int) -> dict:
    try:
        data = json.loads((layers_dir(folder, page_number) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _CutMissing from None
    if not isinstance(data, dict):
        raise _CutMissing
    return data


def _entry_mask(
    folder: BookFolder, page_number: int, entry: dict, size: tuple[int, int]
) -> np.ndarray:
    """The layer's opaque pixels placed on a picture-sized canvas (empty layer: all False)."""
    width, height = size
    mask = np.zeros((height, width), bool)
    if entry.get("empty") or not entry.get("file"):
        return mask
    path = layers_dir(folder, page_number) / str(entry["file"])
    try:
        with Image.open(path) as im:
            alpha = np.asarray(im.convert("RGBA"))[..., 3] > 0
    except OSError:
        raise _CutMissing from None
    x, y = entry["offset"]
    crop = alpha[: height - y, : width - x]
    mask[y : y + crop.shape[0], x : x + crop.shape[1]] = crop
    return mask


def layer_mask(
    folder: BookFolder, page_number: int, name: str, size: tuple[int, int]
) -> np.ndarray:
    """The cut mask of plan layer `name`; `_CutMissing` when the page is not cut."""
    for entry in _manifest(folder, page_number).get("layers") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return _entry_mask(folder, page_number, entry, size)
    raise _CutMissing


def layers_union(folder: BookFolder, page_number: int, size: tuple[int, int]) -> np.ndarray:
    """Every cut layer's mask together (all False when the page is not cut)."""
    width, height = size
    union = np.zeros((height, width), bool)
    try:
        for entry in _manifest(folder, page_number).get("layers") or []:
            if isinstance(entry, dict):
                union |= _entry_mask(folder, page_number, entry, size)
    except _CutMissing:
        return np.zeros((height, width), bool)
    return union


def layer_row_counts(folder: BookFolder, page_number: int, size: tuple[int, int]) -> np.ndarray:
    """Mask pixels per picture row over all cut layers (zeros when not cut)."""
    return layers_union(folder, page_number, size).sum(axis=1)


def _write(
    folder: BookFolder,
    number: int,
    picture: dict,
    status: str,
    reason: str,
    subject: dict | None,
    target: dict | None,
    band: str,
    narration: list | None = None,
    narration_text: list | None = None,
) -> None:
    write_atomic(
        camera_path(folder, number),
        {
            "page_number": number,
            "picture": picture,
            "provider": PROVIDER_NAME,
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "status": status,
            "reason": reason,
            "subject": subject,
            "target": target,
            "band": band,
            "narration": narration or [],
            "narration_text": narration_text or [],
        },
    )


def _name_subject(
    folder: BookFolder,
    number: int,
    script_page: dict,
    layers: list[tuple[str, str]],
    picture: Path,
    size: tuple[int, int],
    *,
    binary: Path,
    root: Path,
    settings: CameraSettings,
    provider: Provider | None,
) -> tuple[dict | None, str]:
    """Ask the agent until it saves a valid subject: (subject, "") or (None, first refusal)."""
    base = subject_prompt(script_page, layers, size)
    feedback = ["no attempts configured"]
    with tempfile.TemporaryDirectory(prefix="kiddo-camera-") as tmp:
        out = Path(tmp) / "subject.json"
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
                    "video_pipeline.camera_mcp",
                    "--layers",
                    ",".join(name for name, _ in layers),
                    "--size",
                    f"{size[0]}x{size[1]}",
                    "--out",
                    str(out),
                ],
                {"PYTHONPATH": str(PROJECT_ROOT)},
                prompt=text,
                images=[picture],
            )
            stamp = time.strftime("%Y%m%d-%H%M%S")
            log = folder.build_dir / "agent" / f"{stamp}-camera-p{number:02d}-{attempt}.jsonl"
            result = (provider or run_codex)(command, log, timeout_s=settings.time_limit_s)
            if not out.exists():
                feedback = _refusals(result)
                continue
            try:
                subject = json.loads(out.read_text(encoding="utf-8"))
            except ValueError as exc:
                feedback = [f"saved subject is not JSON ({exc})"]
                out.unlink(missing_ok=True)
                continue
            if bad := subject_problems(subject, [n for n, _ in layers], size):
                feedback = bad
                out.unlink(missing_ok=True)
                continue
            return subject, ""
    return None, feedback[0]


def ground_page(
    folder: BookFolder,
    book_page: dict,
    script_page: dict,
    settings: CameraSettings,
    *,
    canvas: tuple[int, int],
    plan_layers: list[tuple[str, str]],
    get_segmenter: Callable[[], Segmenter],
    binary: Path,
    root: Path,
    force: bool = False,
    provider: Provider | None = None,
) -> GroundOutcome:
    """Ground one page: keep a current ok/no-Q&A file (unless forced), else name the subject,
    find its mask and write the camera file. A failure is recorded in the file and returned."""
    number = int(script_page["page_number"])
    picture = page_picture(folder, book_page)
    if not picture.is_file():
        return GroundOutcome(number, "failed", f"{picture.name} is missing")
    identity = picture_identity(picture)
    size = (identity.width, identity.height)
    if not force:
        data, _ = load_current(folder, number, identity.sha256)
        if data is not None and data.get("narration_text") == script_page.get(
            "lines", []
        ):  # AC-4.1: a current file is kept whatever its status
            if data.get("status", "ok") == "failed":
                return GroundOutcome(
                    number, "kept", f"grounding failed earlier: {data.get('reason', '')}"
                )
            return GroundOutcome(number, "kept")
    ident = identity.model_dump()
    layers_row_counts = layer_row_counts(folder, number, size)

    def band_of(extra: np.ndarray | None = None) -> str:
        counts = layers_row_counts
        if extra is not None:
            counts = (layers_union(folder, number, size) | extra).sum(axis=1)
        return band_for_rows(counts, size, canvas, settings)

    def fail(reason: str) -> GroundOutcome:
        retry = f"re-run ground_camera --book {folder.slug} --page {number} --force"
        reason = f"{reason} - to retry, {retry}"
        _write(
            folder,
            number,
            ident,
            "failed",
            reason,
            None,
            None,
            band_of(),
            [],
            script_page.get("lines", []),
        )
        return GroundOutcome(number, "failed", reason)

    subject, reason = _name_subject(
        folder,
        number,
        script_page,
        plan_layers,
        picture,
        size,
        binary=binary,
        root=root,
        settings=settings,
        provider=provider,
    )
    if subject is None:
        return fail(reason)
    narration = []
    lines = script_page.get("lines", [])
    for beat in subject.get("narration", []):
        index = beat["line"]
        if index >= len(lines) or lines[index].get("role") != "narration":
            return fail("narration cue references a missing or non-narration line")
        narration.append(
            {
                "line": index,
                "phrase": beat["phrase"],
                "target": target_for_box(tuple(beat["box"]), size, canvas, settings),
            }
        )
    if not script_page.get("has_qa"):
        _write(folder, number, ident, "no_qa", "", None, None, band_of(), narration, lines)
        return GroundOutcome(number, "no_qa")
    if "layer" not in subject and "phrase" not in subject:
        return fail("Q&A requires a subject as well as narration cues")
    try:
        if "layer" in subject:
            name, source = subject["layer"], "layer"
            mask = layer_mask(folder, number, name, size)
        else:
            name, source = subject["phrase"].strip(), "sam"
            x0, y0, x1, y1 = subject["box"]
            with Image.open(picture) as im:
                rgb = np.asarray(im.convert("RGB"))
            probe = Layer(
                name="subject",
                box=(x0, y0, x1, y1),
                z=1,
                depth=0.5,
                pivot=((x0 + x1) // 2, (y0 + y1) // 2),
            )
            mask = np.asarray(get_segmenter().masks(rgb, [probe])[0], bool)
    except _CutMissing:
        return fail(
            "cut layers missing - run python -m video_pipeline.cut_layers "
            f"--book {folder.slug} --page {number}"
        )
    except SegmenterError as exc:
        return fail(f"segmentation failed: {exc}")
    box = mask_bbox(mask) if mask.any() else None
    if box is None:
        return fail(f"the mask for {name!r} is empty")
    share = float(mask.sum()) / (size[0] * size[1])
    if share > settings.max_subject_share:
        return fail(
            f"the mask for {name!r} covers {share:.0%} of the picture "
            f"(over the {settings.max_subject_share:.0%} share limit)"
        )
    target = target_for_box(box, size, canvas, settings)
    _write(
        folder,
        number,
        ident,
        "ok",
        "",
        {"name": name, "source": source, "box": [int(v) for v in box]},
        target,
        band_of(mask),
        narration,
        lines,
    )
    return GroundOutcome(number, "written")


def ground_book(
    folder: BookFolder,
    *,
    root: Path,
    pages: list[int] | None = None,
    force: bool = False,
    provider: Provider | None = None,
    load_segmenter: LoadSegmenter | None = None,
    binary: Path | None = None,
    log: Callable[[str], None] = print,
) -> list[GroundOutcome]:
    """Ground every script page (or only `pages`). A failing page never stops the others;
    AgentUnavailable is raised before any page. Never writes the episode script."""
    root = Path(root)
    binary = binary or codex_status()
    settings = load_settings(root / "config")
    limits = load_limits(root / "config")
    render = load_render_config(root / "config" / "render.json")
    canvas = (render.width, render.height)
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    script = json.loads(folder.script_path.read_text(encoding="utf-8"))
    book_pages = {int(p["page_number"]): p for p in book["pages"]}
    segmenter: list[Segmenter] = []

    def get_segmenter() -> Segmenter:
        if not segmenter:
            models = cut_layers.read_config(root)["models"]
            segmenter.append((load_segmenter or cut_layers.load_real_segmenter)(root, models))
        return segmenter[0]

    outcomes: list[GroundOutcome] = []
    for script_page in sorted(script["pages"], key=lambda p: int(p["page_number"])):
        number = int(script_page["page_number"])
        if (pages and number not in pages) or number not in book_pages:
            continue
        state = plan_state(folder, book_pages[number], limits)
        layers = (
            [(layer.name, layer.description) for layer in state.plan.layers]
            if state.state == "ok" and state.plan
            else []
        )
        outcome = ground_page(
            folder,
            book_pages[number],
            script_page,
            settings,
            canvas=canvas,
            plan_layers=layers,
            get_segmenter=get_segmenter,
            binary=binary,
            root=root,
            force=force,
            provider=provider,
        )
        reason = f" - {outcome.reason}" if outcome.reason else ""
        log(f"  page {outcome.page_number:02d}: {outcome.status}{reason}")
        outcomes.append(outcome)
    return outcomes
