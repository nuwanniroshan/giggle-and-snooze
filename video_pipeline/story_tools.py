"""Rules for a book written from an idea (spec 006), plus the tools the writing agent calls.

Pure checks first: each function returns a list of problems, empty when the data is valid.
`StoryTools` wraps them for the agent: a valid submission is saved atomically, an invalid one is
refused with the problems, and only `max_saves` submissions are accepted per run (FR-7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_pipeline import characters as ch
from video_pipeline.blueprint import (
    Blueprint,
    BlueprintConfig,
    Context,
    blueprint_errors,
    similarity_problems,
)
from video_pipeline.contract import (
    find_disallowed,
    load_disallowed_terms,
    normalize_ws,
    split_sentences,
)
from video_pipeline.episode import MAX_PAGES, MIN_PAGES, book_problems
from video_pipeline.episode_tools import write_atomic

AGE_RANGES = ("Ages 3-6", "Ages 4-7", "Ages 5-8")
IDEA_COUNT = (3, 5)
TITLE_LENGTH = (3, 60)
PREMISE_SENTENCES = (1, 2)
LESSON_SENTENCES = (1, 1)
PAGE_TEXT_SENTENCES = (1, 4)
LAYOUTS = (
    "image_top_text_bottom",
    "full_bleed_card_top_left",
    "full_bleed_card_top_right",
    "full_bleed_card_bottom_left",
    "full_bleed_card_bottom_right",
)
MAX_SAVES = 3
SAVE_LIMIT_ERROR = f"save limit reached ({MAX_SAVES} saves); stop"


def load_gentle_terms(path: Path) -> list[str]:
    """The gentle-content list (`config/gentle_terms.txt`), same format as the disallowed terms."""
    return load_disallowed_terms(path)


def _sentence_count(text: object) -> int:
    if not isinstance(text, str) or not text.strip():
        return 0
    return len(split_sentences(normalize_ws(text)))


def _in_range(count: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= count <= bounds[1]


def _term_errors(where: str, text: str, terms: list[str], gentle: list[str]) -> list[str]:
    errors = [
        f"{where}: '{term}' is not allowed in a children's story"
        for term in find_disallowed(text, terms)
    ]
    errors += [
        f"{where}: '{term}' is too frightening for this story"
        for term in find_disallowed(text, gentle)
    ]
    return errors


def idea_errors(idea: object, where: str, terms: list[str], gentle: list[str]) -> list[str]:
    """Problems with one idea or candidate (FR-1, FR-2, FR-5)."""
    if not isinstance(idea, dict):
        return [f"{where}: must be a JSON object"]
    errors: list[str] = []
    title = idea.get("title")
    if not isinstance(title, str) or not _in_range(len(title.strip()), TITLE_LENGTH):
        errors.append(f"{where}: title must be 3 to 60 characters")
    premise = idea.get("premise")
    if not _in_range(_sentence_count(premise), PREMISE_SENTENCES):
        errors.append(f"{where}: premise must be 1 to 2 sentences")
    if idea.get("age_range") not in AGE_RANGES:
        errors.append(f"{where}: age_range must be one of {', '.join(AGE_RANGES)}")
    lesson = idea.get("lesson")
    if not _in_range(_sentence_count(lesson), LESSON_SENTENCES):
        errors.append(f"{where}: lesson must be one sentence")
    spoken = " ".join(str(idea.get(key, "")) for key in ("title", "premise", "lesson"))
    errors += _term_errors(where, spoken, terms, gentle)
    return errors


def ideas_errors(payload: object, terms: list[str], gentle: list[str]) -> list[str]:
    """Problems with a list of ideas (FR-1)."""
    ideas = payload.get("ideas") if isinstance(payload, dict) else None
    if not isinstance(ideas, list) or not _in_range(len(ideas), IDEA_COUNT):
        count = len(ideas) if isinstance(ideas, list) else 0
        return [f"the list must hold 3 to 5 ideas (it holds {count})"]
    errors: list[str] = []
    for number, idea in enumerate(ideas, start=1):
        errors += idea_errors(idea, f"idea {number}", terms, gentle)
    return errors


def candidate_errors(candidate: object, terms: list[str], gentle: list[str]) -> list[str]:
    """Problems with one improved candidate (FR-2)."""
    return idea_errors(candidate, "candidate", terms, gentle)


def character_errors(
    book: dict, terms: list[str], gentle: list[str], required: bool = True
) -> list[str]:
    """Problems with the submitted character sheet (top-level `characters`) and each page's
    `characters_on_page` (spec 018: AC-1.1..1.3). With `required=False` (a draft written before
    spec 018) a book without the sheet is fine; only what is there is checked."""
    if "characters" not in book:
        if not required:
            return []
        return [f"characters: is missing (list 1 to {ch.MAX_CHARACTERS} with name and look)"]
    entries = book["characters"]
    errors = ch.entry_problems(entries, "characters")
    if not isinstance(entries, list):
        return errors
    if not _in_range(len(entries), (1, ch.MAX_CHARACTERS)):
        errors.append(
            f"characters: must hold 1 to {ch.MAX_CHARACTERS} characters (it holds {len(entries)})"
        )
    names: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        where = f"characters[{number}]"
        name, look = entry.get("name"), entry.get("look")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
            if not _in_range(len(name.strip()), ch.NAME_LENGTH):
                errors.append(f"{where}.name: must be 1 to {ch.NAME_LENGTH[1]} characters")
        if isinstance(look, str) and look.strip():
            low, high = ch.LOOK_LENGTH
            if not _in_range(len(look.strip()), ch.LOOK_LENGTH):
                errors.append(f"{where}.look: must be {low} to {high} characters")
            if not _in_range(_sentence_count(look), ch.LOOK_SENTENCES):
                errors.append(f"{where}.look: must be 1 to 2 sentences")
        for key in ("name", "look"):
            errors += _term_errors(f"{where}.{key}", str(entry.get(key, "")), terms, gentle)
    pages = book.get("pages")
    for number, page in enumerate(pages if isinstance(pages, list) else [], start=1):
        if not isinstance(page, dict):
            continue
        if ch.PAGE_FIELD not in page:
            errors.append(
                f"page {number}: {ch.PAGE_FIELD} is missing (the names of the characters shown, "
                "[] for none)"
            )
            continue
        # A non-list is reported by the load check (book_problems); only names are checked here.
        found = ch.page_name_problems(page, number, names)
        errors += [e for e in found if "unknown character" in e]
    return errors


def book_errors(
    book: object,
    max_pages: int,
    terms: list[str],
    gentle: list[str],
    require_characters: bool = True,
) -> list[str]:
    """Problems with a generated book (FR-3, FR-5). `max_pages` is the producer's maximum; the
    book may be shorter but never longer, and never shorter than the supported minimum. The
    character sheet (spec 018) is required unless `require_characters` is False."""
    if not isinstance(book, dict):
        return ["book must be a JSON object"]
    errors = list(book_problems(book, "book"))
    pages = book.get("pages")
    if not isinstance(pages, list):
        return errors
    if len(pages) < MIN_PAGES:
        errors.append(f"book has {len(pages)} pages; it must have at least {MIN_PAGES} pages")
    if len(pages) > max_pages:
        errors.append(f"book has {len(pages)} pages, more than the maximum of {max_pages}")
    if len(pages) > MAX_PAGES:
        errors.append(f"book has {len(pages)} pages; the supported maximum is {MAX_PAGES}")
    for number, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        where = f"page {number}"
        if not isinstance(page.get("title"), str) or not page["title"].strip():
            errors.append(f"{where}: 'title' is missing or empty")
        if page.get("layout") not in LAYOUTS:
            errors.append(f"{where}: layout must be one of {', '.join(LAYOUTS)}")
        if not isinstance(page.get("illustration_file"), str) or not page["illustration_file"]:
            errors.append(f"{where}: 'illustration_file' is missing or empty")
        text = page.get("text")
        if (
            isinstance(text, str)
            and text.strip()
            and not _in_range(_sentence_count(text), PAGE_TEXT_SENTENCES)
        ):
            errors.append(f"{where}: text must be 1 to 4 sentences")
        spoken = " ".join(str(page.get(key, "")) for key in ("title", "text"))
        errors += _term_errors(where, spoken, terms, gentle)
    metadata = book.get("metadata")
    if isinstance(metadata, dict) and metadata.get("age_range") not in AGE_RANGES:
        errors.append(f"metadata.age_range must be one of {', '.join(AGE_RANGES)}")
    errors += character_errors(book, terms, gentle, required=require_characters)
    return errors


def blueprint_check(
    payload: object,
    ctx: Context,
    others: dict[str, Blueprint],
    cfg: BlueprintConfig,
    where: str = "blueprint.json",
) -> list[str]:
    """Schema, content and distinctness problems of a submitted blueprint (FR-1..FR-4)."""
    errors = blueprint_errors(payload, ctx, where)
    if errors:
        return errors
    return similarity_problems(Blueprint.model_validate(payload), others, cfg)


@dataclass
class StoryTools:
    """What the writing agent can call: each submit validates, and a valid one is saved to `out`."""

    terms: list[str]
    gentle: list[str]
    out: Path
    pages: int = 20
    max_saves: int = MAX_SAVES
    saves: int = 0
    blueprint_ctx: Context | None = None
    blueprint_others: dict[str, Blueprint] | None = None
    blueprint_cfg: BlueprintConfig | None = None

    def _submit(self, check, payload: object) -> dict:
        if self.saves >= self.max_saves:
            return {"saved": False, "errors": [SAVE_LIMIT_ERROR]}
        self.saves += 1
        errors = check(payload)
        if errors:
            return {"saved": False, "errors": errors}
        write_atomic(Path(self.out), payload)
        return {"saved": True, "errors": []}

    def submit_ideas(self, payload: object) -> dict:
        return self._submit(lambda p: ideas_errors(p, self.terms, self.gentle), payload)

    def submit_candidate(self, payload: object) -> dict:
        return self._submit(lambda p: candidate_errors(p, self.terms, self.gentle), payload)

    def submit_book(self, payload: object) -> dict:
        return self._submit(lambda p: book_errors(p, self.pages, self.terms, self.gentle), payload)

    def submit_blueprint(self, payload: object) -> dict:
        def check(p: object) -> list[str]:
            return blueprint_check(
                p, self.blueprint_ctx, self.blueprint_others or {}, self.blueprint_cfg
            )

        return self._submit(check, payload)
