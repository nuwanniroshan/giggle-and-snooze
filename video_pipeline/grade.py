"""Final grade (spec 013): pure FFmpeg filter text for the picture and the sound, fade shortening,
the grade's cache key and parsers for FFmpeg's loudness reports. No I/O besides what the callers
pass in; the render step runs the commands."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

from video_pipeline.timeline import GradeSettings, RenderConfig, parse_grade

BALANCE = ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh")
GRADE_VERSION = 1  # bump when a code change alters the graded pixels or sound


class LoudnessError(Exception):
    """FFmpeg's loudness report could not be read."""


@dataclass(frozen=True)
class Loudness:
    """What one loudness measurement says (LUFS, dBTP, LU)."""

    integrated: float
    true_peak: float
    loudness_range: float
    threshold: float = 0.0
    offset: float = 0.0


def resolve_grade(shared: GradeSettings, book: dict, book_path: object) -> GradeSettings:
    """The settings a book is graded with: its own `grade` block over the shared ones (FR-2)."""
    return parse_grade(book.get("grade"), f"{book_path}: grade", base=shared)


def is_active(settings: GradeSettings, no_grade: bool = False) -> bool:
    """Whether the render runs the grading pass at all (FR-6)."""
    return settings.enabled and (settings.picture or settings.sound) and not no_grade


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def fade_lengths(fade_in: float, fade_out: float, limit: float) -> tuple[float, float]:
    """The two fades shortened in proportion so together they fit in `limit` seconds (AC-2.4).
    A zero fade stays zero."""
    total = fade_in + fade_out
    if limit <= 0:
        return 0.0, 0.0
    if total <= limit:
        return fade_in, fade_out
    scale = limit / total
    return fade_in * scale, fade_out * scale


def picture_filter(g: GradeSettings, duration: float, fps: int) -> str:
    """The picture grade for a video of `duration` seconds: eq, colour balance, light sharpening,
    vignette, then the fades (the last frame is fully black), tagged BT.709, yuv420p. Neutral
    settings add no filter. "" when the picture grade is off."""
    if not g.picture:
        return ""
    steps: list[str] = []
    eq = [
        f"{key}={_num(value)}"
        for key, value, neutral in (
            ("contrast", g.contrast, 1.0),
            ("saturation", g.saturation, 1.0),
            ("gamma", g.gamma, 1.0),
            ("brightness", g.brightness, 0.0),
        )
        if value != neutral
    ]
    if eq:
        steps.append("eq=" + ":".join(eq))
    balance = [f"{k}={_num(getattr(g, k))}" for k in BALANCE if getattr(g, k)]
    if balance:
        steps.append("colorbalance=" + ":".join(balance))
    if g.unsharp_amount or g.unsharp_chroma_amount:
        size = g.unsharp_size
        steps.append(
            f"unsharp={size}:{size}:{_num(g.unsharp_amount)}:{size}:{size}:"
            f"{_num(g.unsharp_chroma_amount)}"
        )
    if g.vignette_angle:
        steps.append(f"vignette=angle={_num(g.vignette_angle)}")
    frame = 1.0 / fps
    fade_in, fade_out = fade_lengths(g.picture_fade_in, g.picture_fade_out, duration - frame)
    if fade_in > 0:
        steps.append(f"fade=t=in:st=0:d={_num(fade_in)}")
    if fade_out > 0:
        steps.append(f"fade=t=out:st={_num(duration - frame - fade_out)}:d={_num(fade_out)}")
    steps.append("format=yuv420p")
    steps.append("setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv")
    return ",".join(steps)


def _loudnorm(g: GradeSettings, measured: Loudness | None, report: bool) -> str:
    text = f"loudnorm=I={_num(g.loudness_lufs)}:TP={_num(g.true_peak_dbfs)}:LRA={_num(g.loudness_range)}"
    if measured is not None:
        text += (
            f":measured_I={_num(measured.integrated)}:measured_TP={_num(measured.true_peak)}"
            f":measured_LRA={_num(measured.loudness_range)}"
            f":measured_thresh={_num(measured.threshold)}:offset={_num(measured.offset)}"
            ":linear=true"
        )
    if report:
        text += ":print_format=json"
    return text


def sound_filter(
    g: GradeSettings,
    duration: float,
    rate: int,
    measured: Loudness | None = None,
    measuring: bool = False,
) -> str:
    """The sound grade: high-pass, light compression, fades, then one loudness. With `measuring`
    the loudnorm step only reports (pass one); with `measured` it applies those numbers as one
    constant gain (pass two) and the sound is resampled back to `rate`. "" when off."""
    if not g.sound:
        return ""
    steps: list[str] = []
    if g.highpass_hz:
        steps.append(f"highpass=f={_num(g.highpass_hz)}")
    if g.compressor_ratio > 1:
        steps.append(
            f"acompressor=threshold={_num(g.compressor_threshold_db)}dB:ratio={_num(g.compressor_ratio)}"
            f":attack={_num(g.compressor_attack_ms)}:release={_num(g.compressor_release_ms)}"
            f":makeup={_num(g.compressor_makeup)}"
        )
    fade_in, fade_out = fade_lengths(g.sound_fade_in, g.sound_fade_out, duration)
    if fade_in > 0:
        steps.append(f"afade=t=in:st=0:d={_num(fade_in)}")
    if fade_out > 0:
        steps.append(f"afade=t=out:st={_num(duration - fade_out)}:d={_num(fade_out)}")
    if not measuring and measured is None:
        return ",".join(steps)  # loudness step skipped (nothing audible to measure)
    steps.append(_loudnorm(g, None if measuring else measured, report=measuring))
    if not measuring:
        steps.append(f"aresample={rate}")
    return ",".join(steps)


def measure_command(src: str, g: GradeSettings, duration: float, cfg: RenderConfig) -> list[str]:
    """Pass one of the loudness step: the sound chain up to `loudnorm` with its report on stderr."""
    chain = sound_filter(g, duration, cfg.video.audio_rate, measuring=True)
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-vn", "-af", chain, "-f", "null", "-"]


def grade_command(
    src: str,
    out: str,
    g: GradeSettings,
    duration: float,
    cfg: RenderConfig,
    measured: Loudness | None,
) -> list[str]:
    """The one grading pass: re-encode the picture (x264, BT.709, yuv420p) and the sound (AAC at
    the render's rate); a part that is switched off is copied."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-map", "0:v", "-map", "0:a"]
    picture = picture_filter(g, duration, cfg.fps)
    sound = sound_filter(g, duration, cfg.video.audio_rate, measured=measured)
    if picture:
        cmd += [
            "-vf",
            picture,
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
        ]
    else:
        cmd += ["-c:v", "copy"]
    if sound:
        cmd += ["-af", sound]
    cmd += ["-c:a", "aac", "-b:a", cfg.video.audio_bitrate, "-ar", str(cfg.video.audio_rate)]
    cmd += ["-ac", "2", "-movflags", "+faststart", out]
    if not sound:
        cmd[cmd.index("aac")] = "copy"
        for flag in ("-b:a", "-ar", "-ac"):
            at = cmd.index(flag)
            del cmd[at : at + 2]
    return cmd


def loudness_command(path: str) -> list[str]:
    """Measure a finished file (EBU R128, true peak) with its summary on stderr."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        path,
        "-vn",
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]


_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def parse_loudnorm_report(text: str) -> Loudness | None:
    """Pass one's JSON report as a `Loudness`, or None when the sound is silent (nothing to
    normalise). Raises LoudnessError when no report is found."""
    blocks = _JSON_RE.findall(text)
    if not blocks:
        raise LoudnessError("no loudnorm report in FFmpeg's output")
    try:
        data = json.loads(blocks[-1])
        values = {k: float(data[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh")}
        offset = float(data["target_offset"])
    except (ValueError, KeyError, TypeError) as exc:
        raise LoudnessError(f"unreadable loudnorm report ({exc})") from None
    if not all(math.isfinite(v) for v in (*values.values(), offset)):
        return None
    return Loudness(
        values["input_i"], values["input_tp"], values["input_lra"], values["input_thresh"], offset
    )


_I_RE = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.MULTILINE)
_LRA_RE = re.compile(r"^\s*LRA:\s*(-?[\d.]+)\s*LU", re.MULTILINE)
_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.MULTILINE)


def parse_ebur128_summary(text: str) -> Loudness:
    """Integrated loudness, true peak and loudness range from ebur128's closing summary."""
    summary = text[text.rfind("Summary:") :] if "Summary:" in text else ""
    found = [rx.search(summary) for rx in (_I_RE, _LRA_RE, _PEAK_RE)]
    if not all(found):
        raise LoudnessError("no loudness summary in FFmpeg's output")
    i, lra, peak = (float(m.group(1)) for m in found if m)
    return Loudness(i, peak, lra)


def grade_key(ungraded_sha256: str, g: GradeSettings, cfg: RenderConfig) -> str:
    """Cache key of the graded video: its input's checksum, the resolved settings and what the
    encode depends on (a settings-only change gives a new key, an unchanged one the same)."""
    payload = {
        "version": GRADE_VERSION,
        "input": ungraded_sha256,
        "grade": asdict(g),
        "fps": cfg.fps,
        "video": asdict(cfg.video),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
