"""Deterministic episode planning and assembly: everything except the model's banter."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from video_pipeline.contract import (
    INTRO_EFFECT,
    KIDS,
    MAX_NARRATION_SENTENCES,
    MAX_NARRATION_WORDS,
    NARRATION_MOTIONS,
    OUTRO_EFFECT,
    Banter,
    BanterLine,
    qa_schedule_errors,
    slugify,
    split_sentences,
    strip_tags,
    word_count,
)

MIN_PAGES = 8
MAX_PAGES = 32
DEFAULT_ZOOM = {"x": 0.5, "y": 0.5, "zoom": 1.3}
X_CUES = {"left": 0.3, "right": 0.7}
Y_CUES = {
    "top": 0.3,
    "above": 0.3,
    "sky": 0.3,
    "background": 0.4,
    "foreground": 0.65,
    "bottom": 0.7,
    "below": 0.7,
}
SPEAKER_SETS = (("kid_older",), ("kid_younger",), ("kid_older", "kid_younger"))


class BookError(Exception):
    """The book data file is missing or does not meet the input rules (FR-1)."""


@dataclass(frozen=True)
class EpisodePlan:
    qa_speakers: dict[int, tuple[str, ...]]
    motions: tuple[str, ...]


def load_book(path: Path) -> dict:
    try:
        book = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BookError(f"book data file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise BookError(f"book data file is not valid JSON: {path} ({exc})") from None
    title = str(book.get("metadata", {}).get("title", "")).strip()
    if not title:
        raise BookError(f"{path}: metadata.title is missing")
    if not slugify(title):
        raise BookError(f"{path}: title has no letters or digits to build a slug from")
    pages = book.get("pages")
    if not isinstance(pages, list) or not MIN_PAGES <= len(pages) <= MAX_PAGES:
        count = len(pages) if isinstance(pages, list) else 0
        raise BookError(
            f"{path}: book has {count} pages; supported range is {MIN_PAGES}-{MAX_PAGES}"
        )
    for expected, page in enumerate(pages, start=1):
        if page.get("page_number") != expected:
            raise BookError(
                f"{path}: page {expected}: page_number must be {expected} "
                "(pages must be numbered 1..N in order)"
            )
        for field in ("text", "prompt"):
            value = page.get(field)
            if not isinstance(value, str) or not value.strip():
                raise BookError(f"{path}: page {expected}: '{field}' is missing or empty")
    return book


def split_narration(text: str) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for sentence in split_sentences(text):
        candidate = [*current, sentence]
        too_big = (
            len(candidate) > MAX_NARRATION_SENTENCES
            or word_count(" ".join(candidate)) > MAX_NARRATION_WORDS
        )
        if current and too_big:
            lines.append(" ".join(current))
            current = [sentence]
        else:
            current = candidate
    if current:
        lines.append(" ".join(current))
    return lines


def choose_qa_pages(page_count: int, rng: random.Random) -> list[int]:
    for _ in range(10_000):
        pages = [rng.randint(1, 3)]
        while (following := pages[-1] + rng.choice((2, 3))) <= page_count:
            pages.append(following)
        if not qa_schedule_errors(pages, page_count):
            return pages
    raise ValueError(f"no valid Q&A schedule found for a {page_count}-page book")


def assign_speakers(count: int, rng: random.Random) -> list[tuple[str, ...]]:
    picks = [*SPEAKER_SETS, *(rng.choice(SPEAKER_SETS) for _ in range(count - 3))]
    rng.shuffle(picks)
    return picks


def plan_episode(book: dict, seed: int) -> EpisodePlan:
    rng = random.Random(seed)
    page_count = len(book["pages"])
    qa_pages = choose_qa_pages(page_count, rng)
    speakers = assign_speakers(len(qa_pages), rng)
    motions = tuple(rng.choice(NARRATION_MOTIONS) for _ in range(page_count))
    return EpisodePlan(dict(zip(qa_pages, speakers, strict=True)), motions)


def zoom_target(prompt: str, grounding: str) -> dict:
    """Best-effort zoom on the grounding detail from position words in its clause (FR-6)."""
    needle = grounding.lower().strip()
    clauses = [c for c in re.split(r"[,;.]", prompt.lower()) if needle and needle in c]
    if not clauses:
        return dict(DEFAULT_ZOOM)
    words = re.findall(r"[a-z]+", clauses[0])
    x = next((X_CUES[w] for w in words if w in X_CUES), None)
    y = next((Y_CUES[w] for w in words if w in Y_CUES), None)
    if x is None and y is None:
        return dict(DEFAULT_ZOOM)
    return {"x": x if x is not None else 0.5, "y": y if y is not None else 0.5, "zoom": 1.3}


def _qa_line(line: BanterLine) -> dict:
    if line.speaker == "storyteller":
        role = "answer"
    else:
        role = "question" if strip_tags(line.text).endswith("?") else "reaction"
    return {"speaker": line.speaker, "role": role, "text": line.text}


def _bookend_line(line: BanterLine) -> dict:
    return {"speaker": line.speaker, "text": line.text}


def assemble_script(book: dict, cast: dict, plan: EpisodePlan, banter: Banter) -> dict:
    title = book["metadata"]["title"]
    moments = {moment.page_number: moment for moment in banter.moments}
    pages = []
    for page in book["pages"]:
        number = page["page_number"]
        lines = [
            {"speaker": "storyteller", "role": "narration", "text": text}
            for text in split_narration(page["text"])
        ]
        entry = {
            "page_number": number,
            "has_qa": number in plan.qa_speakers,
            "ffmpeg_camera": {"narration_motion": plan.motions[number - 1]},
            "lines": lines,
        }
        if entry["has_qa"]:
            moment = moments.get(number)
            grounding = moment.grounding if moment else ""
            lines.extend(_qa_line(line) for line in (moment.lines if moment else []))
            entry["qa_grounding"] = grounding
            entry["ffmpeg_camera"]["qa_zoom_target"] = zoom_target(page["prompt"], grounding)
        pages.append(entry)
    return {
        "book_slug": slugify(title),
        "title": title,
        "aspect_ratio": "16:9",
        "resolution": [1920, 1080],
        "cast": cast,
        "intro": {"ffmpeg_effect": INTRO_EFFECT, "lines": [_bookend_line(x) for x in banter.intro]},
        "pages": pages,
        "outro": {"ffmpeg_effect": OUTRO_EFFECT, "lines": [_bookend_line(x) for x in banter.outro]},
    }


def banter_plan_errors(banter: Banter, plan: EpisodePlan) -> list[str]:
    errors: list[str] = []
    moments = {moment.page_number: moment for moment in banter.moments}
    for number, kids in sorted(plan.qa_speakers.items()):
        moment = moments.get(number)
        if moment is None:
            errors.append(f"missing Q&A moment for page {number}")
            continue
        spoken = sorted({line.speaker for line in moment.lines if line.speaker in KIDS})
        if spoken != sorted(kids):
            errors.append(
                f"page {number}: kids speaking must be exactly {sorted(kids)} (got {spoken})"
            )
    for number in sorted(moments.keys() - plan.qa_speakers.keys()):
        errors.append(f"unexpected Q&A moment for page {number}")
    return errors
