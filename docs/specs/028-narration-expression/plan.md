# Implementation Plan: Story-aware sound tags in the narration

**Spec:** docs/specs/028-narration-expression/spec.md (Approved 2026-11-14)
**Decisions:** none (PRD 028: optional output field inserted by deterministic code)
**Created:** 2026-11-14

## Global Constraints

- Allowed tags are exactly `<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>` (`contract.ALLOWED_TAGS`). Positions are `start` or `end` of a narration sentence; sentences are those of `contract.split_sentences(page text)`, numbered from 1.
- Limits (config, defaults): 1 tag per page; across the book at most `ceil(0.5 × page count)` tags. Enforced only on newly generated banter (like `banter_feeling_errors`), never in `validate_script` of saved or hand-edited scripts (AC-3.1, AC-3.2).
- A tag may not fall inside an open quotation, may not duplicate another entry's sentence and position, and may not land on a position that already carries a tag (AC-2.5, AC-2.6).
- Narration text with tags stripped must still equal the book text; captions never show tag text (AC-1.3, AC-1.4). The book data is never modified (NFR-3).
- Refusals list every problem at once and reuse the existing attempt limit (AC-2.4, FR-5). No tag entry is applied to the script unless valid (assembly must never crash on bad entries).
- No `narration_tags` (or an empty list), and configs without the new `narration_tags` block, behave exactly as today: legacy goldens and the existing brief text stay byte-identical (AC-1.5, FR-6).
- Both providers share one path: `EpisodeTools.check` plus `Banter.model_json_schema()` and `build_brief`.
- Python 3.12, Ruff (line 100), pathlib, pure logic unit-tested without Codex or Gemini; run only tests for changed files (project memory). Commits `type(scope): message` with the Co-Authored-By trailer, staged by explicit path (the working tree has unrelated uncommitted edits: `.claude/launch.json`, `config/covers.json`, `config/voices.json`, `illustrate.py`, `covers.py`, `synthesize_voices.py`, `write_story.py`, test files — never stage them).

## Goal

Codex can return an optional `narration_tags` list; validated, deterministic code inserts each tag at the start or end of the named narration sentence, so newly generated scripts have story-aware, book-specific narration tags within the limits.

---

## Phase 0: Contracts and tests first

**Implements:** FR-1, FR-2, FR-3, FR-5 | **Satisfies:** AC-1.1–1.5, AC-2.1–2.6, AC-3.1, AC-3.2
**Files:** `tests/test_narration_tags.py` (new), additions to `tests/test_emotions.py`
**Interfaces:** Consumes nothing. Produces the failing tests Phases 1–2 satisfy: `contract.NarrationTag`, `Banter.narration_tags`, `episode.narration_tag_errors(banter, book, limits) -> list[str]`, `episode.split_narration(text, decorations=None)`, `emotions.NarrationLimits`, `EmotionSettings.narration`.

- [ ] Write `tests/test_narration_tags.py` (book fixture: two pages with known sentences; helper `banter(tags)` builds a minimal valid `Banter` using the same minimal banter helper as `tests/test_episode.py`):

```python
import pytest

from tests.helpers import MINI_BOOK
from video_pipeline import episode
from video_pipeline.contract import Banter, NarrationTag, strip_tags
from video_pipeline.emotions import NarrationLimits

LIMITS = NarrationLimits(per_page_max=1, per_book_ratio=0.5)
PAGE = {"page_number": 1, "text": 'Pip sat down. "I need help," said Pip. The moon rose.', "prompt": "p"}
BOOK = {"metadata": {"title": "T"}, "pages": [PAGE, {**PAGE, "page_number": 2}, {**PAGE, "page_number": 3}, {**PAGE, "page_number": 4}]}


def tag(page=1, sentence=1, position="end", name="sigh"):
    return {"page_number": page, "sentence": sentence, "position": position, "tag": name}


def errors(*tags, book=BOOK):
    return episode.narration_tag_errors(tags_list=list(tags), book=book, limits=LIMITS)


def test_valid_tags_have_no_errors():
    assert errors(tag(1, 3, "start", "gasp"), tag(3, 1, "end", "yawn")) == []


def test_empty_is_valid():
    assert errors() == []


def test_per_page_limit():
    problems = errors(tag(1, 1), tag(1, 3))
    assert any("page 1" in p and "at most 1" in p for p in problems)


def test_book_average_limit():
    problems = errors(tag(1), tag(2), tag(3))   # 3 > ceil(0.5 * 4) = 2
    assert any("3 narration tags" in p and "at most 2" in p for p in problems)


@pytest.mark.parametrize("bad", [
    tag(name="wink"), tag(page=9), tag(sentence=0), tag(sentence=99), tag(position="middle"),
])
def test_unknown_values_refused_with_entry(bad):
    assert errors(bad)


def test_duplicates_refused():
    assert any("duplicate" in p for p in errors(tag(2, 1, "end", "sigh"), tag(2, 1, "end", "yawn")))


def test_inside_open_quotation_refused():
    quoted = {**PAGE, "text": '"Hello there. How are you today?" said Pip. The end.'}
    book = {**BOOK, "pages": [quoted, *BOOK["pages"][1:]]}
    assert any("quotation" in p for p in errors(tag(1, 2, "start"), book=book))
    assert any("quotation" in p for p in errors(tag(1, 1, "end"), book=book))
    assert errors(tag(1, 3, "start"), book=book) == []   # after the closing quote


def test_all_problems_listed_together():
    assert len(errors(tag(name="wink"), tag(page=9), tag(2, 1), tag(2, 1))) >= 3


def test_decorations_insert_at_sentence_edges_and_strip_to_book_text():
    text = PAGE["text"]
    lines = episode.split_narration(text, {(1, "start"): "<gasp>", (3, "end"): "<yawn>"})
    joined = " ".join(lines)
    assert joined.startswith("<gasp> Pip sat down.")
    assert joined.endswith("The moon rose. <yawn>")
    assert strip_tags(joined) == strip_tags(text)


def test_no_decorations_equal_today():
    assert episode.split_narration(PAGE["text"], None) == episode.split_narration(PAGE["text"])


def test_assemble_applies_only_valid_entries(...):  # see Phase 1; bad entries are skipped, never crash
    ...


def test_banter_parses_optional_list():
    raw = '{"intro": [], "moments": [], "outro": [], "narration_tags": []}'
    assert Banter.model_fields["narration_tags"].default == []
```

(`test_assemble_applies_only_valid_entries` is written in full against `episode.assemble_script` with a banter holding one valid and one out-of-range entry: the script gains exactly the valid tag, and narration with tags stripped equals the book text for every page.)

- [ ] Add to `tests/test_emotions.py`: `load_emotions` with a `narration_tags` block yields `settings.narration == NarrationLimits(1, 0.5)`; without the block `settings.narration is None`; a bad value (`per_page_max: 0`, ratio `2`) is a `ConfigError` naming the field.
- [ ] Run: `uv run pytest tests/test_narration_tags.py tests/test_emotions.py -q` — expect FAIL
- [ ] Commit: `test(script): contract tests for narration tags (spec 028)`

---

## Phase 1: Pure logic — model, limits config, validation, insertion

**Implements:** FR-1, FR-2, FR-3, FR-5 | **Satisfies:** AC-1.1, AC-1.2, AC-1.3, AC-1.5, AC-2.1–2.6, AC-3.1, AC-3.2
**Files:** `video_pipeline/contract.py`, `video_pipeline/emotions.py`, `config/emotions.json`, `video_pipeline/episode.py`, tests from Phase 0
**Interfaces:** Consumes `contract.split_sentences`, `ALLOWED_TAGS`, `strip_tags`. Produces `contract.NarrationTag(page_number, sentence, position, tag)`, `Banter.narration_tags: list[NarrationTag] = []`, `emotions.NarrationLimits(per_page_max, per_book_ratio)`, `EmotionSettings.narration: NarrationLimits | None = None`, `episode.narration_tag_errors(tags_list, book, limits)`, `episode.valid_decorations(banter, book) -> dict[int, dict[tuple[int, str], str]]`, `episode.split_narration(text, decorations=None)`.

- [ ] `contract.py`: add

```python
class NarrationTag(BaseModel):
    """A sound tag the writer wants on a narration sentence (spec 028)."""

    page_number: int
    sentence: int = Field(ge=1)
    position: Literal["start", "end"]
    tag: str
```

and `narration_tags: list[NarrationTag] = Field(default_factory=list)` on `Banter` (after `outro`). `tag` is a plain string here so a bad tag is refused with the entry named (a `Literal` would fail JSON parsing with a less helpful message); the allowed set is checked in `narration_tag_errors`. Update the `Banter` docstring.

- [ ] `emotions.py` and `config/emotions.json`: add

```python
class NarrationLimits(BaseModel):
    """Narration sound-tag limits for newly generated banter (spec 028)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    per_page_max: int = Field(ge=1, le=3)
    per_book_ratio: float = Field(gt=0, le=1)

    def book_max(self, pages: int) -> int:
        return math.ceil(self.per_book_ratio * pages)
```

(import `math`), field `narration: NarrationLimits | None = None` on `EmotionSettings`, `_describe` unchanged (its fallback names `narration.per_page_max`). In `config/emotions.json` add `"narration_tags": {"per_page_max": 1, "per_book_ratio": 0.5}` and map it with `Field(alias="narration_tags")`/`populate_by_name=True` so the JSON key is `narration_tags` (keep `extra="forbid"`).

- [ ] `episode.py`:

```python
def _quote_state_before(sentences: list[str], index: int) -> bool:
    """True when an open quotation is still open at the start of sentence `index` (0-based)."""
    opened = False
    for sentence in sentences[:index]:
        for ch in sentence:
            if ch in '“”"':
                opened = not opened if ch != "”" else False
                if ch == "“":
                    opened = True
    return opened


def _quote_state_after(sentences: list[str], index: int) -> bool:
    return _quote_state_before(sentences, index + 1)


def narration_tag_errors(tags_list: list, book: dict, limits: NarrationLimits) -> list[str]:
    """Every problem with the writer's `narration_tags` against the book (spec 028 FR-3, FR-5)."""
    errors: list[str] = []
    pages = {int(p["page_number"]): split_sentences(p["text"]) for p in book["pages"]}
    seen: set[tuple[int, int, str]] = set()
    per_page: dict[int, int] = {}
    for i, raw in enumerate(tags_list, start=1):
        entry = raw if isinstance(raw, dict) else raw.model_dump()
        where = f"narration tag {i} (page {entry.get('page_number')}, sentence {entry.get('sentence')})"
        number, sentence, position = entry.get("page_number"), entry.get("sentence"), entry.get("position")
        if entry.get("tag") not in ALLOWED_TAGS:
            errors.append(f"{where}: tag {entry.get('tag')!r} is not one of {', '.join(ALLOWED_TAGS)}")
        if position not in ("start", "end"):
            errors.append(f"{where}: position must be start or end")
        sentences = pages.get(number)
        if sentences is None:
            errors.append(f"{where}: page {number} is not a story page of the book")
            continue
        if not isinstance(sentence, int) or not 1 <= sentence <= len(sentences):
            errors.append(f"{where}: sentence must be 1-{len(sentences)} on page {number}")
            continue
        key = (number, sentence, position)
        if key in seen:
            errors.append(f"{where}: duplicate of an earlier entry at the same position")
        seen.add(key)
        if position == "start" and _quote_state_before(sentences, sentence - 1):
            errors.append(f"{where}: the start of this sentence is inside a quotation")
        if position == "end" and _quote_state_after(sentences, sentence - 1):
            errors.append(f"{where}: the end of this sentence is inside a quotation")
        per_page[number] = per_page.get(number, 0) + 1
    errors += [
        f"page {n}: {c} narration tags (at most {limits.per_page_max} per page)"
        for n, c in sorted(per_page.items())
        if c > limits.per_page_max
    ]
    total, cap = len(tags_list), limits.book_max(len(book["pages"]))
    if total > cap:
        errors.append(
            f"{total} narration tags (at most {cap} for {len(book['pages'])} pages: about one "
            "per two pages)"
        )
    return errors
```

(The quote helper must treat straight `"` as a toggle and curly `“` open / `”` close; write it plainly in the file, with the Phase 0 quote tests as its contract — the draft above is the intent, simplify the toggle logic when implementing.)

```python
def valid_decorations(banter: Banter, book: dict) -> dict[int, dict[tuple[int, str], str]]:
    """The tags that pass every check, per page: {page: {(sentence, position): "<tag>"}}.
    Invalid entries are left out; `narration_tag_errors` reports them (never crash here)."""
    limits = NarrationLimits(per_page_max=99, per_book_ratio=1.0)
    out: dict[int, dict[tuple[int, str], str]] = {}
    for entry in banter.narration_tags:
        if narration_tag_errors([entry], book, limits):
            continue
        out.setdefault(entry.page_number, {}).setdefault(
            (entry.sentence, entry.position), f"<{entry.tag}>"
        )
    return out
```

`split_narration(text, decorations: dict[tuple[int, str], str] | None = None)`: decorate each sentence before grouping — `sentence = f"{tag} {sentence}"` for `(i, "start")` and `f"{sentence} {tag}"` for `(i, "end")` — leaving the grouping logic untouched (`word_count` already strips tags, so line splits are the same as without tags). With `decorations` empty or `None` the output is byte-identical to today.
- [ ] `assemble_script`: `deco = valid_decorations(banter, book)`; call `split_narration(page["text"], deco.get(number))`.
- [ ] `episode.banter_feeling_errors` unchanged. Add `narration_tag_errors` call in `EpisodeTools.check` in Phase 2.
- [ ] Run: `uv run pytest tests/test_narration_tags.py tests/test_emotions.py tests/test_episode.py tests/test_contract.py -q` — expect PASS; ruff clean
- [ ] Commit: `feat(script): narration tag model, limits and insertion (spec 028)`

---

## Phase 2: Wire into generation (check, brief, schema)

**Implements:** FR-3, FR-4, FR-5, FR-6 | **Satisfies:** AC-1.6, AC-2.4, AC-3.1, AC-3.2, AC-4.1, AC-4.2
**Files:** `video_pipeline/episode_tools.py`, `video_pipeline/llm.py`, `tests/test_episode_tools.py`, `tests/test_llm.py`, `tests/test_blueprint_consumers.py` (golden unchanged check)
**Interfaces:** Consumes Phase 1. Produces `EpisodeTools.check` running `narration_tag_errors` for new banter only when `emotions.narration` is configured; `llm._narration_block(limits, pages) -> str`; `build_brief` appends it when `feelings.narration` is set.

- [ ] Tests (fail first): `EpisodeTools.check` on banter with a valid tag returns a script whose narration contains it and whose stripped text equals the book; with 2 tags on one page returns the per-page error; the same script passed through `validate_script` directly (a saved or hand-edited script with 3 tags on a page) has no limit error (AC-3.2); with `emotions.narration is None` a `narration_tags` entry is ignored for limits but invalid entries are still skipped by assembly; `build_brief` contains the new NARRATION block (limits, "follow the story", "not the same pattern in every book", the entry shape) when configured and is byte-identical to the legacy golden when `narration` is absent (`tests/fixtures/legacy_golden.json`); `get_episode_plan()["banter_schema"]` lists `narration_tags`; the Gemini prompt (`llm.py` line ~165 embeds `Banter.model_json_schema()`) therefore includes it (assert the prompt text contains `narration_tags`).
- [ ] Run those tests — expect FAIL
- [ ] `episode_tools.py` `check`: after `banter_feeling_errors`, add

```python
        if self.emotions is not None and self.emotions.narration is not None:
            errors += narration_tag_errors(parsed.narration_tags, self.book, self.emotions.narration)
```

(import from `video_pipeline.episode`).
- [ ] `llm.py`:

```python
def _narration_block(limits: NarrationLimits, pages: int) -> str:
    return f"""
NARRATION TAGS
- The Storyteller reads the book text word for word, but you may add a few sound tags to the \
narration so it sounds alive. Put them in the optional "narration_tags" list: each entry is \
{{"page_number": N, "sentence": S, "position": "start" or "end", "tag": "gasp"}}, where S counts \
the sentences of that page's TEXT from 1 and the tag is one of giggle, laugh, chuckle, gasp, \
sigh, yawn (write the name only, no angle brackets).
- Use them only where this story calls for them: a gasp at a surprise, a sigh of relief, a \
yawn where a character is truly sleepy. Do not repeat a pattern you would use in every book, and \
never add a yawn at the end just because it is bedtime.
- At most {limits.per_page_max} per page and at most {limits.book_max(pages)} in this book \
({pages} pages). Never inside a quotation. No tags at all is fine for a page that needs none.
"""
```

and in `build_brief`: `if feelings is not None and feelings.narration is not None: direction += _narration_block(feelings.narration, len(book["pages"]))`. Nothing changes when `narration` is absent.
- [ ] Run: `uv run pytest tests/test_narration_tags.py tests/test_episode_tools.py tests/test_llm.py tests/test_blueprint_consumers.py tests/test_generate_script.py tests/test_mcp_server.py tests/test_episode.py -q` — expect PASS; `uv run ruff check` / `ruff format --check` on touched files
- [ ] Commit: `feat(script): ask for and validate narration tags in generated banter (spec 028)`

---

## Phase 3: Verification, docs and the live check

**Implements:** All FRs | **Satisfies:** All ACs

- [ ] Run changed-area tests: `uv run pytest tests/test_narration_tags.py tests/test_emotions.py tests/test_episode.py tests/test_episode_tools.py tests/test_contract.py tests/test_llm.py tests/test_blueprint_consumers.py tests/test_generate_script.py tests/test_mcp_server.py tests/test_synthesize_voices.py -q` — all PASS (`test_synthesize_voices.py` also carries unrelated uncommitted edits in the working tree; if it fails only because of those, say so); ruff clean
- [ ] Add AC-3.3/3.4 tests if missing: a script with a narration tag added has a different `file_hash.script_sha256` than without; `synthesize_voices.clip_key` differs only for the tagged narration line's text and is identical for untagged lines (use the existing clip-key helpers).
- [ ] Opt-in `tests/test_narration_tags_real.py` (`-m codex`): generate a script for `tests/fixtures/mini_book.json` with real Codex, assert every tag is valid, within limits, narration strips to the book text.
- [ ] Update `CLAUDE.md`: Stage 1 bullet mentions optional `narration_tags` (inserted by `assemble_script`, limits in `config/emotions.json` `narration_tags`, generation only); one sentence in the spec 017 context if present.
- [ ] Live check on the `pip-and-the-sleepy-lantern` book (user asked to re-render): `uv run python -m video_pipeline.generate_script --book pip-and-the-sleepy-lantern --force`, confirm tags in `episode_script.json` narration, review mark and voices (`autopilot.mark_review` and `approve_audio` as before, listening note), `ground_camera` (still current), `render_episode`.
- [ ] Commit: `docs(script): narration tags docs and tests (spec 028)`

---

## Quickstart Validation

```bash
uv run python -m video_pipeline.generate_script --book <slug> --force   # script gains narration_tags within limits
python3 -c "import json;s=json.load(open('books/<slug>/episode/episode_script.json'));print([l['text'] for p in s['pages'] for l in p['lines'] if l['role']=='narration' and '<' in l['text']])"
uv run python -m video_pipeline.synthesize_voices --book <slug> --reviewed   # tagged narration lines are spoken again, others reused
```
