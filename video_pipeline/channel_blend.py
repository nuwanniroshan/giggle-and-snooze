"""Cross-dissolve joins between the brand clips and the episode (spec 012, FR-5).

Pure planning and filtergraph text; `render_episode` runs the FFmpeg commands. Lengths are in
whole frames. Each join between neighbouring parts overlaps them by `blend` frames. Only the small
windows around the joins are re-encoded (picture `xfade`); everything else is cut from the source
at keyframes and joined with `-c copy`, so memory stays low however long the body is. The sound is
one streaming `acrossfade` chain over the whole programme, which keeps it gapless and in step with
the picture (the picture and the sound both lose exactly `blend` frames per join).

A part too short to hold a copied middle between its two windows (the opening clip is ~1.2 s)
is taken whole into one chained blend: previous tail, the short part, next head.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class BlendError(ValueError):
    """The parts cannot be blended (for example one is shorter than the dissolve)."""


@dataclass(frozen=True)
class Cut:
    """Frames [start, end) of part number `part`."""

    part: int
    start: int
    end: int

    @property
    def frames(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Step:
    """`copy`: one cut taken as is. `blend`: cuts dissolved into each other, in order."""

    kind: str
    cuts: tuple[Cut, ...]


def blend_frames(lengths: Sequence[int], blend: int) -> int:
    """Length of `lengths` dissolved one into the next, each overlapping by `blend` frames."""
    return sum(lengths) - blend * (len(lengths) - 1)


def plan_blend(
    lengths: Sequence[int], keyframes: Sequence[Sequence[int]], blend: int
) -> list[Step]:
    """The steps that join the parts in order with `blend`-frame dissolves.

    `keyframes[i]` lists the keyframe frame numbers of part i (0 included). The steps cover every
    frame of every part exactly once, so their lengths add up to `blend_frames(lengths, blend)`
    once blends are counted at their own (shorter) length."""
    if blend <= 0:
        raise BlendError("blend must be at least one frame")
    count = len(lengths)
    for index, length in enumerate(lengths):
        if count > 1 and length < blend:
            raise BlendError(
                f"part {index} is {length} frames, shorter than the {blend}-frame blend"
            )
    head: list[int] = []  # frames of part i re-encoded after its join with the previous part
    tail: list[int] = []  # first frame of part i re-encoded before its join with the next part
    for i, length in enumerate(lengths):
        keys = sorted(keyframes[i])
        after = [k for k in keys if k >= blend]
        before = [k for k in keys if k <= length - blend]
        head.append(0 if i == 0 else (after[0] if after else length))
        tail.append(length if i == count - 1 else (before[-1] if before else 0))
    steps: list[Step] = []
    chain: list[Cut] = []
    for i, length in enumerate(lengths):
        if head[i] > tail[i]:  # too short for a copied middle: taken whole into the chain
            chain.append(Cut(i, 0, length))
            continue
        if i > 0:
            chain.append(Cut(i, 0, head[i]))
            steps.append(Step("blend", tuple(chain)))
            chain = []
        if head[i] < tail[i]:
            steps.append(Step("copy", (Cut(i, head[i], tail[i]),)))
        if i < count - 1:
            chain = [Cut(i, tail[i], length)]
    return steps


def step_frames(step: Step, blend: int) -> int:
    """Frames the step produces (a blend loses `blend` frames per internal join)."""
    if step.kind == "copy":
        return step.cuts[0].frames
    return blend_frames([c.frames for c in step.cuts], blend)


def video_blend_graph(lengths: Sequence[int], blend: int, fps: int, norm: str, tail: str) -> str:
    """Filtergraph chaining `xfade=fade` over inputs of `lengths` frames (input i is `[i:v]`).

    `norm` normalises each input (size, pixel format, timebase); `tail` closes the output
    (colour tags); the result is labelled `[vout]`."""
    seconds = f"{blend / fps:.6f}"
    parts = [f"[{i}:v]{norm},setpts=PTS-STARTPTS[v{i}]" for i in range(len(lengths))]
    label, acc = "v0", lengths[0]
    for i in range(1, len(lengths)):
        offset = (acc - blend) / fps
        out = f"x{i}"
        parts.append(
            f"[{label}][v{i}]xfade=transition=fade:duration={seconds}:offset={offset:.6f}[{out}]"
        )
        label, acc = out, acc + lengths[i] - blend
    parts.append(f"[{label}]{tail}[vout]")
    return ";".join(parts)


def audio_blend_graph(
    lengths: Sequence[int], blend: int, fps: int, sample_rate: int, first_input: int = 1
) -> str:
    """Filtergraph chaining `acrossfade` over whole parts (part i is input `first_input + i`).

    Each part is trimmed or padded to its picture length first, so the sound stays frame-exact
    against the picture; the result is labelled `[aout]`."""
    seconds = f"{blend / fps:.6f}"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo"
    parts = []
    for i, length in enumerate(lengths):
        end = f"{length / fps:.6f}"
        parts.append(
            f"[{first_input + i}:a]{fmt},atrim=end={end},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={end}[a{i}]"
        )
    label = "a0"
    for i in range(1, len(lengths)):
        out = "aout" if i == len(lengths) - 1 else f"y{i}"
        parts.append(f"[{label}][a{i}]acrossfade=d={seconds}:c1=qsin:c2=qsin[{out}]")
        label = out
    if len(lengths) == 1:
        parts.append("[a0]anull[aout]")
    return ";".join(parts)
