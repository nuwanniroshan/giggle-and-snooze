"""Stage 3 render (spec 003): FFmpeg command builders and runner.

Each section becomes its own silent H.264 clip with an exact frame count, each transition a short
clip made from the neighbouring sections' boundary frames, and all clips share encode settings so
the concat demuxer can join them with `-c copy` before the master audio is muxed in.

The builders are pure (they return argument lists or filtergraph strings); `run_ffmpeg` and
`probe` are the only places that start a process. `render_video` gates on approved audio, resolves
the pictures, caches sections/transitions by key and assembles the final video.

Run: uv run python -m video_pipeline.render_episode --book SLUG [--force] [--preview-page N]
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, UnidentifiedImageError

from video_pipeline import channel_blend as blend
from video_pipeline import sound_library, timeline
from video_pipeline.animate import (
    AnimationError,
    PageAnimation,
    PageEligibility,
    animated_key,
    canvas_frames,
    load_page_animation,
    page_eligibility,
    picture_frames,
    rest_frames,
)
from video_pipeline.contract import ANIMATION_OVERRIDE_RANGE, ConfigError, _animation_errors
from video_pipeline.cover_design import CoverPalette, compose_cover, cover_palette
from video_pipeline.cut_layers import layers_dir
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.frames import (
    PAGE_TURN_FILE,
    PictureError,
    SoundError,
    _read_cache,
    canvas_parts,
    compose_canvas,
    load_page_turn,
    mix_master,
    page_turn_sound,
    render_caption,
)
from video_pipeline.grade import (
    Loudness,
    LoudnessError,
    grade_command,
    grade_key,
    is_active,
    loudness_command,
    measure_command,
    parse_ebur128_summary,
    parse_loudnorm_report,
    resolve_grade,
)
from video_pipeline.library import PROJECT_ROOT, BookFolder, LibraryError, book_folder
from video_pipeline.motion_plan import PLAN_DIR
from video_pipeline.sound_library import Cue, ambience_problems, build_cues, render_bed
from video_pipeline.synthesize_voices import (
    AudioPaths,
    approval_problems,
    current_settings_sha,
    load_voice_config,
    sha256_file,
    write_wav_atomic,
)
from video_pipeline.timeline import (
    GradeSettings,
    RenderConfig,
    Section,
    build_sections,
    load_render_config,
    page_section_name,
    section_key,
    section_times,
    total_frames,
    transition_frames,
    transition_key,
    zoompan_expr,
)

VIDEO_TIMESCALE = 15360  # shared by every clip so concat `-c copy` keeps exact timing
_STDERR_TAIL = 600
# FFmpeg 8 encodes the frames' colour properties over the -color_* options, so every graph tags
# its frames BT.709 itself (the encode options are kept as well).
_BT709_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
_OUTPUT_FORMAT = f"{_BT709_TAGS},format=yuv420p"


class RenderError(Exception):
    """A render step failed; the message names the step and the tool's error."""


class AudioNotApproved(RenderError):
    """The audio gate refused: the audio is missing, unapproved or changed since approval."""


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
    _check_caption_count(section, caption_count)
    w, h, fps = cfg.width, cfg.height, cfg.fps
    z, x, y = zoompan_expr(section.camera)
    head = (
        f"[0:v]{_to_bt709_yuv(cfg.upscale * w, cfg.upscale * h)},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={section.frames}:s={w}x{h}:fps={fps}[base]"
    )
    return _graph_with_tail(head, section, cfg)


def animated_section_filtergraph(section: Section, caption_count: int, cfg: RenderConfig) -> str:
    """An animated section's filtergraph (spec 010 FR-12): input 0 is the piped rgb24 frames with
    the camera already applied, converted to BT.709 yuv420p; then the same caption overlays and
    (outro) fade/tag tail as `section_filtergraph`."""
    _check_caption_count(section, caption_count)
    return _graph_with_tail(f"[0:v]{_to_bt709_yuv(cfg.width, cfg.height)}[base]", section, cfg)


def _check_caption_count(section: Section, caption_count: int) -> None:
    if caption_count != len(section.captions):
        raise ValueError(
            f"section {section.name} has {len(section.captions)} captions, "
            f"{caption_count} caption inputs given"
        )


def _graph_with_tail(head: str, section: Section, cfg: RenderConfig) -> str:
    """`head` (ending in `[base]`), then one overlay per caption, the outro fade and the tags."""
    fps = cfg.fps
    chains = [head]
    label = "base"
    for index, card in enumerate(section.captions, start=1):
        # Half-open [start, end): contiguous cards never share a boundary frame. The quotes keep
        # the commas away from the filtergraph parser.
        enable = f"gte(t,{_sec(card.start)})*lt(t,{_sec(card.end)})"
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


def animated_section_command(
    section: Section,
    caption_pngs: Sequence[Path],
    script_path: Path,
    out_path: Path,
    cfg: RenderConfig,
) -> list[str]:
    """FFmpeg arguments encoding an animated section: input 0 is rgb24 W×H frames on stdin (full
    range by definition; no -color_range on it), inputs 1..K the caption stills, exactly
    `section.frames` frames out (AC-1.6)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cfg.width}x{cfg.height}"]
    cmd += ["-framerate", str(cfg.fps), "-i", "pipe:0"]
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


def _ffmpeg_failure(
    cmd: list[str], what: str, stderr: str | bytes | None, code: int
) -> RenderError:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    if "No space left on device" in (stderr or ""):
        folder = Path(cmd[-1]).parent
        return RenderError(f"{what}: not enough disk space to write video in {folder}")
    return RenderError(f"{what}: {_tail(stderr) or f'ffmpeg exit {code}'}")


def run_ffmpeg(cmd: list[str], what: str) -> None:
    """Run an FFmpeg command; a missing binary or a failure becomes RenderError naming `what`."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RenderError(f"{what}: ffmpeg not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        raise _ffmpeg_failure(cmd, what, exc.stderr, exc.returncode) from None


def measure_ffmpeg(cmd: list[str], what: str) -> str:
    """Run an FFmpeg command whose result is its report on stderr (spec 013 loudness steps)."""
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise RenderError(f"{what}: ffmpeg not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        raise _ffmpeg_failure(cmd, what, exc.stderr, exc.returncode) from None
    return result.stderr


_FRAME_QUEUE = 3  # frames buffered between the drawing loop and the pipe writer


def run_ffmpeg_frames(cmd: list[str], frames: Iterable[bytes], what: str) -> None:
    """Run an FFmpeg command that reads raw frames on stdin (spec 010 FR-12, spike fact 8).

    A writer thread feeds the pipe from a small queue while this thread draws the next frame;
    stderr is drained on its own thread. Close stdin, then collect stderr, then wait. A failure
    becomes RenderError naming `what` with the stderr tail, as in `run_ffmpeg`; an error while
    drawing frames kills FFmpeg and propagates unchanged."""
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
    except FileNotFoundError:
        raise RenderError(f"{what}: ffmpeg not found; brew install ffmpeg") from None
    assert proc.stdin is not None and proc.stderr is not None
    stdin, stderr = proc.stdin, proc.stderr
    err: list[bytes] = []
    reader = threading.Thread(target=lambda: err.append(stderr.read()), daemon=True)
    reader.start()
    pending: queue.Queue[bytes | None] = queue.Queue(maxsize=_FRAME_QUEUE)
    broken = threading.Event()

    def write() -> None:
        while (item := pending.get()) is not None:
            if broken.is_set():
                continue  # FFmpeg is gone: drain the queue so the producer never blocks
            try:
                stdin.write(item)
            except OSError:  # BrokenPipeError: FFmpeg exited early; its stderr says why
                broken.set()

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    try:
        for frame in frames:
            if broken.is_set():
                break
            pending.put(frame)
    except BaseException:
        proc.kill()
        raise
    finally:
        pending.put(None)
        writer.join()
        try:
            stdin.close()
        except OSError:
            broken.set()
        reader.join()
        code = proc.wait()
    if code != 0 or broken.is_set():
        raise _ffmpeg_failure(cmd, what, b"".join(err), code)


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


# --- paths, pictures and the approval gate (Phase 4) ----------------------------------------


@dataclass(frozen=True)
class VideoPaths:
    """Where one book's video work lives: `build/<slug>/video/`."""

    root: Path

    @property
    def sections(self) -> Path:
        return self.root / "sections"

    @property
    def transitions(self) -> Path:
        return self.root / "transitions"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    def final(self, slug: str) -> Path:
        return self.root / f"{slug}.mp4"

    def record(self, slug: str) -> Path:
        return self.root / f"{slug}.json"

    def ungraded(self, slug: str) -> Path:
        """The joined video before the final grade (spec 013): kept so a regrade re-runs only
        the grading step."""
        return self.root / f"{slug}.ungraded.mp4"

    def preview(self, page_number: int) -> Path:
        """A page preview (spec 010 FR-15): `preview/page_NN.mp4`."""
        return self.root / "preview" / f"page_{page_number:02d}.mp4"


@dataclass(frozen=True)
class ResolvedPictures:
    """`files`: logical picture id → file; `by_section`: section name → logical picture id."""

    files: dict[str, Path]
    by_section: dict[str, str]


NOOKS = ("intro_nook", "outro_nook")


def _illustration_file(page: dict) -> str:
    return page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png"


def _page_picture(folder: BookFolder, page: dict) -> Path:
    """A story page's illustration; missing → RenderError naming the page and file."""
    path = folder.illustrations / _illustration_file(page)
    if not path.is_file():
        raise RenderError(f"page {int(page['page_number'])}: illustration not found: {path}")
    return path


def resolve_pictures(book: dict, folder: BookFolder, shared_nooks: Path) -> ResolvedPictures:
    """Every picture the episode shows (FR-1, AC-6.1, AC-6.3).

    Pages and covers come from the book's `illustrations/` (missing → RenderError naming the page
    and file). A nook image is taken from the book's `nooks/` first, then `shared_nooks`; with
    neither, the intro shows the front cover and the outro the back cover.
    """
    illustrations = folder.illustrations
    files: dict[str, Path] = {}
    for page in book["pages"]:
        files[f"page_{int(page['page_number']):02d}"] = _page_picture(folder, page)
    for cover in ("cover_front", "cover_back"):
        path = illustrations / f"{cover}.png"
        if not path.is_file():
            raise RenderError(f"{cover.replace('_', ' ')} not found: {path}")
        files[cover] = path
    by_section = {"intro": "cover_front", "back-cover": "cover_back", "outro": "cover_back"}
    for nook in NOOKS:
        for nook_dir in (folder.nooks, Path(shared_nooks)):
            path = nook_dir / f"{nook}.png"
            if path.exists():
                files[nook] = path
                by_section[nook.removesuffix("_nook")] = nook
                break
    for page in book["pages"]:
        number = int(page["page_number"])
        by_section[page_section_name(number)] = f"page_{number:02d}"
    return ResolvedPictures(files, by_section)


def _check_picture(path: Path) -> None:
    """Fail early (before any rendering) on a picture Pillow cannot open."""
    try:
        with Image.open(path) as im:
            im.verify()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError) as exc:
        raise PictureError(path, str(exc)) from None


def _load_tracks(
    audio: AudioPaths, names: list[str]
) -> tuple[dict[str, dict], dict[str, np.ndarray], int]:
    """Timing JSON and samples of every track; a missing file → RenderError naming it (AC-2.3)."""
    timings: dict[str, dict] = {}
    tracks: dict[str, np.ndarray] = {}
    rates: set[int] = set()
    for name in names:
        for path in (audio.track(name), audio.track_timing(name)):
            if not path.is_file():
                raise RenderError(f"missing audio for {name}: {path}")
        try:
            timings[name] = json.loads(audio.track_timing(name).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RenderError(
                f"unreadable track timing {audio.track_timing(name)}: {exc}"
            ) from None
        try:
            samples, rate = sf.read(audio.track(name), dtype="float32", always_2d=False)
        except (OSError, sf.LibsndfileError, RuntimeError) as exc:
            raise RenderError(f"unreadable audio track {audio.track(name)}: {exc}") from None
        if samples.ndim != 1:
            raise RenderError(f"audio track {audio.track(name)} is not mono")
        tracks[name] = samples
        rates.add(int(rate))
    if len(rates) != 1:
        raise RenderError(f"audio tracks in {audio.tracks} have different sample rates: {rates}")
    return timings, tracks, rates.pop()


# --- render ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration: float
    rendered: int
    reused: int
    up_to_date: bool


Runner = Callable[[list[str], str], None]
FrameRunner = Callable[[list[str], Iterable[bytes], str], None]
Progress = Callable[[str, str], None]
Measurer = Callable[[list[str], str], str]  # runs FFmpeg and returns its stderr (loudness reports)


def _digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _reusable(clip: Path, meta: Path, key: str, force: bool) -> bool:
    if force or not clip.exists():
        return False
    data = _read_json(meta)
    return data is not None and data.get("key") == key


def _tmp_clip(clip: Path) -> Path:
    return clip.with_name(clip.stem + ".tmp.mp4")


def _run_to(run: Runner, cmd_for: Callable[[Path], list[str]], out: Path, what: str) -> None:
    """Run a command writing to a temporary file, then move it to `out` (AC-7.4)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_clip(out)
    try:
        run(cmd_for(tmp), what)
        os.replace(tmp, out)  # noqa: PTH105 - atomic replace
    finally:
        tmp.unlink(missing_ok=True)


def _run_frames_to(
    frame_run: FrameRunner,
    cmd_for: Callable[[Path], list[str]],
    frames: Iterable[bytes],
    out: Path,
    what: str,
) -> None:
    """`_run_to` for a command fed frames on stdin: write to a temporary file, then move it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_clip(out)
    try:
        frame_run(cmd_for(tmp), frames, what)
        os.replace(tmp, out)  # noqa: PTH105 - atomic replace
    finally:
        tmp.unlink(missing_ok=True)


def animated_canvas_frames(
    anim: PageAnimation, picture: Path, section: Section, cfg: RenderConfig
) -> Iterator[bytes]:
    """The section's rgb24 W×H frames: each animated picture frame placed on the still canvas's
    backdrop (made once per section) with the section's camera applied (spec 010 FR-12)."""
    try:
        with Image.open(picture) as im:
            rgb = im.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise PictureError(picture, str(exc)) from None
    backdrop, size, offset = canvas_parts(rgb, cfg)
    # frames at rest paste the picture resized exactly as the still canvas does (AC-1.4)
    rest = np.asarray(rgb.resize(size, Image.Resampling.LANCZOS), dtype=np.uint8)
    return canvas_frames(
        picture_frames(anim, section.frames),
        np.asarray(backdrop, dtype=np.uint8),
        size,
        offset,
        section.camera,
        section.frames,
        cfg.width,
        cfg.height,
        cfg.upscale,
        rest,
        min(rest_frames(anim), section.frames),
    )


def _render_captions(section: Section, work: Path, cfg: RenderConfig) -> list[Path]:
    captions = []
    for number, card in enumerate(section.captions):
        path = work / f"caption_{number:03d}.png"
        render_caption(card.text, cfg, path)
        captions.append(path)
    return captions


def _render_section(
    section: Section,
    picture: Path,
    clip: Path,
    work: Path,
    cfg: RenderConfig,
    run: Runner,
    frame_run: FrameRunner = run_ffmpeg_frames,
    anim: PageAnimation | None = None,
) -> None:
    """Render one section clip: the still canvas through zoompan, or, given `anim`, the animated
    frames piped through `frame_run` with the same captions, tail and encoding."""
    if anim is not None:
        if anim.frames != section.frames:
            raise ValueError(f"animation has {anim.frames} frames, section {section.frames}")
        work.mkdir(parents=True, exist_ok=True)
        captions = _render_captions(section, work, cfg)
        graph = work / "graph.txt"
        graph.write_text(
            animated_section_filtergraph(section, len(captions), cfg), encoding="utf-8"
        )
        _run_frames_to(
            frame_run,
            lambda out: animated_section_command(section, captions, graph, out, cfg),
            animated_canvas_frames(anim, picture, section, cfg),
            clip,
            f"section {section.name}",
        )
        return
    canvas = work / "canvas.png"
    compose_canvas(picture, cfg, canvas)
    captions = _render_captions(section, work, cfg)
    graph = work / "graph.txt"
    graph.write_text(section_filtergraph(section, len(captions), cfg), encoding="utf-8")
    _run_to(
        run,
        lambda out: section_command(section, canvas, captions, graph, out, cfg),
        clip,
        f"section {section.name}",
    )


def _render_transition(
    a: Path, b: Path, names: tuple[str, str], clip: Path, work: Path, cfg: RenderConfig, run: Runner
) -> None:
    work.mkdir(parents=True, exist_ok=True)
    last_png, first_png = work / f"{names[0]}_last.png", work / f"{names[1]}_first.png"
    run(frame_grab_command(a, last_png, last=True), f"grab last frame of {names[0]}")
    run(frame_grab_command(b, first_png, last=False), f"grab first frame of {names[1]}")
    _run_to(
        run,
        lambda out: transition_command(last_png, first_png, out, cfg),
        clip,
        f"transition {names[0]}__{names[1]}",
    )


@dataclass(frozen=True)
class _Inputs:
    """Everything the video is made from, loaded and checked without rendering or writing."""

    slug: str
    cfg: RenderConfig
    audio: AudioPaths
    track_names: list[str]
    tracks: dict[str, np.ndarray]
    sample_rate: int
    pictures: ResolvedPictures
    sections: list[Section]
    keys: list[str]
    trans_keys: list[str]
    paths: VideoPaths
    ambience: list[Cue] = field(default_factory=list)
    ambience_key: list = field(default_factory=list)
    eligibility: dict[str, PageEligibility] = field(default_factory=dict)
    still_notes: list[tuple[str, str]] = field(default_factory=list)
    still_keys: list[str] = field(default_factory=list)  # equal to `keys` for still sections
    channel_intro_sha: str = ""  # sha256 of the channel intro clip, "" when there is none
    bookend_clips: list[ChannelClip] = field(default_factory=list)  # spec 012: open, then close
    grade: GradeSettings = field(default_factory=GradeSettings)  # spec 013: shared + the book's


def _check_overrides(script: dict, cfg: RenderConfig) -> None:
    """The script's `animation` overrides, checked before any work (spec 010 FR-9, AC-3.5): each
    page's is `false` or a number in the script contract's range, narrowed by render.json's
    `override_min`–`override_max` while animation is on; the intro and outro carry none."""
    lo, hi = ANIMATION_OVERRIDE_RANGE
    narrowed = ""
    if cfg.animation.enabled:
        lo, hi = max(lo, cfg.animation.override_min), min(hi, cfg.animation.override_max)
        if (lo, hi) != ANIMATION_OVERRIDE_RANGE:
            narrowed = " (animation.override_min–override_max in config/render.json)"
    errors = [
        f"{name}: animation is allowed only on story pages, not the {name}"
        for name in ("intro", "outro")
        if isinstance(script.get(name), dict) and "animation" in script[name]
    ]
    for page in script["pages"]:
        errors += [
            error + narrowed
            for error in _animation_errors(f"page {int(page['page_number'])}", page, lo, hi)
        ]
    if errors:
        raise RenderError("; ".join(errors))


def _page_eligibility(
    folder: BookFolder, book: dict, script: dict, cfg: RenderConfig, root: Path
) -> tuple[dict[str, PageEligibility], list[tuple[str, str]]]:
    """Eligibility of every story page by section name (spec 010 FR-11), and the still pages to
    log as ("page NN", "still – reason"): none for a book without an animation folder or with
    animation off, so existing books render and log exactly as before (NFR-5)."""
    book_pages = {int(p["page_number"]): p for p in book["pages"]}
    out: dict[str, PageEligibility] = {}
    for script_page in script["pages"]:
        number = int(script_page["page_number"])
        page = book_pages.get(number, {"page_number": number})
        out[page_section_name(number)] = page_eligibility(
            folder, page, script_page, cfg.animation, Path(root)
        )
    if not cfg.animation.enabled or not (folder.folder / PLAN_DIR).is_dir():
        return out, []
    notes = [
        (f"page {name.removeprefix('page-')}", f"still – {el.reason}")
        for name, el in out.items()
        if not el.animated
    ]
    return out, notes


def _read_script(script_path: Path) -> dict:
    try:
        return json.loads(script_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RenderError(f"no episode script: {script_path}") from None
    except json.JSONDecodeError as exc:
        raise RenderError(f"episode script is not valid JSON: {script_path} ({exc})") from None


def _load_inputs(folder: BookFolder, root: Path) -> _Inputs:
    """Gate on approved audio, then load the script, tracks and pictures and key every section
    and transition. Reads only; raises RenderError (or PictureError) on anything missing."""
    book = load_book(folder.book_json)
    slug = folder.slug
    script_path = folder.script_path
    audio = AudioPaths(folder.build_dir / "audio")
    voice_cfg = load_voice_config(Path(root) / "config" / "voices.json")
    settings_sha = current_settings_sha(voice_cfg, book, folder.book_json, script_path)
    problems = approval_problems(audio, script_path, settings_sha, folder.book_json)
    if problems:
        raise AudioNotApproved("; ".join(problems))
    script = _read_script(script_path)
    cfg = load_render_config(Path(root) / "config" / "render.json")

    track_names = [
        "intro",
        *[page_section_name(int(p["page_number"])) for p in script["pages"]],
        "outro",
    ]
    timings, tracks, sample_rate = _load_tracks(audio, track_names)
    pictures = resolve_pictures(book, folder, Path(root) / "assets" / "nook")
    cover_notes: list[tuple[str, str]] = []
    if folder.blueprint_path.is_file():
        cover_notes = _use_titled_cover(book, folder, cfg, pictures, Path(root))
    for path in pictures.files.values():
        _check_picture(path)
    picture_sha = {pid: sha256_file(path) for pid, path in pictures.files.items()}
    try:
        sections = build_sections(script, timings, cfg, pictures.by_section)
    except ValueError as exc:
        raise RenderError(str(exc)) from None
    _check_overrides(script, cfg)
    eligibility, still_notes = _page_eligibility(folder, book, script, cfg, Path(root))
    still_notes = cover_notes + still_notes
    keys, still_keys = [], []
    for s in sections:
        key = section_key(s, picture_sha[s.picture], cfg)
        el = eligibility.get(s.name)
        still_keys.append(key)
        keys.append(animated_key(key, el, cfg.animation) if el and el.animated else key)
    trans_keys = [transition_key(a, b, cfg) for a, b in zip(keys, keys[1:], strict=False)]
    paths = VideoPaths(folder.build_dir / "video")
    cues = _load_ambience(script, sections, cfg, Path(root))
    ambience_key = [[c.sound.name, sha256_file(c.sound.file), c.start, c.end] for c in cues]
    return _Inputs(
        slug,
        cfg,
        audio,
        track_names,
        tracks,
        sample_rate,
        pictures,
        sections,
        keys,
        trans_keys,
        paths,
        cues,
        ambience_key,
        eligibility,
        still_notes,
        still_keys,
        sha256_file(intro) if (intro := _channel_intro_file(cfg, Path(root))) else "",
        _bookend_clips(cfg, Path(root)),
        resolve_grade(cfg.grade, book, folder.book_json),
    )


COVER_RECIPE = 3  # bump when the cover design changes (spec 015 FR-3)


def cover_key(
    cover_sha: str,
    words: list[str],
    palette: CoverPalette,
    logo_sha: str,
    size: tuple[int, int],
) -> str:
    """The lettered cover's cache key: picture, words, palette, logo and design version."""
    return _digest(
        {
            "recipe": COVER_RECIPE,
            "cover": cover_sha,
            "words": words,
            "palette": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in asdict(palette).values()],
            "logo": logo_sha,
            "size": list(size),
        }
    )


def _blueprint_data(folder: BookFolder) -> dict | None:
    """The book's blueprint as plain data; None when it is missing or not JSON."""
    try:
        data = json.loads(folder.blueprint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _use_titled_cover(
    book: dict, folder: BookFolder, cfg: RenderConfig, pictures: ResolvedPictures, root: Path
) -> list[tuple[str, str]]:
    """Letter the book's front cover like a picture book (spec 015): title plate, subtitle and
    the channel logo badge, in the blueprint's palette.

    Books made from a blueprint get a cover painted without lettering; the lettering is drawn
    here (Pillow), so it is always spelled right. The lettered cover is a derived file under
    `build/video/frames/`, rebuilt only when the cover, the words, the palette, the logo or the
    design change. Returns render-log notes (a missing logo, AC-1.8)."""
    meta = book["metadata"]
    cover = pictures.files["cover_front"]
    words = [meta.get("title", ""), meta.get("subtitle", "")]
    palette = cover_palette(_blueprint_data(folder))
    notes: list[tuple[str, str]] = []
    logo: Path | None = None
    if cfg.channel_logo:
        logo = Path(root) / cfg.channel_logo
        if not logo.is_file():
            notes.append(("cover", f"logo not found: {logo} (cover made without the badge)"))
            logo = None
    stamp = cover_key(
        sha256_file(cover),
        words,
        palette,
        sha256_file(logo) if logo else "",
        (cfg.width, cfg.height),
    )
    out = folder.build_dir / "video" / "frames" / "cover_front_titled.png"
    marker = out.with_suffix(".json")
    if not (out.is_file() and (_read_json(marker) or {}).get("key") == stamp):
        compose_cover(cover, words[0], words[1], palette, logo, cfg, out)
        write_atomic(marker, {"key": stamp})
    pictures.files["cover_front"] = out
    return notes


def _channel_intro_file(cfg: RenderConfig, root: Path) -> Path | None:
    if not cfg.channel_intro:
        return None
    path = Path(root) / cfg.channel_intro
    if not path.is_file():
        raise RenderError(f"channel intro not found: {path}")
    return path


@dataclass(frozen=True)
class ChannelClip:
    """A shared brand clip joined to the episode (spec 012): `role` is "open" or "close"."""

    role: str
    path: Path
    sha256: str


def _bookend_clips(cfg: RenderConfig, root: Path) -> list[ChannelClip]:
    """The configured opening and ending clips, in that order. A configured file that does not
    exist raises RenderError naming it, before anything is encoded (spec 012 AC-3.4)."""
    clips: list[ChannelClip] = []
    for role, setting, label in (
        ("open", cfg.channel_open_clip, "opening clip"),
        ("close", cfg.channel_close_clip, "ending clip"),
    ):
        if not setting:
            continue
        path = Path(root) / setting
        if not path.is_file():
            raise RenderError(f"{label} not found: {path}")
        clips.append(ChannelClip(role, path, sha256_file(path)))
    return clips


def channel_intro_command(
    src: Path, out_path: Path, cfg: RenderConfig, has_audio: bool = True
) -> list[str]:
    """FFmpeg arguments re-encoding a brand clip to the episode's exact video format (size, frame
    rate, BT.709, timescale) and stereo AAC, so it can be joined to the episode untouched.

    A source without an audio track gets silence as long as its picture (`has_audio=False`)."""
    encode = [a for a in ENCODE_ARGS(cfg) if a != "-an"]
    graph = (
        f"scale={cfg.width}:{cfg.height}:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
        f"{_to_bt709_yuv(cfg.width, cfg.height)}"
    )
    if has_audio:
        inputs = ["-i", str(src)]
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        tail: list[str] = []
    else:
        silence = f"anullsrc=channel_layout=stereo:sample_rate={cfg.video.audio_rate}"
        inputs = ["-i", str(src), "-f", "lavfi", "-i", silence]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
        tail = ["-shortest"]
    return [
        "ffmpeg", "-y", "-loglevel", "error", *inputs,
        *maps, "-vf", graph, *encode,
        "-c:a", "aac", "-b:a", cfg.video.audio_bitrate, "-ar", str(cfg.video.audio_rate),
        "-ac", "2", *tail, "-f", "mp4", str(out_path),
    ]  # fmt: skip


def _conform_clip(
    role: str, src: Path, cfg: RenderConfig, work: Path, run: Runner
) -> tuple[Path, float]:
    """The clip re-encoded once to the episode's format (cached next to the video by the clip's
    sha256 and the encode settings) and its length in seconds."""
    prepared, meta = work / f"channel_{role}.mp4", work / f"channel_{role}.json"
    key = _digest(
        {
            "src": sha256_file(src),
            "size": [cfg.width, cfg.height, cfg.fps],
            "video": asdict(cfg.video),
            "version": timeline.RENDER_VERSION,
        }
    )
    if not _reusable(prepared, meta, key, False):
        meta.unlink(missing_ok=True)
        has_audio = any(s.get("codec_type") == "audio" for s in probe(src).get("streams", []))
        _run_to(
            run,
            lambda out: channel_intro_command(src, out, cfg, has_audio),
            prepared,
            f"clip {role}",
        )
        write_atomic(meta, {"key": key})
    return prepared, float(probe(prepared)["format"]["duration"])


def video_frame_count(path: Path, fps: int) -> int:
    """Whole frames in the first video stream of `path`: its frame count, else its stream length
    (not the container's, which an audio track can stretch)."""
    info = probe(path)
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    if str(stream.get("nb_frames", "")).isdigit() and int(stream["nb_frames"]) > 0:
        return int(stream["nb_frames"])
    seconds = float(stream.get("duration") or info["format"]["duration"])
    return round(seconds * fps)


def keyframe_frames(path: Path, fps: int) -> list[int]:
    """Frame numbers of the keyframes of the first video stream of `path` (packets only, no
    decoding)."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path),
    ]  # fmt: skip
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise RenderError("ffprobe not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        raise RenderError(
            f"probe {path}: {_tail(exc.stderr) or f'ffprobe exit {exc.returncode}'}"
        ) from None
    frames: set[int] = set()
    for line in out.splitlines():
        time, _, flags = line.partition(",")
        if time and "K" in flags:
            frames.add(round(float(time) * fps))
    return sorted(frames | {0})


def blend_copy_command(src: Path, cut: blend.Cut, out_path: Path, cfg: RenderConfig) -> list[str]:
    """Cut frames [start, end) of `src` without re-encoding; `start` must be a keyframe. The seek
    time is its time rounded up to the microsecond: rounding down could land on the keyframe
    before, and any larger step would shorten the piece."""
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{math.ceil(cut.start / cfg.fps * 1e6) / 1e6:.6f}",
        "-i", str(src), "-map", "0:v:0", "-frames:v", str(cut.frames), "-c", "copy", "-an",
        "-video_track_timescale", str(VIDEO_TIMESCALE), "-f", "mp4", str(out_path),
    ]  # fmt: skip


def blend_window_command(
    sources: Sequence[Path], step: blend.Step, out_path: Path, cfg: RenderConfig
) -> list[str]:
    """Re-encode one small blend window: its cuts dissolved into each other (picture only)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for src, cut in zip(sources, step.cuts, strict=True):
        cmd += [
            "-ss", f"{max(0.0, cut.start - 0.25) / cfg.fps:.6f}",
            "-t", f"{(cut.frames - 0.25) / cfg.fps:.6f}", "-i", str(src),
        ]  # fmt: skip
    norm = f"{_to_bt709_yuv(cfg.width, cfg.height)},fps={cfg.fps},settb=AVTB"
    graph = blend.video_blend_graph(
        [c.frames for c in step.cuts], blend_size(cfg), cfg.fps, norm, _OUTPUT_FORMAT
    )
    return [
        *cmd, "-filter_complex", graph, "-map", "[vout]",
        "-frames:v", str(blend.blend_frames([c.frames for c in step.cuts], blend_size(cfg))),
        *ENCODE_ARGS(cfg), "-f", "mp4", str(out_path),
    ]  # fmt: skip


def blend_size(cfg: RenderConfig) -> int:
    """The dissolve length in whole frames (`channel_blend_seconds`, at least one frame)."""
    return max(1, round(cfg.channel_blend_seconds * cfg.fps))


def blend_join_command(
    listing: Path, audio_sources: Sequence[Path], script: Path, out_path: Path, cfg: RenderConfig
) -> list[str]:
    """Join the picture pieces with `-c copy` and add the sound: every part's audio cross-faded
    by the filtergraph in `script`, encoded once as stereo AAC."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    for src in audio_sources:
        cmd += ["-i", str(src)]
    return [
        *cmd, "-filter_complex_script", str(script), "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", cfg.video.audio_bitrate,
        "-ar", str(cfg.video.audio_rate), "-ac", "2", "-movflags", "+faststart",
        "-f", "mp4", str(out_path),
    ]  # fmt: skip


def join_channel_clips(
    body: Path,
    before: Sequence[tuple[str, Path]],
    after: Sequence[tuple[str, Path]],
    out_path: Path,
    cfg: RenderConfig,
    work: Path,
    run: Runner,
    body_audio: Path | None = None,
) -> dict[str, float]:
    """Join the finished episode `body` between brand clips: `before` plays first, `after` last.

    Each (role, source) clip is conformed (and cached) by `_conform_clip`. Neighbouring parts
    are cross-dissolved over `channel_blend_seconds` (spec 012 FR-5): only the windows around
    the joins are re-encoded, the rest is cut at keyframes and joined with `-c copy`, and the
    sound is one `acrossfade` chain. `body_audio` (default: the body's own track) is the body's
    sound. Returns each clip's own length in seconds by role."""
    lengths: dict[str, float] = {}
    prepared: dict[str, Path] = {}
    for role, src in (*before, *after):
        prepared[role], _ = _conform_clip(role, src, cfg, work, run)
    parts = [prepared[r] for r, _ in before] + [body] + [prepared[r] for r, _ in after]
    frames = [video_frame_count(p, cfg.fps) for p in parts]
    roles = [r for r, _ in before] + [None] + [r for r, _ in after]
    for role, count in zip(roles, frames, strict=True):
        if role is not None:
            lengths[role] = count / cfg.fps
    keys = [keyframe_frames(p, cfg.fps) for p in parts]
    size = blend_size(cfg)
    try:
        steps = blend.plan_blend(frames, keys, size)
    except blend.BlendError as exc:
        raise RenderError(f"channel clips: {exc}") from None
    scratch = work / "blend"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        pieces: list[Path] = []
        for number, step in enumerate(steps):
            piece = scratch / f"{number:02d}_{step.kind}.mp4"
            if step.kind == "copy":
                cut = step.cuts[0]
                cmd_for = lambda out, cut=cut: blend_copy_command(parts[cut.part], cut, out, cfg)  # noqa: E731
            else:
                sources = [parts[c.part] for c in step.cuts]
                cmd_for = lambda out, s=step, src=sources: blend_window_command(src, s, out, cfg)  # noqa: E731
            _run_to(
                run,
                cmd_for,
                piece,
                f"blend {number + 1}" if step.kind == "blend" else f"cut {number + 1}",
            )
            pieces.append(piece)
        listing = work / "channel_join.txt"
        write_concat_list(pieces, listing)
        script = scratch / "audio_blend.txt"
        script.write_text(
            blend.audio_blend_graph(frames, size, cfg.fps, cfg.video.audio_rate), encoding="utf-8"
        )
        audio = list(parts)
        audio[len(before)] = body_audio or body
        cmd_for = lambda out: blend_join_command(listing, audio, script, out, cfg)  # noqa: E731
        _run_to(run, cmd_for, out_path, "join clips")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return lengths


def grade_video(
    src: Path,
    out: Path,
    settings: GradeSettings,
    duration: float,
    cfg: RenderConfig,
    run: Runner,
    measure: Measurer,
) -> Loudness:
    """The final grade (spec 013): one re-encode of `src` into `out`, with the loudness measured
    first (pass one) and applied as one gain (pass two), then the result measured for the record.
    A failure raises RenderError with FFmpeg's message and leaves `src` and any earlier `out`."""
    measured: Loudness | None = None
    try:
        if settings.sound:
            report = measure(measure_command(str(src), settings, duration, cfg), "measure loudness")
            measured = parse_loudnorm_report(report)
        _run_to(
            run,
            lambda tmp: grade_command(str(src), str(tmp), settings, duration, cfg, measured),
            out,
            "grade",
        )
        return parse_ebur128_summary(measure(loudness_command(str(out)), "measure result"))
    except LoudnessError as exc:
        raise RenderError(f"grade: {exc}") from None


def _loudness_record(result: Loudness) -> dict:
    return {
        "integrated_lufs": result.integrated,
        "true_peak_dbfs": result.true_peak,
        "loudness_range": result.loudness_range,
    }


_GRADE_FIELDS = ("ungraded_sha256", "grade_key", "grade", "loudness", "graded_at")


def _grade_is_current(record: dict, inputs: _Inputs, active: bool) -> bool:
    """Whether the video the record describes carries the grade that is now asked for."""
    if not active:
        return not any(record.get(k) for k in _GRADE_FIELDS)
    sha = record.get("ungraded_sha256")
    return bool(sha) and record.get("grade_key") == grade_key(sha, inputs.grade, inputs.cfg)


def _copy_atomic(src: Path, dst: Path) -> None:
    tmp = _tmp_clip(dst)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)  # noqa: PTH105 - atomic replace
    finally:
        tmp.unlink(missing_ok=True)


def _regrade(
    inputs: _Inputs,
    record: dict,
    active: bool,
    run: Runner,
    measure: Measurer,
    on_progress: Progress | None,
) -> bool:
    """Spec 013 AC-4.2: the sections are current and only the grade changed: grade (or restore)
    from the kept ungraded video and update the record. False when that video is missing or
    changed, so the caller renders in full."""
    paths, slug, cfg = inputs.paths, inputs.slug, inputs.cfg
    final, ungraded, record_path = paths.final(slug), paths.ungraded(slug), paths.record(slug)
    base = {k: v for k, v in record.items() if k not in _GRADE_FIELDS}
    if record.get("ungraded_sha256"):
        if not ungraded.exists() or sha256_file(ungraded) != record["ungraded_sha256"]:
            return False
    elif final.exists():  # a plain render: the video is its own ungraded source
        _copy_atomic(final, ungraded)
    else:
        return False
    record_path.unlink(missing_ok=True)  # stale until the new video is complete
    if not active:
        _copy_atomic(ungraded, final)
        ungraded.unlink(missing_ok=True)
        write_atomic(record_path, base)
        return True
    sha = sha256_file(ungraded)
    write_atomic(record_path, {**base, "ungraded_sha256": sha})
    result = grade_video(ungraded, final, inputs.grade, float(base["duration"]), cfg, run, measure)
    write_atomic(record_path, _graded_record(inputs, base, sha, result))
    if on_progress:
        on_progress("grade", "rendered")
    return True


def _graded_record(inputs: _Inputs, base: dict, sha: str, result: Loudness) -> dict:
    return {
        **base,
        "ungraded_sha256": sha,
        "grade_key": grade_key(sha, inputs.grade, inputs.cfg),
        "grade": asdict(inputs.grade),
        "loudness": _loudness_record(result),
        "graded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _load_ambience(script: dict, sections: list[Section], cfg: RenderConfig, root: Path) -> list:
    """The ambience cues (spec 007). The library is touched only when the script has an `ambience`
    field; an unknown or missing sound fails here, before any section is rendered."""
    blocks = [script["intro"], script["outro"], *script["pages"]]
    if not any("ambience" in block for block in blocks):
        return []
    try:
        library = sound_library.load_library(root / "assets" / "sound_library")
    except sound_library.LibraryError as exc:
        raise RenderError(str(exc)) from None
    problems = ambience_problems(script, library)
    if problems:
        raise RenderError("; ".join(problems))
    cues = build_cues(script, sections, section_times(sections, cfg), library)
    missing = sorted({c.sound.name for c in cues if not c.sound.file.is_file()})
    if missing:
        raise RenderError(f"sound file missing: {', '.join(missing)}")
    return cues


def _decode_sounds(inputs: _Inputs) -> dict[str, np.ndarray]:
    """Decode each distinct sound of the ambience cues; a failure names the sound (RenderError)."""
    decoded: dict[str, np.ndarray] = {}
    for cue in inputs.ambience:
        if cue.sound.name not in decoded:
            try:
                decoded[cue.sound.name] = sound_library.decode_sound(cue.sound, inputs.sample_rate)
            except (sound_library.LibraryError, SoundError) as exc:
                raise RenderError(str(exc)) from None
    return decoded


def _render_bed(inputs: _Inputs, decoded: dict[str, np.ndarray]) -> np.ndarray | None:
    """The levelled ambience bed for the whole episode, or None when there are no cues."""
    if not inputs.ambience:
        return None
    length = round(total_frames(inputs.sections, inputs.cfg) / inputs.cfg.fps * inputs.sample_rate)
    return render_bed(inputs.ambience, decoded, length, inputs.sample_rate, inputs.cfg.audio)


def _page_turn_digest(page_turn: np.ndarray) -> str:
    return hashlib.sha256(page_turn.tobytes()).hexdigest()


def _final_key(inputs: _Inputs, page_turn_sha: str) -> str:
    """The key of the finished video: the ordered section and transition keys, the tracks, the
    page-turn sound and the audio/encode settings (AC-7.1, AC-7.2)."""
    return _digest(
        {
            "render_version": timeline.RENDER_VERSION,
            "sections": [[s.name, k] for s, k in zip(inputs.sections, inputs.keys, strict=True)],
            "transitions": inputs.trans_keys,
            "tracks": {name: sha256_file(inputs.audio.track(name)) for name in inputs.track_names},
            "page_turn": page_turn_sha,
            "sample_rate": inputs.sample_rate,
            "audio": asdict(inputs.cfg.audio),
            "video": asdict(inputs.cfg.video),
            **({"ambience": inputs.ambience_key} if inputs.ambience_key else {}),
            **({"channel_intro": inputs.channel_intro_sha} if inputs.channel_intro_sha else {}),
            **(
                {"channel_blend_seconds": inputs.cfg.channel_blend_seconds}
                if inputs.channel_intro_sha or inputs.bookend_clips
                else {}
            ),
            **(
                {"bookend_clips": {c.role: c.sha256 for c in inputs.bookend_clips}}
                if inputs.bookend_clips
                else {}
            ),
        }
    )


def _supplied_page_turn_sha(sfx_dir: Path) -> str | None:
    """sha256 of the supplied page-turn file, or None when the generated sound is used."""
    supplied = Path(sfx_dir) / PAGE_TURN_FILE
    return sha256_file(supplied) if supplied.exists() else None


def _sfx_dir(sfx_dir: Path | None, root: Path) -> Path:
    """The page-turn folder: `sfx_dir` when given, else `<root>/assets/audio_sfx` (shared by every
    book, spec 005 FR-3)."""
    return Path(sfx_dir) if sfx_dir is not None else Path(root) / "assets" / "audio_sfx"


def _generated_page_turn(cache_path: Path, sample_rate: int) -> np.ndarray:
    """The generated page-turn sound exactly as `load_page_turn` would return it (read back from
    a 16-bit WAV), without writing its cache file."""
    if cache_path.exists():
        cached = _read_cache(cache_path, sample_rate)
        if cached is not None:
            return cached
    buffer = io.BytesIO()
    sf.write(buffer, page_turn_sound(sample_rate), sample_rate, subtype="PCM_16", format="WAV")
    buffer.seek(0)
    audio, _rate = sf.read(buffer, dtype="float32", always_2d=False)
    return audio


def render_check(folder: BookFolder, root: Path, sfx_dir: Path | None = None) -> str:
    """Whether `render_video` would reuse the finished video, without rendering or writing:
    "blocked" (audio not approved, missing or unusable inputs), "not_rendered" (no video or
    record), "up_to_date" (the final key matches) or "out_of_date" (spec 004 AC-6.2)."""
    return render_check_detail(folder, root, sfx_dir)[0]


def render_check_detail(
    folder: BookFolder, root: Path, sfx_dir: Path | None = None
) -> tuple[str, str]:
    """`render_check`'s state and, when "blocked", why: "the audio is not approved: ..." for the
    approval gate, else the missing or unusable input (a picture, a track, the book data).

    A supplied page-turn file is never decoded here: its decoded hash is taken from the video
    record when the file is the one that video was made with."""
    try:
        inputs = _load_inputs(folder, root)
    except AudioNotApproved as exc:
        return "blocked", f"the audio is not approved: {exc}"
    except (RenderError, PictureError, BookError, ConfigError) as exc:
        return "blocked", str(exc)
    final, record = inputs.paths.final(inputs.slug), _read_json(inputs.paths.record(inputs.slug))
    if not final.exists() or record is None:
        return "not_rendered", ""
    file_sha = _supplied_page_turn_sha(_sfx_dir(sfx_dir, root))
    if file_sha is None:
        cache = Path(root) / "build" / "sfx" / "page_turn.wav"
        page_turn_sha = _page_turn_digest(_generated_page_turn(cache, inputs.sample_rate))
    elif record.get("page_turn_file_sha256") == file_sha and record.get("page_turn_sha256"):
        page_turn_sha = record["page_turn_sha256"]
    else:
        return "out_of_date", ""
    if record.get("final_key") == _final_key(inputs, page_turn_sha) and _grade_is_current(
        record, inputs, is_active(inputs.grade)
    ):
        return "up_to_date", ""
    return "out_of_date", ""


def render_video(
    folder: BookFolder,
    root: Path,
    force: bool = False,
    run: Runner = run_ffmpeg,
    on_progress: Progress | None = None,
    sfx_dir: Path | None = None,
    frame_run: FrameRunner = run_ffmpeg_frames,
    measure: Measurer = measure_ffmpeg,
    no_grade: bool = False,
) -> RenderResult:
    """Render the approved episode of one book to `books/<slug>/build/video/<slug>.mp4` (spec 003).

    Sections and transitions are cached by key; the final video is reused when the ordered keys,
    the tracks, the page-turn sound and the audio/encode settings are unchanged (AC-7.1, AC-7.2).
    The page-turn sound comes from `sfx_dir` (default `<root>/assets/audio_sfx`).

    An animated page whose layer files can't be loaded renders still and is logged; its clip is
    recorded under the still key and the video record gets no final key, so the next run tries
    the animation again instead of calling the still fallback up to date (spec 010 Phase 4).

    The finished joined video then gets the final grade (spec 013) unless it is switched off or
    `no_grade` is set: the ungraded video is kept beside the result, so changing only a grade
    setting re-runs just that pass (the record's `grade_key` covers its checksum and settings).
    """
    inputs = _load_inputs(folder, root)
    slug, cfg = inputs.slug, inputs.cfg
    sections, keys = inputs.sections, inputs.keys
    pictures, tracks, sample_rate = inputs.pictures, inputs.tracks, inputs.sample_rate
    pairs = list(zip(sections, sections[1:], strict=False))
    if on_progress:
        for name, note in inputs.still_notes:
            on_progress(name, note)

    paths = inputs.paths
    try:
        sfx = _sfx_dir(sfx_dir, root)
        page_turn = load_page_turn(sfx, sample_rate, Path(root) / "build" / "sfx" / "page_turn.wav")
        page_turn_sha = _page_turn_digest(page_turn)
        page_turn_fields = {
            "page_turn_sha256": page_turn_sha,
            "page_turn_file_sha256": _supplied_page_turn_sha(sfx),
        }
        final_key = _final_key(inputs, page_turn_sha)
        duration = total_frames(sections, cfg) / cfg.fps
        final, record_path = paths.final(slug), paths.record(slug)
        record = _read_json(record_path)
        active = is_active(inputs.grade, no_grade)
        if not force and record and record.get("final_key") == final_key:
            if final.exists() and _grade_is_current(record, inputs, active):
                stale = any(record.get(k) != v for k, v in page_turn_fields.items())
                if stale and page_turn_fields["page_turn_file_sha256"] is not None:
                    # Records written before spec 004 lack these; render_check needs them.
                    write_atomic(record_path, {**record, **page_turn_fields})
                return RenderResult(final, duration, 0, len(sections), True)
            if _regrade(inputs, record, active, run, measure, on_progress):
                return RenderResult(final, duration, 0, len(sections), False)
        decoded = _decode_sounds(inputs)  # fail on a bad sound before any encoding
        record_path.unlink(missing_ok=True)  # stale until the new video is complete

        rendered = reused = 0
        fell_back: list[str] = []
        made_keys: list[str] = []  # the key each section clip is recorded under
        work = _SectionWork(paths, cfg, folder, slug, run, frame_run, force, on_progress)
        for index, (section, key) in enumerate(zip(sections, keys, strict=True)):
            action, fallback, made_key = _ensure_section(
                work,
                section,
                key,
                inputs.still_keys[index],
                inputs.eligibility.get(section.name),
                pictures.files[section.picture],
            )
            made_keys.append(made_key)
            if fallback is not None:
                fell_back.append(section.name)
            rendered += action == "rendered"
            reused += action == "reused"
            if on_progress:
                on_progress(section.name, action)

        # Keyed on the clips as made: a transition next to a page that fell back to still is
        # keyed on the still clip, so it is re-made once the page animates (spec 010 AC-2.2).
        trans_keys = [
            transition_key(a, b, cfg) for a, b in zip(made_keys, made_keys[1:], strict=False)
        ]
        clips: list[Path] = [paths.sections / f"{sections[0].name}.mp4"]
        for (a, b), key in zip(pairs, trans_keys, strict=True):
            clip = paths.transitions / f"{a.name}__{b.name}.mp4"
            meta = paths.transitions / f"{a.name}__{b.name}.json"
            if not _reusable(clip, meta, key, force):
                meta.unlink(missing_ok=True)
                _render_transition(
                    paths.sections / f"{a.name}.mp4",
                    paths.sections / f"{b.name}.mp4",
                    (a.name, b.name),
                    clip,
                    paths.frames / "transitions",
                    cfg,
                    run,
                )
                write_atomic(meta, {"key": key})
            clips += [clip, paths.sections / f"{b.name}.mp4"]

        master = paths.audio / "master.wav"
        bed = _render_bed(inputs, decoded)
        write_wav_atomic(
            master,
            mix_master(sections, tracks, page_turn, cfg, sample_rate, ambience=bed),
            sample_rate,
        )
        list_file = paths.root / "concat.txt"
        write_concat_list(clips, list_file)
        joined = paths.root / f"{slug}.joined.mp4"
        # With the grade on, the join is kept as the ungraded video and graded into `final`.
        join_out = paths.ungraded(slug) if active else final
        if not active:
            paths.ungraded(slug).unlink(missing_ok=True)
        clip_seconds: dict[str, float] = {}
        try:
            _run_to(run, lambda out: concat_command(list_file, out), joined, "join")
            intro_src = _channel_intro_file(cfg, root)
            before = [("intro", intro_src)] if intro_src is not None else []
            before += [(c.role, c.path) for c in inputs.bookend_clips if c.role == "open"]
            after = [(c.role, c.path) for c in inputs.bookend_clips if c.role == "close"]
            if not (before or after):
                _run_to(run, lambda out: mux_command(joined, master, out, cfg), join_out, "mux")
            else:
                clip_seconds = join_channel_clips(
                    joined, before, after, join_out, cfg, paths.root, run, body_audio=master
                )
                duration += sum(clip_seconds.values())
                duration -= (len(before) + len(after)) * blend_size(cfg) / cfg.fps
        finally:
            joined.unlink(missing_ok=True)
        base_record = {
            "final_key": None if fell_back else final_key,
            "duration": round(duration, 3),
            "frames": total_frames(sections, cfg),
            "sections": [
                {"name": s.name, "key": k, "picture": str(pictures.files[s.picture])}
                for s, k in zip(sections, made_keys, strict=True)
            ],
            **page_turn_fields,
            **(
                {
                    "bookend_clips": {
                        c.role: {"sha256": c.sha256, "seconds": round(clip_seconds[c.role], 3)}
                        for c in inputs.bookend_clips
                    }
                }
                if inputs.bookend_clips
                else {}
            ),
            "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if not active:
            write_atomic(record_path, base_record)
            return RenderResult(final, duration, rendered, reused, False)
        sha = sha256_file(join_out)
        write_atomic(record_path, {**base_record, "ungraded_sha256": sha})  # kept if grading fails
        result = grade_video(join_out, final, inputs.grade, duration, cfg, run, measure)
        write_atomic(record_path, _graded_record(inputs, base_record, sha, result))
        if on_progress:
            on_progress("grade", "rendered")
        return RenderResult(final, duration, rendered, reused, False)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise RenderError(f"not enough disk space to write video in {paths.root}") from None
        raise


@dataclass(frozen=True)
class _SectionWork:
    """What rendering or reusing one section clip needs besides the section itself."""

    paths: VideoPaths
    cfg: RenderConfig
    folder: BookFolder
    slug: str
    run: Runner
    frame_run: FrameRunner
    force: bool
    on_progress: Progress | None


def _ensure_section(
    work: _SectionWork,
    section: Section,
    key: str,
    still_key: str,
    el: PageEligibility | None,
    picture: Path,
) -> tuple[str, str | None, str]:
    """Reuse the section clip in `sections/` when its key matches, else render it (animated when
    `el` says so) and record its key. Returns "reused" or "rendered", why an animated page fell
    back to still (None when it did not) and the key the clip is recorded under: `still_key`
    after a fallback, else `key`."""
    paths = work.paths
    clip = paths.sections / f"{section.name}.mp4"
    meta = paths.sections / f"{section.name}.json"
    if _reusable(clip, meta, key, work.force):
        return "reused", None, key
    meta.unlink(missing_ok=True)
    anim, fallback = _load_animation(work, section, el, picture)
    if fallback is not None:
        key = still_key
        if work.on_progress and el is not None and el.plan is not None:
            work.on_progress(f"page {el.plan.page_number:02d}", f"still – {fallback}")
    _render_section(
        section,
        picture,
        clip,
        paths.frames / section.name,
        work.cfg,
        work.run,
        work.frame_run,
        anim,
    )
    write_atomic(meta, {"key": key, "frames": section.frames, "picture": str(picture)})
    return "rendered", fallback, key


def _load_animation(
    work: _SectionWork, section: Section, el: PageEligibility | None, picture: Path
) -> tuple[PageAnimation | None, str | None]:
    """The section's page animation, or None for a still section; when its files can't be
    loaded, None and the reason."""
    if el is None or not el.animated or el.plan is None:
        return None, None
    number = el.plan.page_number
    try:
        anim = load_page_animation(
            el.plan,
            layers_dir(work.folder, number),
            picture,
            work.cfg.animation,
            el.scale,
            work.slug,
            number,
            work.cfg.fps,
            section.frames,
        )
    except AnimationError as exc:
        fix = f"python -m video_pipeline.cut_layers --book {work.slug} --page {number} --force"
        return None, f"{exc} – re-run {fix}"
    return anim, None


# --- page preview (spec 010 FR-15) ----------------------------------------------------------


@dataclass(frozen=True)
class PreviewResult:
    path: Path
    animated: bool
    reason: str  # why the page is still ("" when animated)


def voices_command(slug: str) -> str:
    return f"python -m video_pipeline.synthesize_voices --book {slug} --reviewed"


def render_preview(
    folder: BookFolder,
    root: Path,
    page: int,
    *,
    run: Runner = run_ffmpeg,
    frame_run: FrameRunner = run_ffmpeg_frames,
    on_progress: Progress | None = None,
    force: bool = False,
) -> PreviewResult:
    """Render one story page's full section with its voice to `video/preview/page_NN.mp4`.

    No approval gate: it needs the script, the render config and that page's track only. The
    section goes through the same code, cache folder and key as the full render, so a later
    render reuses it (spec 010 FR-15, AC-6.1, AC-6.3)."""
    root = Path(root)
    slug = folder.slug
    book = load_book(folder.book_json)
    script = _read_script(folder.script_path)
    cfg = load_render_config(root / "config" / "render.json")
    script_page = next((p for p in script["pages"] if int(p["page_number"]) == page), None)
    if script_page is None:
        numbers = sorted(int(p["page_number"]) for p in script["pages"])
        span = f"{numbers[0]}–{numbers[-1]}" if numbers else "none"
        raise RenderError(f"page {page} is not a story page of {slug} (story pages: {span})")
    name = page_section_name(page)
    audio = AudioPaths(folder.build_dir / "audio")
    if not (audio.track(name).is_file() and audio.track_timing(name).is_file()):
        raise RenderError(f"no voice track for page {page} – run {voices_command(slug)}")
    timings, tracks, sample_rate = _load_tracks(audio, [name])
    book_page = next(
        (p for p in book["pages"] if int(p["page_number"]) == page), {"page_number": page}
    )
    picture = _page_picture(folder, book_page)
    _check_picture(picture)
    _check_overrides(script, cfg)
    try:
        section = timeline.page_section(script_page, timings[name], cfg, f"page_{page:02d}")
    except (KeyError, TypeError, ValueError) as exc:
        raise RenderError(f"unusable track timing for {name}: {exc}") from None
    still_key = section_key(section, sha256_file(picture), cfg)
    el = page_eligibility(folder, book_page, script_page, cfg.animation, root)
    key = animated_key(still_key, el, cfg.animation) if el.animated else still_key

    paths = VideoPaths(folder.build_dir / "video")
    work = _SectionWork(paths, cfg, folder, slug, run, frame_run, force, on_progress)
    action, fallback, _made = _ensure_section(work, section, key, still_key, el, picture)
    if on_progress:
        on_progress(section.name, action)
    out = paths.preview(page)
    wav = out.with_suffix(".wav")
    silent_turn = np.zeros(0, dtype=np.float32)
    write_wav_atomic(wav, mix_master([section], tracks, silent_turn, cfg, sample_rate), sample_rate)
    clip = paths.sections / f"{section.name}.mp4"
    try:
        _run_to(run, lambda tmp: mux_command(clip, wav, tmp, cfg), out, "mux")
    finally:
        wav.unlink(missing_ok=True)
    animated = el.animated and fallback is None
    return PreviewResult(out, animated, "" if animated else (fallback or el.reason))


# --- CLI ------------------------------------------------------------------------------------


def _minutes(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.render_episode",
        description="Render the approved episode into a 1920x1080 video (spec 003).",
        allow_abbrev=False,
    )
    parser.add_argument("--book", required=True, help="book slug, e.g. the-snail-mail-express")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root")
    parser.add_argument(
        "--sfx-dir",
        type=Path,
        default=None,
        help="folder with an optional page_turn.wav (default: <root>/assets/audio_sfx)",
    )
    parser.add_argument("--force", action="store_true", help="re-render every section")
    parser.add_argument(
        "--no-grade",
        action="store_true",
        help="skip the final grade (colour, sharpness, fades, loudness; spec 013)",
    )
    parser.add_argument(
        "--preview-page",
        type=int,
        default=None,
        metavar="N",
        help="render only story page N with its voice to build/video/preview/page_NN.mp4 "
        "(needs the page's voice track, not the audio approval)",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    run: Runner | None = None,
    frame_run: FrameRunner | None = None,
    measure: Measurer | None = None,
) -> int:
    args = parse_args(argv)
    folder: BookFolder | None = None
    progress: Progress = lambda name, action: print(f"  {name}: {action}", flush=True)  # noqa: E731
    try:
        folder = book_folder(args.book, args.root)
        if args.preview_page is not None:
            preview = render_preview(
                folder,
                args.root,
                args.preview_page,
                run=run or run_ffmpeg,
                frame_run=frame_run or run_ffmpeg_frames,
                on_progress=progress,
                force=args.force,
            )
            print(f"preview: {preview.path}")
            state = "animated" if preview.animated else f"still – {preview.reason}"
            print(f"page {args.preview_page:02d}: {state}")
            return 0
        result = render_video(
            folder,
            args.root,
            force=args.force,
            run=run or run_ffmpeg,
            on_progress=progress,
            sfx_dir=args.sfx_dir,
            frame_run=frame_run or run_ffmpeg_frames,
            no_grade=args.no_grade,
            measure=measure or measure_ffmpeg,
        )
    except (RenderError, PictureError, SoundError, BookError, ConfigError, LibraryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            where = folder.build_dir / "video" if folder is not None else args.root
            print(f"error: not enough disk space to write video in {where}", file=sys.stderr)
            return 1
        raise
    if result.up_to_date:
        print(f"video is up to date: {result.path} ({_minutes(result.duration)})")
    else:
        print(f"video: {result.path}")
        print(f"duration: {_minutes(result.duration)}")
        print(f"sections: {result.rendered} rendered, {result.reused} reused")
    print("Watch it before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
