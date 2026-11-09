import dataclasses

import numpy as np
import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, load_json
from tests.voice_helpers import VOICES_PATH, FakeEngine, identity_shift, make_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import ConfigError
from video_pipeline.synthesize_voices import (
    AudioPaths,
    LineFailed,
    build_preview,
    build_tracks,
    clip_key,
    load_pronunciations,
    load_voice_config,
    plan_jobs,
    spoken_text,
    synthesize_clips,
)

CFG = load_voice_config(VOICES_PATH)


@pytest.fixture
def script():
    return make_script(MINI_BOOK)


def run(script, tmp_path, engine=None, cfg=CFG, prons=None, force=False):
    engine = engine or FakeEngine()
    jobs = plan_jobs(script, cfg, prons or {})
    paths = AudioPaths(tmp_path / "audio")
    outcomes = synthesize_clips(jobs, paths, lambda: engine, cfg, force=force, shift=identity_shift)
    names, rebuilt = build_tracks(outcomes, paths, cfg)
    return jobs, paths, outcomes, names, rebuilt, engine


def test_spoken_text_keeps_allowed_tags_and_drops_others():  # AC-2.3
    assert spoken_text("<giggle> Hi <whisper>there</whisper>!", {}) == "<giggle> Hi there !"


def test_spoken_text_applies_pronunciations_whole_word():  # AC-5.1
    prons = {"Tilly": "Til-ee"}
    assert spoken_text("TILLY waved at Tilly's friend, not Tillyard.", prons) == (
        "Til-ee waved at Til-ee's friend, not Tillyard."
    )


@pytest.mark.parametrize("bad", [["Tilly"], {"Tilly": ""}, {"": "x"}, {"Tilly": 3}])
def test_load_pronunciations_rejects_malformed(tmp_path, bad):
    with pytest.raises(ConfigError, match="pronunciations"):
        load_pronunciations({"metadata": {"pronunciations": bad}}, tmp_path / "book.json")


def test_load_pronunciations_is_optional(tmp_path):  # AC-5.3
    assert load_pronunciations({"metadata": {}}, tmp_path / "book.json") == {}


def test_plan_jobs_covers_every_line_with_role_voices(script):  # AC-1.1, AC-2.1, AC-2.4
    jobs = plan_jobs(script, CFG, {})
    expected = (
        len(script["intro"]["lines"])
        + sum(len(p["lines"]) for p in script["pages"])
        + len(script["outro"]["lines"])
    )
    assert len(jobs) == expected
    assert jobs[0].line_id == "intro-01" and jobs[-1].track == "outro"
    assert "page-01-01" in {j.line_id for j in jobs}
    voices = {j.speaker: j.voice for j in jobs}
    assert voices == {"storyteller": "tara", "kid_older": "zac", "kid_younger": "zoe"}
    assert {j.speaker: j.semitones for j in jobs} == {
        "storyteller": 0.0,
        "kid_older": 2.0,
        "kid_younger": 3.0,
    }


def test_plan_jobs_rejects_unsupported_voice(script):
    script["cast"]["kid_younger"]["voice"] = "robot"
    with pytest.raises(ConfigError, match="kid_younger.*robot"):
        plan_jobs(script, CFG, {})


def test_clip_key_identity():  # FR-7
    base = clip_key("Hello there.", "tara", 0, CFG)
    assert base == clip_key("Hello there.", "tara", 0, CFG)
    assert base != clip_key("Hello there!", "tara", 0, CFG)
    assert base != clip_key("Hello there.", "zoe", 0, CFG)
    assert base != clip_key("Hello there.", "tara", 2, CFG)
    other_model = dataclasses.replace(
        CFG, model=dataclasses.replace(CFG.model, revision="different")
    )
    assert base != clip_key("Hello there.", "tara", 0, other_model)


def test_pronunciation_changes_only_affected_lines(script):  # AC-5.4
    before = plan_jobs(script, CFG, {})
    after = plan_jobs(script, CFG, {"Tilly": "Til-ee"})
    changed = {a.line_id for a, b in zip(before, after, strict=True) if a.key != b.key}
    assert changed == {j.line_id for j in before if "tilly" in j.text.lower()}
    assert changed


def test_writes_a_clip_per_line_and_every_track(script, tmp_path):  # AC-1.1, AC-1.2
    jobs, paths, _, names, rebuilt, _ = run(script, tmp_path)
    assert all(paths.clip(j.key).exists() for j in jobs)
    expected = ["intro", *[f"page-{n:02d}" for n in range(1, 9)], "outro"]
    assert names == expected and rebuilt == expected
    assert all(paths.track(n).exists() and paths.track_timing(n).exists() for n in names)


def test_track_timing_and_pauses(script, tmp_path):  # AC-1.3, AC-1.4
    jobs, paths, *_ = run(script, tmp_path)
    page = next(p for p in script["pages"] if p["has_qa"])
    timing = load_json(paths.track_timing(f"page-{page['page_number']:02d}"))
    lines = timing["lines"]
    assert [x["text"] for x in lines] == [x["text"] for x in page["lines"]]
    assert {"line_id", "speaker", "role", "start", "end"} <= set(lines[0])
    for prev, nxt in zip(lines, lines[1:], strict=False):
        gap = nxt["start"] - prev["end"]
        if prev["role"] == "narration" and nxt["role"] != "narration":
            assert gap == pytest.approx(1.0, abs=0.05)
        elif prev["speaker"] == nxt["speaker"]:
            assert gap == pytest.approx(0.4, abs=0.05)
        else:
            assert gap == pytest.approx(0.7, abs=0.05)
    audio, sr = sf.read(paths.track(f"page-{page['page_number']:02d}"))
    assert sr == 24000 and timing["duration"] == pytest.approx(len(audio) / sr, abs=0.01)


def test_failing_attempt_is_retried_with_a_new_seed(script, tmp_path):  # AC-3.3
    target = script["outro"]["lines"][-1]["text"]
    engine = FakeEngine(bad={target: 1})
    _, _, outcomes, *_ = run(script, tmp_path, engine=engine)
    seeds = [seed for text, _, seed in engine.calls if text == target]
    assert seeds == [7, 8]
    outcome = next(o for o in outcomes if o.job.text == target)
    assert outcome.attempts == 2 and outcome.problems == []


def test_line_failing_every_attempt_is_reported(script, tmp_path):  # AC-3.4
    target = script["intro"]["lines"][0]["text"]
    with pytest.raises(LineFailed) as excinfo:
        run(script, tmp_path, engine=FakeEngine(bad={target: 99}))
    [(job, problems)] = excinfo.value.failures
    assert job.line_id == "intro-01" and any("clipping" in p for p in problems)
    paths = AudioPaths(tmp_path / "audio")
    assert not paths.tracks.exists()
    assert paths.clips.exists() and any(paths.clips.glob("*.wav"))  # passing clips kept


def test_quality_results_are_recorded(script, tmp_path):  # AC-3.5
    jobs, paths, *_ = run(script, tmp_path)
    meta = load_json(paths.clip_meta(jobs[0].key))
    assert set(meta["measure"]) == {"duration", "peak", "longest_silence", "wps", "cut_off"}
    assert meta["attempts"] == 1


def test_rerun_reuses_every_clip_and_track(script, tmp_path):  # AC-6.1
    jobs, paths, _, names, _, _ = run(script, tmp_path)
    before = {n: paths.track(n).read_bytes() for n in names}

    def no_engine():
        raise AssertionError("no clip should need synthesis")

    outcomes = synthesize_clips(jobs, paths, no_engine, CFG, shift=identity_shift)
    assert not any(o.synthesized for o in outcomes)
    names, rebuilt = build_tracks(outcomes, paths, CFG)
    assert rebuilt == []
    assert {n: paths.track(n).read_bytes() for n in names} == before


def test_one_changed_line_rebuilds_one_track(script, tmp_path):  # AC-6.2
    run(script, tmp_path)
    page = next(p for p in script["pages"] if p["has_qa"])
    page["lines"][-1]["text"] = "What a lovely page!"
    engine = FakeEngine()
    *_, rebuilt, _ = run(script, tmp_path, engine=engine)
    assert [text for text, *_ in engine.calls] == ["What a lovely page!"]
    assert rebuilt == [f"page-{page['page_number']:02d}"]


def test_changed_pitch_resynthesizes_that_speaker(script, tmp_path):  # AC-6.3
    run(script, tmp_path)
    cfg = dataclasses.replace(CFG, pitch_semitones={**CFG.pitch_semitones, "kid_younger": 4.0})
    engine = FakeEngine()
    jobs, *_ = run(script, tmp_path, engine=engine, cfg=cfg)
    expected = {j.key for j in jobs if j.speaker == "kid_younger"}
    assert len(engine.calls) == len(expected) and expected


def test_force_resynthesizes_everything(script, tmp_path):  # AC-6.4
    jobs, *_ = run(script, tmp_path)
    engine = FakeEngine()
    run(script, tmp_path, engine=engine, force=True)
    assert len(engine.calls) == len({j.key for j in jobs})


def test_interrupted_clip_write_leaves_no_partial_file(script, tmp_path, monkeypatch):  # AC-6.5
    def boom(src, dst):
        raise OSError("interrupted")

    monkeypatch.setattr(sv.os, "replace", boom)
    with pytest.raises(OSError):
        run(script, tmp_path)
    clips = AudioPaths(tmp_path / "audio").clips
    assert list(clips.glob("*.wav")) == []


def test_preview_joins_tracks_with_gaps(script, tmp_path):  # FR-4
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    total = sum(load_json(paths.track_timing(n))["duration"] for n in names)
    audio, sr = sf.read(paths.preview)
    assert len(audio) / sr == pytest.approx(total + 1.0 * (len(names) - 1), abs=0.05)


def test_clips_are_mono_24k_pcm(script, tmp_path):  # FR-4 format
    jobs, paths, *_ = run(script, tmp_path)
    info = sf.info(paths.clip(jobs[0].key))
    assert (info.channels, info.samplerate, info.subtype) == (1, 24000, "PCM_16")


def test_pitch_shift_receives_the_role_lift(script, tmp_path):  # AC-2.2 wiring
    seen = []

    def recording_shift(audio, sample_rate, semitones):
        seen.append(semitones)
        return audio

    jobs = plan_jobs(script, CFG, {})
    synthesize_clips(jobs, AudioPaths(tmp_path / "a"), FakeEngine, CFG, shift=recording_shift)
    assert set(seen) == {0.0, 2.0, 3.0}


def test_engine_with_another_sample_rate_is_rejected(script, tmp_path):
    class Wrong(FakeEngine):
        sample_rate = 16000

    jobs = plan_jobs(script, CFG, {})
    with pytest.raises(ConfigError, match="sample rate"):
        synthesize_clips(jobs, AudioPaths(tmp_path / "a"), Wrong, CFG, shift=identity_shift)


def test_stitched_audio_matches_clips(script, tmp_path):
    jobs, paths, *_ = run(script, tmp_path)
    timing = load_json(paths.track_timing("intro"))
    track, sr = sf.read(paths.track("intro"), dtype="float32")
    first = timing["lines"][0]
    clip, _ = sf.read(paths.clip(jobs[0].key), dtype="float32")
    start = int(round(first["start"] * sr))
    assert np.allclose(track[start : start + len(clip)], clip, atol=1e-4)
