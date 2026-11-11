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
COME_BACK_CUES = ("come back", "next story", "next time")

TAG_RE = re.compile(r"<[^<>]*>")
# Stage directions and markup in generated lines (FR-5): *giggles*, [laughs], (whispering), _softly_
STAGE_DIRECTION_RE = re.compile(r"\*[^*]*\*|\[[^\]]*\]|\([^()]*\)|(?<!\w)_[^_]+_(?!\w)")
_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)]*$")
_ABBREVIATIONS = frozenset({"Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr."})
_CLOSING_QUOTE_RE = re.compile(r"[.!?…][\"'”’]+$")
_MAX_TAG_NAME_WORDS = 3
# Verbs that turn "Name verb" after a closing quotation into a dialogue tag (FR-3). Includes
# every verb the pilot book uses after a quote and a name (called, declared, exclaimed, piped,
# said, beamed, groaned, wondered, ribbited, explained).
SPEECH_VERBS = frozenset(
    {
        "added", "admitted", "announced", "answered", "asked", "beamed", "begged", "buzzed",
        "called", "cheered", "chirped", "chuckled", "cried", "croaked", "declared", "exclaimed",
        "explained", "gasped", "giggled", "groaned", "hooted", "laughed", "murmured", "muttered",
        "piped", "replied", "ribbited", "sang", "said", "shouted", "sighed", "sniffled",
        "squeaked", "whispered", "wondered", "yelled",
    }
)  # fmt: skip

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


def _is_dialogue_tag(words: list[str]) -> bool:
    """True when words start with 1-3 capitalised name words followed by a speech verb."""
    for count in range(1, _MAX_TAG_NAME_WORDS + 1):
        if len(words) <= count or not all(word[:1].isupper() for word in words[:count]):
            return False
        if re.sub(r"\W+$", "", words[count]) in SPEECH_VERBS:
            return True
    return False


def split_sentences(text: str) -> list[str]:
    """Split at whitespace after . ! ? … (plus closing quotes), unless an abbreviation,
    a lowercase word (e.g. '"Wait!" she said') or, after a closing quotation mark, a
    dialogue tag such as '"Wait!" Granny Dusk whispered' follows."""
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
            and not (_CLOSING_QUOTE_RE.search(token) and _is_dialogue_tag(tokens[i + 1 :]))
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


def qa_bounds(page_count: int) -> tuple[int, int]:
    """The fewest and most Q&A pages spec 001 allows for a book of `page_count` pages."""
    return math.ceil(page_count / 3), math.floor(page_count / 2.5)


def qa_schedule_errors(qa_pages: list[int], page_count: int) -> list[str]:
    low, high = qa_bounds(page_count)
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


def validate_script(
    script: dict, book: dict, terms: list[str], allowed_ambience: set[str] | None = None
) -> list[str]:
    """Check a complete episode script against every spec rule; [] means valid.

    `allowed_ambience` (the blueprint's sound palette) limits the ambience names; None allows
    any name (the library check happens at render time)."""
    missing = [key for key in TOP_LEVEL_KEYS if key not in script]
    if missing:
        return [f"missing top-level key: {key}" for key in missing]
    title = book["metadata"]["title"]
    errors: list[str] = []
    if script["book_slug"] != slugify(title):
        errors.append(f"book_slug must be {slugify(title)!r}")
    if script["title"] != title:
        errors.append(f"title must be {title!r}")
    if script["aspect_ratio"] != "16:9":
        errors.append("aspect_ratio must be '16:9'")
    if script["resolution"] != [1920, 1080]:
        errors.append("resolution must be [1920, 1080]")
    errors += _cast_errors(script["cast"])
    errors += _bookend_errors(script["intro"], "intro", terms, title, allowed_ambience)
    errors += _bookend_errors(script["outro"], "outro", terms, title, allowed_ambience)
    errors += _page_errors(script["pages"], book, terms, allowed_ambience)
    return errors


def _cast_errors(cast: object) -> list[str]:
    if not isinstance(cast, dict) or set(cast) != set(SPEAKERS):
        return ["cast must define exactly: storyteller, kid_older, kid_younger"]
    return [
        f"cast.{role}.{field} is required"
        for role in SPEAKERS
        for field in CAST_FIELDS
        if not str(cast[role].get(field, "")).strip()
    ]


def _line_errors(where: str, line: dict, terms: list[str], generated: bool) -> list[str]:
    errors: list[str] = []
    speaker, text = line.get("speaker"), line.get("text", "")
    if speaker not in SPEAKERS:
        errors.append(f"{where}: unknown speaker {speaker!r}")
    if not strip_tags(text):
        errors.append(f"{where}: empty text")
    for tag in TAG_RE.findall(text):
        if tag[1:-1] not in ALLOWED_TAGS:
            errors.append(f"{where}: tag {tag} is not allowed")
    if generated:
        for markup in STAGE_DIRECTION_RE.findall(text):
            errors.append(f"{where}: stage direction or markup {markup!r} is not allowed")
        for term in find_disallowed(text, terms):
            errors.append(f"{where}: disallowed term {term!r}")
        if speaker in KIDS and word_count(text) > MAX_KID_WORDS:
            errors.append(f"{where}: kid line has {word_count(text)} words (max {MAX_KID_WORDS})")
    return errors


def _ambience_errors(where: str, block: dict, allowed: set[str] | None = None) -> list[str]:
    if "ambience" not in block:
        return []
    names = block["ambience"]
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        return [f"{where}: ambience must be a list of sound names"]
    if allowed is None:
        return []
    return [
        f"{where}: ambience {name!r} is not in the blueprint's sound palette"
        for name in names
        if name not in allowed
    ]


# spec 010 FR-9: a page may turn its animation off or scale it; render.json may narrow this range
ANIMATION_OVERRIDE_RANGE = (0.25, 2.0)


def _animation_errors(
    where: str,
    block: dict,
    lo: float = ANIMATION_OVERRIDE_RANGE[0],
    hi: float = ANIMATION_OVERRIDE_RANGE[1],
) -> list[str]:
    """A story page's optional `animation` override: `false` or a number from `lo` to `hi`."""
    if "animation" not in block:
        return []
    value = block["animation"]
    if value is False or (
        isinstance(value, int | float) and not isinstance(value, bool) and lo <= value <= hi
    ):
        return []
    return [f"{where}: animation must be false or a number from {lo} to {hi} (got {value!r})"]


def _bookend_errors(
    block: dict, name: str, terms: list[str], title: str, allowed: set[str] | None = None
) -> list[str]:
    lines = block.get("lines", [])
    errors = _ambience_errors(name, block, allowed)
    if "animation" in block:
        errors.append(f"{name}: animation is allowed only on story pages, not the {name}")
    errors += [
        error
        for i, line in enumerate(lines)
        for error in _line_errors(f"{name} line {i + 1}", line, terms, generated=True)
    ]
    low, high, effect = (2, 4, INTRO_EFFECT) if name == "intro" else (3, 5, OUTRO_EFFECT)
    speakers = [line.get("speaker") for line in lines]
    if not low <= len(lines) <= high:
        errors.append(f"{name} must have {low}-{high} lines (has {len(lines)})")
    if not set(speakers) & set(KIDS):
        errors.append(f"{name} needs at least one kid line")
    if block.get("ffmpeg_effect") != effect:
        errors.append(f"{name}.ffmpeg_effect must be {effect!r}")
    if name == "intro":
        if "storyteller" not in speakers:
            errors.append("intro needs at least one storyteller line")
        if not any(title.lower() in line.get("text", "").lower() for line in lines):
            errors.append(f"intro must mention the title {title!r}")
    else:
        last = lines[-1] if lines else {}
        text = last.get("text", "").lower()
        invites = "subscribe" in text and any(cue in text for cue in COME_BACK_CUES)
        if last.get("speaker") != "storyteller" or not invites:
            errors.append(
                "outro must end with a storyteller line inviting viewers to subscribe "
                "and come back for the next story"
            )
    return errors


def _page_errors(
    pages: list[dict], book: dict, terms: list[str], allowed: set[str] | None = None
) -> list[str]:
    book_pages = book["pages"]
    expected = [page["page_number"] for page in book_pages]
    if [page.get("page_number") for page in pages] != expected:
        return [f"pages must be numbered 1..{len(expected)} in book order"]
    errors: list[str] = []
    qa_pages: list[int] = []
    kinds: list[frozenset[str]] = []
    for book_page, page in zip(book_pages, pages, strict=True):
        number, lines = book_page["page_number"], page.get("lines", [])
        split = 0
        while split < len(lines) and lines[split].get("role") == "narration":
            split += 1
        narration, qa = lines[:split], lines[split:]
        errors += _ambience_errors(f"page {number}", page, allowed)
        errors += _animation_errors(f"page {number}", page)
        errors += _narration_errors(number, narration, book_page["text"], terms)
        if any(line.get("role") == "narration" for line in qa):
            errors.append(f"page {number}: narration lines must come before Q&A lines")
        camera = page.get("ffmpeg_camera", {})
        if camera.get("narration_motion") not in NARRATION_MOTIONS:
            errors.append(f"page {number}: narration_motion must be one of {NARRATION_MOTIONS}")
        if page.get("has_qa"):
            qa_pages.append(number)
            kinds.append(frozenset(line.get("speaker") for line in qa) & frozenset(KIDS))
            errors += _qa_errors(number, qa, page, book_page, terms)
        elif qa or "qa_zoom_target" in camera or "qa_grounding" in page:
            errors.append(f"page {number}: has_qa is false but the page has Q&A content")
    errors += qa_schedule_errors(qa_pages, len(book_pages))
    for kind, label in (
        (frozenset({"kid_older"}), "only Leo (kid_older)"),
        (frozenset({"kid_younger"}), "only Mia (kid_younger)"),
        (frozenset(KIDS), "both kids"),
    ):
        if kind not in kinds:
            errors.append(f"needs at least one Q&A moment with {label}")
    return errors


def _narration_errors(number: int, narration: list[dict], text: str, terms: list[str]) -> list[str]:
    if not narration:
        return [f"page {number}: no narration lines"]
    errors: list[str] = []
    for i, line in enumerate(narration):
        where = f"page {number} narration line {i + 1}"
        errors += _line_errors(where, line, terms, generated=False)
        if line.get("speaker") != "storyteller":
            errors.append(f"{where}: narration must be spoken by the storyteller")
        plain = strip_tags(line.get("text", ""))
        sentences = split_sentences(plain)
        if i < len(narration) - 1 and not ends_sentence(plain):
            errors.append(f"{where}: must end at a sentence end")
        if i > 0:
            prev_plain = strip_tags(narration[i - 1].get("text", ""))
            joined_count = len(split_sentences(prev_plain + " " + plain))
            if joined_count < len(split_sentences(prev_plain)) + len(sentences):
                errors.append(f"{where}: must begin at a sentence start")
        if len(sentences) > MAX_NARRATION_SENTENCES:
            errors.append(f"{where}: has {len(sentences)} sentences (max 2)")
        if len(sentences) > 1 and word_count(plain) > MAX_NARRATION_WORDS:
            errors.append(f"{where}: has {word_count(plain)} words (max 40 unless one sentence)")
    joined = strip_tags(" ".join(line.get("text", "") for line in narration))
    if joined != normalize_ws(text):
        errors.append(f"page {number}: narration does not match the page text word for word")
    return errors


def _qa_errors(
    number: int, qa: list[dict], page: dict, book_page: dict, terms: list[str]
) -> list[str]:
    errors: list[str] = []
    for i, line in enumerate(qa):
        where = f"page {number} Q&A line {i + 1}"
        errors += _line_errors(where, line, terms, generated=True)
        speaker, role = line.get("speaker"), line.get("role")
        if speaker == "storyteller" and role != "answer":
            errors.append(f"{where}: storyteller Q&A lines must have role 'answer'")
        if speaker in KIDS and role not in ("question", "reaction"):
            errors.append(f"{where}: kid lines must have role 'question' or 'reaction'")
    if not 2 <= len(qa) <= 5:
        errors.append(f"page {number}: Q&A moment must have 2-5 lines (has {len(qa)})")
    if not any(line.get("speaker") in KIDS for line in qa):
        errors.append(f"page {number}: Q&A moment needs at least one kid line")
    if not qa or qa[-1].get("speaker") != "storyteller":
        errors.append(f"page {number}: Q&A moment must end with a storyteller answer")
    grounding = normalize_ws(page.get("qa_grounding", "")).lower()
    sources = (normalize_ws(book_page["text"]).lower(), normalize_ws(book_page["prompt"]).lower())
    if not grounding or not any(grounding in source for source in sources):
        errors.append(
            f"page {number}: qa_grounding must be a phrase copied word for word "
            "from the page text or illustration description"
        )
    zoom = page.get("ffmpeg_camera", {}).get("qa_zoom_target")
    valid_zoom = isinstance(zoom, dict) and all(
        isinstance(zoom.get(key), (int, float)) for key in ("x", "y", "zoom")
    )
    if not valid_zoom or not (
        0 <= zoom["x"] <= 1 and 0 <= zoom["y"] <= 1 and 1 <= zoom["zoom"] <= 2
    ):
        errors.append(f"page {number}: qa_zoom_target needs x, y in [0, 1] and zoom in [1, 2]")
    return errors
