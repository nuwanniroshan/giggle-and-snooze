import numpy as np
import pytest

from video_pipeline.audio_checks import (
    CheckSettings,
    clip_problems,
    is_cut_off,
    longest_silence,
    measure_clip,
    pause_between,
    squeeze_silences,
    stitch,
    trim_silence,
)

SR = 24000
S = CheckSettings()
PAUSES = {"same_speaker": 0.4, "speaker_change": 0.7, "narration_to_qa": 1.0}


def tone(seconds, amp=0.3, freq=220.0):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(SR * seconds), dtype=np.float32)


def test_trim_keeps_at_most_the_edge_silence():  # AC-3.2
    trimmed = trim_silence(np.concatenate([silence(1), tone(2), silence(1)]), SR, -40, 0.1)
    assert 2.0 <= len(trimmed) / SR <= 2.0 + 0.2 + 0.04


def test_trim_of_pure_silence_is_empty():
    assert len(trim_silence(silence(1), SR, -40, 0.1)) == 0


def test_longest_silence():
    audio = np.concatenate([tone(1), silence(1.6), tone(1)])
    assert longest_silence(audio, SR, -40) == pytest.approx(1.6, abs=0.04)


def test_cut_off_detects_speech_at_the_end():
    assert is_cut_off(tone(2), SR, -40, 0.2)
    assert not is_cut_off(np.concatenate([tone(2), silence(0.3)]), SR, -40, 0.2)


def clean_measure(words=10, body=None):
    body = tone(4) if body is None else body
    raw = np.concatenate([silence(0.2), body, silence(0.5)])
    return measure_clip(raw, trim_silence(raw, SR, -40, 0.1), SR, words, S)


def test_clean_clip_has_no_problems():  # AC-3.1
    measure = clean_measure()
    assert clip_problems(measure, S) == []
    assert set(measure.to_dict()) == {"duration", "peak", "longest_silence", "wps", "cut_off"}


@pytest.mark.parametrize(
    ("raw", "words", "expected"),
    [
        (silence(1), 3, "too short or empty"),
        (np.concatenate([tone(4, amp=1.0), silence(0.3)]), 10, "clipping"),
        (np.concatenate([tone(2), silence(2), tone(2), silence(0.3)]), 10, "silence of"),
        (np.concatenate([tone(4), silence(0.3)]), 40, "speaking rate"),
        (np.concatenate([tone(4), silence(0.3)]), 1, "speaking rate"),
        (tone(4), 10, "cut off"),
    ],
)
def test_clip_problems(raw, words, expected):  # AC-3.1
    measure = measure_clip(raw, trim_silence(raw, SR, -40, 0.1), SR, words, S)
    assert any(expected in problem for problem in clip_problems(measure, S))


def test_stitch_places_clips_with_gaps():  # AC-1.3, AC-1.4
    audio, spans = stitch([tone(1), tone(2), tone(1)], [0.4, 1.0], SR)
    assert spans == [(0.0, 1.0), (1.4, 3.4), (4.4, 5.4)]
    assert len(audio) / SR == pytest.approx(5.4, abs=0.001)


def test_stitch_needs_one_gap_fewer_than_clips():
    with pytest.raises(ValueError):
        stitch([tone(1), tone(1)], [], SR)


@pytest.mark.parametrize(
    ("prev", "nxt", "expected"),
    [
        (("storyteller", "narration"), ("storyteller", "narration"), 0.4),
        (("storyteller", "narration"), ("kid_older", "question"), 1.0),
        (("storyteller", "narration"), ("storyteller", "answer"), 1.0),
        (("kid_older", "question"), ("kid_younger", "reaction"), 0.7),
        (("kid_older", "question"), ("kid_older", "question"), 0.4),
        (("kid_younger", None), ("storyteller", None), 0.7),
    ],
)
def test_pause_between(prev, nxt, expected):  # AC-1.4
    assert pause_between(*prev, *nxt, PAUSES) == expected


def test_squeeze_silences_shortens_long_stalls_only():
    audio = np.concatenate([tone(1), silence(2.4), tone(1), silence(0.3), tone(1)])
    out = squeeze_silences(audio, SR, -40, 0.6)
    assert abs(len(out) / SR - (3 + 0.6 + 0.3)) < 0.05
    assert squeeze_silences(tone(1), SR, -40, 0.6) is not None
