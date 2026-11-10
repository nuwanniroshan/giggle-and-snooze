"""Cut planned layers and fill the background (spec 009, Phase 4)."""

from __future__ import annotations

import json
import resource
import shutil
import socket
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import FIXTURES, ROOT, make_root
from video_pipeline import cut_layers
from video_pipeline.cut_layers import (
    DOWNLOAD_HINT,
    MANIFEST,
    CutResult,
    ModelsMissing,
    SegmenterError,
    check_embedding,
    clean_mask,
    compute_unit_name,
    crop_box,
    cut_book,
    cut_hash,
    dilate,
    download,
    fill_holes,
    hole_regions,
    layers_dir,
    load_real_models,
    main,
    merge_regions,
    model_ids,
    model_path,
    models_dir,
    occlude,
    prompt_points,
    select_mask,
    upsample_logits,
)
from video_pipeline.library import BookFolder
from video_pipeline.motion_plan import (
    Layer,
    MotionPlan,
    picture_identity,
    plan_path,
    save_plan,
)

W, H = 64, 48
SQUARE = (20, 12, 36, 28)  # the red square, x0 y0 x1 y1
BLUE, RED = (30, 60, 200), (220, 20, 20)
MODELS_CFG = json.loads((ROOT / "config" / "animation.json").read_text(encoding="utf-8"))["models"]


# -- fakes and fixtures ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_hf_offline(monkeypatch) -> None:
    """Loading sets HF_HUB_OFFLINE; give it a recorded value so the test restores it."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")


class FakeSegmenter:
    """Each layer's box as a filled rectangle; a layer named `nothing` gets an empty mask."""

    def __init__(self) -> None:
        self.calls = 0

    def masks(self, image: np.ndarray, layers: list[Layer]) -> list[np.ndarray]:
        self.calls += 1
        out = []
        for layer in layers:
            mask = np.zeros(image.shape[:2], bool)
            if layer.name != "nothing":
                x0, y0, x1, y1 = layer.box
                mask[y0:y1, x0:x1] = True
            out.append(mask)
        return out


class FakeInpainter:
    """Fill the hole with the mean colour of the rest of the picture."""

    def fill(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        out = image.copy()
        out[mask] = image[~mask].mean(axis=0).round().astype(np.uint8)
        return out


def fake_models(counter: list | None = None):
    def load(root: Path, cfg: dict):
        if counter is not None:
            counter.append(1)
        return FakeSegmenter(), FakeInpainter()

    return load


def no_models(root: Path, cfg: dict):
    raise AssertionError("load_models must not be called")


def write_picture(folder: BookFolder, n: int = 1, square: bool = True) -> Path:
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[:] = BLUE
    if square:
        x0, y0, x1, y1 = SQUARE
        rgb[y0:y1, x0:x1] = RED
    path = folder.illustrations / f"page_{n:02d}.png"
    Image.fromarray(rgb).save(path)
    return path


def layer(name: str = "square", box=SQUARE, z: int = 1, **over) -> dict:
    data = {
        "name": name,
        "box": list(box),
        "z": z,
        "depth": 0.5,
        "pivot": [28, 28],
        "motions": [{"type": "sway", "amplitude": 2.0, "period_s": 6.0}],
    }
    data.update(over)
    return data


def write_plan(folder: BookFolder, n: int = 1, layers: list[dict] | None = None) -> MotionPlan:
    picture = folder.illustrations / f"page_{n:02d}.png"
    plan = MotionPlan.model_validate(
        {
            "page_number": n,
            "picture": picture_identity(picture).model_dump(),
            "layers": layers or [layer()],
        }
    )
    save_plan(plan_path(folder, n), plan)
    return plan


@pytest.fixture
def book(tmp_path: Path) -> BookFolder:
    folder = make_root(tmp_path)
    write_picture(folder)
    write_plan(folder)
    return folder


def run(folder: BookFolder, **kw) -> list[CutResult]:
    kw.setdefault("load_models", fake_models())
    kw.setdefault("log", lambda line: None)
    return cut_book(folder, root=folder.root, **kw)


def read_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).astype(np.float32)


def snapshot(out: Path) -> dict[str, tuple[int, bytes]]:
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(out.iterdir())}


# -- AC-4.1 – AC-4.3: outputs ------------------------------------------------------------------------


def test_cut_writes_layer_background_and_manifest(book: BookFolder) -> None:
    results = run(book)
    assert [(r.page_number, r.status) for r in results if r.status != "no plan"] == [(1, "cut")]
    out = layers_dir(book, 1)
    assert out == book.build_dir / "layers" / "page_01"
    with Image.open(out / "square.png") as im:
        assert im.mode == "RGBA"
        layer_size = im.size
    with Image.open(out / "background.png") as im:
        assert (im.mode, im.size) == ("RGB", (W, H))
    manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    (entry,) = manifest["layers"]
    assert entry["name"] == "square" and entry["file"] == "square.png"
    assert entry["empty"] is False
    assert tuple(entry["size"]) == layer_size
    x, y = entry["offset"]
    # Cropped to the (feathered) mask bounds: the square plus at most the feather width.
    assert SQUARE[0] - 2 <= x <= SQUARE[0] and SQUARE[1] - 2 <= y <= SQUARE[1]
    assert manifest["background"] == "background.png"
    assert manifest["feather_px"] == 2
    assert manifest["picture_sha256"] == picture_identity(book.illustrations / "page_01.png").sha256
    assert manifest["model_ids"] == model_ids(MODELS_CFG)
    plan = json.loads(plan_path(book, 1).read_text(encoding="utf-8"))
    assert manifest["cut_hash"] == cut_hash(
        MotionPlan.model_validate(plan), 2, model_ids(MODELS_CFG)
    )


def test_composite_matches_original_inside_mask(book: BookFolder) -> None:
    run(book)
    out = layers_dir(book, 1)
    entry = json.loads((out / MANIFEST).read_text(encoding="utf-8"))["layers"][0]
    with Image.open(out / "background.png") as bg, Image.open(out / "square.png") as fg:
        canvas = bg.convert("RGBA")
        canvas.alpha_composite(fg, tuple(entry["offset"]))
    composite = np.asarray(canvas.convert("RGB")).astype(np.float32)
    original = read_rgb(book.illustrations / "page_01.png")
    x0, y0, x1, y1 = SQUARE
    diff = np.abs(composite[y0:y1, x0:x1] - original[y0:y1, x0:x1]).mean() / 255
    assert diff <= 2 / 255


def test_background_unchanged_outside_and_filled_inside(book: BookFolder) -> None:
    run(book)
    background = read_rgb(layers_dir(book, 1) / "background.png")
    original = read_rgb(book.illustrations / "page_01.png")
    mask = np.zeros((H, W), bool)
    x0, y0, x1, y1 = SQUARE
    mask[y0:y1, x0:x1] = True
    grown = dilate(mask, 2)
    assert np.array_equal(background[~grown], original[~grown])
    inside = background[mask]
    assert (inside.sum(axis=1) > 0).all()  # not black
    assert not np.array_equal(inside, original[mask])  # the red is gone


# -- AC-4.4, AC-4.5, interrupted writes: cache -------------------------------------------------------


def test_second_run_loads_no_model_and_changes_nothing(book: BookFolder) -> None:
    run(book)
    before = snapshot(layers_dir(book, 1))
    results = run(book, load_models=no_models)
    assert [r.status for r in results if r.page_number == 1] == ["cached"]
    assert snapshot(layers_dir(book, 1)) == before


def test_force_recuts_bit_exact(book: BookFolder) -> None:
    run(book)
    before = {k: v[1] for k, v in snapshot(layers_dir(book, 1)).items()}
    results = run(book, force=True)
    assert [r.status for r in results if r.page_number == 1] == ["cut"]
    assert {k: v[1] for k, v in snapshot(layers_dir(book, 1)).items()} == before


def test_editing_a_motion_or_pivot_does_not_recut(book: BookFolder) -> None:
    run(book)
    path = plan_path(book, 1)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["layers"][0]["motions"][0]["amplitude"] = 3.0
    data["layers"][0]["depth"] = 0.9
    data["layers"][0]["pivot"] = [30, 30]
    path.write_text(json.dumps(data), encoding="utf-8")
    results = run(book, load_models=no_models)
    assert [r.status for r in results if r.page_number == 1] == ["cached"]


def _set_z(folder: BookFolder, zs: dict[str, int]) -> None:
    path = plan_path(folder, 1)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["layers"]:
        entry["z"] = zs.get(entry["name"], entry["z"])
    path.write_text(json.dumps(data), encoding="utf-8")


def test_swapping_z_of_overlapping_layers_recuts(book: BookFolder) -> None:
    # occlusion gives the shared pixels to the front layer, so the old cut has stale holes
    write_plan(
        book,
        layers=[
            layer("back", box=(10, 10, 40, 40), z=1),
            layer("front", box=(30, 20, 50, 30), z=2),
        ],
    )
    run(book)
    _set_z(book, {"back": 2, "front": 1})
    calls: list = []
    results = run(book, load_models=fake_models(calls))
    assert [r.status for r in results if r.page_number == 1] == ["cut"]
    assert calls == [1]


def test_changing_z_of_a_non_overlapping_layer_stays_cached(book: BookFolder) -> None:
    write_plan(
        book,
        layers=[layer("left", box=(2, 2, 20, 20), z=1), layer("right", box=(40, 2, 60, 20), z=2)],
    )
    run(book)
    _set_z(book, {"left": 7})
    results = run(book, load_models=no_models)
    assert [r.status for r in results if r.page_number == 1] == ["cached"]


def test_editing_a_box_recuts(book: BookFolder) -> None:
    run(book)
    write_plan(book, layers=[layer(box=(18, 10, 38, 30))])
    calls: list = []
    results = run(book, load_models=fake_models(calls))
    assert [r.status for r in results if r.page_number == 1] == ["cut"]
    assert calls == [1]


def test_missing_manifest_means_recut(book: BookFolder) -> None:
    run(book)
    (layers_dir(book, 1) / MANIFEST).unlink()
    results = run(book)
    assert [r.status for r in results if r.page_number == 1] == ["cut"]


def test_missing_layer_file_means_recut(book: BookFolder) -> None:
    run(book)
    (layers_dir(book, 1) / "square.png").unlink()
    results = run(book)
    assert [r.status for r in results if r.page_number == 1] == ["cut"]


def test_models_load_once_for_several_pages(book: BookFolder) -> None:
    write_picture(book, 2)
    write_plan(book, 2)
    calls: list = []
    results = run(book, load_models=fake_models(calls))
    assert [r.status for r in results if r.status != "no plan"] == ["cut", "cut"]
    assert calls == [1]


def test_page_filter(book: BookFolder) -> None:
    write_picture(book, 2)
    write_plan(book, 2)
    results = run(book, pages=[2])
    assert [(r.page_number, r.status) for r in results] == [(2, "cut")]
    assert not layers_dir(book, 1).exists()


# -- AC-4.6, AC-4.7: stale and missing plans ----------------------------------------------------------


def test_stale_plan_is_refused_and_others_cut(book: BookFolder) -> None:
    write_picture(book, 2)
    write_plan(book, 2)
    write_picture(book, 1, square=False)  # picture 1 changed after its plan
    lines: list[str] = []
    results = run(book, log=lines.append)
    by_page = {r.page_number: r for r in results}
    assert by_page[1].status == "refused"
    assert "stale plan" in by_page[1].detail
    assert "python -m video_pipeline.motion_plan" in by_page[1].detail
    assert by_page[2].status == "cut"
    assert any("page 01: refused" in line for line in lines)


def test_main_exits_1_on_a_stale_plan(book: BookFolder, capsys) -> None:
    write_picture(book, 1, square=False)
    code = main(["--book", book.slug, "--root", str(book.root)], load_models=fake_models())
    assert code == 1
    assert "1 refused" in capsys.readouterr().out


def test_main_exits_0_when_all_cut(book: BookFolder, capsys) -> None:
    code = main(["--book", book.slug, "--root", str(book.root)], load_models=fake_models())
    assert code == 0
    out = capsys.readouterr().out
    assert "1 cut" in out and "0 cached" in out and "0 failed" in out


def test_main_unknown_book_exits_1(tmp_path: Path, capsys) -> None:
    make_root(tmp_path)
    assert main(["--book", "no-such-book", "--root", str(tmp_path)]) == 1
    assert "error" in capsys.readouterr().err


def test_no_plan_is_listed_and_skipped(book: BookFolder) -> None:
    results = run(book)
    others = [r for r in results if r.page_number != 1]
    assert others and all(r.status == "no plan" for r in others)
    assert not layers_dir(book, 2).exists()


def test_invalid_plan_fails_the_page(book: BookFolder) -> None:
    write_picture(book, 2)
    path = plan_path(book, 2)
    path.write_text("{not json", encoding="utf-8")
    results = run(book)
    by_page = {r.page_number: r for r in results}
    assert by_page[2].status == "failed"
    assert by_page[1].status == "cut"


def test_too_many_points_fails_the_page_naming_the_layer(book: BookFolder) -> None:
    points = [[1 + i, 1] for i in range(13)]  # box (2) + 13 = 15 > 14
    write_plan(book, layers=[layer(include_points=points)])
    results = run(book, load_models=no_models)
    (r,) = [r for r in results if r.page_number == 1]
    assert r.status == "failed"
    assert "square" in r.detail and "14" in r.detail


# -- AC-4.8: models missing ---------------------------------------------------------------------------


def test_models_missing_names_download_and_opens_no_socket(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    def no_network(*a, **kw):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", no_network)
    with pytest.raises(ModelsMissing) as err:
        load_real_models(ROOT, MODELS_CFG)
    assert DOWNLOAD_HINT in str(err.value)
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_cut_book_propagates_models_missing(book: BookFolder, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "empty"))
    with pytest.raises(ModelsMissing):
        cut_book(book, root=book.root, log=lambda line: None)
    assert not (layers_dir(book, 1) / MANIFEST).exists()


def test_main_exits_1_when_models_missing(book: BookFolder, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "empty"))
    assert main(["--book", book.slug, "--root", str(book.root)]) == 1
    assert DOWNLOAD_HINT in capsys.readouterr().err


def _fake_models_dir(base: Path, *, without_prompt_weights: bool = False) -> None:
    """Every file a complete download has (empty placeholders); optionally leave out the prompt
    encoder's weights, as a download stopped half-way would."""
    seg, fill = MODELS_CFG["segmenter"], MODELS_CFG["fill"]
    files = [model_path(base, fill) / fill["file"]]
    for key in ("encoder", "prompt_encoder", "decoder"):
        package = model_path(base, seg) / seg[key]
        data = package / "Data" / "com.apple.CoreML"
        files += [package / "Manifest.json", data / "model.mlmodel"]
        if not (without_prompt_weights and key == "prompt_encoder"):
            files.append(data / "weights" / "weight.bin")
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def test_half_downloaded_package_without_weights_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "models"))
    _fake_models_dir(tmp_path / "models", without_prompt_weights=True)
    with pytest.raises(ModelsMissing) as err:
        load_real_models(ROOT, MODELS_CFG)
    assert "weight.bin" in str(err.value) and DOWNLOAD_HINT in str(err.value)
    assert MODELS_CFG["segmenter"]["prompt_encoder"] in str(err.value)


def test_main_exits_1_with_a_message_when_a_model_fails_to_load(
    book: BookFolder, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path / "models"))
    _fake_models_dir(tmp_path / "models")

    class Broken:
        def __init__(self, base, cfg):
            raise RuntimeError("Error reading protobuf spec. validator error: bad file")

    monkeypatch.setattr(cut_layers, "SamCoreML", Broken)
    assert main(["--book", book.slug, "--root", str(book.root)]) == 1
    err = capsys.readouterr().err
    assert "could not load the segmenter model" in err
    assert "bad file" in err and DOWNLOAD_HINT in err
    assert "Traceback" not in err


def test_models_dir_from_env_or_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path))
    assert models_dir() == tmp_path
    monkeypatch.delenv("KIDDO_MODELS_DIR")
    assert models_dir() == Path.home() / ".cache" / "kiddo" / "models"


def test_model_path_is_repo_and_short_revision(tmp_path: Path) -> None:
    cfg = MODELS_CFG["segmenter"]
    assert model_path(tmp_path, cfg) == tmp_path / "apple__coreml-sam2.1-small@883f5787eb0b"


def test_download_stores_real_files_and_prints_what(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIDDO_MODELS_DIR", str(tmp_path))
    calls: list[dict] = []

    def fake_snapshot(**kw):
        calls.append(kw)
        return kw["local_dir"]

    lines: list[str] = []
    paths = download(ROOT, snapshot=fake_snapshot, log=lines.append)
    seg, fill = MODELS_CFG["segmenter"], MODELS_CFG["fill"]
    assert [c["repo_id"] for c in calls] == [seg["repo"], fill["repo"]]
    assert [c["revision"] for c in calls] == [seg["revision"], fill["revision"]]
    assert calls[0]["local_dir"] == str(model_path(tmp_path, seg))
    assert calls[0]["allow_patterns"] == [
        f"{seg[k]}/*" for k in ("encoder", "prompt_encoder", "decoder")
    ]
    assert calls[1]["allow_patterns"] == [fill["file"]]
    assert paths == [str(model_path(tmp_path, seg)), str(model_path(tmp_path, fill))]
    text = "\n".join(lines)
    for cfg in (seg, fill):
        for key in ("repo", "revision", "license", "source"):
            assert cfg[key] in text


def test_main_download_subcommand(tmp_path: Path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(cut_layers, "download", lambda root, **kw: seen.append(root) or [])
    assert main(["download", "--root", str(tmp_path)]) == 0
    assert seen == [tmp_path]


# -- AC-4.10: empty masks ------------------------------------------------------------------------------


def test_empty_layer_completes_and_is_named(book: BookFolder) -> None:
    write_plan(book, layers=[layer(), layer("nothing", box=(2, 2, 10, 10), z=2)])
    lines: list[str] = []
    results = run(book, log=lines.append)
    (r,) = [r for r in results if r.page_number == 1]
    assert r.status == "cut" and r.empty == ("nothing",)
    manifest = json.loads((layers_dir(book, 1) / MANIFEST).read_text(encoding="utf-8"))
    entries = {e["name"]: e for e in manifest["layers"]}
    assert entries["nothing"]["empty"] is True and entries["nothing"]["file"] is None
    assert not (layers_dir(book, 1) / "nothing.png").exists()
    assert any("nothing" in line and "empty" in line for line in lines)


def test_all_layers_empty_keeps_the_picture_as_background(book: BookFolder) -> None:
    write_plan(book, layers=[layer("nothing", box=(2, 2, 10, 10))])
    run(book)
    background = read_rgb(layers_dir(book, 1) / "background.png")
    assert np.array_equal(background, read_rgb(book.illustrations / "page_01.png"))


# -- occlusion -----------------------------------------------------------------------------------------


def test_occlude_gives_each_pixel_to_the_front_layer() -> None:
    back = np.zeros((10, 10), bool)
    back[2:8, 2:8] = True
    front = np.zeros((10, 10), bool)
    front[4:10, 4:10] = True
    out_back, out_front = occlude([back, front], [1, 2])
    assert np.array_equal(out_front, front)
    assert np.array_equal(out_back, back & ~front)
    # order in the list does not matter, only z
    out_front2, out_back2 = occlude([front, back], [2, 1])
    assert np.array_equal(out_back2, out_back) and np.array_equal(out_front2, out_front)


def test_occlude_equal_z_keeps_both() -> None:
    a = np.ones((4, 4), bool)
    b = np.ones((4, 4), bool)
    out = occlude([a, b], [1, 1])
    assert all(m.all() for m in out)


def test_overlapping_layers_are_cut_front_most(book: BookFolder) -> None:
    write_plan(
        book,
        layers=[
            layer("back", box=(10, 10, 40, 40), z=1),
            layer("front", box=(30, 20, 50, 30), z=2),
        ],
    )
    run(book)
    out = layers_dir(book, 1)
    entries = {e["name"]: e for e in json.loads((out / MANIFEST).read_text())["layers"]}
    with Image.open(out / "back.png") as im:
        alpha = np.asarray(im)[..., 3]
    bx, by = entries["back"]["offset"]
    # a pixel deep inside the front layer, away from the feather, is not in the back layer
    assert alpha[25 - by, 38 - bx] == 0
    assert alpha[15 - by, 15 - bx] == 255


# -- cut hash (FR-9) -----------------------------------------------------------------------------------


def _plan(**over) -> MotionPlan:
    data = {
        "page_number": 1,
        "picture": {"file": "page_01.png", "sha256": "ab" * 32, "width": W, "height": H},
        "layers": [layer()],
    }
    data.update(over)
    return MotionPlan.model_validate(data)


def test_cut_hash_changes_with_cut_inputs_only() -> None:
    ids = model_ids(MODELS_CFG)
    base = cut_hash(_plan(), 2, ids)
    assert cut_hash(_plan(), 2, ids) == base
    other_pic = {"file": "page_01.png", "sha256": "cd" * 32, "width": W, "height": H}
    assert cut_hash(_plan(picture=other_pic), 2, ids) != base
    assert cut_hash(_plan(layers=[layer(box=(1, 1, 30, 30))]), 2, ids) != base
    assert cut_hash(_plan(layers=[layer(include_points=[[25, 20]])]), 2, ids) != base
    assert cut_hash(_plan(layers=[layer(exclude_points=[[25, 20]])]), 2, ids) != base
    assert cut_hash(_plan(), 3, ids) != base
    assert cut_hash(_plan(), 2, {**ids, "segmenter": "x@y:CPU_ONLY"}) != base
    moved = layer(motions=[{"type": "bob", "amplitude": 0.01, "period_s": 3.0}], z=9, depth=0.1)
    assert cut_hash(_plan(layers=[moved]), 2, ids) == base  # a lone layer's z occludes nothing
    back, front = (
        layer("back", box=(10, 10, 40, 40), z=1),
        layer("front", box=(30, 20, 50, 30), z=2),
    )
    stacked = cut_hash(_plan(layers=[back, front]), 2, ids)
    swapped = cut_hash(_plan(layers=[{**back, "z": 2}, {**front, "z": 1}]), 2, ids)
    assert swapped != stacked


def test_model_ids_include_compute_unit_and_provider() -> None:
    ids = model_ids(MODELS_CFG)
    seg, fill = MODELS_CFG["segmenter"], MODELS_CFG["fill"]
    assert ids["segmenter"] == f"{seg['repo']}@{seg['revision']}:CPU_AND_GPU"
    assert ids["fill"] == f"{fill['repo']}@{fill['revision']}:CPUExecutionProvider"
    changed = json.loads(json.dumps(MODELS_CFG))
    changed["segmenter"]["postprocess"]["clean"]["close_px"] = 9
    assert model_ids(changed)["cut_settings"] != ids["cut_settings"]
    assert model_ids(changed)["segmenter"] == ids["segmenter"]


# -- pure helpers for the real adapters ----------------------------------------------------------------


def test_compute_unit_refuses_the_neural_engine() -> None:
    assert compute_unit_name({"compute_units": "CPU_AND_GPU"}) == "CPU_AND_GPU"
    assert compute_unit_name({"compute_units": "CPU_ONLY"}) == "CPU_ONLY"
    for bad in ("ALL", "CPU_AND_NE", "BOGUS"):
        with pytest.raises(SegmenterError, match=bad):
            compute_unit_name({"compute_units": bad})


def test_check_embedding_rejects_overflowed_encoder() -> None:
    check_embedding(np.array([-2.7, 2.4], np.float32), "CPU_AND_GPU")
    with pytest.raises(SegmenterError, match="CPU_AND_GPU"):
        check_embedding(np.array([-5780.0, 4392.0], np.float32), "CPU_AND_GPU")
    with pytest.raises(SegmenterError):
        check_embedding(np.array([np.nan], np.float32), "CPU_AND_GPU")


def test_prompt_points_scale_x_and_y_separately() -> None:
    lay = Layer.model_validate(
        layer(box=(100, 50, 300, 150), include_points=[[200, 100]], exclude_points=[[10, 20]])
    )
    points, labels = prompt_points(lay, 2048, 512, size=1024, max_points=14)
    assert points.dtype == np.float32 and points.shape == (1, 4, 2)
    assert labels.dtype == np.int32 and labels.tolist() == [[2, 3, 1, 0]]
    assert points[0].tolist() == [[50.0, 100.0], [150.0, 300.0], [100.0, 200.0], [5.0, 40.0]]


def test_prompt_points_over_the_limit_names_the_layer() -> None:
    lay = Layer.model_validate(layer(include_points=[[1, 1]] * 13))
    with pytest.raises(ValueError, match="square"):
        prompt_points(lay, W, H, size=1024, max_points=14)
    lay_ok = Layer.model_validate(layer(include_points=[[1, 1]] * 12))
    assert prompt_points(lay_ok, W, H, size=1024, max_points=14)[0].shape == (1, 14, 2)


def test_select_mask_takes_largest_near_best() -> None:
    lows = np.full((3, 4, 4), -1.0, np.float32)
    lows[0, :1, :1] = 1.0  # tiny, best score
    lows[1, :3, :3] = 1.0  # bigger, score within 0.05
    lows[2] = 1.0  # biggest, score far below
    assert select_mask(lows, np.array([0.94, 0.92, 0.5]), margin=0.05) == 1
    assert select_mask(lows, np.array([0.94, 0.80, 0.5]), margin=0.05) == 0


def test_upsample_logits_bilinear_threshold_zero() -> None:
    low = np.full((4, 4), -5.0, np.float32)
    low[:, 2:] = 5.0
    mask = upsample_logits(low, (8, 6))
    assert mask.shape == (6, 8) and mask.dtype == bool
    assert not mask[:, 0].any() and mask[:, -1].all()


def test_dilate_is_a_square_max_filter() -> None:
    m = np.zeros((9, 9), bool)
    m[4, 4] = True
    d = dilate(m, 2)
    assert d.sum() == 25 and d[2:7, 2:7].all()
    assert np.array_equal(dilate(m, 0), m)


def test_fill_holes_fills_enclosed_only() -> None:
    m = np.zeros((7, 7), bool)
    m[1:6, 1:6] = True
    m[3, 3] = False  # enclosed hole
    m[1, 3] = False  # notch open to the outside
    out = fill_holes(m)
    assert out[3, 3] and not out[1, 3]
    assert not out[0].any()


def test_clean_mask_closes_gaps_and_fills_holes() -> None:
    m = np.zeros((40, 40), bool)
    m[10:30, 10:19] = True
    m[10:30, 21:30] = True  # a 2 px gap down the middle
    m[15, 12] = False  # a speck hole
    out = clean_mask(m, close_px=2)
    assert out[10:30, 10:30].all()
    assert not out[:8].any() and not out[32:].any()


def test_clean_mask_empty_stays_empty() -> None:
    assert not clean_mask(np.zeros((8, 8), bool), close_px=3).any()


def test_hole_regions_and_merge() -> None:
    hole = np.zeros((200, 300), bool)
    hole[10:20, 10:20] = True
    hole[150:160, 250:280] = True
    regions = hole_regions(hole)
    assert sorted(regions) == [(10, 10, 20, 20), (250, 150, 280, 160)]
    assert sorted(merge_regions(regions, gap=10)) == sorted(regions)
    assert merge_regions(regions, gap=300) == [(10, 10, 280, 160)]
    assert hole_regions(np.zeros((5, 5), bool)) == []


def test_crop_box_square_min_size_clipped() -> None:
    # a small region in a 1024 square picture: a 512 square around it, clipped to the picture
    assert crop_box((900, 900, 950, 950), 1024, 1024, margin=96, min_crop=512) == (
        512,
        512,
        1024,
        1024,
    )
    assert crop_box((400, 400, 500, 500), 1024, 1024, margin=96, min_crop=512) == (
        194,
        194,
        706,
        706,
    )
    # a big region: the whole picture, never bigger
    assert crop_box((50, 50, 1000, 1000), 1024, 1024, margin=96, min_crop=512) == (0, 0, 1024, 1024)
    # a picture smaller than min_crop: the whole picture
    assert crop_box((5, 5, 10, 10), 64, 48, margin=96, min_crop=512) == (0, 0, 48, 48)


def test_crop_box_wide_region_in_wide_picture_covers_the_region() -> None:
    x0, y0, x1, y1 = crop_box((100, 400, 1800, 600), 1920, 1080, margin=96, min_crop=512)
    assert x0 <= 100 and x1 >= 1800 and y0 <= 400 and y1 >= 600
    assert x0 >= 0 and x1 <= 1920 and y0 >= 0 and y1 <= 1080


# -- real models (marker `models`) ---------------------------------------------------------------------


PILOT = "the-snail-mail-express"


@pytest.mark.models
def test_real_cut_pilot_page_one(tmp_path: Path) -> None:
    src = BookFolder(PILOT, ROOT)
    folder = make_root(tmp_path, src.book_json, slug=PILOT)
    folder.illustrations.mkdir(parents=True)
    shutil.copy(src.illustrations / "page_01.png", folder.illustrations / "page_01.png")
    plan_file = plan_path(folder, 1)
    plan_file.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "plan_page_01.json", plan_file)
    plan = MotionPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    assert plan.picture.sha256 == picture_identity(folder.illustrations / "page_01.png").sha256

    lines: list[str] = []
    first = cut_book(folder, root=folder.root, pages=[1], log=lines.append)
    assert [r.status for r in first] == ["cut"], lines
    seconds = float(first[0].detail.split()[0])
    out = layers_dir(folder, 1)
    files = {p.name: p.read_bytes() for p in out.iterdir()}
    manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    for entry in manifest["layers"]:
        if entry["empty"]:
            continue
        box = next(lay.box for lay in plan.layers if lay.name == entry["name"])
        with Image.open(out / entry["file"]) as im:
            alpha = np.asarray(im)[..., 3] > 127
        x, y = entry["offset"]
        full = np.zeros((plan.picture.height, plan.picture.width), bool)
        full[y : y + alpha.shape[0], x : x + alpha.shape[1]] = alpha
        bx0, by0, bx1, by1 = box
        ys, xs = np.nonzero(full[by0:by1, bx0:bx1])
        cover = (np.ptp(xs) + 1) * (np.ptp(ys) + 1) / ((bx1 - bx0) * (by1 - by0))
        assert cover >= 0.5, (entry["name"], cover)

    again = cut_book(folder, root=folder.root, pages=[1], force=True, log=lines.append)
    assert [r.status for r in again] == ["cut"]
    assert {p.name: p.read_bytes() for p in out.iterdir()} == files  # bit-exact
    peak_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3  # bytes on macOS
    print(f"\nreal cut: {seconds:.2f} s first page, {again[0].detail} again, peak {peak_gb:.2f} GB")
    assert seconds <= 10
    assert peak_gb <= 4


def test_limit_regions_merges_the_cheapest_pair_until_under_the_cap() -> None:
    boxes = [(0, 0, 10, 10), (20, 0, 30, 10), (500, 500, 510, 510)]
    out = cut_layers.limit_regions(boxes, 2)
    assert out == [(0, 0, 30, 10), (500, 500, 510, 510)]


def test_limit_regions_keeps_boxes_already_under_the_cap() -> None:
    boxes = [(0, 0, 10, 10), (500, 500, 510, 510)]
    assert cut_layers.limit_regions(boxes, 3) == boxes
    assert cut_layers.limit_regions(boxes, 1) == [(0, 0, 510, 510)]
