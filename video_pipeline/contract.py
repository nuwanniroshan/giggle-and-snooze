"""Episode script contract: shared constants, text helpers, the banter model and validation."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

ALLOWED_TAGS = ("giggle", "laugh", "chuckle", "gasp", "sigh", "yawn")
NARRATION_MOTIONS = ("slow_push_in", "slow_pull_out", "pan_left", "pan_right", "static")
INTRO_EFFECT = "zoom_in_center"
OUTRO_EFFECT = "zoom_out_warm_fade"
SPEAKERS = ("storyteller", "kid_older", "kid_younger")
KIDS = ("kid_older", "kid_younger")
CAST_FIELDS = ("name", "voice", "description")
TOP_LEVEL_KEYS = (
    "book_slug",
    "title",
    "aspect_ratio",
    "resolution",
    "cast",
    "intro",
    "pages",
    "outro",
)
MAX_KID_WORDS = 25
MAX_NARRATION_WORDS = 40
MAX_NARRATION_SENTENCES = 2

TAG_RE = re.compile(r"<[^<>]*>")
_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)]*$")
_ABBREVIATIONS = frozenset({"Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr."})

Speaker = Literal["storyteller", "kid_older", "kid_younger"]


class ConfigError(Exception):
    """A required project configuration file is missing or invalid."""


class BanterParseError(Exception):
    """The model's reply is not valid banter JSON."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class BanterLine(BaseModel):
    speaker: Speaker
    text: str = Field(min_length=1)


class Moment(BaseModel):
    page_number: int
    grounding: str = Field(min_length=1)
    lines: list[BanterLine] = Field(min_length=2, max_length=5)


class Banter(BaseModel):
    """What the text model returns: every spoken line that is not read from the book."""

    intro: list[BanterLine] = Field(min_length=2, max_length=4)
    moments: list[Moment]
    outro: list[BanterLine] = Field(min_length=3, max_length=5)


def parse_banter(raw: str) -> Banter:
    try:
        return Banter.model_validate_json(raw)
    except ValidationError as exc:
        errors = [
            f"response JSON invalid at {'.'.join(map(str, err['loc'])) or 'root'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise BanterParseError(errors) from None


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def strip_tags(text: str) -> str:
    return normalize_ws(TAG_RE.sub(" ", text))


def word_count(text: str) -> int:
    return len(strip_tags(text).split())


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END_RE.search(normalize_ws(text)))


def split_sentences(text: str) -> list[str]:
    """Split at whitespace after . ! ? … (plus closing quotes), unless an abbreviation
    or a lowercase word follows (e.g. '"Wait!" she said')."""
    tokens = normalize_ws(text).split(" ") if text.strip() else []
    sentences: list[str] = []
    current: list[str] = []
    for i, token in enumerate(tokens):
        current.append(token)
        following = tokens[i + 1] if i + 1 < len(tokens) else ""
        if (
            _SENTENCE_END_RE.search(token)
            and token not in _ABBREVIATIONS
            and following
            and not following[0].islower()
        ):
            sentences.append(" ".join(current))
            current = []
    if current:
        sentences.append(" ".join(current))
    return sentences


def slugify(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    return "-".join(word for word in re.split(r"[\s-]+", cleaned) if word)


def find_disallowed(text: str, terms: list[str]) -> list[str]:
    return [
        term for term in terms if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE)
    ]


def load_disallowed_terms(path: Path) -> list[str]:
    if not path.is_file():
        raise ConfigError(f"disallowed-terms list not found: {path} (one term per line)")
    stripped = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in stripped if line and not line.startswith("#")]


def qa_schedule_errors(qa_pages: list[int], page_count: int) -> list[str]:
    low, high = math.ceil(page_count / 3), math.floor(page_count / 2.5)
    errors: list[str] = []
    if not low <= len(qa_pages) <= high:
        article = "an" if page_count in (8, 11, 18) else "a"
        errors.append(
            f"needs {low}-{high} Q&A pages for {article} {page_count}-page book "
            f"(has {len(qa_pages)})"
        )
    if qa_pages and qa_pages[0] > 3:
        errors.append("first Q&A must be on page 3 or earlier")
    if qa_pages and qa_pages[-1] < page_count - 3:
        errors.append(f"last Q&A must be within 3 pages of page {page_count}")
    for before, after in zip(qa_pages, qa_pages[1:], strict=False):
        if after - before not in (2, 3):
            errors.append(f"gap between Q&A pages {before} and {after} must be 2 or 3 pages")
    return errors
