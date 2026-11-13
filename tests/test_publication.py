"""Spec 023 Phase 1: design config, colour maths, ramp, crop and words fitting."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from video_pipeline import publication as pub
from video_pipeline import publication_render as pr
from video_pipeline.library import PROJECT_ROOT


@pytest.fixture(scope="module")
def design() -> dict:
    return pub.load_design()


# ---------------------------------------------------------------- config


def test_load_design_reads_project_config(design: dict) -> None:
    assert design["colors"]["dusk"] == [0.84, 0.06, 300]
    assert design["ramp"]["night_floor"] == 0.85


def test_load_design_missing_key_names_file_and_key(tmp_path: Path) -> None:
    data = json.loads((PROJECT_ROOT / pub.CONFIG_PATH).read_text())
    del data["colors"]["dusk"]
    (tmp_path / "config").mkdir()
    (tmp_path / pub.CONFIG_PATH).write_text(json.dumps(data))
    with pytest.raises(pub.PublicationError) as err:
        pub.load_design(tmp_path)
    assert "publication.json" in str(err.value)
    assert "colors.dusk" in str(err.value)


def test_load_design_missing_section(tmp_path: Path) -> None:
    data = json.loads((PROJECT_ROOT / pub.CONFIG_PATH).read_text())
    del data["ramp"]
    (tmp_path / "config").mkdir()
    (tmp_path / pub.CONFIG_PATH).write_text(json.dumps(data))
    with pytest.raises(pub.PublicationError, match="ramp"):
        pub.load_design(tmp_path)


def test_load_design_missing_and_bad_file(tmp_path: Path) -> None:
    with pytest.raises(pub.PublicationError, match="publication.json"):
        pub.load_design(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / pub.CONFIG_PATH).write_text("{nope")
    with pytest.raises(pub.PublicationError, match="publication.json"):
        pub.load_design(tmp_path)


# ---------------------------------------------------------------- colour


def test_oklch_white_and_black() -> None:
    assert pub.oklch_to_srgb([1, 0, 0]) == (255, 255, 255)
    assert pub.oklch_to_srgb([0, 0, 0]) == (0, 0, 0)


def test_oklch_night_matches_independent_reference() -> None:
    # Reference computed with the forward sRGB -> OKLab matrices: (25, 27, 59) <-> oklch(0.2393
    # 0.0604 278.1), i.e. oklch(0.24 0.06 278). Also round-trips red.
    for got, want in zip(pub.oklch_to_srgb([0.24, 0.06, 278]), (25, 27, 59), strict=True):
        assert abs(got - want) <= 1
    assert pub.oklch_to_srgb([0.628, 0.2577, 29.23]) == (255, 0, 0)


def test_mix_oklch_linear_and_shorter_hue_arc() -> None:
    assert pub.mix_oklch([0.2, 0.1, 350], [0.6, 0.3, 10], 0.5) == pytest.approx((0.4, 0.2, 0.0))
    mixed = pub.mix_oklch([0.5, 0.1, 10], [0.5, 0.1, 350], 0.5)
    assert mixed[2] % 360 == pytest.approx(0.0)
    assert pub.mix_oklch([0.5, 0.1, 100], [0.5, 0.1, 200], 0.25)[2] == pytest.approx(125)


def test_contrast_ratio_extremes() -> None:
    assert pub.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert pub.contrast_ratio((9, 9, 9), (9, 9, 9)) == pytest.approx(1.0)
    assert pub.relative_luminance((255, 255, 255)) == pytest.approx(1.0)


# ---------------------------------------------------------------- ramp and tone


def test_ramp_first_page_is_zero(design: dict) -> None:
    assert pub.ramp(1, 20, design) == 0
    assert pub.ramp(1, 1, design) == 0


@pytest.mark.parametrize("total", [1, 2, 5, 10, 20])
def test_ramp_non_decreasing_and_night_floor(design: dict, total: int) -> None:
    values = [pub.ramp(n, total, design) for n in range(1, total + 1)]
    assert values == sorted(values)
    if total > 1:
        assert values[-1] >= 0.85
    assert not any(0.65 <= v < 0.85 for v in values)


def test_ramp_matches_formula(design: dict) -> None:
    assert pub.ramp(6, 11, design) == pytest.approx(0.5**2 * 0.9, abs=1e-3)


def test_page_tone_day_and_night(design: dict) -> None:
    day = pub.page_tone(0.0, design)
    assert day.night is False
    assert day.ink == pub.oklch_to_srgb(design["colors"]["ink"])
    assert day.ink_soft == pub.oklch_to_srgb(design["colors"]["ink_soft"])
    night = pub.page_tone(0.9, design)
    assert night.night is True
    assert night.ink == pub.oklch_to_srgb(design["colors"]["moon"])
    assert night.ink_soft == pub.oklch_to_srgb(design["colors"]["moon_soft"])
    # between the threshold and the floor the page is raised to night
    assert pub.page_tone(0.7, design).night is True
    assert pub.page_tone(0.7, design).scrim == pub.page_tone(0.85, design).scrim


def test_page_tone_scrim_runs_day_dusk_night(design: dict) -> None:
    c = design["colors"]
    assert pub.page_tone(0.0, design).scrim == pub.oklch_to_srgb(c["day"])
    assert pub.page_tone(0.5, design).scrim == pub.oklch_to_srgb(c["dusk"])
    assert pub.page_tone(1.0, design).scrim == pub.oklch_to_srgb(c["night"])


def test_page_tone_below_night_runs_day_dusk_then_dusk_night(design: dict) -> None:
    """AC-2.2: day->dusk for t < 0.5, dusk->night for 0.5 <= t < threshold, dark ink."""
    c = design["colors"]
    ink = pub.oklch_to_srgb(c["ink"])
    early = pub.page_tone(0.25, design)
    assert early.scrim == pub.oklch_to_srgb(pub.mix_oklch(c["day"], c["dusk"], 0.5))
    late = pub.page_tone(0.6, design)
    assert late.scrim == pub.oklch_to_srgb(pub.mix_oklch(c["dusk"], c["night"], 0.2))
    assert not early.night and not late.night
    assert early.ink == late.ink == ink


def test_contrast_of_ink_on_scrim_for_every_tone(design: dict) -> None:
    for i in range(101):
        t = i / 100
        tone = pub.page_tone(t, design)
        assert pub.contrast_ratio(tone.ink, tone.scrim) >= 4.5, t


# ---------------------------------------------------------------- crop


def test_crop_box_square_from_wide_picture() -> None:
    x0, y0, x1, y1 = pub.crop_box(1672, 941, 100, 100, 0.45)
    assert (x1 - x0, y1 - y0) == (941, 941)
    assert x0 == round(0.45 * 1672 - 941 / 2)


def test_crop_box_clamps_at_edges() -> None:
    assert pub.crop_box(1672, 941, 100, 100, 0.0) == (0, 0, 941, 941)
    assert pub.crop_box(1672, 941, 100, 100, 1.0) == (1672 - 941, 0, 1672, 941)


def test_crop_box_tall_destination_uses_focus_y() -> None:
    x0, y0, x1, y1 = pub.crop_box(1000, 1000, 50, 100, 0.5, 0.0)
    assert (x0, y0, x1, y1) == (250, 0, 750, 1000)
    x0, y0, x1, y1 = pub.crop_box(1000, 500, 100, 25, 0.5, 1.0)
    assert (x0, y0, x1, y1) == (0, 250, 1000, 500)


# ---------------------------------------------------------------- subject and focus


def _plan(sha: str = "abc", boxes: list[list[int]] | None = None) -> dict:
    boxes = boxes if boxes is not None else [[100, 100, 300, 400], [500, 200, 900, 800]]
    return {
        "picture": {"sha256": sha, "width": 1000, "height": 1000},
        "layers": [{"name": f"l{i}", "box": b} for i, b in enumerate(boxes)],
    }


def test_subject_box_union_of_two_largest() -> None:
    boxes = [[0, 0, 10, 10], [100, 100, 300, 400], [500, 200, 900, 800]]
    box = pub.subject_box(_plan("abc", boxes), "abc")
    assert box == pytest.approx((0.1, 0.1, 0.9, 0.8))


def test_subject_box_single_layer() -> None:
    box = pub.subject_box(_plan("abc", [[100, 200, 300, 400]]), "abc")
    assert box == pytest.approx((0.1, 0.2, 0.3, 0.4))


@pytest.mark.parametrize(
    "plan",
    [
        None,
        {},
        _plan("other"),
        _plan("abc", []),
        {"picture": {"sha256": "abc"}, "layers": [{"box": [1, 2, 3, 4]}]},
        {"picture": {"sha256": "abc", "width": 0, "height": 10}, "layers": [{"box": [1, 2, 3, 4]}]},
        {"picture": {"sha256": "abc", "width": 10, "height": 10}, "layers": [{"box": "x"}]},
        {"picture": {"sha256": "abc", "width": 10, "height": 10}, "layers": "nope"},
        {"picture": "bad", "layers": []},
    ],
)
def test_subject_box_none_for_missing_stale_or_malformed(plan: object) -> None:
    assert pub.subject_box(plan, "abc") is None  # type: ignore[arg-type]


def test_choose_focus_side_with_plan_subject_low_puts_words_top() -> None:
    plan = _plan("abc", [[200, 600, 500, 950], [550, 650, 800, 900]])
    page = {"layout": "image_top_text_bottom"}
    fx, side = pub.choose_focus_side(page, plan, "abc")
    assert fx == pytest.approx(0.5)
    assert side == "top"


def test_choose_focus_side_with_plan_subject_high_puts_words_bottom() -> None:
    plan = _plan("abc", [[200, 50, 500, 350]])
    fx, side = pub.choose_focus_side({"layout": "full_bleed_card_top_left"}, plan, "abc")
    assert fx == pytest.approx(0.35)
    assert side == "bottom"


def test_choose_focus_side_tie_uses_layout_rule() -> None:
    plan = _plan("abc", [[100, 0, 300, 1000]])  # spans both bands equally
    assert pub.choose_focus_side({"layout": "full_bleed_card_top_left"}, plan, "abc")[1] == "top"
    assert pub.choose_focus_side({"layout": "image_top_text_bottom"}, plan, "abc")[1] == "bottom"


def test_choose_focus_side_stale_plan_falls_back_to_layout() -> None:
    plan = _plan("old")
    assert pub.choose_focus_side({"layout": "full_bleed_card_top_left"}, plan, "new") == (
        0.5,
        "top",
    )
    assert pub.choose_focus_side({"layout": "image_top_text_bottom"}, plan, "new") == (
        0.5,
        "bottom",
    )
    assert pub.choose_focus_side({}, None, None) == (0.5, "bottom")


def test_choose_focus_side_print_layout_overrides() -> None:
    plan = _plan("abc", [[200, 600, 500, 950]])
    page = {"layout": "image_top_text_bottom", "print_layout": {"focus_x": 0.8, "words": "bottom"}}
    assert pub.choose_focus_side(page, plan, "abc") == (0.8, "bottom")
    only_x = {"layout": "full_bleed_card_top_left", "print_layout": {"focus_x": 0.2}}
    assert pub.choose_focus_side(only_x, None, None) == (0.2, "top")
    bad = {"layout": "image_top_text_bottom", "print_layout": {"focus_x": 3, "words": "left"}}
    assert pub.choose_focus_side(bad, None, None) == (0.5, "bottom")


# ---------------------------------------------------------------- wrapping and fitting


def _w(text: str) -> float:
    return float(len(text))


def test_wrap_lines_greedy_and_long_word() -> None:
    assert pub.wrap_lines("aa bb cc dd", 5, _w) == ["aa bb", "cc dd"]
    assert pub.wrap_lines("a verylongword b", 5, _w) == ["a", "verylongword", "b"]
    assert pub.wrap_lines("", 5, _w) == []


U = 8.5 * 72 / 100  # 1% of the trim width in points
SENTENCE = "Jack giggled as the little car rolled across the moonlit floor and back again. "


@pytest.fixture(scope="module")
def fonts(design: dict) -> None:
    pr.register_fonts(design)


def _fit(design: dict, title: str, text: str, label: str = "page 4") -> pub.WordsFit:
    p, t = design["page"], design["type"]
    return pub.fit_words(
        title,
        text,
        measure=pr.measure,
        base_size=t["story_u"] * U,
        title_size=t["title_u"] * U,
        leading=t["story_leading"],
        title_gap=p["title_gap_u"] * U,
        column_chars=p["column_chars"],
        full_column=(100 - 2 * p["safe_u"] - p["folio_gap_u"]) * U,
        max_height=p["words_max"] * 8.5 * 72,
        floor=p["size_floor"],
        label=label,
    )


def test_fit_words_short_text_keeps_base_size_narrow_column(design: dict, fonts: None) -> None:
    text = ("Jack giggled and the car rolled away over the soft moonlit rug. " * 2)[:130].strip()
    fit = _fit(design, "A Little Car", text)
    assert fit.scale == 1.0
    assert fit.size == pytest.approx(3.3 * U)
    narrow = pr.measure("x" * 32, "body", 3.3 * U)
    assert fit.column == pytest.approx(narrow)
    assert fit.title_lines == ["A Little Car"]
    assert fit.text_lines
    assert fit.height <= 0.55 * 612


def test_fit_words_long_text_shrinks_within_floor(design: dict, fonts: None) -> None:
    text = (SENTENCE * 9)[:650].strip()
    fit = _fit(design, "A Little Car", text)
    assert 0.6 <= fit.scale <= 1.0
    assert fit.height <= 0.55 * 612
    assert fit.size == pytest.approx(3.3 * U * fit.scale)
    assert " ".join(fit.text_lines).split() == text.split()


def test_fit_words_height_formula(design: dict, fonts: None) -> None:
    fit = _fit(design, "A Little Car", "Short words here.")
    expect = (
        len(fit.title_lines) * fit.title_size * 1.25
        + 1.6 * U * fit.scale * (1 if fit.title_lines else 0)
        + len(fit.text_lines) * fit.size * 1.42
    )
    assert fit.height == pytest.approx(expect)


def test_fit_words_empty_title_has_no_gap(design: dict, fonts: None) -> None:
    fit = _fit(design, "", "Short words here.")
    assert fit.title_lines == []
    assert fit.height == pytest.approx(len(fit.text_lines) * fit.size * 1.42)


def test_fit_words_steps_widen_then_shrink() -> None:
    # fake measure: width = chars * size
    def measure(text: str, role: str, size: float) -> float:
        return len(text) * size

    kwargs = dict(
        measure=measure,
        base_size=10.0,
        title_size=8.0,
        leading=1.0,
        title_gap=0.0,
        column_chars=10,
        full_column=400.0,
        floor=0.5,
        label="page 2",
    )
    text = " ".join(["word"] * 40)  # 199 chars; narrow column holds 2 words per line
    narrow = pub.fit_words("", text, max_height=1e9, **kwargs)
    assert narrow.scale == 1.0 and narrow.column == 100.0
    wide = pub.fit_words("", text, max_height=60.0, **kwargs)  # 5 lines at the full column
    assert wide.scale == 1.0 and wide.column == 400.0
    small = pub.fit_words("", text, max_height=30.0, **kwargs)
    assert small.column == 400.0
    assert 0.5 <= small.scale < 1.0
    assert small.height <= 30.0


def test_fit_words_includes_floor_exactly() -> None:
    def measure(text: str, role: str, size: float) -> float:
        return len(text) * size

    # one word per line at any size (column too narrow), height = lines * size
    fit = pub.fit_words(
        "",
        "ab " * 10,
        measure=measure,
        base_size=10.0,
        title_size=5.0,
        leading=1.0,
        title_gap=0.0,
        column_chars=1,
        full_column=1.0,
        max_height=60.001,
        floor=0.6,
        label="page 1",
    )
    assert fit.scale == pytest.approx(0.6)
    assert fit.height == pytest.approx(60.0)


def test_fit_words_absurd_text_raises_naming_label(design: dict, fonts: None) -> None:
    with pytest.raises(pub.PublicationError, match="page 7: words do not fit"):
        _fit(design, "Title", SENTENCE * 80, label="page 7")


# ---------------------------------------------------------------- fonts


def test_font_paths_resolve_to_bundled_files(design: dict) -> None:
    paths = pr.font_paths(design)
    assert set(paths) == {"display", "body", "body_bold"}
    assert all(p.is_file() for p in paths.values())


def test_font_paths_missing_file_names_it(design: dict, tmp_path: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "assets" / "fonts", tmp_path / "assets" / "fonts")
    (tmp_path / "assets" / "fonts" / "Andika-Bold.ttf").unlink()
    with pytest.raises(pub.PublicationError, match="font file not found:.*Andika-Bold.ttf"):
        pr.font_paths(design, tmp_path)


def test_register_fonts_is_idempotent(design: dict) -> None:
    from reportlab.pdfbase import pdfmetrics

    names = pr.register_fonts(design)
    assert names == pr.FONT_NAMES
    assert pr.register_fonts(design) == pr.FONT_NAMES
    assert set(pr.FONT_NAMES.values()) <= set(pdfmetrics.getRegisteredFontNames())
    assert pr.measure("hello", "body", 10) > pr.measure("hi", "body", 10)
