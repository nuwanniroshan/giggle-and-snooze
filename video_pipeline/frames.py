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


def canvas_parts(
    picture: Image.Image, cfg: RenderConfig
) -> tuple[Image.Image, tuple[int, int], tuple[int, int]]:
    """(blurred darkened backdrop W×H, contain size, paste offset): the parts `compose_canvas`
    puts together, shared with the animated sections (spec 010 FR-12)."""
    size = (cfg.width, cfg.height)
    backdrop = ImageOps.fit(picture, size, Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(cfg.backdrop.blur))
    darken = cfg.backdrop.darken
    backdrop = backdrop.point(lambda v: round(v * darken))
    front = _contain_size(picture.size, size)
    offset = ((size[0] - front[0]) // 2, (size[1] - front[1]) // 2)
    return backdrop, front, offset


def _contain_size(image: tuple[int, int], size: tuple[int, int]) -> tuple[int, int]:
    """The size `ImageOps.contain(image, size)` resizes to (Pillow's arithmetic, so the still
    canvas stays byte-identical)."""
    im_ratio = image[0] / image[1]
    dest_ratio = size[0] / size[1]
    if im_ratio == dest_ratio:
        return size
    if im_ratio > dest_ratio:
        return size[0], round(image[1] / image[0] * size[0])
    return round(image[0] / image[1] * size[1]), size[1]


def compose_canvas(picture_path: Path, cfg: RenderConfig, out_path: Path) -> None:
    """Write the RGB W×H canvas: the whole picture at full frame height, centred, over a blurred,
    darkened copy of itself filling the frame (AC-3.1)."""
    picture_path = Path(picture_path)
    try:
        with Image.open(picture_path) as im:
            picture = im.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise PictureError(picture_path, str(exc)) from None
    backdrop, size, offset = canvas_parts(picture, cfg)
    backdrop.paste(picture.resize(size, Image.Resampling.LANCZOS), offset)
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

_PAGE_TURN_SECONDS = 0.7
_PAGE_TURN_PEAK = 0.5


def _band(noise: np.ndarray, sample_rate: int, low: float, high: float) -> np.ndarray:
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(len(noise), 1 / sample_rate)
    spectrum[(freqs < low) | (freqs > high)] = 0
    return np.fft.irfft(spectrum, len(noise))


def page_turn_sound(sample_rate: int, seed: int = 0) -> np.ndarray:
    """A deterministic ~0.7 s paper page turn; float32 mono, peak 0.5, faded to silence.

    Three layers, like a real page: a paper-lift scrape (mid-band noise with a crackle of tiny
    fibre clicks that rises slowly), the swish of the page crossing (brighter noise with a smooth
    bell envelope) and a soft landing tap (a short low thump plus a dull click)."""
    rng = np.random.default_rng(seed)
    n = round(_PAGE_TURN_SECONDS * sample_rate)
    t = np.arange(n) / sample_rate

    # 1. Lift: 0.00-0.22 s, 800-3500 Hz, crackled by sparse random clicks.
    lift_noise = _band(rng.standard_normal(n), sample_rate, 800, 3500)
    clicks = (rng.random(n) < 0.004) * rng.uniform(0.4, 1.0, n)
    kernel_t = np.arange(round(0.004 * sample_rate)) / sample_rate
    crackle = np.convolve(clicks, np.exp(-kernel_t / 0.0008), mode="full")[:n]
    lift_env = np.clip(t / 0.22, 0, 1) ** 2 * np.clip((0.26 - t) / 0.04, 0, 1)
    lift = (lift_noise * (0.35 + 2.5 * crackle)) * lift_env * 0.5

    # 2. Swish: 0.12-0.50 s, 2000-9000 Hz, bell-shaped (raised cosine).
    swish_noise = _band(rng.standard_normal(n), sample_rate, 2000, 9000)
    local = (t - 0.12) / 0.38
    swish_env = np.where((local >= 0) & (local <= 1), np.sin(np.pi * np.clip(local, 0, 1)) ** 2, 0)
    swish = swish_noise * swish_env * (0.8 + 0.2 * np.sin(2 * np.pi * 23 * t))

    # 3. Landing tap at 0.50 s: a falling low thump and a short dull click.
    tap_t = t - 0.50
    thump_env = np.where(tap_t >= 0, np.exp(-np.clip(tap_t, 0, None) / 0.035), 0)
    thump = np.sin(2 * np.pi * (150 - 300 * np.clip(tap_t, 0, 0.05)) * np.clip(tap_t, 0, None))
    click = _band(rng.standard_normal(n), sample_rate, 400, 2500) * np.where(
        tap_t >= 0, np.exp(-np.clip(tap_t, 0, None) / 0.008), 0
    )
    tap = (thump * 0.9 + click * 1.4) * thump_env

    def level(x: np.ndarray) -> np.ndarray:
        peak = np.abs(x).max()
        return x / peak if peak else x

    sound = 0.55 * level(lift) + 1.0 * level(swish) + 0.8 * level(tap)
    sound *= np.clip((t[-1] - t) / 0.08, 0, 1)
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


def _sliding_min(values: np.ndarray, width: int) -> np.ndarray:
    """out[i] = min(values[i : i + width]) for every full window (log2(width) numpy passes)."""
    span, cur = 1, values
    while span * 2 <= width:
        cur = np.minimum(cur[:-span], cur[span:])
        span *= 2
    count = len(values) - width + 1
    return np.minimum(cur[:count], cur[width - span : width - span + count])


def _limiter_gain(region: np.ndarray, limit: float, width: int) -> np.ndarray:
    """Gain curve keeping `region` under `limit`: the gain each peak needs, spread over
    `width` samples before and after it (a minimum filter, then a moving average of the same
    width, which can never exceed the gain a sample itself needs)."""
    half = width // 2
    need = np.minimum(1.0, limit / np.maximum(np.abs(region), 1e-12))
    # Edges repeat the first/last value: beside a stretch of the mix they are 1 (padding
    # covers the reach), and at the very start or end of the mix there is nothing beyond.
    padded = np.pad(need, half, mode="edge")
    low = _sliding_min(padded, width)  # one value per region sample
    ramp = np.concatenate([[0.0], np.cumsum(np.pad(low, half, mode="edge"))])
    return (ramp[width:] - ramp[:-width]) / width


def limit_peaks(mix: np.ndarray, limit: float, window_ms: float, sample_rate: int) -> None:
    """Hold `mix` (in place) at or under `limit` with a gentle look-ahead limiter.

    Only the stretches around peaks over the limit change, by a smooth dip of about `window_ms`
    on each side; everything else is untouched. The earlier whole-mix scaling cancelled the
    +6 dB voice gain of spec 012, because one short peak lowered every sample."""
    over = np.flatnonzero(np.abs(mix) > limit)
    if over.size == 0:
        return
    width = max(3, round(window_ms / 1000 * sample_rate)) | 1  # odd
    reach = 2 * width
    breaks = np.flatnonzero(np.diff(over) > 2 * reach)
    firsts = np.concatenate([[over[0]], over[breaks + 1]])
    lasts = np.concatenate([over[breaks], [over[-1]]])
    for first, last in zip(firsts, lasts, strict=True):
        start, end = max(0, int(first) - reach), min(len(mix), int(last) + reach + 1)
        mix[start:end] *= _limiter_gain(mix[start:end], limit, width)


def mix_master(
    sections: list[Section],
    tracks: dict[str, np.ndarray],
    page_turn: np.ndarray,
    cfg: RenderConfig,
    sample_rate: int,
    ambience: np.ndarray | None = None,
) -> np.ndarray:
    """The episode's mono float32 audio, exactly as long as the video.

    Each section's track starts at `section start + lead_in`; the page-turn sound (at
    `sfx_gain_db`) starts at each transition, i.e. the end of the previous section. The voice tracks
    are raised by `voice_gain_db` first (spec 012 FR-6). Peaks over `peak_dbfs` are held down by
    `limit_peaks`, a short look-ahead dip around each peak; the rest of the mix is untouched.
    `ambience` (spec 007), a bed already levelled and exactly as long as the mix, is added just
    before the peak limit; `None` gives the same output as before the bed existed.
    """
    length = round(total_frames(sections, cfg) / cfg.fps * sample_rate)
    mix = np.zeros(length, dtype=np.float64)
    voice_gain = 10 ** (cfg.audio.voice_gain_db / 20)
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
        mix[at : at + len(track)] += track * voice_gain
    gain = 10 ** (cfg.audio.sfx_gain_db / 20)
    turn = np.asarray(page_turn, dtype=np.float64) * gain
    for _start, end in times[:-1]:
        at = round(end * sample_rate)
        piece = turn[: max(0, length - at)]
        mix[at : at + len(piece)] += piece
    if ambience is not None:
        if len(ambience) != length:
            raise ValueError(f"ambience bed has {len(ambience)} samples, the mix has {length}")
        mix += np.asarray(ambience, dtype=np.float64)
    limit_peaks(mix, 10 ** (cfg.audio.peak_dbfs / 20), cfg.audio.limiter_ms, sample_rate)
    return mix.astype(np.float32)
