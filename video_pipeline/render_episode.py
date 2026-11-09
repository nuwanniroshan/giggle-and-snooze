"""Stage 3 render (spec 003): FFmpeg command builders and runner.

Each section becomes its own silent H.264 clip with an exact frame count, each transition a short
clip made from the neighbouring sections' boundary frames, and all clips share encode settings so
the concat demuxer can join them with `-c copy` before the master audio is muxed in.

The builders are pure (they return argument lists or filtergraph strings); `run_ffmpeg` and
`probe` are the only places that start a process.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from video_pipeline.timeline import RenderConfig, Section, transition_frames, zoompan_expr

VIDEO_TIMESCALE = 15360  # shared by every clip so concat `-c copy` keeps exact timing
_STDERR_TAIL = 600
# FFmpeg 8 encodes the frames' colour properties over the -color_* options, so every graph tags
# its frames BT.709 itself (the encode options are kept as well).
_BT709_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
_OUTPUT_FORMAT = f"{_BT709_TAGS},format=yuv420p"


class RenderError(Exception):
    """A render step failed; the message names the step and the tool's error."""


def _sec(value: float) -> str:
    return f"{value:.3f}"


def ENCODE_ARGS(cfg: RenderConfig) -> list[str]:  # noqa: N802 - named as a constant in the plan
    """Encoder settings shared by every section and transition clip (silent, BT.709)."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        cfg.video.preset,
        "-crf",
        str(cfg.video.crf),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(cfg.fps),
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-video_track_timescale",
        str(VIDEO_TIMESCALE),
        "-an",
    ]


def _to_bt709_yuv(width: int, height: int) -> str:
    """Scale step converting RGB pictures to limited-range BT.709 4:2:0 (matching the tags)."""
    return f"scale={width}:{height}:out_color_matrix=bt709:out_range=tv,format=yuv420p"


def section_filtergraph(section: Section, caption_count: int, cfg: RenderConfig) -> str:
    """The section's filtergraph: upscaled canvas → zoompan camera → caption overlays → (outro)
    fade-out → `[vout]` in yuv420p. Input 0 is the canvas, inputs 1..K the caption stills, in
    the order of `section.captions`; overlay times are section-relative seconds."""
    if caption_count != len(section.captions):
        raise ValueError(
            f"section {section.name} has {len(section.captions)} captions, "
            f"{caption_count} caption inputs given"
        )
    w, h, fps = cfg.width, cfg.height, cfg.fps
    z, x, y = zoompan_expr(section.camera)
    chains = [
        f"[0:v]{_to_bt709_yuv(cfg.upscale * w, cfg.upscale * h)},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={section.frames}:s={w}x{h}:fps={fps}[base]"
    ]
    label = "base"
    for index, card in enumerate(section.captions, start=1):
        enable = f"between(t,{_sec(card.start)},{_sec(card.end)})"
        chains.append(
            f"[{label}][{index}:v]overlay=x=0:y=0:eof_action=repeat:enable='{enable}'[v{index}]"
        )
        label = f"v{index}"
    tail: list[str] = []
    fade = section.camera.fade_out_frames
    if section.kind == "outro" and fade > 0:
        start = (section.frames - fade) / fps
        tail.append(f"fade=t=out:st={_sec(start)}:d={_sec(fade / fps)}")
    tail.append(_OUTPUT_FORMAT)
    chains.append(f"[{label}]{','.join(tail)}[vout]")
    return ";\n".join(chains)


def _input_still(path: Path, fps: int) -> list[str]:
    return ["-framerate", str(fps), "-i", str(path)]


def section_command(
    section: Section,
    canvas_png: Path,
    caption_pngs: Sequence[Path],
    script_path: Path,
    out_path: Path,
    cfg: RenderConfig,
) -> list[str]:
    """FFmpeg arguments rendering one section clip; the filtergraph (from `section_filtergraph`)
    is read from `script_path`. Every still is a single frame: zoompan emits `d` frames from the
    canvas and overlay repeats each caption's frame (`eof_action=repeat`). Looping the full-frame
    captions instead decodes every one of them on every frame (about 3x slower at 1080p)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *_input_still(canvas_png, cfg.fps)]
    for caption in caption_pngs:
        cmd += _input_still(caption, cfg.fps)
    cmd += [
        "-filter_complex_script",
        str(script_path),
        "-map",
        "[vout]",
        "-frames:v",
        str(section.frames),
        *ENCODE_ARGS(cfg),
        str(out_path),
    ]
    return cmd


def transition_command(
    last_png: Path, first_png: Path, out_path: Path, cfg: RenderConfig
) -> list[str]:
    """FFmpeg arguments for a transition clip: the last frame of section A into the first frame of
    section B with `xfade`, exactly `round(transition * fps)` frames."""
    seconds = _sec(cfg.transition)
    norm = f"{_to_bt709_yuv(cfg.width, cfg.height)},fps={cfg.fps},settb=AVTB"
    graph = (
        f"[0:v]{norm}[a];[1:v]{norm}[b];"
        f"[a][b]xfade=transition={cfg.transition_style}:duration={seconds}:offset=0,"
        f"{_OUTPUT_FORMAT}[vout]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for still in (last_png, first_png):
        cmd += ["-loop", "1", "-framerate", str(cfg.fps), "-t", seconds, "-i", str(still)]
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[vout]",
        "-frames:v",
        str(transition_frames(cfg)),
        *ENCODE_ARGS(cfg),
        str(out_path),
    ]
    return cmd


def frame_grab_command(video: Path, out_png: Path, last: bool) -> list[str]:
    """FFmpeg arguments writing the first or the last frame of `video` as a PNG.

    Last: decode only the final second (`-sseof -1`) and keep overwriting one image
    (`-update 1`), so the file left behind is the final frame."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if last:
        cmd += ["-sseof", "-1", "-i", str(video), "-map", "0:v:0", "-update", "1"]
    else:
        cmd += ["-i", str(video), "-map", "0:v:0", "-frames:v", "1"]
    cmd += ["-f", "image2", "-c:v", "png", str(out_png)]
    return cmd


def _concat_entry(path: Path) -> str:
    escaped = str(Path(path).resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def write_concat_list(clips: Sequence[Path], list_file: Path) -> None:
    """Write a concat-demuxer list of `clips` (absolute, quoted) atomically."""
    list_file = Path(list_file)
    list_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = list_file.with_name(list_file.name + ".tmp")
    try:
        tmp.write_text("".join(_concat_entry(c) + "\n" for c in clips), encoding="utf-8")
        os.replace(tmp, list_file)  # noqa: PTH105 - atomic replace, as elsewhere
    finally:
        tmp.unlink(missing_ok=True)


def concat_command(list_file: Path, out_path: Path) -> list[str]:
    """FFmpeg arguments joining the listed clips without re-encoding."""
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-map",
        "0:v",
        "-c",
        "copy",
        str(out_path),
    ]


def mux_command(video: Path, wav: Path, out_path: Path, cfg: RenderConfig) -> list[str]:
    """FFmpeg arguments muxing the joined video with the master audio as stereo AAC, moov first."""
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-i",
        str(wav),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        cfg.video.audio_bitrate,
        "-ar",
        str(cfg.video.audio_rate),
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out_path),
    ]


def _tail(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return (text or "").strip()[-_STDERR_TAIL:]


def run_ffmpeg(cmd: list[str], what: str) -> None:
    """Run an FFmpeg command; a missing binary or a failure becomes RenderError naming `what`."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RenderError("ffmpeg not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        detail = _tail(exc.stderr) or f"ffmpeg exit {exc.returncode}"
        raise RenderError(f"{what}: {detail}") from None


def probe(path: Path) -> dict:
    """ffprobe's streams and format of `path`, as parsed JSON."""
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RenderError("ffprobe not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        detail = _tail(exc.stderr) or f"ffprobe exit {exc.returncode}"
        raise RenderError(f"probe {path}: {detail}") from None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError(f"probe {path}: unreadable ffprobe output ({exc})") from None
