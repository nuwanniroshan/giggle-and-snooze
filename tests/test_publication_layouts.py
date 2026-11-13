"""Spec 023 Phase 2: page layouts (pure geometry) and their raster renderers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.helpers import PILOT_BOOK
from video_pipeline import publication as pub
from video_pipeline import publication_render as pr

LONG_TEXT = (
    "Clara peered through her brass magnifying glass at the muddy lane and spotted a tiny "
    "red polka-dot thread caught on a bramble. Barnaby wiggled his antennae, curious. "
    "Together they followed the thread past the mossy stones, around the tea-stop birch and "
    "over the little bridge where Fergus the frog was humming. The thread led them, at last, "
    "to Millie's rock, where the ant was sighing over a sail with nothing to sew it with. "
    "Clara smiled, opened the mail sack, and held up a silver thimble that sparkled like dew."
)


@pytest.fixture(scope="module")
def design() -> dict:
    d = pub.load_design()
    pr.register_fonts(d)
    return d


def measure(text: str, role: str, size: float) -> float:
    return pr.measure(text, role, size)


def page(n: int = 1, text: str = LONG_TEXT) -> dict:
    return {"page_number": n, "title": "A Thread to Follow", "text": text, "prompt": "a lane"}


def story(design: dict, frame: pub.Frame, side: str, n: int = 1, total: int = 10, text=LONG_TEXT):
    return pub.layout_story(
        design,
        frame,
        page(n, text),
        n=n,
        total=total,
        focus_x=0.4,
        side=side,
        picture=Path("page.png"),
        measure=measure,
    )


def words(layout: pub.PageLayout) -> list[pub.TextRun]:
    return [t for t in layout.texts if t.role in ("body", "body_bold")]


def safe(design: dict, frame: pub.Frame) -> tuple[float, float, float, float]:
    return frame.safe_box(design)


# ---------------------------------------------------------------- frames


def test_digital_and_kdp_frames(design: dict) -> None:
    digital = pub.Frame.digital()
    assert (digital.width, digital.height, digital.trim_x, digital.trim_y) == (612, 612, 0, 0)
    left = pub.Frame.kdp(bleed_left=True)
    right = pub.Frame.kdp(bleed_left=False)
    assert (left.width, left.height) == pytest.approx((621, 630))
    assert (left.trim_x, left.trim_y, right.trim_x, right.trim_y) == (9, 9, 0, 9)
    assert digital.u == pytest.approx(6.12)
    x0, y0, x1, y1 = safe(design, left)
    assert (x0, y0) == pytest.approx((9 + 45.9, 9 + 45.9))
    assert (x1, y1) == pytest.approx((9 + 612 - 45.9, 9 + 612 - 45.9))


# ---------------------------------------------------------------- story pages


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_story_words_sit_inside_the_safe_area_and_column(design: dict, side: str) -> None:
    frame = pub.Frame.digital()
    layout = story(design, frame, side)
    x0, y0, x1, y1 = safe(design, frame)
    column_right = x1 - design["page"]["folio_gap_u"] * frame.u
    runs = words(layout)
    assert runs[0].role == "body_bold" and runs[0].text.startswith("A Thread")
    assert " ".join(r.text for r in runs if r.role == "body") == LONG_TEXT
    for run in runs:
        assert run.x == pytest.approx(x0)
        assert run.x + measure(run.text, run.role, run.size) <= column_right + 0.01
        assert y0 + run.size * 0.7 <= run.y <= y1
    tone = pub.page_tone(pub.ramp(1, 10, design), design)
    assert all(r.color == tone.ink for r in runs)


def test_story_picture_fills_the_whole_page(design: dict) -> None:
    for frame in (pub.Frame.digital(), pub.Frame.kdp(bleed_left=True)):
        layout = story(design, frame, "bottom")
        assert layout.picture == Path("page.png")
        assert layout.picture_box == pytest.approx((0, 0, frame.width, frame.height))
        assert layout.focus_x == 0.4
        assert (layout.width, layout.height) == (frame.width, frame.height)
        assert layout.alt == "a lane"


def test_top_scrim_covers_every_line_and_fades_past_the_block(design: dict) -> None:
    frame = pub.Frame.digital()
    layout = story(design, frame, "top")
    (scrim,) = layout.scrims
    fade = design["page"]["fade_u"] * frame.u
    runs = words(layout)
    assert scrim.side == "top" and scrim.y0 == 0 and scrim.fade == pytest.approx(fade)
    solid_end = scrim.y1 - fade
    for run in runs:
        assert run.y + 0.3 * run.size <= solid_end  # descenders inside the solid part
    last = max(r.y for r in runs)
    assert solid_end - last < 2 * runs[-1].size  # the scrim hugs the words
    assert all(r.y < frame.height * 0.65 for r in runs)  # words take at most 55% of the trim
    tone = pub.page_tone(pub.ramp(1, 10, design), design)
    assert scrim.color == tone.scrim == layout.ground
    assert (scrim.alpha_edge, scrim.alpha_text) == (0.95, 0.88)


def test_bottom_scrim_mirrors_the_top(design: dict) -> None:
    frame = pub.Frame.digital()
    layout = story(design, frame, "bottom")
    (scrim,) = layout.scrims
    fade = design["page"]["fade_u"] * frame.u
    runs = words(layout)
    assert scrim.side == "bottom" and scrim.y1 == frame.height
    solid_start = scrim.y0 + fade
    for run in runs:
        assert run.y - run.size <= frame.height
        assert run.y - run.size >= solid_start - 0.01  # cap tops inside the solid part
    first_top = min(r.y - r.size for r in runs)
    assert first_top - solid_start < 2 * runs[0].size
    assert all(r.y > frame.height * 0.35 for r in runs)


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_folio_sits_at_the_outer_corner_of_the_words(design: dict, side: str) -> None:
    frame = pub.Frame.digital()
    layout = story(design, frame, side, n=7)
    (folio,) = [t for t in layout.texts if t.role == "display"]
    x0, y0, x1, y1 = safe(design, frame)
    assert folio.text == "7" and folio.align == "right"
    assert folio.x == pytest.approx(x1)
    assert folio.size == pytest.approx(design["type"]["folio_u"] * frame.u)
    tone = pub.page_tone(pub.ramp(7, 10, design), design)
    assert folio.color == tone.ink_soft
    if side == "bottom":
        assert folio.y == pytest.approx(y1)
    else:
        assert y0 < folio.y <= y0 + folio.size


def test_kdp_facing_pages_shift_the_words_by_the_bleed(design: dict) -> None:
    left = story(design, pub.Frame.kdp(bleed_left=True), "bottom")
    right = story(design, pub.Frame.kdp(bleed_left=False), "bottom")
    assert words(left)[0].x - words(right)[0].x == pytest.approx(9)
    assert words(left)[0].y == pytest.approx(words(right)[0].y)


def test_night_story_page_uses_light_ink(design: dict) -> None:
    layout = story(design, pub.Frame.digital(), "bottom", n=10, total=10)
    tone = pub.page_tone(pub.ramp(10, 10, design), design)
    assert tone.night
    assert all(r.color == tone.ink for r in words(layout))


def test_story_text_too_long_names_the_page(design: dict) -> None:
    with pytest.raises(pub.PublicationError, match="page 3"):
        story(design, pub.Frame.digital(), "bottom", n=3, text=LONG_TEXT * 4)


# ---------------------------------------------------------------- front and back matter


def inside(design: dict, layout: pub.PageLayout, frame: pub.Frame) -> None:
    x0, y0, x1, y1 = safe(design, frame)
    for run in layout.texts:
        width = measure(run.text, run.role, run.size)
        left = run.x - width if run.align == "right" else run.x
        assert x0 - 0.01 <= left and left + width <= x1 + 0.01, run
        assert y0 <= run.y - 0.7 * run.size and run.y <= y1 + 0.01, run
    for img in layout.images:
        assert x0 - 0.01 <= img.x and img.x + img.w <= x1 + 0.01
        assert y0 - 0.01 <= img.y and img.y + img.h <= y1 + 0.01


def day(design: dict) -> tuple[int, int, int]:
    return pub.oklch_to_srgb(design["colors"]["day"])


def logo() -> pub.Logo:
    return pub.Logo(Path("logo.png"), 733 / 1256)


def test_half_title_has_logo_title_and_bookplate(design: dict) -> None:
    frame = pub.Frame.kdp(bleed_left=False)
    layout = pub.layout_half_title(
        design,
        frame,
        title="Jack and the Topsy-Turvy Dream",
        subtitle="A Little Listening, a New Friend",
        bookplate_line="This storybook belongs to:",
        logo=logo(),
        measure=measure,
    )
    assert layout.ground == day(design) and layout.picture is None
    assert layout.images[0].w == pytest.approx(design["logo"]["plain_u"] * frame.u)
    displays = [t for t in layout.texts if t.role == "display"]
    assert " ".join(t.text for t in displays) == "Jack and the Topsy-Turvy Dream"
    assert all(t.color == pub.oklch_to_srgb(design["colors"]["snooze"]) for t in displays)
    texts = [t.text for t in layout.texts]
    assert "This storybook belongs to:" in texts
    assert any("A Little Listening" in t for t in texts)
    assert [s.kind for s in layout.shapes] == ["rect"]
    inside(design, layout, frame)
    # centred vertically
    top = layout.images[0].y
    bottom = max(s.y + s.h for s in layout.shapes)
    assert (top - frame.trim_y) == pytest.approx(frame.trim_y + frame.trim - bottom, abs=1)


def test_half_title_without_logo(design: dict) -> None:
    layout = pub.layout_half_title(
        design,
        pub.Frame.digital(),
        title="T",
        subtitle="",
        bookplate_line="Mine",
        logo=None,
        measure=measure,
    )
    assert layout.images == ()


def test_title_page_is_bottom_aligned(design: dict) -> None:
    frame = pub.Frame.kdp(bleed_left=True)
    lines = [
        "Copyright © 2026.",
        "No part may be copied in any form",
        "without permission.",
        "",
        "ISBN: 979",
    ]
    layout = pub.layout_title(
        design,
        frame,
        dedication="For little listeners and the friends they find.",
        author="Giggle & Snooze",
        copyright_lines=lines,
        measure=measure,
    )
    texts = [t.text for t in layout.texts]
    assert "By Giggle & Snooze" in texts
    assert "Copyright © 2026." in texts
    assert "No part may be copied in any form without permission." in texts  # rejoined
    assert texts[-1] == "ISBN: 979"
    assert layout.ground == day(design)
    x0, y0, x1, y1 = safe(design, frame)
    assert layout.texts[-1].y > y1 - 2 * layout.texts[-1].size
    inside(design, layout, frame)


def test_map_page_contains_the_map(design: dict) -> None:
    frame = pub.Frame.digital()
    layout = pub.layout_map(
        design,
        frame,
        title="Map of Whisperwood Hollow",
        subtitle="The daily route",
        legend=["1. The Great Oak Post Office (Start)     2. Bramble Pond & Millie's Rock"],
        picture=Path("map.png"),
        picture_size=(1600, 1200),
        measure=measure,
    )
    (img,) = layout.images
    assert img.w / img.h == pytest.approx(1600 / 1200, rel=1e-3)
    assert layout.picture is None and layout.ground == day(design)
    inside(design, layout, frame)
    titles = [t for t in layout.texts if t.role == "display"]
    legend = [t for t in layout.texts if t.text.startswith("1.")]
    assert max(t.y for t in titles) < img.y < img.y + img.h < min(t.y for t in legend)


def test_blank_page_is_day_ground_only(design: dict) -> None:
    layout = pub.layout_blank(design, pub.Frame.digital())
    assert layout.ground == day(design)
    assert not (layout.texts or layout.images or layout.shapes or layout.scrims)


def pilot_activity() -> dict:
    return json.loads(PILOT_BOOK.read_text(encoding="utf-8"))["print_extras"]["activity"]


def test_activity_fits_the_pilots_long_content_on_dusk(design: dict) -> None:
    frame = pub.Frame.kdp(bleed_left=True)
    activity = {"questions_title": "Questions", **pilot_activity()}
    layout = pub.layout_activity(design, frame, activity, measure=measure)
    dusk = pub.page_tone(0.5, design)
    assert layout.ground == dusk.scrim
    joined = " ".join(t.text for t in layout.texts)
    assert "The Junior Postmaster's" in joined
    for item in activity["items"]:
        assert item["title"] in joined
    assert "Bedtime Questions for Young Readers" in joined
    assert "1." in [t.text for t in layout.texts]
    inside(design, layout, frame)


def test_activity_too_long_names_the_page(design: dict) -> None:
    activity = {**pilot_activity(), "questions_title": "Q"}
    activity["questions"] = [LONG_TEXT] * 6
    with pytest.raises(pub.PublicationError, match="activity"):
        pub.layout_activity(design, pub.Frame.digital(), activity, measure=measure)


def test_goodnight_message_prefers_the_outro_hook() -> None:
    book = {"metadata": {"dedication": "For you."}}
    assert pub.goodnight_message(book, {"qa": {"outro_hook": "Sleep tight."}}) == "Sleep tight."
    assert pub.goodnight_message(book, None) == "For you."
    assert pub.goodnight_message(book, {"qa": {}}) == "For you."
    assert pub.goodnight_message({"metadata": {}}, None) == ""


def test_goodnight_page_is_night(design: dict) -> None:
    frame = pub.Frame.digital()
    layout = pub.layout_goodnight(
        design, frame, message="Sleep tight, little one.", logo=logo(), measure=measure
    )
    night = pub.page_tone(1.0, design)
    assert layout.ground == night.scrim
    assert layout.texts[0].text == "Goodnight" and layout.texts[0].role == "display"
    assert all(t.color == night.ink for t in layout.texts)
    assert len(layout.images) == 1
    inside(design, layout, frame)


def test_drawing_page_is_light_with_a_dashed_frame(design: dict) -> None:
    frame = pub.Frame.kdp(bleed_left=False)
    layout = pub.layout_drawing(
        design, frame, caption="My drawing", title="The Lantern Bug Parade", measure=measure
    )
    assert layout.ground == day(design)
    (box,) = layout.shapes
    x0, y0, x1, y1 = safe(design, frame)
    assert box.kind == "frame" and box.dashed and box.radius > 0
    assert (box.x, box.y + 0) == pytest.approx((x0, y0 + 6 * frame.u))
    assert (box.x + box.w, box.y + box.h) == pytest.approx((x1, y1))
    assert [t.text for t in layout.texts] == ["My drawing", "The Lantern Bug Parade"]
    assert layout.texts[1].align == "right"
    inside(design, layout, frame)


# ---------------------------------------------------------------- covers


def test_front_cover_title_fits_two_lines(design: dict) -> None:
    frame = pub.Frame.digital()
    title = "The Moonlight Guardians and the Star Sweep"  # 42 characters
    layout = pub.layout_front_cover(
        design,
        frame,
        title=title,
        subtitle="A Bedtime Story",
        picture=Path("cover.png"),
        focus_x=0.3,
        logo=logo(),
        publisher="Giggle & Snooze",
        measure=measure,
    )
    displays = [t for t in layout.texts if t.role == "display"]
    assert 1 <= len(displays) <= 2
    assert " ".join(t.text for t in displays) == title
    assert layout.focus_x == 0.3 and layout.picture_box == (0, 0, 612, 612)
    (scrim,) = layout.scrims
    assert scrim.side == "top" and scrim.color == day(design)
    assert scrim.y1 == pytest.approx(0.4 * 612)
    (img,) = layout.images
    x0, y0, x1, y1 = safe(design, frame)
    assert img.x + img.w == pytest.approx(x1) and img.y + img.h == pytest.approx(y1)
    assert img.w == pytest.approx(design["logo"]["cover_u"] * frame.u)
    inside(design, layout, frame)


def test_short_cover_title_keeps_the_full_size(design: dict) -> None:
    layout = pub.layout_front_cover(
        design,
        pub.Frame.digital(),
        title="Jack",
        subtitle="",
        picture=None,
        focus_x=0.5,
        logo=None,
        publisher="Giggle & Snooze",
        measure=measure,
    )
    (title,) = [t for t in layout.texts if t.text == "Jack"]
    assert title.size == pytest.approx(design["type"]["cover_title_u"] * 6.12)
    publisher = layout.texts[-1]
    assert publisher.text == "Giggle & Snooze" and publisher.align == "right"


def test_back_cover_has_blurb_logo_and_barcode_slot(design: dict) -> None:
    frame = pub.Frame.digital()
    layout = pub.layout_back_cover(
        design,
        frame,
        blurb=LONG_TEXT[:300],
        picture=Path("back.png"),
        logo=logo(),
        publisher="Giggle & Snooze",
        measure=measure,
    )
    (scrim,) = layout.scrims
    assert scrim.side == "bottom" and scrim.y1 == 612
    assert scrim.y0 == pytest.approx(612 * (1 - 0.66))
    assert scrim.color == pub.oklch_to_srgb(design["colors"]["night"])
    (slot,) = layout.shapes
    x0, y0, x1, y1 = safe(design, frame)
    assert slot.kind == "rect" and slot.color == pub.oklch_to_srgb(design["colors"]["paper"])
    assert (slot.w, slot.h) == pytest.approx((24 * frame.u, 14 * frame.u))
    assert slot.x + slot.w == pytest.approx(x1) and slot.y + slot.h == pytest.approx(y1)
    (img,) = layout.images
    assert img.x == pytest.approx(x0) and img.y + img.h == pytest.approx(y1)
    moon = pub.oklch_to_srgb(design["colors"]["moon"])
    assert all(t.color == moon for t in layout.texts)
    assert max(t.y for t in layout.texts) < min(slot.y, img.y)
    inside(design, layout, frame)


def test_back_cover_without_picture_is_night(design: dict) -> None:
    layout = pub.layout_back_cover(
        design,
        pub.Frame.digital(),
        blurb="Short.",
        picture=None,
        logo=None,
        publisher="Giggle & Snooze",
        measure=measure,
    )
    assert layout.picture is None
    assert layout.ground == pub.oklch_to_srgb(design["colors"]["night"])


# ---------------------------------------------------------------- renderers


def test_scrim_alpha_profile() -> None:
    top = pub.Scrim(0, 100, "top", (0, 0, 0), 0.9, 0.8, 20)
    assert pr.scrim_alpha(top, np.array([0.0, 80.0, 90.0, 100.0, 150.0])) == pytest.approx(
        [0.9, 0.8, 0.4, 0.0, 0.0]
    )
    bottom = pub.Scrim(100, 200, "bottom", (0, 0, 0), 0.9, 0.8, 20)
    assert pr.scrim_alpha(bottom, np.array([50.0, 100.0, 110.0, 120.0, 200.0])) == pytest.approx(
        [0.0, 0.0, 0.4, 0.8, 0.9]
    )


def test_render_background_fills_picture_and_scrim(tmp_path: Path) -> None:
    picture = tmp_path / "p.png"
    Image.new("RGB", (160, 90), (200, 30, 30)).save(picture)
    layout = pub.PageLayout(
        width=72,
        height=72,
        ground=(0, 0, 255),
        picture=picture,
        picture_box=(0, 0, 72, 72),
        focus_x=0.5,
        scrims=(pub.Scrim(0, 36, "top", (255, 255, 255), 1.0, 1.0, 0.0001),),
    )
    img = pr.render_background(layout, dpi=100)
    assert img.size == (100, 100) and img.mode == "RGB"
    assert img.getpixel((50, 10)) == (255, 255, 255)
    assert img.getpixel((50, 90)) == (200, 30, 30)


def test_render_background_without_picture_is_ground() -> None:
    layout = pub.PageLayout(72, 72, (10, 20, 30), None, (0, 0, 72, 72), 0.5)
    assert pr.render_background(layout, dpi=50).getpixel((5, 5)) == (10, 20, 30)


def test_render_png_draws_text_and_shapes(design: dict) -> None:
    layout = pub.PageLayout(
        72,
        72,
        (255, 255, 255),
        None,
        (0, 0, 72, 72),
        0.5,
        texts=(pub.TextRun("Hi", "display", 30, 5, 40, (0, 0, 0)),),
        shapes=(pub.Shape("rect", 50, 50, 20, 20, (255, 0, 0)),),
    )
    img = pr.render_png(layout, 144, design)
    assert img.size == (144, 144)
    assert img.getpixel((120, 120)) == (255, 0, 0)
    dark = np.asarray(img)[:80, :70].min()
    assert dark < 60
