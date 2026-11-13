"""The character sheet of a book (spec 018): each recurring character's name and fixed look.

A book written from an idea keeps it in `style_bible.characters` (`[{"name", "look"}]`) and lists
on each page, in `characters_on_page`, the names of the characters that page shows. Books without
a sheet (the pilot, older books) have neither field, and every helper here treats them exactly as
before: no problems, no extra picture text.

Pure helpers, no imports from `episode` or `story_tools`, so both (and later specs) can use them.
"""

from __future__ import annotations

MAX_CHARACTERS = 6
NAME_LENGTH = (1, 40)
LOOK_LENGTH = (20, 300)
LOOK_SENTENCES = (1, 2)
PAGE_FIELD = "characters_on_page"
PAGE_HEADING = "Characters in this picture (keep exactly this look):"
COVER_HEADING = "Characters of this book, if they appear (keep exactly this look):"


def sheet(book: dict) -> list[dict]:
    """style_bible.characters, or [] when the book has none (pilot, older books)."""
    style_bible = book.get("style_bible") if isinstance(book, dict) else None
    entries = style_bible.get("characters") if isinstance(style_bible, dict) else None
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def entry_problems(entries: object, field: str) -> list[str]:
    """Shape problems of a sheet held in `field`: a list of {name, look} non-empty strings with
    unique names. Messages start with the field, e.g. `characters[2].look: ...`."""
    if not isinstance(entries, list):
        return [f"{field}: must be a list of {{name, look}} entries"]
    problems: list[str] = []
    seen: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        where = f"{field}[{number}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: must be an object with name and look")
            continue
        for key in ("name", "look"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}.{key}: is missing or empty")
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            if name.strip() in seen:
                problems.append(f"{where}.name: duplicate name '{name.strip()}'")
            seen.add(name.strip())
    return problems


def page_name_problems(page: dict, number: int, names: set[str]) -> list[str]:
    """Problems of one page's `characters_on_page` when present: a list of known names."""
    if PAGE_FIELD not in page:
        return []
    listed = page[PAGE_FIELD]
    if not isinstance(listed, list) or not all(isinstance(n, str) for n in listed):
        return [f"page {number}: {PAGE_FIELD} must be a list of character names"]
    return [
        f"page {number}: {PAGE_FIELD} names unknown character '{name}'"
        for name in listed
        if name.strip() not in names
    ]


def sheet_problems(book: dict, where: str) -> list[str]:
    """Shape problems of the optional fields (load check, FR-3); [] when they are absent."""
    problems: list[str] = []
    style_bible = book.get("style_bible")
    has_sheet = isinstance(style_bible, dict) and "characters" in style_bible
    if has_sheet:
        problems += entry_problems(style_bible["characters"], "style_bible.characters")
    names = {e["name"].strip() for e in sheet(book) if isinstance(e.get("name"), str)}
    pages = book.get("pages")
    for number, page in enumerate(pages if isinstance(pages, list) else [], start=1):
        if not isinstance(page, dict) or PAGE_FIELD not in page:
            continue
        found = page_name_problems(page, number, names)
        if not has_sheet:  # names can only be checked against a sheet
            found = [p for p in found if "unknown character" not in p]
        problems += found
    return [f"{where}: {p}" for p in problems]


def _block(heading: str, entries: list[dict]) -> str:
    if not entries:
        return ""
    lines = [f"- {e['name'].strip()}: {e['look'].strip()}" for e in entries]
    return "\n".join([heading, *lines])


def _usable(book: dict) -> list[dict]:
    return [
        e for e in sheet(book) if isinstance(e.get("name"), str) and isinstance(e.get("look"), str)
    ]


def page_characters_text(book: dict, page: dict) -> str:
    """'Characters in this picture (keep exactly this look):\\n- Name: look' for the page's
    characters_on_page in sheet order; '' when the book has no sheet or the page lists none."""
    listed = page.get(PAGE_FIELD)
    if not isinstance(listed, list):
        return ""
    wanted = {n.strip() for n in listed if isinstance(n, str)}
    return _block(PAGE_HEADING, [e for e in _usable(book) if e["name"].strip() in wanted])


def cover_characters_text(book: dict) -> str:
    """The same block for every character in the sheet; '' without a sheet."""
    return _block(COVER_HEADING, _usable(book))
