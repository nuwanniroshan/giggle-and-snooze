"""Spec 012 FR-5 (Story 5): the cross-dissolve planner and filtergraphs (pure, no FFmpeg)."""

import pytest

from video_pipeline import channel_blend as cb

B = 24  # 0.8 s at 30 fps
B15 = 45  # 1.5 s at 30 fps (the shipped channel_blend_seconds)


def _keys(length, every=150):
    return list(range(0, length, every))


def _covered(steps, lengths):
    """Frames of each part that the steps take, as sorted (start, end) pieces."""
    seen = {i: [] for i in range(len(lengths))}
    for step in steps:
        for cut in step.cuts:
            seen[cut.part].append((cut.start, cut.end))
    return {i: sorted(v) for i, v in seen.items()}


def _assert_partition(steps, lengths):
    for part, pieces in _covered(steps, lengths).items():
        edge = 0
        for start, end in pieces:
            assert start == edge and end > start
            edge = end
        assert edge == lengths[part]


def test_total_length_is_the_parts_minus_one_blend_per_join():  # AC-5.1, AC-5.2
    lengths = [300, 36, 27000, 515]
    steps = cb.plan_blend(lengths, [_keys(n) for n in lengths], B)
    _assert_partition(steps, lengths)
    assert sum(cb.step_frames(s, B) for s in steps) == sum(lengths) - 3 * B


def test_only_windows_around_joins_are_blended_the_rest_is_copied():
    lengths = [300, 27000]
    steps = cb.plan_blend(lengths, [_keys(n) for n in lengths], B)
    kinds = [s.kind for s in steps]
    assert kinds == ["copy", "blend", "copy"]
    window = steps[1]
    assert [c.part for c in window.cuts] == [0, 1]
    assert all(c.frames < 400 for c in window.cuts)  # small windows, not whole parts


def test_window_starts_on_a_keyframe_and_covers_the_overlap():
    steps = cb.plan_blend([300, 1000], [[0, 100, 200], [0, 90, 180]], B)
    tail, head = steps[1].cuts
    assert tail.start == 200 and tail.end == 300  # last keyframe at or before 300 - 24
    assert head.start == 0 and head.end == 90  # first keyframe at or after 24


def test_clip_shorter_than_two_blends_goes_whole_into_one_chain():  # AC-5.3
    lengths = [300, 36, 27000]  # 36 frames = 1.2 s at 30 fps
    steps = cb.plan_blend(lengths, [_keys(n) for n in lengths], B)
    chain = next(s for s in steps if len(s.cuts) == 3)
    assert chain.cuts[1] == cb.Cut(1, 0, 36)
    assert cb.step_frames(chain, B) == chain.cuts[0].frames + 36 + chain.cuts[2].frames - 2 * B


def test_clip_without_inner_keyframes_is_taken_whole():
    steps = cb.plan_blend([300, 515], [[0, 150], [0]], B)  # the second has only frame 0
    _assert_partition(steps, [300, 515])
    assert [s.kind for s in steps] == ["copy", "blend"]
    assert steps[1].cuts[1] == cb.Cut(1, 0, 515)


def test_single_part_needs_no_blend():
    assert cb.plan_blend([100], [[0]], B) == [cb.Step("copy", (cb.Cut(0, 0, 100),))]


def test_part_shorter_than_the_blend_is_refused():
    with pytest.raises(cb.BlendError, match="part 1"):
        cb.plan_blend([300, 10, 300], [[0], [0], [0]], B)


def test_blend_must_be_positive():
    with pytest.raises(cb.BlendError):
        cb.plan_blend([100, 100], [[0], [0]], 0)


def test_video_graph_chains_xfade_with_offsets_that_follow_the_overlaps():
    graph = cb.video_blend_graph([100, 36, 100], B, 30, "scale=1:1", "setparams=x")
    assert graph.count("xfade=transition=fade:duration=0.800000") == 2
    assert "offset=2.533333" in graph  # (100 - 24) / 30
    assert "offset=2.933333" in graph  # (100 + 36 - 24 - 24) / 30
    assert graph.endswith("[x2]setparams=x[vout]")


def test_audio_graph_trims_each_part_to_its_picture_and_crossfades_between():
    graph = cb.audio_blend_graph([100, 36, 100], B, 30, 48000)
    assert graph.count("acrossfade=d=0.800000") == 2
    assert "[1:a]" in graph and "[3:a]" in graph and "[0:a]" not in graph
    assert "atrim=end=1.200000" in graph and "apad=whole_dur=1.200000" in graph
    assert graph.endswith("[aout]")


def _realistic(lengths, every=48):
    return [_keys(n, every) for n in lengths]


def test_one_and_a_half_second_blend_over_logo_opening_clip_and_body():  # AC-5.1, AC-5.2
    lengths = [300, 279, 5000]  # logo, 9.3 s opening clip, body
    steps = cb.plan_blend(lengths, _realistic(lengths), B15)
    _assert_partition(steps, lengths)
    assert cb.blend_frames(lengths, B15) == sum(lengths) - 2 * B15
    assert sum(cb.step_frames(s, B15) for s in steps) == sum(lengths) - 2 * B15


def test_one_and_a_half_second_blend_with_a_close_clip_after_the_body():  # AC-5.1, AC-5.2
    lengths = [300, 279, 5000, 300]  # logo, opening clip, body, 10 s ending clip
    steps = cb.plan_blend(lengths, _realistic(lengths), B15)
    _assert_partition(steps, lengths)
    assert sum(cb.step_frames(s, B15) for s in steps) == sum(lengths) - 3 * B15
    assert sum(cb.step_frames(s, B15) for s in steps) == cb.blend_frames(lengths, B15)
