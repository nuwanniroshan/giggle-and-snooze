"""Stage 3 pictures and sound (spec 003): the 16:9 canvas, caption images, the page-turn sound and
the master audio track.

Pillow + numpy only; the one external call is FFmpeg decoding a supplied page-turn sound.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from video_pipeline.synthesize_voices import write_wav_atomic
from video_pipeline.timeline import RenderConfig, Section, section_times, total_frames, wrap_lines

PAGE_TURN_FILE = "page_turn.wav"
_LINE_GAP = 0.25  # gap between caption lines, as a fraction of the font size
_MIN_FONT_SIZE = 12


class PictureError(Exception):
    """A picture (illustration, cover or nook image) could not be read."""

    def __init__(self, path: Path, reason: str = "") -> None:
        self.path = Path(path)
        detail = f": {reason}" if reason else ""
        super().__init__(f"cannot read picture {self.path}{detail}")


class SoundError(Exception):
    """A supplied sound file could not be decoded."""

    def __init__(self, path: Path, reason: str = "") -> None:
        self.path = Path(path)
        detail = f": {reason}" if reason else ""
        super().__init__(f"cannot read sound {self.path}{detail}")


def _save_png_atomic(image: Image.Image, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + ".tmp.png")
    try:
        image.save(tmp, format="PNG")
        os.replace(tmp, out_path)  # noqa: PTH105 - mirrors write_wav_atomic
    finally:
        tmp.unlink(missing_ok=True)


# --- canvas ---------------------------------------------------------------------------------


def compose_canvas(picture_path: Path, cfg: RenderConfig, out_path: Path) -> None:
    """Write the RGB W×H canvas: the whole picture at full frame height, centred, over a blurred,
    darkened copy of itself filling the frame (AC-3.1)."""
    picture_path = Path(picture_path)
    size = (cfg.width, cfg.height)
    try:
        with Image.open(picture_path) as im:
            picture = im.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise PictureError(picture_path, str(exc)) from None
    backdrop = ImageOps.fit(picture, size, Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(cfg.backdrop.blur))
    darken = cfg.backdrop.darken
    backdrop = backdrop.point(lambda v: round(v * darken))
    front = ImageOps.contain(picture, size, Image.Resampling.LANCZOS)
    backdrop.paste(front, ((size[0] - front.width) // 2, (size[1] - front.height) // 2))
    _save_png_atomic(backdrop, out_path)


# --- captions -------------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _load_font(font: str, fallback: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (font, fallback):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _text_block(lines: list[str], font) -> tuple[int, int, int]:
    """(width, line height, gap) of a block of lines in `font`."""
    ascent, descent = font.getmetrics()
    width = max(round(font.getlength(line)) for line in lines)
    return width, ascent + descent, round(font.size * _LINE_GAP)


def render_caption(text: str, cfg: RenderConfig, out_path: Path) -> None:
    """Write a transparent RGBA W×H caption: white centred lines on a black translucent rounded
    band, `margin` above the bottom edge (AC-4.4).

    Lines come from `timeline.wrap_lines`; more than `max_lines` is a ValueError (cards are split
    upstream). If the block would be wider than the frame allows (an over-long word), the font is
    shrunk until it fits, so the caption always stays inside the margins (AC-4.3).
    """
    style = cfg.captions
    lines = wrap_lines(text, style.max_chars)
    if not lines:
        raise ValueError("caption text is empty")
    if len(lines) > style.max_lines:
        raise ValueError(
            f"caption needs {len(lines)} lines, at most {style.max_lines} allowed: {text!r}"
        )
    max_width = cfg.width - 2 * style.margin - 2 * style.padding
    size = style.font_size
    font = _load_font(style.font, style.fallback_font, size)
    width, line_h, gap = _text_block(lines, font)
    while width > max_width and size > _MIN_FONT_SIZE:
        size = max(_MIN_FONT_SIZE, int(size * max_width / width))
        font = _load_font(style.font, style.fallback_font, size)
        width, line_h, gap = _text_block(lines, font)
    block_h = len(lines) * line_h + (len(lines) - 1) * gap

    band_w, band_h = width + 2 * style.padding, block_h + 2 * style.padding
    x0 = (cfg.width - band_w) // 2
    y1 = cfg.height - style.margin
    y0 = y1 - band_h
    band = Image.new("RGBA", (cfg.width, cfg.height), (0, 0, 0, 0))
    ImageDraw.Draw(band).rounded_rectangle(
        (x0, y0, x0 + band_w - 1, y1 - 1),
        radius=style.padding,
        fill=(0, 0, 0, round(style.band_opacity * 255)),
    )
    layer = Image.new("RGBA", (cfg.width, cfg.height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    y = y0 + style.padding
    for line in lines:
        line_w = font.getlength(line)
        draw.text((round((cfg.width - line_w) / 2), y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + gap
    _save_png_atomic(Image.alpha_composite(band, layer), out_path)


# --- page-turn sound ------------------------------------------------------------------------

_BURSTS = ((0.0, 0.050), (0.11, 0.070), (0.24, 0.090))  # (onset s, decay time constant s)
_PAGE_TURN_SECONDS = 0.6
_PAGE_TURN_PEAK = 0.5


def page_turn_sound(sample_rate: int, seed: int = 0) -> np.ndarray:
    """A deterministic ~0.6 s paper rustle: three band-limited noise bursts with a fast attack and
    exponential decay, faded to silence at the end; float32 mono, peak 0.5."""
    rng = np.random.default_rng(seed)
    n = round(_PAGE_TURN_SECONDS * sample_rate)
    t = np.arange(n) / sample_rate
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sample_rate)
    spectrum[(freqs < 700) | (freqs > 7000)] = 0
    noise = np.fft.irfft(spectrum, n)
    envelope = np.zeros(n)
    attack = 0.004
    for (onset, decay), level in zip(_BURSTS, rng.uniform(0.6, 1.0, len(_BURSTS)), strict=True):
        local = t - onset
        rise = np.clip(local / attack, 0, 1)
        fall = np.exp(-np.clip(local - attack, 0, None) / decay)
        envelope += np.where(local >= 0, level * rise * fall, 0)
    fade = np.clip((t[-1] - t) / 0.08, 0, 1)
    sound = noise * envelope * fade
    sound *= _PAGE_TURN_PEAK / np.abs(sound).max()
    return sound.astype(np.float32)


def _decode_with_ffmpeg(path: Path, sample_rate: int) -> np.ndarray:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "pipe:1",
    ]
    try:
        # Binary stdout, so no text=True.
        result = subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise SoundError(path, "ffmpeg not found; brew install ffmpeg") from None
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else ""
        raise SoundError(path, stderr.strip()[-500:] or f"ffmpeg exit {exc.returncode}") from None
    audio = np.frombuffer(result.stdout, dtype="<f4")
    if audio.size == 0:
        raise SoundError(path, "no audio decoded")
    return audio.astype(np.float32)


def _read_cache(cache_path: Path, sample_rate: int) -> np.ndarray | None:
    try:
        audio, rate = sf.read(cache_path, dtype="float32", always_2d=False)
    except (OSError, sf.LibsndfileError, RuntimeError):
        return None
    if rate != sample_rate or audio.ndim != 1 or audio.size == 0:
        return None
    return audio


def load_page_turn(assets_dir: Path, sample_rate: int, cache_path: Path) -> np.ndarray:
    """The page-turn sound at `sample_rate`, mono float32 (AC-5.3).

    `assets_dir` is the shared audio-effects folder (`assets/audio_sfx`). A supplied
    `page_turn.wav` there is decoded with FFmpeg (unreadable → SoundError, no fallback); otherwise
    the generated sound is written once to `cache_path` and read back from it on every call.
    """
    supplied = Path(assets_dir) / PAGE_TURN_FILE
    if supplied.exists():
        return _decode_with_ffmpeg(supplied, sample_rate)
    cache_path = Path(cache_path)
    cached = _read_cache(cache_path, sample_rate) if cache_path.exists() else None
    if cached is not None:
        return cached
    write_wav_atomic(cache_path, page_turn_sound(sample_rate), sample_rate)
    cached = _read_cache(cache_path, sample_rate)
    if cached is None:
        raise SoundError(cache_path, "generated page-turn sound could not be read back")
    return cached


# --- master audio ---------------------------------------------------------------------------


def mix_master(
    sections: list[Section],
    tracks: dict[str, np.ndarray],
    page_turn: np.ndarray,
    cfg: RenderConfig,
    sample_rate: int,
) -> np.ndarray:
    """The episode's mono float32 audio, exactly as long as the video.

    Each section's track starts at `section start + lead_in`; the page-turn sound (at
    `sfx_gain_db`) starts at each transition, i.e. the end of the previous section. If the peak
    exceeds `peak_dbfs` the whole mix is scaled down; speech is otherwise untouched (AC-1.3).
    """
    length = round(total_frames(sections, cfg) / cfg.fps * sample_rate)
    mix = np.zeros(length, dtype=np.float64)
    times = section_times(sections, cfg)
    for section, (start, end) in zip(sections, times, strict=True):
        if section.track is None:
            continue
        if section.track not in tracks:
            raise ValueError(f"no audio for track {section.track} (section {section.name})")
        track = np.asarray(tracks[section.track], dtype=np.float64)
        at = round((start + cfg.lead_in) * sample_rate)
        if at + len(track) > round(end * sample_rate):
            raise ValueError(f"track {section.track} is longer than section {section.name} allows")
        mix[at : at + len(track)] += track
    gain = 10 ** (cfg.audio.sfx_gain_db / 20)
    turn = np.asarray(page_turn, dtype=np.float64) * gain
    for _start, end in times[:-1]:
        at = round(end * sample_rate)
        piece = turn[: max(0, length - at)]
        mix[at : at + len(piece)] += piece
    limit = 10 ** (cfg.audio.peak_dbfs / 20)
    peak = float(np.abs(mix).max()) if length else 0.0
    if peak > limit:
        mix *= limit / peak
    return mix.astype(np.float32)
