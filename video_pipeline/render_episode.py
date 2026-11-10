"""Stage 3 render (spec 003): FFmpeg command builders and runner.

Each section becomes its own silent H.264 clip with an exact frame count, each transition a short
clip made from the neighbouring sections' boundary frames, and all clips share encode settings so
the concat demuxer can join them with `-c copy` before the master audio is muxed in.

The builders are pure (they return argument lists or filtergraph strings); `run_ffmpeg` and
`probe` are the only places that start a process. `render_video` gates on approved audio, resolves
the pictures, caches sections/transitions by key and assembles the final video.

Run: uv run python -m video_pipeline.render_episode [--book PATH] [--force]
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, UnidentifiedImageError

from video_pipeline import sound_library, timeline
from video_pipeline.animate import PageEligibility, animated_key, page_eligibility
from video_pipeline.contract import ConfigError
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import write_atomic
from video_pipeline.frames import (
    PAGE_TURN_FILE,
    PictureError,
    SoundError,
    _read_cache,
    compose_canvas,
    load_page_turn,
    mix_master,
    page_turn_sound,
    render_caption,
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
        raise RenderError(f"{what}: ffmpeg not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        if "No space left on device" in (stderr or ""):
            folder = Path(cmd[-1]).parent
            raise RenderError(f"{what}: not enough disk space to write video in {folder}") from None
        detail = _tail(stderr) or f"ffmpeg exit {exc.returncode}"
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


@dataclass(frozen=True)
class ResolvedPictures:
    """`files`: logical picture id → file; `by_section`: section name → logical picture id."""

    files: dict[str, Path]
    by_section: dict[str, str]


NOOKS = ("intro_nook", "outro_nook")


def _illustration_file(page: dict) -> str:
    return page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png"


def resolve_pictures(book: dict, folder: BookFolder, shared_nooks: Path) -> ResolvedPictures:
    """Every picture the episode shows (FR-1, AC-6.1, AC-6.3).

    Pages and covers come from the book's `illustrations/` (missing → RenderError naming the page
    and file). A nook image is taken from the book's `nooks/` first, then `shared_nooks`; with
    neither, the intro shows the front cover and the outro the back cover.
    """
    illustrations = folder.illustrations
    files: dict[str, Path] = {}
    for page in book["pages"]:
        number = int(page["page_number"])
        path = illustrations / _illustration_file(page)
        if not path.is_file():
            raise RenderError(f"page {number}: illustration not found: {path}")
        files[f"page_{number:02d}"] = path
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
Progress = Callable[[str, str], None]


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


def _render_section(
    section: Section, picture: Path, clip: Path, work: Path, cfg: RenderConfig, run: Runner
) -> None:
    canvas = work / "canvas.png"
    compose_canvas(picture, cfg, canvas)
    captions = []
    for number, card in enumerate(section.captions):
        path = work / f"caption_{number:03d}.png"
        render_caption(card.text, cfg, path)
        captions.append(path)
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


def _check_overrides(script: dict, cfg: RenderConfig) -> None:
    """Each page's numeric `animation` override must sit in render.json's narrower range."""
    lo, hi = cfg.animation.override_min, cfg.animation.override_max
    for page in script["pages"]:
        value = page.get("animation")
        if isinstance(value, int | float) and not isinstance(value, bool) and not lo <= value <= hi:
            raise RenderError(
                f"page {int(page['page_number'])}: animation {value!r} is outside "
                f"animation.override_min–override_max ({lo}–{hi}) in config/render.json"
            )


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
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"episode script is not valid JSON: {script_path} ({exc})") from None
    cfg = load_render_config(Path(root) / "config" / "render.json")

    track_names = [
        "intro",
        *[page_section_name(int(p["page_number"])) for p in script["pages"]],
        "outro",
    ]
    timings, tracks, sample_rate = _load_tracks(audio, track_names)
    pictures = resolve_pictures(book, folder, Path(root) / "assets" / "nook")
    for path in pictures.files.values():
        _check_picture(path)
    picture_sha = {pid: sha256_file(path) for pid, path in pictures.files.items()}
    try:
        sections = build_sections(script, timings, cfg, pictures.by_section)
    except ValueError as exc:
        raise RenderError(str(exc)) from None
    _check_overrides(script, cfg)
    eligibility, still_notes = _page_eligibility(folder, book, script, cfg, Path(root))
    keys = []
    for s in sections:
        key = section_key(s, picture_sha[s.picture], cfg)
        el = eligibility.get(s.name)
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
    )


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
    if record.get("final_key") == _final_key(inputs, page_turn_sha):
        return "up_to_date", ""
    return "out_of_date", ""


def render_video(
    folder: BookFolder,
    root: Path,
    force: bool = False,
    run: Runner = run_ffmpeg,
    on_progress: Progress | None = None,
    sfx_dir: Path | None = None,
) -> RenderResult:
    """Render the approved episode of one book to `books/<slug>/build/video/<slug>.mp4` (spec 003).

    Sections and transitions are cached by key; the final video is reused when the ordered keys,
    the tracks, the page-turn sound and the audio/encode settings are unchanged (AC-7.1, AC-7.2).
    The page-turn sound comes from `sfx_dir` (default `<root>/assets/audio_sfx`).
    """
    inputs = _load_inputs(folder, root)
    slug, cfg = inputs.slug, inputs.cfg
    sections, keys, trans_keys = inputs.sections, inputs.keys, inputs.trans_keys
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
        if not force and final.exists() and record and record.get("final_key") == final_key:
            stale = any(record.get(k) != v for k, v in page_turn_fields.items())
            if stale and page_turn_fields["page_turn_file_sha256"] is not None:
                # Records written before spec 004 lack these; render_check needs them.
                write_atomic(record_path, {**record, **page_turn_fields})
            return RenderResult(final, duration, 0, len(sections), True)
        decoded = _decode_sounds(inputs)  # fail on a bad sound before any encoding
        record_path.unlink(missing_ok=True)  # stale until the new video is complete

        rendered = reused = 0
        for section, key in zip(sections, keys, strict=True):
            clip = paths.sections / f"{section.name}.mp4"
            meta = paths.sections / f"{section.name}.json"
            picture = pictures.files[section.picture]
            if _reusable(clip, meta, key, force):
                reused += 1
                action = "reused"
            else:
                meta.unlink(missing_ok=True)
                _render_section(section, picture, clip, paths.frames / section.name, cfg, run)
                write_atomic(meta, {"key": key, "frames": section.frames, "picture": str(picture)})
                rendered += 1
                action = "rendered"
            if on_progress:
                on_progress(section.name, action)

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
        try:
            _run_to(run, lambda out: concat_command(list_file, out), joined, "join")
            _run_to(run, lambda out: mux_command(joined, master, out, cfg), final, "mux")
        finally:
            joined.unlink(missing_ok=True)
        write_atomic(
            record_path,
            {
                "final_key": final_key,
                "duration": round(duration, 3),
                "frames": total_frames(sections, cfg),
                "sections": [
                    {"name": s.name, "key": k, "picture": str(pictures.files[s.picture])}
                    for s, k in zip(sections, keys, strict=True)
                ],
                **page_turn_fields,
                "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        return RenderResult(final, duration, rendered, reused, False)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise RenderError(f"not enough disk space to write video in {paths.root}") from None
        raise


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, run: Runner | None = None) -> int:
    args = parse_args(argv)
    try:
        folder = book_folder(args.book, args.root)
        result = render_video(
            folder,
            args.root,
            force=args.force,
            run=run or run_ffmpeg,
            on_progress=lambda name, action: print(f"  {name}: {action}", flush=True),
            sfx_dir=args.sfx_dir,
        )
    except (RenderError, PictureError, SoundError, BookError, ConfigError, LibraryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            print(
                f"error: not enough disk space to write video in {args.build_dir}", file=sys.stderr
            )
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
