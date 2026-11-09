import dataclasses
import json

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


def test_voice_config_limits_bf16_support_repo_to_tokenizer_files():
    repos = {repo: (revision, files) for repo, revision, files in CFG.model.support_repos}
    assert repos["mlx-community/snac_24khz"][1] is None
    assert repos["mlx-community/orpheus-3b-0.1-ft-bf16"][1] == (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
    )


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


# --- Review fixes -------------------------------------------------------------------------


class Quieter(FakeEngine):
    """Same timing as FakeEngine, different audio content."""

    def synthesize(self, text, voice, seed):
        return super().synthesize(text, voice, seed) * 0.5


def test_force_with_new_audio_rebuilds_every_track(script, tmp_path):  # review fix 1
    jobs, paths, _, names, _, _ = run(script, tmp_path)
    *_, rebuilt, _ = run(script, tmp_path, engine=Quieter(), force=True)
    assert rebuilt == names
    timing = load_json(paths.track_timing("intro"))
    track, sr = sf.read(paths.track("intro"), dtype="float32")
    clip, _ = sf.read(paths.clip(jobs[0].key), dtype="float32")
    start = int(round(timing["lines"][0]["start"] * sr))
    assert np.allclose(track[start : start + len(clip)], clip, atol=1e-4)
    assert np.abs(clip).max() < 0.2  # the new, quieter clip


def test_deleted_clip_resynthesized_rebuilds_its_track(script, tmp_path):  # review fix 1
    jobs, paths, *_ = run(script, tmp_path)
    paths.clip(jobs[0].key).unlink()
    *_, rebuilt, _ = run(script, tmp_path, engine=Quieter())
    assert rebuilt == ["intro"]


def test_case_only_edit_mapped_by_pronunciation_updates_timing(script, tmp_path):  # fix 3
    prons = {"Tilly": "Til-ee"}
    run(script, tmp_path, prons=prons)
    page = next(p for p in script["pages"] if any("Tilly" in x["text"] for x in p["lines"]))
    index = next(i for i, x in enumerate(page["lines"]) if "Tilly" in x["text"])
    page["lines"][index]["text"] = page["lines"][index]["text"].replace("Tilly", "TILLY")
    engine = FakeEngine()
    *_, rebuilt, _ = run(script, tmp_path, engine=engine, prons=prons)
    assert engine.calls == []  # same spoken text: clip reused
    name = f"page-{page['page_number']:02d}"
    assert rebuilt == [name]
    timing = load_json(AudioPaths(tmp_path / "audio").track_timing(name))
    assert "TILLY" in timing["lines"][index]["text"]


def test_role_change_with_same_pause_updates_timing(script, tmp_path):  # review fix 3
    run(script, tmp_path)
    page = next(p for p in script["pages"] if any(x["role"] == "reaction" for x in p["lines"]))
    index = next(i for i, x in enumerate(page["lines"]) if x["role"] == "reaction")
    page["lines"][index]["role"] = "question"
    *_, rebuilt, _ = run(script, tmp_path)
    name = f"page-{page['page_number']:02d}"
    assert rebuilt == [name]
    timing = load_json(AudioPaths(tmp_path / "audio").track_timing(name))
    assert timing["lines"][index]["role"] == "question"


def test_preview_rebuilt_after_interrupted_run(script, tmp_path):  # review fix 2
    _, paths, _, names, _, _ = run(script, tmp_path)
    assert build_preview(names, paths, CFG) is True
    assert build_preview(names, paths, CFG) is False
    old = paths.preview.read_bytes()
    run(script, tmp_path, engine=Quieter(), force=True)  # tracks rebuilt, preview not
    _, _, _, names, rebuilt, _ = run(script, tmp_path)
    assert rebuilt == []
    assert build_preview(names, paths, CFG) is True
    assert paths.preview.read_bytes() != old


def test_preview_without_sidecar_is_rebuilt(script, tmp_path):  # review fix 2
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    (paths.root / "preview.json").unlink()
    assert build_preview(names, paths, CFG) is True
    assert (paths.root / "preview.json").exists()


def test_between_tracks_change_rebuilds_only_the_preview(script, tmp_path):  # review fix 2
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    cfg = dataclasses.replace(CFG, pauses={**CFG.pauses, "between_tracks": 2.0})
    *_, rebuilt, _ = run(script, tmp_path, cfg=cfg)
    assert rebuilt == []
    assert build_preview(names, paths, cfg) is True
    total = sum(load_json(paths.track_timing(n))["duration"] for n in names)
    audio, sr = sf.read(paths.preview)
    assert len(audio) / sr == pytest.approx(total + 2.0 * (len(names) - 1), abs=0.05)


def test_engine_error_names_the_line(script, tmp_path):  # review fix 6
    target = script["pages"][0]["lines"][0]["text"]

    class Broken(FakeEngine):
        def synthesize(self, text, voice, seed):
            if text == target:
                raise RuntimeError("metal out of memory")
            return super().synthesize(text, voice, seed)

    with pytest.raises(sv.LineError, match=r"page 1 line 1 \(storyteller\): RuntimeError: metal"):
        run(script, tmp_path, engine=Broken())


def test_pitch_shift_error_names_the_line(script, tmp_path):  # review fix 6
    def failing(audio, sample_rate, semitones):
        if semitones == 3.0:
            raise sv.PitchShiftError("ffmpeg pitch shift failed: bad")
        return audio

    jobs = plan_jobs(script, CFG, {})
    with pytest.raises(sv.LineError, match=r"\(kid_younger\): PitchShiftError: ffmpeg"):
        synthesize_clips(jobs, AudioPaths(tmp_path / "a"), FakeEngine, CFG, shift=failing)


def test_lines_sharing_a_failing_clip_are_all_named(script, tmp_path):  # review fix 7
    text = "The very same words."
    script["intro"]["lines"][0]["text"] = text
    script["outro"]["lines"][0]["text"] = text
    script["outro"]["lines"][0]["speaker"] = script["intro"]["lines"][0]["speaker"]
    with pytest.raises(LineFailed) as excinfo:
        run(script, tmp_path, engine=FakeEngine(bad={text: 99}))
    assert {job.line_id for job, _ in excinfo.value.failures} == {"intro-01", "outro-01"}


def test_reused_clip_failing_current_checks_is_resynthesized(script, tmp_path):  # fix 8
    jobs, paths, *_ = run(script, tmp_path)
    meta = load_json(paths.clip_meta(jobs[0].key))
    meta["measure"]["peak"] = 1.0  # out of range for today's checks
    paths.clip_meta(jobs[0].key).write_text(sv.json.dumps(meta), encoding="utf-8")
    engine = FakeEngine()
    run(script, tmp_path, engine=engine)
    assert [text for text, *_ in engine.calls] == [jobs[0].spoken]
    assert load_json(paths.clip_meta(jobs[0].key))["measure"]["peak"] < 1.0


def test_plan_jobs_rejects_two_roles_sharing_a_voice(script):  # AC-2.4
    script["cast"]["kid_younger"]["voice"] = "zac"
    with pytest.raises(ConfigError, match="kid_older.*kid_younger.*zac"):
        plan_jobs(script, CFG, {})


def test_seed_is_not_part_of_clip_identity(script):  # ruling: seed change keeps clips
    other = dataclasses.replace(CFG, seed=99)
    assert [j.key for j in plan_jobs(script, CFG, {})] == [
        j.key for j in plan_jobs(script, other, {})
    ]


# --- FR-7 review fix: files edited on disk are rebuilt --------------------------------------


def _scribble(path):
    """Replace a WAV with different, still valid audio (an edit made outside synthesis)."""
    audio, sr = sf.read(path, dtype="float32")
    sf.write(path, audio * 0.5, sr, subtype="PCM_16")


def test_edited_track_wav_is_rebuilt(script, tmp_path):  # FR-7
    _, paths, _, names, _, _ = run(script, tmp_path)
    assert build_preview(names, paths, CFG) is True
    name = names[1]
    original = paths.track(name).read_bytes()
    preview = paths.preview.read_bytes()
    _scribble(paths.track(name))
    assert paths.track(name).read_bytes() != original
    *_, rebuilt, _ = run(script, tmp_path)
    assert rebuilt == [name]
    assert paths.track(name).read_bytes() == original
    assert load_json(paths.track_timing(name))["wav_sha256"] == sv.sha256_file(paths.track(name))
    # The restored track is byte-identical to the one the preview was joined from.
    assert build_preview(names, paths, CFG) is False
    assert paths.preview.read_bytes() == preview


def test_unchanged_rerun_rebuilds_nothing(script, tmp_path):  # FR-7
    _, paths, _, names, _, _ = run(script, tmp_path)
    assert build_preview(names, paths, CFG) is True
    *_, rebuilt, _ = run(script, tmp_path)
    assert rebuilt == []
    assert build_preview(names, paths, CFG) is False


def test_edited_preview_wav_is_rebuilt(script, tmp_path):  # FR-7
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    original = paths.preview.read_bytes()
    assert load_json(paths.preview_meta)["wav_sha256"] == sv.sha256_file(paths.preview)
    _scribble(paths.preview)
    assert build_preview(names, paths, CFG) is True
    assert paths.preview.read_bytes() == original


def test_preview_follows_a_track_edited_without_its_key(script, tmp_path):  # FR-7
    # A preview joined from a track file that no longer matches is stale, even if the track's
    # timing key still matches: the preview key covers the track audio itself.
    _, paths, _, names, _, _ = run(script, tmp_path)
    build_preview(names, paths, CFG)
    timing = load_json(paths.track_timing(names[0]))
    timing["wav_sha256"] = "0" * 64
    paths.track_timing(names[0]).write_text(json.dumps(timing), encoding="utf-8")
    assert build_preview(names, paths, CFG) is True


def test_timing_without_wav_sha_is_rebuilt_once(script, tmp_path):  # FR-7 upgrade path
    _, paths, _, names, _, _ = run(script, tmp_path)
    for name in names:
        timing = load_json(paths.track_timing(name))
        del timing["wav_sha256"]
        paths.track_timing(name).write_text(json.dumps(timing), encoding="utf-8")
    *_, rebuilt, _ = run(script, tmp_path)
    assert rebuilt == names
    *_, rebuilt, _ = run(script, tmp_path)
    assert rebuilt == []
