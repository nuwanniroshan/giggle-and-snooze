"""Drawing side of the publication design (spec 023): fonts for reportlab.

Fonts are code assets, so callers resolve them from PROJECT_ROOT even when a build runs with
`--root` for tests.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from video_pipeline.library import PROJECT_ROOT
from video_pipeline.publication import PublicationError

FONT_NAMES = {"display": "GSDisplay", "body": "GSBody", "body_bold": "GSBody-Bold"}


def font_paths(design: dict, root: Path = PROJECT_ROOT) -> dict[str, Path]:
    """Bundled font files by role. A missing file stops the build, naming it (AC-7.2)."""
    paths = {role: root / design["fonts"][role] for role in FONT_NAMES}
    for path in paths.values():
        if not path.is_file():
            raise PublicationError(f"font file not found: {path}")
    return paths


def register_fonts(design: dict, root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Register the bundled TTFs with reportlab once; returns role -> registered name."""
    paths = font_paths(design, root)
    registered = set(pdfmetrics.getRegisteredFontNames())
    for role, name in FONT_NAMES.items():
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, str(paths[role])))
    return dict(FONT_NAMES)


def measure(text: str, role: str, size: float) -> float:
    """Width of `text` in points. Call `register_fonts` first."""
    return pdfmetrics.stringWidth(text, FONT_NAMES[role], size)
