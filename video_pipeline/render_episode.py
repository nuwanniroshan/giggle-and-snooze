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
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, UnidentifiedImageError

from video_pipeline.contract import ConfigError, slugify
from video_pipeline.episode import BookError, load_book
from video_pipeline.episode_tools import CONFIG_DIR, ROOT, write_atomic
from video_pipeline.frames import (
    PictureError,
    SoundError,
    compose_canvas,
    load_page_turn,
    mix_master,
    render_caption,
)
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


def resolve_pictures(
    book: dict, slug: str, episodes_dir: Path, assets_dir: Path
) -> ResolvedPictures:
    """Every picture the episode shows (FR-1, AC-6.1, AC-6.3).

    Pages and covers come from `<assets>/illustrations/` (missing → RenderError naming the page
    and file). A nook image is taken from `episodes/<slug>/` first, then `<assets>/nook/`; with
    neither, the intro shows the front cover and the outro the back cover.
    """
    illustrations = Path(assets_dir) / "illustrations"
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
        for folder in (Path(episodes_dir) / slug, Path(assets_dir) / "nook"):
            path = folder / f"{nook}.png"
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


def render_video(
    book_path: Path,
    episodes_dir: Path,
    build_dir: Path,
    config_dir: Path,
    assets_dir: Path,
    force: bool = False,
    run: Runner = run_ffmpeg,
    on_progress: Progress | None = None,
) -> RenderResult:
    """Render the approved episode of `book_path` to `build/<slug>/video/<slug>.mp4` (spec 003).

    Sections and transitions are cached by key; the final video is reused when the ordered keys,
    the tracks, the page-turn sound and the audio/encode settings are unchanged (AC-7.1, AC-7.2).
    """
    book = load_book(book_path)
    slug = slugify(book["metadata"]["title"])
    script_path = Path(episodes_dir) / slug / "episode_script.json"
    audio = AudioPaths(Path(build_dir) / slug / "audio")
    voice_cfg = load_voice_config(Path(config_dir) / "voices.json")
    settings_sha = current_settings_sha(voice_cfg, book, Path(book_path), script_path)
    problems = approval_problems(audio, script_path, settings_sha, Path(book_path))
    if problems:
        raise RenderError("; ".join(problems))
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"episode script is not valid JSON: {script_path} ({exc})") from None
    cfg = load_render_config(Path(config_dir) / "render.json")

    track_names = [
        "intro",
        *[page_section_name(int(p["page_number"])) for p in script["pages"]],
        "outro",
    ]
    timings, tracks, sample_rate = _load_tracks(audio, track_names)
    pictures = resolve_pictures(book, slug, Path(episodes_dir), Path(assets_dir))
    for path in pictures.files.values():
        _check_picture(path)
    picture_sha = {pid: sha256_file(path) for pid, path in pictures.files.items()}
    try:
        sections = build_sections(script, timings, cfg, pictures.by_section)
    except ValueError as exc:
        raise RenderError(str(exc)) from None
    keys = [section_key(s, picture_sha[s.picture], cfg) for s in sections]
    pairs = list(zip(sections, sections[1:], strict=False))
    trans_keys = [transition_key(a, b, cfg) for a, b in zip(keys, keys[1:], strict=False)]

    paths = VideoPaths(Path(build_dir) / slug / "video")
    try:
        page_turn = load_page_turn(
            Path(assets_dir) / "audio_sfx", sample_rate, Path(build_dir) / "sfx" / "page_turn.wav"
        )
        final_key = _digest(
            {
                "sections": [[s.name, k] for s, k in zip(sections, keys, strict=True)],
                "transitions": trans_keys,
                "tracks": {name: sha256_file(audio.track(name)) for name in track_names},
                "page_turn": hashlib.sha256(page_turn.tobytes()).hexdigest(),
                "sample_rate": sample_rate,
                "audio": asdict(cfg.audio),
                "video": asdict(cfg.video),
            }
        )
        duration = total_frames(sections, cfg) / cfg.fps
        final, record_path = paths.final(slug), paths.record(slug)
        record = _read_json(record_path)
        if not force and final.exists() and record and record.get("final_key") == final_key:
            return RenderResult(final, duration, 0, len(sections), True)
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
        write_wav_atomic(
            master, mix_master(sections, tracks, page_turn, cfg, sample_rate), sample_rate
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
    parser.add_argument("--book", type=Path, default=ROOT / "book_data.json")
    parser.add_argument("--episodes-dir", type=Path, default=ROOT / "episodes")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--assets-dir", type=Path, default=ROOT / "assets")
    parser.add_argument("--force", action="store_true", help="re-render every section")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, run: Runner | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_video(
            args.book,
            args.episodes_dir,
            args.build_dir,
            args.config_dir,
            args.assets_dir,
            force=args.force,
            run=run or run_ffmpeg,
            on_progress=lambda name, action: print(f"  {name}: {action}", flush=True),
        )
    except (RenderError, PictureError, SoundError, BookError, ConfigError) as exc:
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
