"""Phase 2 of spec 003: canvas, caption images, page-turn sound and master audio."""

import dataclasses
import subprocess

import numpy as np
import pytest
import soundfile as sf
from PIL import Image, ImageFilter, ImageOps

from tests.helpers import ROOT
from video_pipeline import frames
from video_pipeline.frames import (
    PictureError,
    SoundError,
    canvas_parts,
    compose_canvas,
    load_page_turn,
    mix_master,
    page_turn_sound,
    render_caption,
)
from video_pipeline.timeline import (
    Section,
    load_render_config,
    section_times,
    total_frames,
    wrap_lines,
)

SR = 24000


@pytest.fixture
def cfg():
    return load_render_config(ROOT / "config" / "render.json")


def _picture(path, size=1024):
    """Solid red with a white square in the top-left quarter."""
    im = Image.new("RGB", (size, size), (220, 30, 30))
    im.paste((255, 255, 255), (0, 0, size // 2, size // 2))
    im.save(path)
    return path


# --- compose_canvas -------------------------------------------------------------------------


def test_compose_canvas_size_and_centred_picture(cfg, tmp_path):
    out = tmp_path / "canvas.png"
    compose_canvas(_picture(tmp_path / "pic.png"), cfg, out)
    with Image.open(out) as im:
        assert im.size == (1920, 1080)
        assert im.mode == "RGB"
        canvas = np.asarray(im).astype(int)
    left = (1920 - 1080) // 2  # 420
    # White square occupies the picture's top-left quarter: canvas x 420..960, y 0..540.
    assert (canvas[100, left + 100] >= 250).all()
    assert (canvas[270, left + 270] >= 250).all()
    # Red elsewhere in the picture.
    assert abs(canvas[800, left + 800] - [220, 30, 30]).max() <= 3
    assert abs(canvas[300, left + 900] - [220, 30, 30]).max() <= 3
    # The picture's edges are where expected.
    assert abs(canvas[800, left + 2] - [220, 30, 30]).max() <= 3
    assert abs(canvas[800, left + 1077] - [220, 30, 30]).max() <= 3


def test_compose_canvas_backdrop_is_darker(cfg, tmp_path):
    out = tmp_path / "canvas.png"
    compose_canvas(_picture(tmp_path / "pic.png"), cfg, out)
    with Image.open(out) as im:
        canvas = np.asarray(im).astype(int)
    left = (1920 - 1080) // 2
    backdrop = np.concatenate([canvas[:, : left - 5], canvas[:, left + 1085 :]], axis=1)
    picture = canvas[:, left + 5 : left + 1075]
    assert backdrop.mean() < picture.mean() * 0.8
    assert backdrop.max() <= 255 * cfg.backdrop.darken + 2


def test_compose_canvas_atomic_and_no_tmp_left(cfg, tmp_path):
    out = tmp_path / "sub" / "canvas.png"
    compose_canvas(_picture(tmp_path / "pic.png"), cfg, out)
    assert out.exists()
    assert [p.name for p in out.parent.iterdir()] == ["canvas.png"]


@pytest.mark.parametrize("make", ["missing", "garbage"])
def test_compose_canvas_unreadable_picture(cfg, tmp_path, make):
    bad = tmp_path / "bad.png"
    if make == "garbage":
        bad.write_bytes(b"not an image")
    with pytest.raises(PictureError) as err:
        compose_canvas(bad, cfg, tmp_path / "canvas.png")
    assert str(bad) in str(err.value)
    assert err.value.path == bad
    assert not (tmp_path / "canvas.png").exists()


def _old_compose_canvas(picture_path, cfg, out_path):
    """compose_canvas as it was before spec 010 Phase 4 split out canvas_parts."""
    size = (cfg.width, cfg.height)
    with Image.open(picture_path) as im:
        picture = im.convert("RGB")
    backdrop = ImageOps.fit(picture, size, Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(cfg.backdrop.blur))
    darken = cfg.backdrop.darken
    backdrop = backdrop.point(lambda v: round(v * darken))
    front = ImageOps.contain(picture, size, Image.Resampling.LANCZOS)
    backdrop.paste(front, ((size[0] - front.width) // 2, (size[1] - front.height) // 2))
    backdrop.save(out_path, format="PNG")


@pytest.mark.parametrize("size", [(1024, 1024), (1600, 900), (700, 1000)])
def test_compose_canvas_bytes_unchanged_by_the_split(cfg, tmp_path, size):  # spec 010 AC-2.4
    pic = tmp_path / "pic.png"
    im = Image.new("RGB", size, (220, 30, 30))
    im.paste((255, 255, 255), (0, 0, size[0] // 2, size[1] // 3))
    im.paste((10, 200, 90), (size[0] // 3, size[1] // 2, size[0] - 5, size[1] - 7))
    im.save(pic)
    old, new = tmp_path / "old.png", tmp_path / "new.png"
    _old_compose_canvas(pic, cfg, old)
    compose_canvas(pic, cfg, new)
    assert new.read_bytes() == old.read_bytes()


def test_canvas_parts_backdrop_size_and_offset(cfg, tmp_path):  # spec 010 FR-12
    with Image.open(_picture(tmp_path / "pic.png")) as im:
        picture = im.convert("RGB")
    backdrop, size, offset = canvas_parts(picture, cfg)
    assert backdrop.size == (1920, 1080) and backdrop.mode == "RGB"
    assert size == (1080, 1080)
    assert offset == (420, 0)
    canvas = backdrop.copy()
    canvas.paste(picture.resize(size, Image.Resampling.LANCZOS), offset)
    compose_canvas(tmp_path / "pic.png", cfg, tmp_path / "canvas.png")
    with Image.open(tmp_path / "canvas.png") as im:
        assert np.array_equal(np.asarray(im), np.asarray(canvas))


# --- render_caption -------------------------------------------------------------------------

LONG = "Barnaby the snail hurried along the garden path, carrying a tiny parcel for Clara."


def _caption(cfg, tmp_path, text):
    out = tmp_path / "cap.png"
    render_caption(text, cfg, out)
    with Image.open(out) as im:
        assert im.mode == "RGBA"
        assert im.size == (1920, 1080)
        return np.asarray(im).astype(int)


def _text_rows(cap):
    """Runs of rows containing near-white opaque pixels (text), as (top, bottom) inclusive."""
    white = (cap[..., :3].min(axis=2) >= 200) & (cap[..., 3] >= 200)
    rows = np.flatnonzero(white.any(axis=1))
    runs = []
    start = prev = rows[0]
    for r in rows[1:]:
        if r != prev + 1:
            runs.append((start, prev))
            start = r
        prev = r
    runs.append((start, prev))
    return runs


def test_render_caption_layout(cfg, tmp_path):
    cap = _caption(cfg, tmp_path, "Off we go!")
    alpha = cap[..., 3]
    assert (alpha[:540] == 0).all()  # top half fully transparent
    m = cfg.captions.margin
    assert (alpha[-m:] == 0).all()
    assert (alpha[:, :m] == 0).all()
    assert (alpha[:, -m:] == 0).all()
    band = alpha[alpha > 0]
    expected = round(cfg.captions.band_opacity * 255)
    # Most visible pixels are band pixels at the band opacity.
    assert np.mean(np.abs(band - expected) <= 2) > 0.5
    # The band is black where there is no text.
    band_px = cap[(np.abs(alpha - expected) <= 2)]
    assert band_px[:, :3].max() <= 10


def test_render_caption_wraps_to_max_lines_and_tall_text(cfg, tmp_path):
    lines = wrap_lines(LONG, cfg.captions.max_chars)
    assert len(lines) == 2
    cap = _caption(cfg, tmp_path, LONG)
    runs = _text_rows(cap)
    assert len(runs) == len(lines) <= cfg.captions.max_lines
    for top, bottom in runs:
        assert bottom - top + 1 >= 40  # AC-4.4
    # Text sits inside the band and the band inside the margins.
    alpha = cap[..., 3]
    m = cfg.captions.margin
    assert (alpha[-m:] == 0).all()
    assert (alpha[:, :m] == 0).all()
    assert (alpha[:, -m:] == 0).all()


def test_render_caption_centred(cfg, tmp_path):
    cap = _caption(cfg, tmp_path, "Hello there")
    cols = np.flatnonzero((cap[..., 3] > 0).any(axis=0))
    assert abs((cols[0] + cols[-1]) / 2 - 960) <= 2


def test_render_caption_rejects_too_many_lines(cfg, tmp_path):
    with pytest.raises(ValueError, match="lines"):
        render_caption(LONG * 3, cfg, tmp_path / "cap.png")


def test_render_caption_overlong_word_stays_in_frame(cfg, tmp_path):
    cap = _caption(cfg, tmp_path, "W" * 80)
    alpha = cap[..., 3]
    m = cfg.captions.margin
    assert (alpha[:, :m] == 0).all()
    assert (alpha[:, -m:] == 0).all()


def test_render_caption_font_fallback(cfg, tmp_path):
    style = dataclasses.replace(
        cfg.captions, font=str(tmp_path / "nope.ttf"), fallback_font=str(tmp_path / "no.ttf")
    )
    cap = _caption(dataclasses.replace(cfg, captions=style), tmp_path, LONG)
    assert len(_text_rows(cap)) == 2


# --- page-turn sound ------------------------------------------------------------------------


def test_page_turn_sound_shape():
    a = page_turn_sound(SR)
    assert a.dtype == np.float32
    assert a.ndim == 1
    assert 0.4 <= len(a) / SR <= 0.8
    assert 0.3 <= np.abs(a).max() <= 0.6
    assert np.abs(a[-int(0.02 * SR) :]).max() < 0.01  # ends near silence
    assert np.array_equal(a, page_turn_sound(SR))
    assert np.array_equal(a, page_turn_sound(SR, seed=0))
    assert not np.array_equal(a, page_turn_sound(SR, seed=1))


def test_load_page_turn_generates_and_caches(tmp_path, monkeypatch):
    sfx = tmp_path / "audio_sfx"
    sfx.mkdir()
    cache = tmp_path / "build" / "sfx" / "page_turn.wav"

    def no_ffmpeg(*a, **k):
        raise AssertionError("ffmpeg should not run without a supplied file")

    monkeypatch.setattr(frames.subprocess, "run", no_ffmpeg)
    first = load_page_turn(sfx, SR, cache)
    assert cache.exists()
    info = sf.info(cache)
    assert info.samplerate == SR
    assert info.channels == 1
    stamp = cache.stat().st_mtime_ns

    monkeypatch.setattr(frames, "page_turn_sound", lambda *a, **k: pytest.fail("regenerated"))
    second = load_page_turn(sfx, SR, cache)
    assert cache.stat().st_mtime_ns == stamp
    assert np.array_equal(first, second)
    assert first.dtype == np.float32


def test_load_page_turn_supplied_file_decoded(tmp_path, monkeypatch):
    sfx = tmp_path / "audio_sfx"
    sfx.mkdir()
    supplied = sfx / "page_turn.wav"
    supplied.write_bytes(b"RIFF....")
    samples = np.array([0.0, 0.25, -0.5], dtype="<f4")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, samples.tobytes(), b"")

    monkeypatch.setattr(frames.subprocess, "run", fake_run)
    out = load_page_turn(sfx, SR, tmp_path / "cache.wav")
    assert np.array_equal(out, samples)
    cmd, kwargs = calls[0]
    assert isinstance(cmd, list)
    assert cmd[0] == "ffmpeg"
    assert str(supplied) in cmd
    assert cmd[cmd.index("-f") : cmd.index("-f") + 2] == ["-f", "f32le"]
    assert str(SR) in cmd
    assert kwargs.get("check") is True
    assert not (tmp_path / "cache.wav").exists()


def test_load_page_turn_supplied_unreadable(tmp_path, monkeypatch):
    sfx = tmp_path / "audio_sfx"
    sfx.mkdir()
    supplied = sfx / "page_turn.wav"
    supplied.write_bytes(b"junk")

    def failing(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, b"", b"Invalid data found")

    monkeypatch.setattr(frames.subprocess, "run", failing)
    with pytest.raises(SoundError) as err:
        load_page_turn(sfx, SR, tmp_path / "cache.wav")
    assert str(supplied) in str(err.value)
    assert err.value.path == supplied


def test_load_page_turn_supplied_empty_output(tmp_path, monkeypatch):
    sfx = tmp_path / "audio_sfx"
    sfx.mkdir()
    (sfx / "page_turn.wav").write_bytes(b"junk")
    monkeypatch.setattr(
        frames.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, b"", b"")
    )
    with pytest.raises(SoundError):
        load_page_turn(sfx, SR, tmp_path / "cache.wav")


# --- mix_master -----------------------------------------------------------------------------


def _sections(cfg):
    return [
        Section("intro", "intro", "cover_front", 90, "intro"),
        Section("page-01", "page", "page_01", 120, "page-01"),
        Section("back-cover", "beat", "cover_back", 75, None),
        Section("outro", "outro", "cover_back", 60, "outro"),
    ]


def _tracks(cfg, value=0.1):
    lead = cfg.lead_in
    rng = np.random.default_rng(3)
    # Each track fits its section: lead_in + track ≤ section length.
    secs = {"intro": 90 / 30 - lead - 0.5, "page-01": 120 / 30 - lead - 0.5, "outro": 1.0}
    return {
        name: (rng.uniform(-value, value, round(s * SR))).astype(np.float32)
        for name, s in secs.items()
    }


def test_mix_master_length_exact(cfg):
    sections = _sections(cfg)
    mix = mix_master(sections, _tracks(cfg), page_turn_sound(SR), cfg, SR)
    assert mix.dtype == np.float32
    assert len(mix) == round(total_frames(sections, cfg) / cfg.fps * SR)


def test_mix_master_track_unaltered_at_offset(cfg):
    sections = _sections(cfg)
    tracks = _tracks(cfg)
    mix = mix_master(sections, tracks, np.zeros(10, np.float32), cfg, SR)
    times = section_times(sections, cfg)
    for section, (start, _end) in zip(sections, times, strict=True):
        if section.track is None:
            continue
        track = tracks[section.track]
        at = round((start + cfg.lead_in) * SR)
        assert np.array_equal(mix[at : at + len(track)], track)
        assert not mix[at - 50 : at].any()


def test_mix_master_page_turn_at_transitions_and_no_voice_inside(cfg):
    sections = _sections(cfg)
    times = section_times(sections, cfg)
    burst = np.ones(100, np.float32) * 0.5
    voice_only = mix_master(sections, _tracks(cfg), np.zeros(1, np.float32), cfg, SR)
    turns_only = mix_master(sections, {k: v * 0 for k, v in _tracks(cfg).items()}, burst, cfg, SR)
    gain = 10 ** (cfg.audio.sfx_gain_db / 20)
    for (_s0, prev_end), (next_start, _e1) in zip(times, times[1:], strict=False):
        a, b = round(prev_end * SR), round(next_start * SR)
        assert not voice_only[a:b].any()  # AC-5.2
        assert not turns_only[a - 1]
        assert turns_only[a] == pytest.approx(0.5 * gain, rel=1e-5)
    assert np.count_nonzero(turns_only) == 100 * (len(sections) - 1)


def test_mix_master_limits_loud_mix(cfg):
    sections = _sections(cfg)
    tracks = _tracks(cfg, value=1.0)
    tracks["intro"][5] = 3.0
    mix = mix_master(sections, tracks, page_turn_sound(SR), cfg, SR)
    limit = 10 ** (cfg.audio.peak_dbfs / 20)
    assert np.abs(mix).max() == pytest.approx(limit, rel=1e-4)


def test_mix_master_missing_track(cfg):
    tracks = _tracks(cfg)
    del tracks["page-01"]
    with pytest.raises(ValueError, match="page-01"):
        mix_master(_sections(cfg), tracks, page_turn_sound(SR), cfg, SR)


def test_mix_master_track_too_long_for_section(cfg):
    tracks = _tracks(cfg)
    tracks["outro"] = np.zeros(SR * 5, np.float32)
    with pytest.raises(ValueError, match="outro"):
        mix_master(_sections(cfg), tracks, page_turn_sound(SR), cfg, SR)


# --- mix_master with an ambience bed (spec 007) ----------------------------------------------


def _expected_without_bed(sections, tracks, page_turn, cfg):
    """The pre-spec-007 mix, written out independently of mix_master."""
    length = round(total_frames(sections, cfg) / cfg.fps * SR)
    mix = np.zeros(length)
    times = section_times(sections, cfg)
    for section, (start, _end) in zip(sections, times, strict=True):
        if section.track is not None:
            at = round((start + cfg.lead_in) * SR)
            mix[at : at + len(tracks[section.track])] += tracks[section.track]
    turn = page_turn.astype(np.float64) * 10 ** (cfg.audio.sfx_gain_db / 20)
    for _s, end in times[:-1]:
        at = round(end * SR)
        piece = turn[: max(0, length - at)]
        mix[at : at + len(piece)] += piece
    limit = 10 ** (cfg.audio.peak_dbfs / 20)
    peak = np.abs(mix).max()
    if peak > limit:
        mix *= limit / peak
    return mix.astype(np.float32)


def test_mix_master_ambience_none_is_old_output(cfg):
    sections = _sections(cfg)
    tracks = _tracks(cfg, value=1.0)
    tracks["intro"][5] = 3.0  # make the limiter act too
    turn = page_turn_sound(SR)
    expected = _expected_without_bed(sections, tracks, turn, cfg)
    assert np.array_equal(mix_master(sections, tracks, turn, cfg, SR), expected)
    assert np.array_equal(mix_master(sections, tracks, turn, cfg, SR, ambience=None), expected)


def test_mix_master_zero_bed_leaves_speech_unchanged(cfg):
    sections = _sections(cfg)
    tracks = _tracks(cfg)
    turn = page_turn_sound(SR)
    base = mix_master(sections, tracks, turn, cfg, SR)
    length = len(base)
    with_bed = mix_master(sections, tracks, turn, cfg, SR, ambience=np.zeros(length))
    assert np.array_equal(with_bed, base)


def test_mix_master_adds_bed_and_keeps_peak_limit(cfg):
    sections = _sections(cfg)
    tracks = _tracks(cfg)
    turn = page_turn_sound(SR)
    base = mix_master(sections, tracks, turn, cfg, SR)
    bed = np.full(len(base), 0.01)
    mixed = mix_master(sections, tracks, turn, cfg, SR, ambience=bed)
    assert np.allclose(mixed, base + 0.01, atol=1e-6)
    loud = mix_master(sections, tracks, turn, cfg, SR, ambience=np.full(len(base), 5.0))
    assert np.abs(loud).max() == pytest.approx(10 ** (cfg.audio.peak_dbfs / 20), rel=1e-4)


def test_mix_master_ambience_wrong_length(cfg):
    with pytest.raises(ValueError, match="ambience"):
        mix_master(_sections(cfg), _tracks(cfg), page_turn_sound(SR), cfg, SR, ambience=np.zeros(5))
