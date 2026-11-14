# Implementation Plan: Grounded camera targets and caption placement

**Spec:** docs/specs/025-grounded-camera-captions/spec.md (Approved 2026-11-14)
**Decisions:** none new (PRD 025: additive stage, separate file, no ADR)
**Created:** 2026-11-14

## Global Constraints

- `ground_camera` never writes `episode_script.json`; script hash, review mark and voice approval stay unchanged (FR-7, AC-3.3).
- Codex subject naming: validating save tool, 3 attempts, each refusal lists every problem (FR-2).
- A camera file is current only when its picture sha256 equals the picture's (FR-3). Stale, missing or failed → render uses today's guessed target and `bottom` band, log names the reason and fix command (AC-3.2).
- Zoom stays within the existing 1–2 range; the whole subject box stays in frame (AC-1.4).
- Band: smaller share of mask pixels wins; ties and no masks → `bottom`; intro/outro captions stay `bottom` (AC-2.2, AC-2.4).
- Books with no camera files render with identical cache keys and output (AC-3.5); `caption_band` enters the section key only when not `bottom`.
- Segmentation only for non-layer subjects, segmenter loaded alone (NFR-1). Codex text never reaches a shell or file name (NFR-4).
- Python 3.12, Ruff (line 100), pathlib, atomic JSON writes with `indent=2, ensure_ascii=False`, pure logic unit-tested with fakes; run only tests for changed files (project memory).

## Goal

A `ground_camera` stage writes per-page `books/<slug>/camera/page_NN.json`; render and page preview aim the Q&A camera at the named subject and put captions on the emptier edge, without touching the script or approvals.

---

## Phase 0: Contracts and tests first

**Implements:** FR-3, FR-4, FR-5, FR-6 | **Satisfies:** AC-1.4, AC-2.1–2.3, AC-3.1, AC-3.2, AC-3.5, AC-4.4
**Files:** `tests/test_camera_ground.py` (new), additions to `tests/test_timeline.py`, `tests/test_frames.py`
**Interfaces:** Consumes nothing. Produces the failing tests that Phases 1–3 satisfy: `camera_ground.target_for_box`, `camera_ground.band_for_rows`, `camera_ground.problems`, `camera_ground.load_current`, `timeline.grounded_target_on_canvas`, `timeline.page_section(..., grounding=)`, `frames.render_caption(..., band=)`.

- [ ] Write `tests/test_camera_ground.py` pure tests:

```python
import numpy as np
import pytest

from video_pipeline import camera_ground as cg

CANVAS = (1920, 1080)
SETTINGS = cg.CameraSettings(
    zoom_min=1.0, zoom_max=2.0, zoom_margin=0.15, band_fraction=0.25,
    min_mask_share=0.002, max_subject_share=0.6,
)


def test_target_centres_on_box_and_keeps_it_in_frame():
    t = cg.target_for_box((400, 300, 600, 500), (1672, 941), CANVAS, SETTINGS)
    assert t["x"] == pytest.approx(500 / 1672, abs=1e-3)
    assert t["y"] == pytest.approx(400 / 941, abs=1e-3)
    assert 1.0 <= t["zoom"] <= 2.0
    # box width on canvas * (1 + margin) fits in the crop of width W / zoom
    box_w = 200 * 1920 / 1672
    assert box_w * 1.15 <= 1920 / t["zoom"] + 1e-6


def test_large_box_reduces_zoom_to_one():
    t = cg.target_for_box((0, 0, 1672, 941), (1672, 941), CANVAS, SETTINGS)
    assert t["zoom"] == 1.0


def test_square_picture_maps_through_contain():
    t = cg.target_for_box((0, 0, 100, 100), (1000, 1000), CANVAS, SETTINGS)
    assert t["x"] == pytest.approx(0.05, abs=1e-3) and t["y"] == pytest.approx(0.05, abs=1e-3)


def rows(top=0, bottom=0, h=941):
    r = np.zeros(h, np.int64)
    r[: int(h * 0.2)] = top
    r[-int(h * 0.2):] = bottom
    return r


def test_band_prefers_less_covered_edge():
    assert cg.band_for_rows(rows(top=0, bottom=500), (1672, 941), CANVAS, SETTINGS) == "top"
    assert cg.band_for_rows(rows(top=500, bottom=0), (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_band_ties_and_empty_masks_are_bottom():
    assert cg.band_for_rows(rows(300, 300), (1672, 941), CANVAS, SETTINGS) == "bottom"
    assert cg.band_for_rows(np.zeros(941, np.int64), (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_tiny_masks_are_ignored():
    r = rows(top=0, bottom=1)  # far below min_mask_share of 1672*941 pixels
    assert cg.band_for_rows(r, (1672, 941), CANVAS, SETTINGS) == "bottom"


def test_problems_rejects_bad_files():
    good = {"page_number": 2, "picture": {"sha256": "a", "width": 10, "height": 10},
            "status": "ok", "subject": {"name": "car", "source": "layer", "box": [1, 1, 5, 5]},
            "target": {"x": 0.5, "y": 0.5, "zoom": 1.3}, "band": "top"}
    assert cg.problems(good) == []
    assert cg.problems({**good, "band": "left"})
    assert cg.problems({**good, "target": {"x": 1.5, "y": 0.5, "zoom": 1.3}})
    assert cg.problems({**good, "target": {"x": 0.5, "y": 0.5, "zoom": 2.5}})


def test_load_current_reasons(tmp_path, folder_factory):
    folder = folder_factory(tmp_path)
    data, reason = cg.load_current(folder, 2, "sha")
    assert data is None and "ground_camera" in reason
```

(`folder_factory` is a small fixture in the same file that builds a `BookFolder` over `tmp_path/books/x`; add it from `tests/helpers.py`'s `MINI_BOOK` pattern.)

- [ ] Add to `tests/test_timeline.py`:

```python
def test_grounded_target_on_canvas_16x9_and_square(cfg):
    t = {"x": 0.25, "y": 0.5, "zoom": 1.4}
    assert timeline.grounded_target_on_canvas(t, (1672, 941), cfg) == pytest.approx((1.4, 0.25, 0.5), abs=1e-3)
    sq = timeline.grounded_target_on_canvas(t, (1000, 1000), cfg)
    assert sq == timeline.qa_target_on_canvas(t, cfg)  # same as the legacy square mapping


def _page(qa=True):
    return {"page_number": 2, "ffmpeg_camera": {"narration_motion": "static",
            "qa_zoom_target": {"x": 0.5, "y": 0.5, "zoom": 1.3}}}


def test_page_section_uses_grounding(cfg, qa_timing):
    g = {"status": "ok", "band": "top", "picture": {"width": 1672, "height": 941},
         "target": {"x": 0.2, "y": 0.3, "zoom": 1.6}}
    s = timeline.page_section(_page(), qa_timing, cfg, "page_02", grounding=g)
    assert s.caption_band == "top"
    assert s.camera.qa_target == pytest.approx((1.6, 0.2, 0.3), abs=1e-3)


def test_page_section_without_grounding_is_unchanged(cfg, qa_timing):
    a = timeline.page_section(_page(), qa_timing, cfg, "page_02")
    assert a.caption_band == "bottom"
    assert timeline.section_key(a, "sha", cfg) == timeline.section_key(
        dataclasses.replace(a, caption_band="bottom"), "sha", cfg)


def test_band_enters_key_only_when_top(cfg, qa_timing):
    a = timeline.page_section(_page(), qa_timing, cfg, "page_02")
    top = dataclasses.replace(a, caption_band="top")
    assert timeline.section_key(a, "s", cfg) != timeline.section_key(top, "s", cfg)
```

(`qa_timing` fixture: a timing dict with one narration and one question line, built like the existing helpers in `tests/test_timeline.py`.)

- [ ] Add to `tests/test_frames.py`:

```python
def test_caption_top_band_sits_at_margin(cfg, tmp_path):
    out = tmp_path / "c.png"
    render_caption("A short line", cfg, out, band="top")
    with Image.open(out) as im:
        alpha = np.asarray(im)[..., 3]
    ys = np.where(alpha.any(axis=1))[0]
    assert ys.min() == cfg.captions.margin
    assert ys.max() < cfg.height // 2


def test_caption_default_is_bottom(cfg, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    render_caption("A short line", cfg, a)
    render_caption("A short line", cfg, b, band="bottom")
    assert a.read_bytes() == b.read_bytes()
```

- [ ] Run: `uv run pytest tests/test_camera_ground.py tests/test_timeline.py tests/test_frames.py -q` — expect FAIL (names missing)
- [ ] Commit: `test: contract tests for grounded camera and caption band (spec 025)`

---

## Phase 1: Pure grounding logic and config

**Implements:** FR-3, FR-4, FR-5 | **Satisfies:** AC-1.4, AC-2.1, AC-2.2, AC-4.4
**Files:** `config/camera.json` (new), `video_pipeline/camera_ground.py` (new, pure half), `video_pipeline/timeline.py` (`contain_size`, `grounded_target_on_canvas`), `video_pipeline/frames.py` (use `timeline.contain_size`)
**Interfaces:** Consumes `timeline.RenderConfig`. Produces `CameraSettings`, `load_settings(config_dir)`, `target_for_box(box, picture_size, canvas, settings) -> dict`, `band_for_rows(row_counts, picture_size, canvas, settings) -> str`, `problems(data) -> list[str]`, `camera_path(folder, n)`, `load_current(folder, n, picture_sha) -> (dict|None, str)`, `timeline.contain_size(image, size)`, `timeline.grounded_target_on_canvas(target, picture_size, cfg) -> Point`.

- [ ] Create `config/camera.json`:

```json
{
  "zoom_min": 1.0,
  "zoom_max": 2.0,
  "zoom_margin": 0.15,
  "band_fraction": 0.25,
  "min_mask_share": 0.002,
  "max_subject_share": 0.6,
  "attempts": 3,
  "time_limit_s": 600
}
```

- [ ] Move `_contain_size` from `frames.py` to `timeline.py` as public `contain_size` (same body); in `frames.py` replace the definition with `from video_pipeline.timeline import contain_size` and call it in `canvas_parts`. Add to `timeline.py` after `qa_target_on_canvas`:

```python
def grounded_target_on_canvas(target: dict, picture_size: tuple[int, int], cfg: RenderConfig) -> Point:
    """Map a grounded Q&A target (x, y in picture fractions) to canvas fractions, for a picture
    drawn `contain`-fit and centred (spec 025). Equals `qa_target_on_canvas` for square pictures."""
    front_w, front_h = contain_size(picture_size, (cfg.width, cfg.height))
    left, top = (cfg.width - front_w) / 2, (cfg.height - front_h) / 2
    return (
        float(target["zoom"]),
        (left + float(target["x"]) * front_w) / cfg.width,
        (top + float(target["y"]) * front_h) / cfg.height,
    )
```

- [ ] Write `video_pipeline/camera_ground.py` pure half:

```python
"""Spec 025: grounded Q&A camera targets and caption bands, from picture masks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import BookFolder
from video_pipeline.timeline import contain_size

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
    is mask pixels per picture row. Ties, no masks, or masks under `min_mask_share` → bottom."""
    pw, ph = picture_size
    if len(row_counts) != ph or row_counts.sum() < settings.min_mask_share * pw * ph:
        return "bottom"
    front_w, front_h = contain_size(picture_size, canvas)
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
        return None, f"no camera file – {fix}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable camera file ({exc}) – {fix} --force"
    if bad := problems(data):
        return None, f"invalid camera file ({'; '.join(bad)}) – {fix} --force"
    if data["picture"]["sha256"] != picture_sha:
        return None, f"stale camera file (picture changed) – {fix}"
    return data, ""
```

- [ ] Run: `uv run pytest tests/test_camera_ground.py tests/test_frames.py -q -k "not load_current_reasons and not top"` — expect PASS for pure tests
- [ ] Commit: `feat(camera): pure target and band logic (spec 025)`

---

## Phase 2: Render integration (captions, camera, cache keys)

**Implements:** FR-6, FR-7 | **Satisfies:** AC-2.3, AC-2.4, AC-3.1–3.5
**Files:** `video_pipeline/frames.py`, `video_pipeline/timeline.py`, `video_pipeline/render_episode.py`, tests from Phase 0
**Interfaces:** Consumes Phase 1. Produces `frames.render_caption(text, cfg, out_path, band="bottom")`, `Section.caption_band: str = "bottom"`, `page_section(page, timing, cfg, picture, grounding: dict | None = None)`, `build_sections(..., groundings: dict[str, dict] | None = None)` (keys are section names `page-PP`), `camera_ground.load_groundings(folder, script, by_section, picture_sha) -> (dict[str, dict], list[tuple[str, str]])`.

- [ ] `frames.render_caption`: add `band: str = "bottom"`; replace the y computation:

```python
    y0 = style.margin if band == "top" else cfg.height - style.margin - band_h
    y1 = y0 + band_h
```

(`band_h` is already computed above; remove the old `y1`/`y0` lines.)

- [ ] `timeline.py`: add `caption_band: str = "bottom"` to `Section`; in `section_key` add after `"camera"`: `**({"caption_band": section.caption_band} if section.caption_band != "bottom" else {}),`. Change `page_section`:

```python
def page_section(page, timing, cfg, picture, grounding=None) -> Section:
    ...
    target = camera_cfg.get("qa_zoom_target")
    qa_frame = qa_target = None
    band = "bottom"
    grounded = grounding if grounding and grounding.get("band") in ("top", "bottom") else None
    if grounded:
        band = grounded["band"]
    if qa_line is not None:
        if grounded and grounded.get("status") == "ok" and grounded.get("target"):
            qa_target = grounded_target_on_canvas(
                grounded["target"],
                (grounded["picture"]["width"], grounded["picture"]["height"]),
                cfg,
            )
        elif target:
            qa_target = qa_target_on_canvas(target, cfg)
        if qa_target is not None:
            qa_frame = round((cfg.lead_in + float(qa_line["start"])) * cfg.fps)
    return Section(name, "page", picture, frames, name, _section_captions(timing, cfg),
                   Camera(...unchanged...), caption_band=band)
```

and `build_sections(..., groundings=None)` passes `grounding=(groundings or {}).get(name)` to `page_section`.

- [ ] `render_episode.py`: in `_render_captions` call `render_caption(card.text, cfg, path, band=section.caption_band)`. In `_prepare` (line ~900) after `picture_sha`:

```python
    groundings, camera_notes = camera_ground.load_groundings(
        folder, script, pictures.by_section, picture_sha
    )
    sections = build_sections(script, timings, cfg, pictures.by_section, groundings)
    ...
    still_notes = cover_notes + camera_notes + still_notes
```

and in the page-preview function before `timeline.page_section(...)`:

```python
    sha = sha256_file(picture)
    grounding, _reason = camera_ground.load_current(folder, page, sha)
    section = timeline.page_section(script_page, timings[name], cfg, f"page_{page:02d}", grounding=grounding)
    still_key = section_key(section, sha, cfg)
```

- [ ] Add `load_groundings` to `camera_ground.py`:

```python
def load_groundings(folder, script, by_section, picture_sha):
    """{section name: current camera file} for story pages, plus (page, note) lines for pages
    that fall back (AC-3.2). Books with no camera folder produce one summary note at most."""
    found: dict[str, dict] = {}
    notes: list[tuple[str, str]] = []
    folder_exists = (folder.folder / "camera").is_dir()
    for page in script["pages"]:
        n = int(page["page_number"])
        name = f"page-{n:02d}"
        sha = picture_sha.get(by_section.get(name, ""))
        if sha is None:
            continue
        data, reason = load_current(folder, n, sha)
        if data is not None:
            found[name] = data
        elif folder_exists:
            notes.append((name, f"camera: page {n} uses the guessed target and bottom captions – {reason}"))
    return found, notes
```

(The `still_notes` element shape is `(section name, text)`; check against `_page_eligibility` when wiring and adapt the tuple order.)

- [ ] Run: `uv run pytest tests/test_camera_ground.py tests/test_timeline.py tests/test_frames.py tests/test_render_cli.py -q` — expect PASS
- [ ] Commit: `feat(render): use grounded camera target and caption band (spec 025)`

---

## Phase 3: Subject naming (Codex MCP save tool)

**Implements:** FR-2 | **Satisfies:** AC-1.2, AC-1.3, AC-1.5 (no Q&A), AC-1.6 (naming failure)
**Files:** `video_pipeline/camera_mcp.py` (new), `video_pipeline/camera_ground.py` (SubjectTools, prompt), `tests/test_camera_mcp.py` (new)
**Interfaces:** Consumes `agents.build_codex_command`, `run_codex` (pattern of `motion_plan.plan_page`). Produces `SubjectTools(layer_names, picture_size, out).save_camera_subject(subject) -> {"saved": bool, "errors": list[str]}`; `subject_problems(subject, layer_names, picture_size) -> list[str]`; `camera_mcp.py` server exposing `save_camera_subject`.

- [ ] Tests (`tests/test_camera_mcp.py`):

```python
from video_pipeline import camera_ground as cg

SIZE = (1672, 941)


def test_layer_subject_must_exist():
    assert cg.subject_problems({"layer": "car"}, ["car", "boy"], SIZE) == []
    assert cg.subject_problems({"layer": "dog"}, ["car", "boy"], SIZE)


def test_phrase_needs_box_inside_picture():
    ok = {"phrase": "red toy car", "box": [10, 10, 200, 150]}
    assert cg.subject_problems(ok, [], SIZE) == []
    assert cg.subject_problems({**ok, "box": [10, 10, 2000, 150]}, [], SIZE)
    assert cg.subject_problems({**ok, "box": [200, 10, 10, 150]}, [], SIZE)
    assert cg.subject_problems({"phrase": "x" * 200, "box": [1, 1, 5, 5]}, [], SIZE)
    assert cg.subject_problems({"phrase": "a", "layer": "car", "box": [1, 1, 5, 5]}, ["car"], SIZE)


def test_save_tool_writes_only_valid(tmp_path):
    out = tmp_path / "s.json"
    tools = cg.SubjectTools(["car"], SIZE, out)
    assert tools.save_camera_subject({"layer": "dog"})["saved"] is False and not out.exists()
    assert tools.save_camera_subject({"layer": "car"})["saved"] is True and out.exists()
```

- [ ] Run: `uv run pytest tests/test_camera_mcp.py -q` — expect FAIL
- [ ] Implement in `camera_ground.py`:

```python
MAX_PHRASE = 80
MAX_SAVES = 3


def subject_problems(subject, layer_names, picture_size) -> list[str]:
    if not isinstance(subject, dict):
        return ["subject must be an object with `layer`, or `phrase` and `box`"]
    layer, phrase, box = subject.get("layer"), subject.get("phrase"), subject.get("box")
    if layer is not None:
        if phrase is not None or box is not None:
            return ["give either `layer`, or `phrase` and `box`, not both"]
        return [] if layer in layer_names else [
            f"layer {layer!r} is not one of: {', '.join(layer_names) or '(none)'}"
        ]
    errors: list[str] = []
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > MAX_PHRASE:
        errors.append(f"phrase must be 1-{MAX_PHRASE} characters")
    w, h = picture_size
    ok = (
        isinstance(box, list) and len(box) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
        and 0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h
    )
    if not ok:
        errors.append(f"box must be [x0, y0, x1, y1] integers inside the {w}x{h} picture, x0<x1, y0<y1")
    return errors


@dataclass
class SubjectTools:
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
        write_atomic(self.out, subject)
        return {"saved": True, "errors": []}
```

- [ ] `camera_mcp.py` (mirrors `plan_mcp.py`): args `--layers` (comma list), `--size WxH`, `--out`; `create_server(tools)` registers `save_camera_subject(subject: dict) -> dict` with a docstring telling Codex: name the thing the kids' question is about, as `{"layer": name}` when it is a listed layer, else `{"phrase": ..., "box": [x0,y0,x1,y1]}` in picture pixels.
- [ ] Run: `uv run pytest tests/test_camera_mcp.py -q` — expect PASS
- [ ] Commit: `feat(camera): Codex subject save tool (spec 025)`

---

## Phase 4: The ground_camera stage and CLI

**Implements:** FR-1, FR-2, FR-3, FR-5, FR-7 | **Satisfies:** AC-1.1–1.6, AC-2.1, AC-2.2, AC-3.3, AC-4.1
**Files:** `video_pipeline/camera_ground.py` (stage half), `video_pipeline/ground_camera.py` (new CLI), `video_pipeline/cut_layers.py` (`load_real_segmenter`), `tests/test_ground_camera.py` (new)
**Interfaces:** Consumes Phases 1 and 3, `cut_layers.layers_dir`, `cut_layers.Segmenter`, `motion_plan.plan_state`/`Layer`, `agents.run_codex`. Produces `ground_page(...) -> GroundOutcome(page_number, status, reason)`, `ground_book(folder, *, root, pages, force, provider, load_segmenter, log) -> list[GroundOutcome]`, CLI `python -m video_pipeline.ground_camera --book SLUG [--page N] [--force] [--root R]`.

- [ ] Tests with fakes (`tests/test_ground_camera.py`): build a tmp book (MINI_BOOK helper) with a 160×90 picture, a plan with layer `car`, `layers/page_02/` manifest + RGBA png, and a script page with a question line. A `FakeCodex(subject_json)` provider writes the `--out` path found in the command; a `FakeSeg` returns a fixed box mask.

```python
def test_layer_subject_uses_layer_mask_without_loading_segmenter(book):
    loads = []
    out = cg.ground_book(book.folder, root=ROOT, pages=[2], provider=FakeCodex({"layer": "car"}),
                         load_segmenter=lambda *a: loads.append(1) or FakeSeg())
    data = json.loads(cg.camera_path(book.folder, 2).read_text())
    assert out[0].status == "written" and data["subject"]["source"] == "layer"
    assert not loads
    x0, y0, x1, y1 = data["subject"]["box"]
    assert x0 <= data["target"]["x"] * 160 <= x1


def test_phrase_subject_runs_segmenter_once(book): ...  # FakeCodex({"phrase": "kite", "box": [10,10,50,40]}); loads == [1]; source == "sam"


def test_no_qa_page_records_band_only(book_no_qa): ...  # status no_qa, target None, band from layer rows


def test_empty_and_oversized_masks_fail_page_but_not_book(book): ...  # FakeSeg returning empty / full mask → status failed, reason set, other page still written


def test_codex_refusals_exhaust_attempts(book): ...  # provider never saves → status failed, 3 calls, no file target


def test_script_untouched(book):
    before = book.folder.script_path.read_bytes()
    cg.ground_book(...); assert book.folder.script_path.read_bytes() == before


def test_current_file_skipped_unless_forced(book): ...  # second run → kept; force → written
```

(Each `...` test is written in full in the file; they follow the first test's setup.)

- [ ] Run: `uv run pytest tests/test_ground_camera.py -q` — expect FAIL
- [ ] `cut_layers.py`: add

```python
def load_real_segmenter(root: Path, models_cfg: dict) -> Segmenter:
    """Load only the segmenter (spec 025 NFR-1); same checks as `load_real_models`."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    base = models_dir()
    missing = [p for p in (base / f for f in _segmenter_files(models_cfg["segmenter"])) if not p.exists()]
    if missing:
        raise ModelsMissing(f"models not downloaded ({missing[0]} is missing): run {DOWNLOAD_HINT}")
    compute_unit_name(models_cfg["segmenter"])
    try:
        return SamCoreML(base, models_cfg["segmenter"])
    except SegmenterError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ModelLoadError(f"could not load the segmenter: {exc} (run {DOWNLOAD_HINT})") from exc
```

(Confirm `_segmenter_files` return shape against `_required` at implementation; adapt the path join.)

- [ ] `camera_ground.py` stage half. `ground_page` order: current & not forced → `kept`; no Q&A (`page.get("has_qa")` false / no non-narration line) → subject None, status `no_qa`; else run Codex (attempts from settings, refusals fed back like `motion_plan.plan_page`, images=[picture], prompt built by `subject_prompt(page, layer_names, picture_size)` containing the Q&A lines, `qa_grounding`, layer names with descriptions and the size); on 3 refusals → status `failed`, reason = first refusal. Mask: `{"layer": n}` → alpha of that layer's PNG placed at its manifest offset (`layer_mask(folder, n, name, size)`); phrase → `seg.masks(rgb, [Layer(name="subject", box=box, z=1, depth=0.5, pivot=(cx, cy))])[0]` with the segmenter loaded lazily once. Empty mask, or mask share > `max_subject_share` → failed with reason. Row counts = `layer_row_counts(folder, n, size)` (sum of every layer PNG's alpha>0 rows, via manifest) plus the subject mask rows where it is not already in a layer; band = `band_for_rows`. Layers not cut and subject is a layer → failed "cut layers missing – run cut_layers". Write file `{page_number, picture{file,sha256,width,height}, provider, written_at, status, reason, subject{name,source,box}|None, target|None, band}` with `write_atomic`. `ground_book` mirrors `plan_book` (sorted pages, optional `pages`, failures never stop others, `AgentUnavailable` raised before any page via `codex_status()`).
- [ ] `ground_camera.py` CLI: argparse `--book --page (repeat) --force --root`, calls `ground_book`, prints `N written, N kept, N no Q&A, N failed`, exit 1 on failed or `ModelsMissing`/`AgentUnavailable`/`LibraryError`.
- [ ] Run: `uv run pytest tests/test_ground_camera.py tests/test_camera_ground.py -q` — expect PASS
- [ ] Commit: `feat(camera): ground_camera stage and CLI (spec 025)`

---

## Phase 5: Studio stage and Animation view

**Implements:** FR-1 | **Satisfies:** AC-4.2, AC-4.3
**Files:** `video_pipeline/studio/jobs.py`, `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/animation.html`, `animation_page.html`, `tests/test_studio_jobs.py`, `tests/test_studio_books.py`
**Interfaces:** Consumes `camera_ground.camera_path/load_current`. Produces stage name `"ground"` in `ANIMATION_STAGES`, `STAGE_MODULES["ground"] = "video_pipeline.ground_camera"`, row field `camera` in `page_animation` rows (`{"state": "current"|"missing"|"stale"|"failed"|"—", "subject", "box", "target", "band", "reason"}`).

- [ ] Tests: `stage_command("ground", book, paths)` ends with `--book <slug> --root <root>`; `page_animation` row for a page with a current file has `camera.state == "current"` and the subject name; a stale file reads `stale`; a failed file reads `failed` with its reason.
- [ ] Run those tests — expect FAIL
- [ ] `books.py`: `ANIMATION_STAGES = ("plan", "cut", "ground")`, `STAGE_LABELS["ground"] = "camera grounding"`; in `page_animation` add a `camera` dict per row built with `camera_ground.load_current(folder, n, picture_sha)` (state `missing` when no file, `stale` when sha differs, `failed` when `status == "failed"`, else `current`). `jobs.py`: add `"ground": "video_pipeline.ground_camera"` to `STAGE_MODULES`. `app.py`: `STAGE_BUTTONS["ground"] = "Ground camera"`; its gate requires cut layers present (same rule as the page preview's animation precondition: refuse with "cut layers first" when no page has `cut_state == "cut"`).
- [ ] Templates: `animation.html` table gets a "Camera" column showing state and `band`; `animation_page.html` shows subject name, box, target, band and reason, and draws the subject box as an outline over the picture like the layer boxes.
- [ ] Run: `uv run pytest tests/test_studio_jobs.py tests/test_studio_books.py tests/test_studio_app.py -q` — expect PASS
- [ ] Commit: `feat(studio): ground camera stage and Animation view (spec 025)`

---

## Phase 6: Integration verification and docs

**Implements:** All FRs | **Satisfies:** All ACs

- [ ] Run changed-area tests: `uv run pytest tests/test_camera_ground.py tests/test_camera_mcp.py tests/test_ground_camera.py tests/test_timeline.py tests/test_frames.py tests/test_render_cli.py tests/test_studio_jobs.py tests/test_studio_books.py -q` — expect all PASS; `uv run ruff check video_pipeline tests` and `uv run ruff format --check` — clean
- [ ] Add an opt-in real test `tests/test_ground_camera_real.py` (`@pytest.mark.models`) that segments a phrase subject on a pilot picture and asserts the box centre lies in the mask bbox
- [ ] Real run on a book with cut layers (not the pilot Jack book that has uncommitted work): `uv run python -m video_pipeline.ground_camera --book <slug> --page 2`, then `uv run python -m video_pipeline.render_episode --book <slug> --preview-page 2`; confirm `episode_script.json` is byte-identical (`git diff --stat`)
- [ ] Update `CLAUDE.md` command list and Architecture with the stage (`ground_camera`, `camera/page_NN.json`, `config/camera.json`)
- [ ] Commit: `feat: complete grounded camera and captions (spec 025)`

---

## Quickstart Validation

```bash
uv run python -m video_pipeline.ground_camera --book <slug>        # writes books/<slug>/camera/page_NN.json
git diff --stat -- books/<slug>/episode                            # empty: script untouched
uv run python -m video_pipeline.render_episode --book <slug> --preview-page 2   # camera on the subject, captions on the emptier edge
uv run python -m video_pipeline.ground_camera --book <slug>        # second run: all kept
```

In the studio Animation view the Camera column shows `current`, subject and band for each page.
