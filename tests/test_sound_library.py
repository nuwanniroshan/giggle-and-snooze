"""Phase 1 of spec 007: the sound library index, `list` and `check`."""

import json

import numpy as np
import pytest

from tests.helpers import ROOT
from video_pipeline.sound_library import (
    Cue,
    LibraryError,
    ambience_problems,
    build_cues,
    check_library,
    decode_sound,
    load_library,
    loop_fill,
    main,
    render_bed,
)
from video_pipeline.timeline import AudioSettings, Section

CC0 = "CC0 1.0 (public domain dedication): https://creativecommons.org/publicdomain/zero/1.0/"


def _entry(file, kind="ambience", seconds=10.0, **extra):
    return {
        "file": file,
        "title": "A title",
        "author": "someone",
        "url": "https://freesound.org/people/someone/sounds/1/",
        "kind": kind,
        "seconds": seconds,
        **extra,
    }


def make_lib(tmp_path, entries, license=CC0, create_files=True):
    """Write index.json (same shape as assets/sound_library/index.json) plus empty audio files."""
    index = {"license": license, "source": "test", "sounds": entries}
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    if create_files:
        for entry in entries.values():
            (tmp_path / entry["file"]).write_bytes(b"")
    return tmp_path


@pytest.fixture
def good(tmp_path):
    return make_lib(
        tmp_path,
        {
            "rain": _entry("rain.mp3", "ambience", 73.4),
            "pop": _entry("pop.mp3", "sfx", 0.4),
        },
    )


def test_loads_valid_index_and_gets_sound(good):
    lib = load_library(good)
    sound = lib.get("rain")
    assert sound.kind == "ambience"
    assert sound.seconds == 73.4
    assert sound.file == good / "rain.mp3"
    assert sound.license == CC0


def test_get_is_case_insensitive(good):
    assert load_library(good).get("RAIN").name == "rain"


def test_unknown_name_raises_library_error_with_name(good):
    with pytest.raises(LibraryError, match="thunderstorm"):
        load_library(good).get("thunderstorm")


def test_check_good_library_has_no_problems(good):
    assert check_library(good) == []


def test_check_reports_missing_file(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3")}, create_files=False)
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "rain" in problems[0]
    assert "rain.mp3" in problems[0]
    assert "missing" in problems[0]


def test_check_reports_orphan_audio_file(good):
    (good / "stray.mp3").write_bytes(b"")
    problems = check_library(good)
    assert problems == ["stray.mp3: audio file has no index entry"]


def test_check_reports_non_cc0_license(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3", license="CC BY 4.0")})
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "rain" in problems[0]
    assert "CC0" in problems[0]


def test_entry_license_overrides_index_license(tmp_path):
    make_lib(
        tmp_path,
        {"rain": _entry("rain.mp3", license=CC0)},
        license="All rights reserved",
    )
    assert check_library(tmp_path) == []


def test_invalid_json_names_index_json(tmp_path):
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(LibraryError, match=r"index\.json"):
        load_library(tmp_path)


def test_bad_kind_names_index_json_and_field(tmp_path):
    make_lib(tmp_path, {"rain": _entry("rain.mp3", kind="noise")})
    with pytest.raises(LibraryError) as exc:
        load_library(tmp_path)
    assert "index.json" in str(exc.value)
    assert "kind" in str(exc.value)


def test_missing_index_raises_library_error(tmp_path):
    with pytest.raises(LibraryError, match=r"index\.json"):
        load_library(tmp_path)


def test_check_reports_invalid_index_as_problem(tmp_path):
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    problems = check_library(tmp_path)
    assert len(problems) == 1
    assert "index.json" in problems[0]


def test_main_check_exits_0_when_ok(good, capsys):
    assert main(["check", "--root", str(good)]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_main_check_exits_1_with_problems(good, capsys):
    (good / "stray.mp3").write_bytes(b"")
    assert main(["check", "--root", str(good)]) == 1
    out = capsys.readouterr().out
    assert "stray.mp3" in out
    assert "1 problem(s)" in out


def test_main_list_prints_name_kind_length_author(good, capsys):
    assert main(["list", "--root", str(good)]) == 0
    out = capsys.readouterr().out
    rain = next(line for line in out.splitlines() if line.startswith("rain"))
    assert "ambience" in rain
    assert "73.4" in rain
    assert "someone" in rain
    assert "pop" in out


def test_main_list_invalid_index_exits_1(tmp_path, capsys):
    assert main(["list", "--root", str(tmp_path)]) == 1
    assert "index.json" in capsys.readouterr().err


def test_real_library_is_clean():
    assert check_library(ROOT / "assets" / "sound_library") == []


# --- Phase 3: cues and bed mixing -------------------------------------------------------------

SR = 8000
AUDIO = AudioSettings(
    peak_dbfs=-1.0, sfx_gain_db=-6.0, ambience_gain_db=-20.0, ambience_fade=1.0, loop_crossfade=0.25
)


def _sine(seconds=1.0, hz=100.0):
    t = np.arange(round(seconds * SR)) / SR
    return np.sin(2 * np.pi * hz * t)


def _script(**by_block):
    pages = [{"page_number": n, "ambience": by_block.get(f"p{n}", [])} for n in (3, 4, 5)]
    for page in pages:
        if not page["ambience"]:
            del page["ambience"]
    script = {"intro": {}, "pages": pages, "outro": {}}
    for key in ("intro", "outro"):
        if key in by_block:
            script[key]["ambience"] = by_block[key]
    return script


def _sections(*names):
    return [Section(n, "page", "x", 30, None) for n in names]


@pytest.fixture
def lib(tmp_path):
    return load_library(
        make_lib(
            tmp_path,
            {
                "rain": _entry("rain.mp3", "ambience", 1.0),
                "wind": _entry("wind.mp3", "ambience", 1.0),
                "pop": _entry("pop.mp3", "sfx", 0.5),
            },
        )
    )


def test_build_cues_merges_consecutive_pages_with_same_sound(lib):
    script = _script(p3=["rain"], p4=["rain"], p5=["wind"])
    sections = _sections("page-03", "page-04", "page-05")
    times = [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)]
    cues = build_cues(script, sections, times, lib)
    assert [(c.sound.name, c.start, c.end) for c in cues] == [
        ("rain", 0.0, 4.5),
        ("wind", 5.0, 7.0),
    ]


def test_build_cues_same_sound_after_gap_restarts(lib):
    script = _script(p3=["rain"], p5=["rain"])
    sections = _sections("page-03", "page-04", "page-05")
    times = [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0)]
    cues = build_cues(script, sections, times, lib)
    assert [(c.start, c.end) for c in cues] == [(0.0, 2.0), (5.0, 7.0)]


def test_build_cues_two_sounds_on_one_page_and_names_case_insensitive(lib):
    script = _script(p3=["Rain", "wind"])
    cues = build_cues(script, _sections("page-03"), [(1.0, 3.0)], lib)
    assert sorted(c.sound.name for c in cues) == ["rain", "wind"]
    assert all((c.start, c.end) == (1.0, 3.0) for c in cues)


def test_build_cues_intro_outro_and_back_cover_inherits(lib):
    script = _script(intro=["wind"], p5=["rain"], outro=["wind"])
    sections = [
        Section("intro", "intro", "x", 30, None),
        Section("page-05", "page", "x", 30, None),
        Section("back-cover", "beat", "x", 30, None),
        Section("outro", "outro", "x", 30, None),
    ]
    times = [(0.0, 1.0), (1.5, 2.5), (3.0, 4.0), (4.5, 5.5)]
    cues = build_cues(script, sections, times, lib)
    assert [(c.sound.name, c.start, c.end) for c in cues] == [
        ("wind", 0.0, 1.0),
        ("rain", 1.5, 4.0),  # the back cover keeps the last page's rain
        ("wind", 4.5, 5.5),
    ]


def test_build_cues_sfx_is_its_own_cue_per_page(lib):
    script = _script(p3=["pop"], p4=["pop"])
    sections = _sections("page-03", "page-04")
    cues = build_cues(script, sections, [(0.0, 2.0), (2.5, 4.5)], lib)
    assert [(c.sound.name, c.start, c.end) for c in cues] == [("pop", 0.0, 2.0), ("pop", 2.5, 4.5)]


def test_build_cues_no_ambience_gives_no_cues(lib):
    cues = build_cues(_script(), _sections("page-03"), [(0.0, 2.0)], lib)
    assert cues == []


def test_ambience_problems_names_page_and_sound(lib):
    assert ambience_problems(_script(p3=["rainy"]), lib) == ["page 3: unknown sound 'rainy'"]
    assert ambience_problems(_script(intro=["nope"]), lib) == ["intro: unknown sound 'nope'"]
    assert ambience_problems(_script(p3=["RAIN"], p4=["pop"]), lib) == []


def test_loop_fill_length_and_cut():
    sound = _sine(1.0)
    assert len(loop_fill(sound, round(3.5 * SR), 2000)) == round(3.5 * SR)
    assert np.array_equal(loop_fill(sound, 100, 2000), sound[:100])
    assert len(loop_fill(sound, 0, 2000)) == 0
    assert len(loop_fill(np.zeros(0), 50, 10)) == 50


def test_loop_fill_no_click_at_joins():
    sound = _sine(1.0)
    out = loop_fill(sound, round(3.5 * SR), round(0.25 * SR))
    own_step = np.abs(np.diff(sound)).max()
    assert np.abs(np.diff(out)).max() <= 2 * own_step


def test_loop_fill_zero_crossfade_still_fills():
    out = loop_fill(_sine(1.0), round(2.5 * SR), 0)
    assert len(out) == round(2.5 * SR)
    assert np.array_equal(out[SR : SR + 10], _sine(1.0)[:10])


def _cue(name, start, end, kind="ambience"):
    from video_pipeline.sound_library import Sound

    return Cue(Sound(name, None, kind, 1.0, "a", "CC0"), start, end)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


def test_render_bed_silent_outside_and_level_inside():
    length = 10 * SR
    cue = _cue("rain", 3.0, 7.0)
    bed = render_bed([cue], {"rain": _sine()}, length, SR, AUDIO)
    assert len(bed) == length
    assert not bed[: round(2.4 * SR)].any()  # fade starts half a fade (0.5 s) before the cue
    assert not bed[round(7.6 * SR) :].any()
    inside = bed[round(3.6 * SR) : round(6.4 * SR)]
    expected = 10 ** ((-20.0 + AUDIO.ambience_gain_db) / 20)
    assert _rms(inside) == pytest.approx(expected, rel=0.02)


def test_render_bed_overlapping_loop_cues_are_both_audible():  # AC-1.2
    length = 10 * SR
    a, b = _cue("a", 1.0, 9.0), _cue("b", 1.0, 9.0)
    sounds = {"a": _sine(2.0, 100.0), "b": _sine(2.0, 173.0)}
    both = render_bed([a, b], sounds, length, SR, AUDIO)
    alone = render_bed([a], sounds, length, SR, AUDIO)
    span = slice(round(3 * SR), round(7 * SR))
    assert _rms(both[span]) == pytest.approx(np.sqrt(2) * _rms(alone[span]), rel=0.15)
    # b's own frequency is present in the mix, and a's too
    spectrum = np.abs(np.fft.rfft(both[span]))
    freqs = np.fft.rfftfreq(len(both[span]), 1 / SR)
    for hz in (100.0, 173.0):
        assert spectrum[np.argmin(np.abs(freqs - hz))] > 0.2 * spectrum.max()


def test_render_bed_crossfades_between_cues():
    length = 10 * SR
    cues = [_cue("a", 0.0, 5.0), _cue("b", 5.0, 10.0)]
    ones = np.ones(SR)
    bed = render_bed(cues, {"a": ones, "b": ones}, length, SR, AUDIO)
    gain = 10 ** ((-20.0 + AUDIO.ambience_gain_db) / 20)
    # With constant sounds the sum of the two envelopes is the bed / gain.
    span = bed[round(4.5 * SR) : round(5.5 * SR)] / gain
    assert np.allclose(span, 1.0, atol=0.01)
    only_a = render_bed(cues[:1], {"a": ones}, length, SR, AUDIO) / gain
    ramp = only_a[round(4.5 * SR) : round(5.5 * SR)]
    assert np.all(np.diff(ramp) <= 1e-12)  # monotone fade out
    assert ramp[len(ramp) // 2] == pytest.approx(0.5, abs=0.01)  # midpoint at the page change


def test_render_bed_loop_cue_starting_at_zero_starts_silent_and_ends_faded():
    length = 4 * SR
    bed = render_bed([_cue("a", 0.0, 4.0)], {"a": np.ones(SR)}, length, SR, AUDIO)
    assert bed[0] == 0.0
    assert abs(bed[-1]) < 0.01 * 10 ** ((-20.0 + AUDIO.ambience_gain_db) / 20)
    assert bed[2 * SR] == pytest.approx(10 ** ((-20.0 + AUDIO.ambience_gain_db) / 20))


def test_render_bed_cue_wider_than_fade_at_edges_of_short_section():
    # A cue shorter than the fade must not blow up or go negative.
    bed = render_bed([_cue("a", 1.0, 1.2)], {"a": np.ones(SR)}, 3 * SR, SR, AUDIO)
    assert bed.min() >= 0.0 and bed.max() <= 10 ** ((-20.0 + AUDIO.ambience_gain_db) / 20) + 1e-12


def test_render_bed_sfx_plays_once_then_silence():
    length = 6 * SR
    pop = np.ones(SR // 2)
    bed = render_bed([_cue("pop", 2.0, 5.0, "sfx")], {"pop": pop}, length, SR, AUDIO)
    gain = 10 ** ((-20.0 + AUDIO.sfx_gain_db) / 20)
    start = 2 * SR
    assert np.allclose(bed[start : start + SR // 2], gain)  # shorter than the section: no fade
    assert not bed[:start].any()
    assert not bed[start + SR // 2 :].any()


def test_render_bed_sfx_longer_than_section_is_cut_with_short_fade():
    length = 6 * SR
    bed = render_bed([_cue("pop", 1.0, 2.0, "sfx")], {"pop": np.ones(4 * SR)}, length, SR, AUDIO)
    gain = 10 ** ((-20.0 + AUDIO.sfx_gain_db) / 20)
    assert not bed[2 * SR :].any()
    assert bed[round(1.5 * SR)] == pytest.approx(gain)
    assert bed[2 * SR - 1] < 0.05 * gain  # faded over the last 0.1 s


def test_render_bed_cue_beyond_episode_length_is_clipped():
    bed = render_bed(
        [_cue("pop", 9.0, 12.0, "sfx"), _cue("a", 9.0, 12.0)],
        {"pop": np.ones(SR), "a": np.ones(SR)},
        5 * SR,
        SR,
        AUDIO,
    )
    assert len(bed) == 5 * SR and not bed.any()


def test_decode_sound_uses_ffmpeg_decoder_and_names_sound(monkeypatch, lib):
    from video_pipeline import frames

    seen = {}

    def fake(path, rate):
        seen["args"] = (path, rate)
        return np.ones(4, np.float32)

    monkeypatch.setattr(frames, "_decode_with_ffmpeg", fake)
    out = decode_sound(lib.get("rain"), 8000)
    assert seen["args"] == (lib.get("rain").file, 8000) and len(out) == 4

    def boom(path, rate):
        raise frames.SoundError(path, "bad")

    monkeypatch.setattr(frames, "_decode_with_ffmpeg", boom)
    with pytest.raises(LibraryError, match="rain"):
        decode_sound(lib.get("rain"), 8000)


# --- loudness normalisation (AC-1.1, AC-2.5) ---------------------------------------------------


def _db(x):
    return 20 * np.log10(x)


def _scaled_sine(rms_db):
    s = _sine(2.0)
    return s / _rms(s) * 10 ** (rms_db / 20)


@pytest.mark.parametrize("source_db", [-52.0, -12.0])
def test_render_bed_loops_reach_the_same_level_whatever_the_source(source_db):
    from video_pipeline.sound_library import REFERENCE_VOICE_DBFS

    length = 12 * SR
    bed = render_bed([_cue("a", 2.0, 10.0)], {"a": _scaled_sine(source_db)}, length, SR, AUDIO)
    inside = bed[round(3.5 * SR) : round(8.5 * SR)]
    assert _db(_rms(inside)) == pytest.approx(
        REFERENCE_VOICE_DBFS + AUDIO.ambience_gain_db, abs=1.0
    )


def test_render_bed_spike_is_clipped_at_twice_the_peak_ceiling():
    spiky = np.zeros(2 * SR)
    spiky[SR // 2] = 1.4
    spiky += 0.001 * _sine(2.0)
    loop = render_bed([_cue("a", 1.0, 9.0)], {"a": spiky}, 10 * SR, SR, AUDIO)
    assert np.abs(loop).max() <= 2 * 10 ** (-14.0 / 20) + 1e-9
    sfx = render_bed([_cue("a", 1.0, 9.0, "sfx")], {"a": spiky}, 10 * SR, SR, AUDIO)
    assert np.abs(sfx).max() <= 2 * 10 ** (-8.0 / 20) + 1e-9
    assert np.abs(sfx).max() > 0.1  # and it is still audible


def test_render_bed_silent_sound_stays_silent():
    bed = render_bed([_cue("a", 1.0, 5.0)], {"a": np.zeros(SR)}, 6 * SR, SR, AUDIO)
    assert np.isfinite(bed).all() and not bed.any()


def test_normalize_sound_hits_target_rms():
    from video_pipeline.sound_library import normalize_sound

    out = normalize_sound(_scaled_sine(-40.0), -30.0, -6.0)
    assert _db(_rms(out)) == pytest.approx(-30.0, abs=0.01)


def test_normalize_sound_respects_max_boost():
    from video_pipeline.sound_library import normalize_sound

    src = _scaled_sine(-70.0)
    out = normalize_sound(src, -20.0, 0.0, max_boost_db=36.0)
    assert _db(_rms(out) / _rms(src)) == pytest.approx(36.0, abs=0.01)


def test_normalize_sound_ordinary_signal_peaks_at_the_ceiling():
    from video_pipeline.sound_library import normalize_sound

    out = normalize_sound(_scaled_sine(-40.0), -3.0, -14.0)
    assert np.abs(out).max() == pytest.approx(10 ** (-14.0 / 20), rel=0.01)


def test_normalize_sound_spike_is_clipped_at_twice_the_ceiling():
    from video_pipeline.sound_library import normalize_sound

    spiky = np.zeros(1000)
    spiky[10] = 2.0
    spiky[500:600] = 0.01
    out = normalize_sound(spiky, -20.0, -14.0)
    assert np.abs(out).max() <= 2 * 10 ** (-14.0 / 20) + 1e-12


def _noise_with_spike(rms_db=-45.0, n=60000):
    noise = np.random.default_rng(1).standard_normal(n)
    noise *= 10 ** (rms_db / 20) / _rms(noise)
    noise[n // 2] = 1.3
    return noise


def test_normalize_sound_one_spike_does_not_decide_the_level():
    from video_pipeline.sound_library import normalize_sound

    out = normalize_sound(_noise_with_spike(), -34.0, -14.0)
    assert _db(_rms(out)) == pytest.approx(-34.0, abs=1.5)


def test_normalize_sound_one_spike_is_limited_to_twice_the_ceiling():
    from video_pipeline.sound_library import normalize_sound

    out = normalize_sound(_noise_with_spike(), -34.0, -14.0)
    assert np.abs(out).max() <= 2 * 10 ** (-14.0 / 20) + 1e-12


def test_normalize_sound_silence_is_unchanged():
    from video_pipeline.sound_library import normalize_sound

    out = normalize_sound(np.zeros(100), -20.0, -14.0)
    assert not out.any() and np.isfinite(out).all()
