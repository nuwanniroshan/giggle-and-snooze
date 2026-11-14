"""Deterministic episode planning and assembly: everything except the model's banter."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from video_pipeline import characters
from video_pipeline.contract import (
    ALLOWED_TAGS,
    INTRO_EFFECT,
    KIDS,
    MAX_NARRATION_SENTENCES,
    MAX_NARRATION_WORDS,
    NARRATION_MOTIONS,
    OUTRO_EFFECT,
    TAG_RE,
    Banter,
    BanterLine,
    ConfigError,
    qa_bounds,
    qa_schedule_errors,
    slugify,
    split_sentences,
    strip_tags,
    word_count,
)
from video_pipeline.timeline import parse_grade

if TYPE_CHECKING:
    from video_pipeline.blueprint import Ambience, Blueprint
    from video_pipeline.emotions import Generation, NarrationLimits

MIN_PAGES = 5
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
    notes: tuple[str, ...] = ()


def book_problems(book: object, where: str) -> list[str]:
    """Every way `book` breaks the input rules (FR-1), each message prefixed with `where`."""
    if not isinstance(book, dict):
        return [f"{where}: book data must be a JSON object"]
    problems: list[str] = []
    metadata = book.get("metadata")
    title = str(metadata.get("title", "") if isinstance(metadata, dict) else "").strip()
    if not title:
        problems.append(f"{where}: metadata.title is missing")
    elif not slugify(title):
        problems.append(f"{where}: title has no letters or digits to build a slug from")
    pages = book.get("pages")
    if not isinstance(pages, list) or not MIN_PAGES <= len(pages) <= MAX_PAGES:
        count = len(pages) if isinstance(pages, list) else 0
        problems.append(
            f"{where}: book has {count} pages; supported range is {MIN_PAGES}-{MAX_PAGES}"
        )
    if isinstance(metadata, dict) and metadata.get("cover_style", "titled") != "titled":
        problems.append(f"{where}: metadata.cover_style must be 'titled'")
    if "grade" in book:
        try:
            parse_grade(book["grade"], f"{where}: grade")
        except ConfigError as exc:
            problems.append(str(exc))
    if not isinstance(pages, list):
        return problems
    for expected, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            problems.append(f"{where}: page {expected}: must be a JSON object")
            continue
        if page.get("page_number") != expected:
            problems.append(
                f"{where}: page {expected}: page_number must be {expected} "
                "(pages must be numbered 1..N in order)"
            )
        for field in ("text", "prompt"):
            value = page.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}: page {expected}: '{field}' is missing or empty")
    problems += characters.sheet_problems(book, where)
    return problems


def load_book(path: Path) -> dict:
    try:
        book = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BookError(f"book data file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise BookError(f"book data file is not valid JSON: {path} ({exc})") from None
    problems = book_problems(book, str(path))
    if problems:
        raise BookError(problems[0])
    return book


def _decorate(sentence: str, number: int, decorations: dict[tuple[int, str], str]) -> str:
    """`sentence` (numbered from 1) with its spec 028 start/end tags added, if any."""
    start, end = decorations.get((number, "start")), decorations.get((number, "end"))
    if start:
        sentence = f"{start} {sentence}"
    if end:
        sentence = f"{sentence} {end}"
    return sentence


def split_narration(text: str, decorations: dict[tuple[int, str], str] | None = None) -> list[str]:
    """The page text as narration lines; `decorations` {(sentence, position): "<tag>"} adds
    sound tags at sentence edges first (spec 028). Grouping counts words without tags, so the
    line breaks are those of the plain text."""
    lines: list[str] = []
    current: list[str] = []
    sentences = split_sentences(text)
    if decorations:
        sentences = [_decorate(s, i, decorations) for i, s in enumerate(sentences, start=1)]
    for sentence in sentences:
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


def choose_qa_pages(
    page_count: int, rng: random.Random, window: tuple[int, int] | None = None
) -> list[int]:
    """A valid Q&A schedule; `window` (fewest, most) narrows how many pages it may have."""
    for _ in range(10_000):
        pages = [rng.randint(1, 3)]
        while (following := pages[-1] + rng.choice((2, 3))) <= page_count:
            pages.append(following)
        if qa_schedule_errors(pages, page_count):
            continue
        if window is None or window[0] <= len(pages) <= window[1]:
            return pages
    raise ValueError(f"no valid Q&A schedule found for a {page_count}-page book")


def assign_speakers(count: int, rng: random.Random) -> list[tuple[str, ...]]:
    picks = [*SPEAKER_SETS, *(rng.choice(SPEAKER_SETS) for _ in range(count - 3))]
    rng.shuffle(picks)
    return picks[:count]  # a short book has fewer Q&A pages than speaker sets


def plan_episode(book: dict, seed: int, blueprint: Blueprint | None = None) -> EpisodePlan:
    rng = random.Random(seed)
    page_count = len(book["pages"])
    window: tuple[int, int] | None = None
    notes: tuple[str, ...] = ()
    if blueprint is not None:
        from video_pipeline.blueprint import clamp_range

        wanted = (blueprint.qa.moments_min, blueprint.qa.moments_max)
        window = clamp_range(*wanted, *qa_bounds(page_count))
        if window != wanted:
            notes = (
                f"blueprint moment range {wanted[0]}-{wanted[1]} clamped to "
                f"{window[0]}-{window[1]} for {page_count} pages",
            )
    qa_pages = choose_qa_pages(page_count, rng, window)
    speakers = assign_speakers(len(qa_pages), rng)
    if blueprint is None:
        motions = tuple(rng.choice(NARRATION_MOTIONS) for _ in range(page_count))
    else:  # every page moves, and never the same move twice in a row
        picks: list[str] = []
        for _ in range(page_count):
            options = [m for m in NARRATION_MOTIONS if m != "static" and m not in picks[-1:]]
            picks.append(rng.choice(options))
        motions = tuple(picks)
    return EpisodePlan(dict(zip(qa_pages, speakers, strict=True)), motions, notes)


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
    return _with_emotion({"speaker": line.speaker, "role": role, "text": line.text}, line)


def _bookend_line(line: BanterLine) -> dict:
    return _with_emotion({"speaker": line.speaker, "text": line.text}, line)


def _with_emotion(entry: dict, line: BanterLine) -> dict:
    """Copy the line's feeling only when set, so scripts without feelings stay unchanged."""
    if line.emotion is not None:
        entry["emotion"] = line.emotion
    return entry


def assemble_script(
    book: dict,
    cast: dict,
    plan: EpisodePlan,
    banter: Banter,
    ambience: Ambience | None = None,
) -> dict:
    title = book["metadata"]["title"]
    moments = {moment.page_number: moment for moment in banter.moments}
    decorations = valid_decorations(banter, book)
    pages = []
    for page in book["pages"]:
        number = page["page_number"]
        lines = [
            {"speaker": "storyteller", "role": "narration", "text": text}
            for text in split_narration(page["text"], decorations.get(number))
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
        if ambience is not None:
            entry["ambience"] = list(ambience.pages[number - 1])
        pages.append(entry)
    script = {
        "book_slug": slugify(title),
        "title": title,
        "aspect_ratio": "16:9",
        "resolution": [1920, 1080],
        "cast": cast,
        "intro": {"ffmpeg_effect": INTRO_EFFECT, "lines": [_bookend_line(x) for x in banter.intro]},
        "pages": pages,
        "outro": {"ffmpeg_effect": OUTRO_EFFECT, "lines": [_bookend_line(x) for x in banter.outro]},
    }
    if ambience is not None:
        script["intro"]["ambience"] = list(ambience.intro)
        script["outro"]["ambience"] = list(ambience.outro)
    return script


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


def _sound_tags(text: str) -> list[str]:
    return [tag for tag in TAG_RE.findall(text) if tag[1:-1] in ALLOWED_TAGS]


def banter_feeling_errors(banter: Banter, plan: EpisodePlan, gen: Generation) -> list[str]:
    """Spec 017 generation rules: sound-tag count and spread, and feelings per moment.

    Only newly generated banter is held to these (FR-2); saved scripts are never re-checked."""
    named = [(f"intro line {i}", line) for i, line in enumerate(banter.intro, start=1)]
    for moment in banter.moments:
        named += [
            (f"page {moment.page_number} Q&A line {i}", line)
            for i, line in enumerate(moment.lines, start=1)
        ]
    named += [(f"outro line {i}", line) for i, line in enumerate(banter.outro, start=1)]
    moments = len(plan.qa_speakers)
    low, high = moments * gen.tags_per_moment_min, moments * gen.tags_per_moment_max
    count = sum(len(_sound_tags(line.text)) for _, line in named)
    errors: list[str] = []
    if count < low:
        errors.append(
            f"only {count} sound tags (need at least {low} for {moments} Q&A moments); add "
            "<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn> where they fit, spread across "
            "the episode"
        )
    if count > high:
        errors.append(f"{count} sound tags (at most {high} for {moments} Q&A moments)")
    errors += [
        f"{where}: more than one sound tag in a line"
        for where, line in named
        if len(_sound_tags(line.text)) > 1
    ]
    need = gen.feelings_per_moment_min
    for moment in banter.moments:
        felt = sum((line.emotion or "neutral") != "neutral" for line in moment.lines)
        if felt < need:
            errors.append(
                f"page {moment.page_number}: give at least {need} line(s) a feeling other than "
                "neutral that fits the moment"
            )
    return errors


# --- spec 028: narration sound tags --------------------------------------------------------------

POSITIONS = ("start", "end")


def _quote_open_after(sentences: list[str], count: int) -> bool:
    """True when a quotation is still open after the first `count` sentences: a straight `"`
    toggles, a curly `“` opens and `”` closes."""
    opened = False
    for ch in " ".join(sentences[:count]):
        if ch == '"':
            opened = not opened
        elif ch == "“":
            opened = True
        elif ch == "”":
            opened = False
    return opened


def _has_edge_tag(sentence: str, position: str) -> bool:
    """True when the sentence already begins (`start`) or ends (`end`) with a tag."""
    words = sentence.split(" ")
    return bool(TAG_RE.fullmatch(words[0] if position == "start" else words[-1]))


def _entry(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    dump = getattr(raw, "model_dump", None)
    return dump() if callable(dump) else {}


def _tag_entry_problems(entry: dict, pages: dict[int, list[str]]) -> list[str]:
    """Why one narration-tag entry cannot be placed (limits and duplicates aside)."""
    problems: list[str] = []
    number, sentence = entry.get("page_number"), entry.get("sentence")
    position, name = entry.get("position"), entry.get("tag")
    if name not in ALLOWED_TAGS:
        problems.append(f"tag {name!r} is not one of {', '.join(ALLOWED_TAGS)}")
    if position not in POSITIONS:
        problems.append(f"position {position!r} must be start or end")
    is_int = isinstance(number, int) and not isinstance(number, bool)
    sentences = pages.get(number) if is_int else None
    if sentences is None:
        return [*problems, f"page {number!r} is not a story page of the book"]
    if isinstance(sentence, bool) or not isinstance(sentence, int):
        return [*problems, f"sentence must be a number from 1 to {len(sentences)}"]
    if not 1 <= sentence <= len(sentences):
        return [*problems, f"sentence must be 1-{len(sentences)} on page {number}"]
    if position not in POSITIONS:
        return problems
    text = sentences[sentence - 1]
    if _quote_open_after(sentences, sentence - 1 if position == "start" else sentence):
        problems.append(f"the {position} of this sentence is inside a quotation")
    if _has_edge_tag(text, position):
        problems.append(f"the {position} of this sentence already carries a tag")
    return problems


def _book_sentences(book: dict) -> dict[int, list[str]]:
    return {int(page["page_number"]): split_sentences(page["text"]) for page in book["pages"]}


def narration_tag_errors(tags_list: list, book: dict, limits: NarrationLimits) -> list[str]:
    """Every problem with the writer's `narration_tags` against the book and the generation
    limits (spec 028 FR-3, FR-5); [] means every entry can be placed. Entries are dicts or
    `NarrationTag`s. Only newly generated banter is held to this, never saved scripts."""
    pages = _book_sentences(book)
    errors: list[str] = []
    seen: set[tuple[int, int, str]] = set()
    per_page: dict[int, int] = {}
    for i, raw in enumerate(tags_list, start=1):
        entry = _entry(raw)
        number, sentence = entry.get("page_number"), entry.get("sentence")
        where = f"narration tag {i} (page {number}, sentence {sentence})"
        problems = _tag_entry_problems(entry, pages)
        errors += [f"{where}: {problem}" for problem in problems]
        if isinstance(number, int) and not isinstance(number, bool) and number in pages:
            per_page[number] = per_page.get(number, 0) + 1
        key = (number, sentence, entry.get("position"))
        if not problems and key in seen:
            errors.append(f"{where}: duplicate of an earlier entry at the same position")
        if not problems:
            seen.add(key)
    errors += [
        f"page {number}: {count} narration tags (at most {limits.per_page_max} per page)"
        for number, count in sorted(per_page.items())
        if count > limits.per_page_max
    ]
    total, cap = len(tags_list), limits.book_max(len(pages))
    if total > cap:
        errors.append(
            f"{total} narration tags (at most {cap} for {len(pages)} pages: about one per "
            "two pages)"
        )
    return errors


def valid_decorations(banter: Banter, book: dict) -> dict[int, dict[tuple[int, str], str]]:
    """The narration tags that can be placed, per page: {page: {(sentence, position): "<tag>"}}.

    Entries that fail a check are left out (`narration_tag_errors` reports them), so assembly
    never fails on a bad entry; the first of two entries at one position wins."""
    pages = _book_sentences(book)
    out: dict[int, dict[tuple[int, str], str]] = {}
    for tag in banter.narration_tags:
        if _tag_entry_problems(tag.model_dump(), pages):
            continue
        out.setdefault(tag.page_number, {}).setdefault((tag.sentence, tag.position), f"<{tag.tag}>")
    return out
