# Implementation Plan: Episode Script Generator

**Spec:** docs/specs/001-episode-script-generator/spec.md (v2.0.0, Approved; phases 4–6 need re-planning, see Plan Changelog)
**Decisions:** none (no ADRs; tech choices traced inline below)
**Created:** 2026-11-08

> Data model: [data-model.md](data-model.md). Branch `feat/001-episode-script-generator` and the doc-first commit are created by `sdd-execute`.

## Context

Spec 001 asks for one command that turns any `book_data.json` into a validated `episodes/<slug>/episode_script.json`. The script has verbatim narration, varied kid Q&A every 2–3 pages, intro and outro dialogue, and camera hints. It runs locally by default, can use the cloud model per run, gives the same output for the same seed, and never overwrites a script without `--force`. There is no video-pipeline code yet; `scripts/` only builds books. In the venv, Ollama 0.40.6 is running with no models pulled, and pydantic and pytest are not installed.

**Key design choice:** everything the spec makes testable is produced by deterministic code, not by the model: verbatim narration and its line splits, which pages get a Q&A moment, which kids speak in each, camera motions and zoom targets. The text model writes only the free text (kid lines, Storyteller answers, grounding phrases, intro and outro) in a single JSON request. A validator checks the assembled script against every spec rule. If validation fails, it retries with the errors fed back, up to 3 attempts. Doing it this way satisfies AC-2.x, AC-3.1 to AC-3.3, AC-5.x and AC-6.3 by construction, and the model can't break them.

## Global Constraints

- Narration is the page text verbatim: joined narration lines, with tags stripped and whitespace collapsed, equal the page text with whitespace collapsed, character for character (AC-2.2).
- Narration lines split only at sentence ends: at most 2 sentences and 40 words per line, and a single sentence over 40 words stays whole (FR-3).
- Q&A pages: count between ⌈N/3⌉ and ⌊N/2.5⌋, first ≤ page 3, last ≥ N−3, gaps of 2 or 3 (AC-3.1, AC-3.2).
- Each moment has 2–5 lines with at least one kid line and ends with a Storyteller `answer`. Kid lines are at most 25 words. At least one Leo-only, one Mia-only and one both-kids moment (AC-3.3 to AC-3.6).
- Allowed tags: `<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>` only (FR-5).
- Narration motions: `slow_push_in`, `slow_pull_out`, `pan_left`, `pan_right`, `static`. Zoom target `x`, `y` ∈ [0, 1] and `zoom` ∈ [1, 2], on Q&A pages only. Default `0.5 / 0.5 / 1.3`. Intro effect `zoom_in_center`, outro effect `zoom_out_warm_fade` (FR-6).
- Supported books: 8–32 pages, numbered 1..N. Output goes to `episodes/<slug>/episode_script.json`. The slug is the title lowercased, punctuation removed, words joined with hyphens (FR-1).
- Local mode is the default and makes no cloud calls. Cloud is opt-in, and neither mode falls back to the other. Each attempt sends one request, with at most 3 attempts (FR-7).
- Same inputs, settings and seed in local mode give a byte-identical file (AC-6.3). Writes are atomic.
- Cast is fixed in `config/cast.json`: storyteller, kid_older (Leo, age 8), kid_younger (Mia, age 5). Disallowed terms live in `config/disallowed_terms.txt`. A missing terms file is an error; an empty one is a warning.
- Local generation of a 20-page book takes at most 3 minutes on an M1 Pro with 16 GB, and the model is released after the run.
- Python ≥ 3.12, `uv`, Ruff (py312, line length 100). New runtime dependency: `pydantic` only, for one canonical banter model that also produces the JSON schema sent to the model. Dev dependencies: `pytest`, `ruff`. HTTP uses the standard library `urllib`, so there is no SDK dependency.

## Goal

A tested `uv run python -m video_pipeline.generate_script` command that writes a valid, kid-safe, reproducible `episode_script.json` for any 8–32-page book, using the local Ollama model by default.

**Pre-implementation gates**
- **Simplicity:** there are three components: the deterministic core (`contract.py` + `episode.py`), the model backends (`llm.py`) and the CLI (`generate_script.py`). The core is two files because validation rules and episode assembly change for different reasons. The only new runtime dependency is pydantic. ✓
- **Anti-abstraction:** there is one canonical model, `Banter` (pydantic), and the episode script itself is a plain dict validated by `validate_script`. There is no backend class hierarchy; the backends are two functions. ✓
- **Integration-first:** the CLI behaviour tests come in Phase 0, before any implementation. ✓

---

## Phase 0: Project setup and contract tests first

**Implements:** FR-1, FR-7 (CLI surface) | **Satisfies:** sets up AC-1.1–1.4, AC-6.1–6.5, AC-7.2–7.4 (they fail until Phase 5)
**Files:** `pyproject.toml`, `uv.lock`, `config/cast.json`, `config/disallowed_terms.txt`, `video_pipeline/__init__.py`, `tests/__init__.py`, `tests/helpers.py`, `tests/fixtures/mini_book.json`, `tests/test_generate_script.py`
**Interfaces:** Consumes nothing. Produces the project config, the fixtures, the `tests.helpers.make_banter(book, plan) -> Banter` helper, and failing CLI contract tests that fix the CLI's exact flags: `--book`, `--mode {local,cloud}`, `--seed`, `--force`, `--episodes-dir`, `--config-dir`, `--model`.

This phase sets up the uv project and writes the end-to-end behaviour tests for `main()`, with the model stubbed out.

- [ ] Create `pyproject.toml`:
```toml
[project]
name = "kiddo"
version = "0.1.0"
description = "Storybook-to-YouTube read-aloud video pipeline"
requires-python = ">=3.12"
dependencies = [
    "pillow>=10",
    "reportlab>=4",
    "pydantic>=2.7",
]

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6"]

[tool.uv]
package = false

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
markers = ["ollama: needs a running local Ollama with the configured model"]
addopts = "-m 'not ollama'"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "PTH"]
ignore = ["E501"]  # the formatter wraps code; long test messages and comments stay readable
```
- [ ] Run `uv lock && uv sync --inexact`. `--inexact` keeps the ad-hoc packages already in `.venv`, such as pymupdf. Expect pydantic, pytest and ruff to be installed.
- [ ] Create `config/cast.json`:
```json
{
  "storyteller": {
    "name": "Storyteller",
    "engine": "orpheus",
    "voice": "tara",
    "description": "Warm, soothing adult storyteller who reads the book and answers the kids' questions"
  },
  "kid_older": {
    "name": "Leo (Age 8)",
    "engine": "orpheus",
    "voice": "leo",
    "description": "Curious 8-year-old who asks about the plot and loves guessing mysteries"
  },
  "kid_younger": {
    "name": "Mia (Age 5)",
    "engine": "orpheus",
    "voice": "mia",
    "description": "Playful 5-year-old who notices funny and cute visual details, giggles and gasps"
  }
}
```
- [ ] Create `config/disallowed_terms.txt`:
```text
# One term per line, matched case-insensitively as a whole word or phrase in generated lines.
# Lines starting with # are comments. Narration (the book's own text) is not checked.
kill
killed
blood
bloody
gun
weapon
stupid
idiot
dumb
shut up
hate
ugly
damn
hell
```
- [ ] Create empty `video_pipeline/__init__.py` and `tests/__init__.py`.
- [ ] Create `tests/fixtures/mini_book.json`. This is an 8-page book with an abbreviation, quoted speech, paragraph breaks, a sentence over 40 words (page 3) and position words in the illustration descriptions:
```json
{
  "metadata": {"title": "The Lantern Bug Parade", "author": "Test Author", "age_range": "Ages 3-6"},
  "style_bible": {},
  "pages": [
    {"page_number": 1, "text": "At the edge of Glowmoss Meadow lived Tilly, a tiny firefly with a lantern that flickered instead of glowed. Every evening she practiced shining, but her light only blinked like a sleepy star.", "prompt": "Tilly the firefly sitting on the left of a mossy log at dusk, her lantern tail flickering, with soft purple sky in the background."},
    {"page_number": 2, "text": "\"Tonight is the Lantern Bug Parade!\" buzzed Mr. Pebble the beetle. \"Every bug who glows gets to march.\"\n\nTilly looked at her flickering lantern and sighed.", "prompt": "Mr. Pebble the beetle in the foreground holding a parade banner, while Tilly hovers on the right looking at her dim lantern."},
    {"page_number": 3, "text": "She flew past the snoring snails, over the dandelion clocks, under the tall bending grasses, around the whispering puddle where the frogs were humming their evening song, and all the way to the old hollow log where the wise moth Granny Dusk kept her books of light. Granny Dusk smiled.", "prompt": "Tilly flying past snoring snails and dandelion clocks, with an old hollow log at the bottom of the scene."},
    {"page_number": 4, "text": "\"A flicker is not a broken light,\" said Granny Dusk. \"It is a light that dances.\" Tilly blinked her lantern on and off, on and off. It did look a little like dancing!", "prompt": "Granny Dusk the moth wearing tiny spectacles beside a stack of glowing books, Tilly blinking happily."},
    {"page_number": 5, "text": "On the way back, Tilly heard a tiny cry. A baby ladybug was lost in the tall grass. \"I can't find my way home!\" she sniffled.", "prompt": "A baby ladybug crying in tall grass on the right, Tilly hovering above with a worried face."},
    {"page_number": 6, "text": "Tilly flickered her lantern high above the grass. On, off, on, off! The baby ladybug followed the dancing light all the way to her family's leaf.", "prompt": "Tilly's lantern dancing high above the grass at the top of the page, the baby ladybug following below."},
    {"page_number": 7, "text": "When the parade began, the other bugs glowed steady and bright. But Tilly danced her light in loops and swirls. The crowd of bugs cheered and cheered.", "prompt": "A parade of glowing bugs marching across a meadow, Tilly swirling loops of light in the sky."},
    {"page_number": 8, "text": "From that night on, Tilly never wished for a different light. Her flicker was the friendliest light in all of Glowmoss Meadow. The end.", "prompt": "Tilly resting on a dandelion at night, her lantern making a heart-shaped glow in the foreground."}
  ]
}
```
- [ ] Create `tests/helpers.py`:
```python
"""Shared test helpers: fixture paths and a valid stub of the model's banter."""

import json
from pathlib import Path

from video_pipeline.contract import Banter
from video_pipeline.episode import EpisodePlan

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_BOOK = FIXTURES / "mini_book.json"
PILOT_BOOK = ROOT / "book_data.json"
CAST_PATH = ROOT / "config" / "cast.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_banter(book: dict, plan: EpisodePlan) -> Banter:
    """A banter response that satisfies every rule for this plan."""
    title = book["metadata"]["title"]
    moments = []
    for page_number, kids in sorted(plan.qa_speakers.items()):
        prompt = book["pages"][page_number - 1]["prompt"]
        lines = [
            {
                "speaker": kid,
                "text": "<giggle> Look at that!" if kid == "kid_younger" else "Who sent it?",
            }
            for kid in kids
        ]
        lines.append({"speaker": "storyteller", "text": "<chuckle> Let's keep reading and see!"})
        moments.append(
            {
                "page_number": page_number,
                "grounding": " ".join(prompt.split()[:3]),
                "lines": lines,
            }
        )
    return Banter.model_validate(
        {
            "intro": [
                {"speaker": "kid_younger", "text": f"<gasp> Look, it's {title}!"},
                {"speaker": "storyteller", "text": "Let's open the book together."},
            ],
            "moments": moments,
            "outro": [
                {"speaker": "storyteller", "text": "And that's the end of our story!"},
                {"speaker": "kid_older", "text": "That was so much fun!"},
                {"speaker": "storyteller", "text": "Subscribe and come back for our next story!"},
            ],
        }
    )
```
- [ ] Create `tests/test_generate_script.py` (the CLI contract tests):
```python
import json
import shutil

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline import generate_script
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode import plan_episode
from video_pipeline.llm import LLMUnavailable

TERMS = load_disallowed_terms(generate_script.CONFIG_DIR / "disallowed_terms.txt")


class StubModel:
    """Records every request; replies with valid banter (or scripted replies)."""

    def __init__(self, book: dict, seed: int, replies: list[str] | None = None) -> None:
        self.valid = make_banter(book, plan_episode(book, seed)).model_dump_json()
        self.replies = replies
        self.calls: list[tuple[str, int]] = []

    def __call__(self, prompt: str, seed: int, model: str | None = None) -> str:
        self.calls.append((prompt, seed))
        if self.replies is not None:
            return self.replies[min(len(self.calls), len(self.replies)) - 1]
        return self.valid


def never_called(*args, **kwargs):
    raise AssertionError("this backend must not be called")


def run(monkeypatch, tmp_path, book_path=MINI_BOOK, extra=(), local=None, cloud=None):
    monkeypatch.setattr(generate_script, "generate_local", local or never_called)
    monkeypatch.setattr(generate_script, "generate_cloud", cloud or never_called)
    args = ["--book", str(book_path), "--episodes-dir", str(tmp_path / "episodes"), *extra]
    return generate_script.main(args)


def out_path(tmp_path, slug="the-lantern-bug-parade"):
    return tmp_path / "episodes" / slug / "episode_script.json"


def test_writes_valid_script_in_default_mode(monkeypatch, tmp_path):  # AC-1.1, AC-1.2, AC-1.4
    book = load_json(MINI_BOOK)
    stub = StubModel(book, 7)
    assert run(monkeypatch, tmp_path, local=stub) == 0
    script = load_json(out_path(tmp_path))
    assert validate_script(script, book, TERMS) == []
    assert len(stub.calls) == 1


def test_pilot_book_needs_no_changes(monkeypatch, tmp_path):  # AC-1.3 (two different books)
    book = load_json(PILOT_BOOK)
    assert run(monkeypatch, tmp_path, PILOT_BOOK, local=StubModel(book, 7)) == 0
    script = load_json(out_path(tmp_path, "the-snail-mail-express"))
    assert validate_script(script, book, TERMS) == []
    assert sum(p["has_qa"] for p in script["pages"]) in (7, 8)


def test_existing_script_refused_without_force(monkeypatch, tmp_path, capsys):  # AC-6.1
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"hand edited")
    assert run(monkeypatch, tmp_path) == 1
    assert target.read_bytes() == b"hand edited"
    err = capsys.readouterr().err
    assert str(target) in err and "--force" in err


def test_force_overwrites(monkeypatch, tmp_path):  # AC-6.2
    book = load_json(MINI_BOOK)
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert run(monkeypatch, tmp_path, extra=["--force"], local=StubModel(book, 7)) == 0
    assert validate_script(load_json(target), book, TERMS) == []


def test_same_seed_is_byte_identical(monkeypatch, tmp_path):  # AC-6.3
    book = load_json(MINI_BOOK)
    assert run(monkeypatch, tmp_path, extra=["--seed", "11"], local=StubModel(book, 11)) == 0
    first = out_path(tmp_path).read_bytes()
    extra = ["--seed", "11", "--force"]
    assert run(monkeypatch, tmp_path, extra=extra, local=StubModel(book, 11)) == 0
    assert out_path(tmp_path).read_bytes() == first


def test_other_seed_still_valid(monkeypatch, tmp_path):  # AC-6.4
    book = load_json(MINI_BOOK)
    assert run(monkeypatch, tmp_path, extra=["--seed", "3"], local=StubModel(book, 3)) == 0
    assert validate_script(load_json(out_path(tmp_path)), book, TERMS) == []


def test_cloud_mode_retries_with_one_request_per_attempt(monkeypatch, tmp_path):  # AC-6.5
    book = load_json(MINI_BOOK)
    valid = StubModel(book, 7).valid
    stub = StubModel(book, 7, replies=["{}", valid])
    assert run(monkeypatch, tmp_path, extra=["--mode", "cloud"], cloud=stub) == 0
    assert len(stub.calls) == 2
    assert "REJECTED" in stub.calls[1][0]  # validation errors were fed back
    assert validate_script(load_json(out_path(tmp_path)), book, TERMS) == []


def test_gives_up_after_three_invalid_attempts(monkeypatch, tmp_path, capsys):  # FR-7
    stub = StubModel(load_json(MINI_BOOK), 7, replies=["{}"])
    assert run(monkeypatch, tmp_path, local=stub) == 1
    assert len(stub.calls) == 3
    assert not out_path(tmp_path).exists()
    assert "intro" in capsys.readouterr().err  # last attempt's validation errors are listed


def test_local_unavailable_has_no_cloud_fallback(monkeypatch, tmp_path, capsys):  # FR-7
    def down(prompt, seed, model=None):
        raise LLMUnavailable("local model server is not reachable")

    assert run(monkeypatch, tmp_path, local=down) == 1
    assert "not reachable" in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_missing_terms_file_stops_before_any_request(monkeypatch, tmp_path, capsys):  # AC-7.2
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    assert run(monkeypatch, tmp_path, extra=["--config-dir", str(config)]) == 1
    assert str(config / "disallowed_terms.txt") in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_empty_terms_file_warns(monkeypatch, tmp_path, capsys):  # AC-7.3
    book = load_json(MINI_BOOK)
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    (config / "disallowed_terms.txt").write_text("# nothing yet\n", encoding="utf-8")
    extra = ["--config-dir", str(config)]
    assert run(monkeypatch, tmp_path, extra=extra, local=StubModel(book, 7)) == 0
    assert "disallowed-terms list is empty" in capsys.readouterr().out


def test_success_prints_review_notice(monkeypatch, tmp_path, capsys):  # AC-7.4
    assert run(monkeypatch, tmp_path, local=StubModel(load_json(MINI_BOOK), 7)) == 0
    out = capsys.readouterr().out
    assert "reviewed by a person" in out and "before voice synthesis" in out


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b["pages"].pop(), "supported range is 8-32"),
        (lambda b: b["pages"][2].pop("prompt"), "page 3: 'prompt' is missing or empty"),
        (lambda b: b["pages"][4].update(page_number=9), "page 5: page_number must be 5"),
    ],
)
def test_invalid_book_writes_nothing(monkeypatch, tmp_path, capsys, mutate, message):
    book = load_json(MINI_BOOK)
    mutate(book)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(book), encoding="utf-8")
    assert run(monkeypatch, tmp_path, bad) == 1
    assert message in capsys.readouterr().err


def test_missing_book_file(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, tmp_path / "nope.json") == 1
    assert "book data file not found" in capsys.readouterr().err


def test_interrupted_write_keeps_previous_file(monkeypatch, tmp_path):  # Reliability NFR
    target = tmp_path / "episode_script.json"
    target.write_text("previous", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(generate_script.os, "replace", boom)
    with pytest.raises(OSError):
        generate_script.write_atomic(target, {"new": True})
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [target]
```
- [ ] Run `uv run pytest tests/test_generate_script.py -q`. Expect a collection ERROR (`ModuleNotFoundError: video_pipeline.contract`).
- [ ] Commit: `test(script): add CLI contract tests and project setup for episode script generator`

---

## Phase 1: Contract primitives — text helpers, banter model, schedule rule

**Implements:** FR-2 (banter part), FR-5, FR-1 (slug), Security (term matching, list loading) | **Satisfies:** AC-3.1, AC-3.2 (rule), AC-7.1 (matching), AC-7.2/7.3 (loading)
**Files:** `video_pipeline/contract.py`, `tests/test_contract.py`
**Interfaces:** Consumes nothing. Produces the constants `ALLOWED_TAGS`, `NARRATION_MOTIONS`, `INTRO_EFFECT`, `OUTRO_EFFECT`, `SPEAKERS`, `KIDS`, `CAST_FIELDS`, `TOP_LEVEL_KEYS`, `MAX_KID_WORDS`, `MAX_NARRATION_WORDS`, `MAX_NARRATION_SENTENCES` and `TAG_RE`; the classes `ConfigError`, `BanterParseError(errors: list[str])`, `BanterLine`, `Moment` and `Banter`; and the functions `parse_banter(raw) -> Banter`, `normalize_ws`, `strip_tags`, `word_count`, `ends_sentence`, `split_sentences(text) -> list[str]`, `slugify(title) -> str`, `find_disallowed(text, terms) -> list[str]`, `load_disallowed_terms(path) -> list[str]` and `qa_schedule_errors(qa_pages, page_count) -> list[str]`.

- [ ] Write `tests/test_contract.py`:
```python
import pytest

from video_pipeline.contract import (
    BanterParseError,
    ConfigError,
    ends_sentence,
    find_disallowed,
    load_disallowed_terms,
    normalize_ws,
    parse_banter,
    qa_schedule_errors,
    slugify,
    split_sentences,
    strip_tags,
    word_count,
)


def test_normalize_ws_collapses_line_breaks():
    assert normalize_ws("  a\n\nb \t c ") == "a b c"


def test_strip_tags_and_word_count_ignore_tags():
    assert strip_tags("<giggle> Look at   that!") == "Look at that!"
    assert word_count("<gasp> One two <sigh> three") == 3


def test_split_sentences_handles_quotes_abbreviations_and_paragraphs():
    text = (
        '"Tonight is the Lantern Bug Parade!" buzzed Mr. Pebble the beetle. '
        '"Every bug who glows gets to march."\n\nTilly looked at her lantern and sighed.'
    )
    assert split_sentences(text) == [
        '"Tonight is the Lantern Bug Parade!" buzzed Mr. Pebble the beetle.',
        '"Every bug who glows gets to march."',
        "Tilly looked at her lantern and sighed.",
    ]
    assert split_sentences("   ") == []


def test_ends_sentence():
    assert ends_sentence('He waved. "Bye!"')
    assert not ends_sentence("and then")


def test_slugify():
    assert slugify("The Snail Mail Express") == "the-snail-mail-express"
    assert slugify("Tilly's Big-Night, Out!") == "tillys-big-night-out"
    assert slugify("!!!") == ""


def test_find_disallowed_is_whole_word_and_case_insensitive():
    terms = ["hell", "shut up"]
    assert find_disallowed("Hello there", terms) == []
    assert find_disallowed("Oh HELL no, shut up!", terms) == ["hell", "shut up"]


def test_load_disallowed_terms(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text("# comment\n\n kill \nshut up\n", encoding="utf-8")
    assert load_disallowed_terms(path) == ["kill", "shut up"]
    with pytest.raises(ConfigError, match="not found"):
        load_disallowed_terms(tmp_path / "missing.txt")


def test_parse_banter_reports_field_errors():
    with pytest.raises(BanterParseError) as excinfo:
        parse_banter('{"intro": [], "moments": [], "outro": []}')
    assert any("intro" in e for e in excinfo.value.errors)
    with pytest.raises(BanterParseError):
        parse_banter("not json")


@pytest.mark.parametrize(
    ("pages", "count", "expected"),
    [
        ([1, 4, 6, 9, 11, 14, 17, 19], 20, []),
        ([2, 4, 7], 8, []),
        ([4, 6, 9, 11, 14, 17, 19], 20, ["first Q&A must be on page 3 or earlier"]),
        ([1, 4, 6, 9, 11, 14, 16], 20, ["last Q&A must be within 3 pages of page 20"]),
        ([1, 5, 7], 8, ["gap between Q&A pages 1 and 5 must be 2 or 3 pages"]),
        ([1, 3, 5, 7], 8, ["needs 3-3 Q&A pages for an 8-page book (has 4)"]),
    ],
)
def test_qa_schedule_errors(pages, count, expected):
    assert qa_schedule_errors(pages, count) == expected
```
- [ ] Run `uv run pytest tests/test_contract.py -q`. Expect FAIL (`ModuleNotFoundError`).
- [ ] Implement `video_pipeline/contract.py` (part 1; Phase 3 appends the validator):
```python
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
    "book_slug", "title", "aspect_ratio", "resolution", "cast", "intro", "pages", "outro",
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
        term
        for term in terms
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE)
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
```
- [ ] Run `uv run pytest tests/test_contract.py -q`. Expect all PASS.
- [ ] Commit: `feat(script): add contract primitives, banter model and Q&A schedule rule`

---

## Phase 2: Deterministic episode planning and assembly

**Implements:** FR-1, FR-3, FR-4 (placement and speakers), FR-6, FR-7 (seeded determinism) | **Satisfies:** AC-2.1, AC-2.3, AC-2.4, AC-3.1, AC-3.2, AC-3.3, AC-5.1, AC-5.3, AC-5.4, AC-5.5, AC-6.3, AC-6.4
**Files:** `video_pipeline/episode.py`, `tests/test_episode.py`
**Interfaces:** Consumes everything Phase 1 produced in `contract.py`. Produces `BookError`, `MIN_PAGES = 8`, `MAX_PAGES = 32`, `DEFAULT_ZOOM`, and the frozen dataclass `EpisodePlan(qa_speakers: dict[int, tuple[str, ...]], motions: tuple[str, ...])`. Also produces these functions: `load_book(path) -> dict`, `split_narration(text) -> list[str]`, `choose_qa_pages(page_count, rng) -> list[int]`, `assign_speakers(count, rng) -> list[tuple[str, ...]]`, `plan_episode(book, seed) -> EpisodePlan`, `zoom_target(prompt, grounding) -> dict`, `assemble_script(book, cast, plan, banter) -> dict` and `banter_plan_errors(banter, plan) -> list[str]`.

- [ ] Write `tests/test_episode.py`:
```python
import json
import random

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline.contract import (
    KIDS,
    NARRATION_MOTIONS,
    normalize_ws,
    qa_schedule_errors,
    split_sentences,
    word_count,
)
from video_pipeline.episode import (
    DEFAULT_ZOOM,
    BookError,
    assemble_script,
    assign_speakers,
    banter_plan_errors,
    choose_qa_pages,
    load_book,
    plan_episode,
    split_narration,
    zoom_target,
)


def write_book(tmp_path, book):
    path = tmp_path / "book.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    return path


def test_load_book_accepts_both_books():
    assert len(load_book(MINI_BOOK)["pages"]) == 8
    assert len(load_book(PILOT_BOOK)["pages"]) == 20


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b["pages"].pop(), "supported range is 8-32"),
        (lambda b: b["pages"][0].update(text="  "), "page 1: 'text' is missing or empty"),
        (lambda b: b["pages"][1].update(page_number=3), "page 2: page_number must be 2"),
        (lambda b: b["metadata"].update(title="!!!"), "title has no letters or digits"),
    ],
)
def test_load_book_rejects_invalid_books(tmp_path, mutate, message):
    book = load_json(MINI_BOOK)
    mutate(book)
    with pytest.raises(BookError, match=message):
        load_book(write_book(tmp_path, book))


def test_load_book_reports_bad_json(tmp_path):
    path = tmp_path / "book.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(BookError, match="not valid JSON"):
        load_book(path)


@pytest.mark.parametrize("book_path", [MINI_BOOK, PILOT_BOOK])
def test_split_narration_is_verbatim_and_within_limits(book_path):
    for page in load_json(book_path)["pages"]:
        lines = split_narration(page["text"])
        assert normalize_ws(" ".join(lines)) == normalize_ws(page["text"])
        for line in lines:
            sentences = split_sentences(line)
            assert len(sentences) <= 2
            assert len(sentences) == 1 or word_count(line) <= 40


def test_split_narration_keeps_long_sentence_whole():
    text = load_json(MINI_BOOK)["pages"][2]["text"]
    lines = split_narration(text)
    assert len(lines) == 2
    assert word_count(lines[0]) > 40
    assert lines[1] == "Granny Dusk smiled."


@pytest.mark.parametrize("page_count", range(8, 33))
def test_choose_qa_pages_always_valid(page_count):
    for seed in range(10):
        pages = choose_qa_pages(page_count, random.Random(seed))
        assert qa_schedule_errors(pages, page_count) == []


def test_assign_speakers_covers_all_three_kinds():
    picks = assign_speakers(7, random.Random(1))
    assert len(picks) == 7
    assert {("kid_older",), ("kid_younger",), ("kid_older", "kid_younger")} <= set(picks)


def test_plan_episode_is_deterministic_and_seed_sensitive():
    book = load_json(PILOT_BOOK)
    assert plan_episode(book, 7) == plan_episode(book, 7)
    assert len({repr(plan_episode(book, seed)) for seed in range(5)}) > 1
    plan = plan_episode(book, 7)
    assert len(plan.motions) == 20 and set(plan.motions) <= set(NARRATION_MOTIONS)


def test_zoom_target_uses_position_words_near_grounding():
    prompt = "Tilly the firefly sitting on the left of a mossy log, soft sky in the background."
    assert zoom_target(prompt, "Tilly the firefly") == {"x": 0.3, "y": 0.5, "zoom": 1.3}
    assert zoom_target(prompt, "soft sky") == {"x": 0.5, "y": 0.3, "zoom": 1.3}  # "sky" first


def test_zoom_target_defaults_without_position_cue():
    assert zoom_target("Granny Dusk beside her books.", "Granny Dusk") == DEFAULT_ZOOM
    assert zoom_target("A log on the left.", "not in the prompt") == DEFAULT_ZOOM


def test_assemble_script_structure():
    book = load_json(MINI_BOOK)
    cast = load_json(CAST_PATH)
    plan = plan_episode(book, 7)
    script = assemble_script(book, cast, plan, make_banter(book, plan))
    assert script["book_slug"] == "the-lantern-bug-parade"
    assert [p["page_number"] for p in script["pages"]] == list(range(1, 9))
    assert script["intro"]["ffmpeg_effect"] == "zoom_in_center"
    assert script["outro"]["ffmpeg_effect"] == "zoom_out_warm_fade"
    for page in script["pages"]:
        roles = [line["role"] for line in page["lines"]]
        assert roles[0] == "narration"
        assert roles == sorted(roles, key=lambda r: r != "narration")  # narration first
        assert page["has_qa"] == (page["page_number"] in plan.qa_speakers)
        assert ("qa_zoom_target" in page["ffmpeg_camera"]) == page["has_qa"]
        if page["has_qa"]:
            assert page["lines"][-1] == {
                "speaker": "storyteller",
                "role": "answer",
                "text": "<chuckle> Let's keep reading and see!",
            }


def test_banter_plan_errors_detects_mismatch():
    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    banter = make_banter(book, plan)
    assert banter_plan_errors(banter, plan) == []
    first = banter.moments[0]
    wrong = [k for k in KIDS if (k,) != plan.qa_speakers[first.page_number]][0]
    first.lines = [line.model_copy(update={"speaker": wrong}) if line.speaker in KIDS else line
                   for line in first.lines]
    banter.moments.append(first.model_copy(update={"page_number": 99}))
    errors = banter_plan_errors(banter, plan)
    assert any(f"page {first.page_number}: kids speaking must be exactly" in e for e in errors)
    assert "unexpected Q&A moment for page 99" in errors
```
- [ ] Run `uv run pytest tests/test_episode.py -q`. Expect FAIL (`ModuleNotFoundError: video_pipeline.episode`).
- [ ] Implement `video_pipeline/episode.py`:
```python
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
    "top": 0.3, "above": 0.3, "sky": 0.3, "background": 0.4,
    "foreground": 0.65, "bottom": 0.7, "below": 0.7,
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
```
- [ ] Run `uv run pytest tests/test_episode.py -q`. Expect all PASS.
- [ ] Commit: `feat(script): add deterministic episode planning and assembly`

---

## Phase 3: Full script validator

**Implements:** FR-2, FR-3, FR-4, FR-5, FR-6, Security (term check on generated lines only) | **Satisfies:** AC-1.2, AC-2.1–2.4, AC-3.1–3.6, AC-4.1, AC-4.2, AC-5.1–5.4, AC-7.1
**Files:** `video_pipeline/contract.py` (append), `tests/test_validate.py`
**Interfaces:** Consumes the Phase 1 helpers and, in tests only, `assemble_script` and `plan_episode` from Phase 2. Produces `validate_script(script: dict, book: dict, terms: list[str]) -> list[str]`, which returns an empty list when the script is valid.

- [ ] Write `tests/test_validate.py`:
```python
import copy

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline.contract import validate_script
from video_pipeline.episode import assemble_script, plan_episode

TERMS = ["stupid", "shut up"]


def build(book_path=MINI_BOOK, seed=7):
    book = load_json(book_path)
    plan = plan_episode(book, seed)
    return book, assemble_script(book, load_json(CAST_PATH), plan, make_banter(book, plan))


def qa_page(script):
    return next(p for p in script["pages"] if p["has_qa"])


def plain_page(script):
    return next(p for p in script["pages"] if not p["has_qa"])


@pytest.mark.parametrize("book_path", [MINI_BOOK, PILOT_BOOK])
@pytest.mark.parametrize("seed", [1, 7, 42])
def test_assembled_script_is_valid(book_path, seed):  # AC-1.2
    book, script = build(book_path, seed)
    assert validate_script(script, book, TERMS) == []


def test_narration_may_carry_tags_without_breaking_verbatim():  # AC-2.2, FR-3
    book, script = build()
    line = script["pages"][0]["lines"][0]
    line["text"] = "<sigh> " + line["text"]
    assert validate_script(script, book, TERMS) == []


def test_book_text_is_not_term_checked():  # Security: only generated lines
    book, script = build()
    book["pages"][0]["text"] = book["pages"][0]["text"].replace("tiny", "stupid", 1)
    script["pages"][0]["lines"][0]["text"] = script["pages"][0]["lines"][0]["text"].replace(
        "tiny", "stupid", 1
    )
    assert validate_script(script, book, TERMS) == []


def mutate_missing_page(script):
    script["pages"].pop()


def mutate_word(script):
    line = script["pages"][0]["lines"][0]
    line["text"] = line["text"].replace("tiny", "small", 1)


def mutate_qa_before_narration(script):
    page = qa_page(script)
    page["lines"].insert(0, page["lines"].pop())


def mutate_three_sentences(script):
    page = script["pages"][3]  # mini page 4 has 4 sentences in 2 lines
    lines = [x for x in page["lines"] if x["role"] == "narration"]
    lines[0]["text"] = lines[0]["text"] + " " + lines[1]["text"].split(". ")[0] + "."
    lines[1]["text"] = lines[1]["text"].split(". ", 1)[1]


def mutate_all_both(script):
    for page in script["pages"]:
        if page["has_qa"]:
            kids = [x for x in page["lines"] if x["speaker"] != "storyteller"]
            kids[0]["speaker"] = "kid_older"
            page["lines"].insert(-1, {**kids[0], "speaker": "kid_younger"})


def mutate_moment_ends_with_kid(script):
    page = qa_page(script)
    page["lines"].append({"speaker": "kid_younger", "role": "reaction", "text": "Wow!"})


def mutate_moment_too_long(script):
    page = qa_page(script)
    extra = {"speaker": "kid_older", "role": "question", "text": "Why?"}
    for _ in range(5):
        page["lines"].insert(-1, dict(extra))


def mutate_grounding(script):
    qa_page(script)["qa_grounding"] = "a purple elephant"


def mutate_long_kid_line(script):
    page = qa_page(script)
    kid = next(x for x in page["lines"] if x["speaker"] != "storyteller")
    kid["text"] = " ".join(["word"] * 26)


def mutate_bad_tag(script):
    script["intro"]["lines"][0]["text"] = "<whisper> Hello there!"


def mutate_intro_no_title(script):
    script["intro"]["lines"][0]["text"] = "<gasp> Look at that book!"


def mutate_outro_ends_with_kid(script):
    script["outro"]["lines"].append({"speaker": "kid_older", "text": "Bye!"})


def mutate_motion(script):
    script["pages"][0]["ffmpeg_camera"]["narration_motion"] = "spin"


def mutate_zoom_range(script):
    qa_page(script)["ffmpeg_camera"]["qa_zoom_target"]["zoom"] = 2.5


def mutate_zoom_on_plain_page(script):
    plain_page(script)["ffmpeg_camera"]["qa_zoom_target"] = {"x": 0.5, "y": 0.5, "zoom": 1.3}


def mutate_effects(script):
    script["intro"]["ffmpeg_effect"] = "zoom_out_warm_fade"


def mutate_term(script):
    page = qa_page(script)
    kid = next(x for x in page["lines"] if x["speaker"] != "storyteller")
    kid["text"] = "That is so stupid!"


def mutate_cast(script):
    del script["cast"]["kid_younger"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (mutate_missing_page, "pages must be numbered 1..8 in book order"),  # AC-2.1
        (mutate_word, "page 1: narration does not match the page text word for word"),  # AC-2.2
        (mutate_qa_before_narration, "narration lines must come before Q&A lines"),  # AC-2.3
        (mutate_three_sentences, "page 4 narration line 1: has 3 sentences (max 2)"),  # AC-2.4
        (mutate_all_both, "needs at least one Q&A moment with only Leo (kid_older)"),  # AC-3.3
        (mutate_moment_ends_with_kid, "Q&A moment must end with a storyteller answer"),  # AC-3.4
        (mutate_moment_too_long, "Q&A moment must have 2-5 lines"),  # AC-3.4
        (mutate_grounding, "qa_grounding must be a phrase copied word for word"),  # AC-3.5
        (mutate_long_kid_line, "kid line has 26 words (max 25)"),  # AC-3.6
        (mutate_bad_tag, "intro line 1: tag <whisper> is not allowed"),  # FR-5
        (mutate_intro_no_title, "intro must mention the title"),  # AC-4.1
        (mutate_outro_ends_with_kid, "outro must end with a storyteller line inviting"),  # AC-4.2
        (mutate_motion, "page 1: narration_motion must be one of"),  # AC-5.1
        (mutate_zoom_range, "qa_zoom_target needs x, y in [0, 1] and zoom in [1, 2]"),  # AC-5.2
        (mutate_zoom_on_plain_page, "has_qa is false but the page has Q&A content"),  # AC-5.3
        (mutate_effects, "intro.ffmpeg_effect must be 'zoom_in_center'"),  # AC-5.4
        (mutate_term, "disallowed term 'stupid'"),  # AC-7.1
        (mutate_cast, "cast must define exactly: storyteller, kid_older, kid_younger"),  # FR-2
    ],
)
def test_validator_rejects(mutate, expected):
    book, script = build()
    broken = copy.deepcopy(script)
    mutate(broken)
    errors = validate_script(broken, book, TERMS)
    assert any(expected in e for e in errors), errors


def test_missing_top_level_key():
    book, script = build()
    del script["outro"]
    assert validate_script(script, book, TERMS) == ["missing top-level key: outro"]
```
- [ ] Run `uv run pytest tests/test_validate.py -q`. Expect FAIL (`ImportError: cannot import name 'validate_script'`).
- [ ] Append to `video_pipeline/contract.py`:
```python
def validate_script(script: dict, book: dict, terms: list[str]) -> list[str]:
    """Check a complete episode script against every spec rule; [] means valid."""
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
    errors += _bookend_errors(script["intro"], "intro", terms, title)
    errors += _bookend_errors(script["outro"], "outro", terms, title)
    errors += _page_errors(script["pages"], book, terms)
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
        for term in find_disallowed(text, terms):
            errors.append(f"{where}: disallowed term {term!r}")
        if speaker in KIDS and word_count(text) > MAX_KID_WORDS:
            errors.append(
                f"{where}: kid line has {word_count(text)} words (max {MAX_KID_WORDS})"
            )
    return errors


def _bookend_errors(block: dict, name: str, terms: list[str], title: str) -> list[str]:
    lines = block.get("lines", [])
    errors = [
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
        if last.get("speaker") != "storyteller" or "subscribe" not in last.get("text", "").lower():
            errors.append("outro must end with a storyteller line inviting viewers to subscribe")
    return errors


def _page_errors(pages: list[dict], book: dict, terms: list[str]) -> list[str]:
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
        if len(sentences) > MAX_NARRATION_SENTENCES:
            errors.append(f"{where}: has {len(sentences)} sentences (max 2)")
        if len(sentences) > 1 and word_count(plain) > MAX_NARRATION_WORDS:
            errors.append(f"{where}: has {word_count(plain)} words (max 40 unless one sentence)")
    joined = strip_tags(" ".join(line.get("text", "") for line in narration))
    if joined != normalize_ws(text):
        errors.append(f"page {number}: narration does not match the page text word for word")
    return errors


def _qa_errors(number: int, qa: list[dict], page: dict, book_page: dict, terms: list[str]) -> list[str]:
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
```
- [ ] Run `uv run pytest tests/test_contract.py tests/test_validate.py -q`. Expect all PASS.
- [ ] Commit: `feat(script): validate complete episode scripts against the spec rules`

---

## ~~Phase 4: Model backends (local Ollama, opt-in cloud Gemini)~~ (partly invalidated v2.0.0 — `generate_local` removed; `generate_cloud` and `build_prompt` reused by the v2.0.0 phases)

**Implements:** FR-7 (modes, no fallback, one request per attempt), NFR Performance (model released), Security (credentials from env only) | **Satisfies:** AC-1.4, AC-6.5 (backend side)
**Files:** `video_pipeline/llm.py`, `tests/test_llm.py`
**Interfaces:** Consumes `Banter` and `normalize_ws` from `contract.py`, and `EpisodePlan` from `episode.py`. Produces `LOCAL_MODEL = "qwen2.5:7b"`, `OLLAMA_URL = "http://127.0.0.1:11434"`, `CLOUD_MODEL = "gemini-2.5-flash"`, `GEMINI_KEY_ENV = "GEMINI_API_KEY"`, `LLMError`, `LLMUnavailable`, `build_prompt(book, cast, plan, feedback=None) -> str`, `generate_local(prompt, seed, model=LOCAL_MODEL, base_url=OLLAMA_URL) -> str` and `generate_cloud(prompt, seed, model=CLOUD_MODEL) -> str`.

The model choice follows PRD.md (qwen2.5:7b fits in 16 GB). Both backends use the standard library's HTTP client, so no SDK dependency is needed. Ollama gets the `Banter` JSON schema in `format`, `temperature: 0` with the seed for reproducibility (AC-6.3), and `keep_alive: 0` so the model is released afterwards (NFR). Gemini gets `responseMimeType: application/json`, and its key is sent in a header, never in the URL.

- [ ] Write `tests/test_llm.py`:
```python
import io
import json
import urllib.error
import urllib.request

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, load_json
from video_pipeline.episode import plan_episode
from video_pipeline.llm import (
    GEMINI_KEY_ENV,
    LLMError,
    LLMUnavailable,
    build_prompt,
    generate_cloud,
    generate_local,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def fake_urlopen(monkeypatch, payload=None, exc=None):
    requests = []

    def fake(request, timeout=None):
        requests.append(request)
        if exc is not None:
            raise exc
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return requests


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_build_prompt_lists_pages_plan_and_feedback():
    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    prompt = build_prompt(book, load_json(CAST_PATH), plan, feedback=["intro too short"])
    assert '"The Lantern Bug Parade"' in prompt
    assert all(f"PAGE {p['page_number']}" in prompt for p in book["pages"])
    for number in plan.qa_speakers:
        assert f"- page {number}:" in prompt
    assert "REJECTED" in prompt and "- intro too short" in prompt
    assert "Leo (Age 8)" in prompt and "Mia (Age 5)" in prompt


def test_generate_local_sends_reproducible_request(monkeypatch):
    requests = fake_urlopen(monkeypatch, {"message": {"content": '{"ok": 1}'}})
    assert generate_local("hello", 9) == '{"ok": 1}'
    body = json.loads(requests[0].data)
    assert requests[0].full_url == "http://127.0.0.1:11434/api/chat"
    assert body["options"] == {"seed": 9, "temperature": 0}
    assert body["keep_alive"] == 0 and body["stream"] is False
    assert body["format"]["title"] == "Banter"


def test_generate_local_missing_model(monkeypatch):
    fake_urlopen(monkeypatch, exc=http_error(404))
    with pytest.raises(LLMUnavailable, match="ollama pull qwen2.5:7b"):
        generate_local("hello", 1)


def test_generate_local_server_down(monkeypatch):
    fake_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
    with pytest.raises(LLMUnavailable, match="not reachable"):
        generate_local("hello", 1)


def test_generate_local_bad_reply(monkeypatch):
    fake_urlopen(monkeypatch, {"unexpected": True})
    with pytest.raises(LLMError, match="unexpected response"):
        generate_local("hello", 1)


def test_generate_cloud_requires_key_and_sends_nothing_without_it(monkeypatch):
    monkeypatch.delenv(GEMINI_KEY_ENV, raising=False)
    requests = fake_urlopen(monkeypatch, {})
    with pytest.raises(LLMUnavailable, match=GEMINI_KEY_ENV):
        generate_cloud("hello", 1)
    assert requests == []


def test_generate_cloud_sends_key_in_header_only(monkeypatch):
    monkeypatch.setenv(GEMINI_KEY_ENV, "secret-key")
    reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}
    requests = fake_urlopen(monkeypatch, reply)
    assert generate_cloud("hello", 3) == '{"ok": 1}'
    request = requests[0]
    assert "secret-key" not in request.full_url
    assert request.get_header("X-goog-api-key") == "secret-key"
    assert json.loads(request.data)["generationConfig"]["seed"] == 3


def test_generate_cloud_errors_never_leak_key(monkeypatch):
    monkeypatch.setenv(GEMINI_KEY_ENV, "secret-key")
    fake_urlopen(monkeypatch, exc=http_error(403))
    with pytest.raises(LLMError) as excinfo:
        generate_cloud("hello", 1)
    assert "secret-key" not in str(excinfo.value)
```
- [ ] Run `uv run pytest tests/test_llm.py -q`. Expect FAIL (`ModuleNotFoundError: video_pipeline.llm`).
- [ ] Implement `video_pipeline/llm.py`:
```python
"""Text-model backends: local Ollama (default) and opt-in cloud Gemini. One request per call."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from video_pipeline.contract import Banter, normalize_ws
from video_pipeline.episode import EpisodePlan

LOCAL_MODEL = "qwen2.5:7b"
OLLAMA_URL = "http://127.0.0.1:11434"
CLOUD_MODEL = "gemini-2.5-flash"
GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 300

_WHO = {
    ("kid_older",): "kid_older only",
    ("kid_younger",): "kid_younger only",
    ("kid_older", "kid_younger"): "both kid_older and kid_younger",
}


class LLMError(Exception):
    """The model request failed or returned something unusable."""


class LLMUnavailable(Exception):
    """The selected backend cannot be used: not running, model missing, or no credentials."""


def build_prompt(book: dict, cast: dict, plan: EpisodePlan, feedback: list[str] | None = None) -> str:
    meta = book["metadata"]
    title = meta["title"]
    cast_lines = "\n".join(f"- {role}: {c['name']}, {c['description']}" for role, c in cast.items())
    pages = "\n\n".join(
        f"PAGE {p['page_number']}\nTEXT: {normalize_ws(p['text'])}\n"
        f"ILLUSTRATION: {normalize_ws(p['prompt'])}"
        for p in book["pages"]
    )
    moments = "\n".join(
        f"- page {number}: {_WHO[tuple(sorted(kids))]}"
        for number, kids in sorted(plan.qa_speakers.items())
    )
    prompt = f"""You write the spoken banter for a cozy read-aloud storytime video of the children's \
book "{title}" ({meta.get("age_range", "young children")}). The Storyteller reads every page \
aloud; you write only the extra dialogue described below.
Reply with JSON only, matching this JSON schema:
{json.dumps(Banter.model_json_schema())}

CAST
{cast_lines}

BOOK
{pages}

WRITE
1. "intro": 2-4 lines before the book opens, with at least one kid line and one storyteller line. \
One line must say the exact title "{title}".
2. "moments": exactly one Q&A moment for each page below, spoken right after that page is read:
{moments}
   Each moment has 2-5 lines. Only the listed kids speak (when both are listed, both speak). \
The last line is the storyteller answering.
   kid_older asks curious questions about the plot or guesses at mysteries. kid_younger reacts \
to funny or cute details in the illustration.
   "grounding" is a short phrase (2-6 words) copied exactly from that page's TEXT or \
ILLUSTRATION, naming what the moment is about.
   Use only what has happened up to that page. Never reveal later pages.
3. "outro": 3-5 lines after the book closes, with at least one kid line. The last line is the \
storyteller inviting viewers to subscribe and come back for the next story.

RULES
- Kid lines are at most 25 words.
- The only allowed inline tags are <giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>. No other \
tags, markup or stage directions.
- Everything is gentle and suitable for young children. Do not invent story events that are \
not in the book.
"""
    if feedback:
        problems = "\n".join(f"- {error}" for error in feedback)
        prompt += f"\nYOUR PREVIOUS ANSWER WAS REJECTED. Fix these problems:\n{problems}\n"
    return prompt


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def generate_local(prompt: str, seed: int, model: str = LOCAL_MODEL, base_url: str = OLLAMA_URL) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "format": Banter.model_json_schema(),
        "stream": False,
        "options": {"seed": seed, "temperature": 0},
        "keep_alive": 0,
    }
    try:
        data = _post_json(f"{base_url}/api/chat", payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LLMUnavailable(
                f"local model '{model}' is not installed; run: ollama pull {model}"
            ) from None
        raise LLMError(f"local model request failed: HTTP {exc.code}") from None
    except TimeoutError:
        raise LLMError(f"local model did not answer within {TIMEOUT_S} s") from None
    except (urllib.error.URLError, ConnectionError):
        raise LLMUnavailable(
            f"local model server is not reachable at {base_url}; "
            "start Ollama (open the app or run: ollama serve)"
        ) from None
    try:
        return data["message"]["content"]
    except (KeyError, TypeError):
        raise LLMError("local model returned an unexpected response") from None


def generate_cloud(prompt: str, seed: int, model: str = CLOUD_MODEL) -> str:
    key = os.environ.get(GEMINI_KEY_ENV)
    if not key:
        raise LLMUnavailable(f"cloud mode needs the {GEMINI_KEY_ENV} environment variable")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "seed": seed,
        },
    }
    try:
        data = _post_json(GEMINI_URL.format(model=model), payload, {"x-goog-api-key": key})
    except urllib.error.HTTPError as exc:
        raise LLMError(f"cloud model request failed: HTTP {exc.code}") from None
    except TimeoutError:
        raise LLMError(f"cloud model did not answer within {TIMEOUT_S} s") from None
    except (urllib.error.URLError, ConnectionError):
        raise LLMUnavailable("cloud model is not reachable; check the network connection") from None
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("cloud model returned an unexpected response") from None
```
- [ ] Run `uv run pytest tests/test_llm.py -q`. Expect all PASS.
- [ ] Commit: `feat(script): add local and opt-in cloud text-model backends`

---

## ~~Phase 5: CLI, retry loop and atomic write~~ (partly invalidated v2.0.0 — `write_atomic`, overwrite guard, terms gate and review notice kept; backend selection and retry loop rewritten in the v2.0.0 phases)

**Implements:** FR-1, FR-7, NFR Reliability, Security (terms file gate, review notice), Error Scenarios | **Satisfies:** AC-1.1–1.4, AC-6.1–6.5, AC-7.2–7.4 (Phase 0 tests)
**Files:** `video_pipeline/generate_script.py`
**Interfaces:** Consumes `load_book`, `plan_episode`, `assemble_script`, `banter_plan_errors` and `BookError` from `episode.py`; `ConfigError`, `BanterParseError`, `parse_banter`, `load_disallowed_terms`, `slugify` and `validate_script` from `contract.py`; and `generate_local`, `generate_cloud`, `build_prompt`, `LLMError`, `LLMUnavailable`, `LOCAL_MODEL` and `CLOUD_MODEL` from `llm.py`. Produces `CONFIG_DIR`, `MAX_ATTEMPTS = 3`, `DEFAULT_SEED = 7`, `REVIEW_NOTICE`, `GenerationFailed(errors)`, `output_path(book, episodes_dir) -> Path`, `load_cast(path) -> dict`, `generate_episode_script(book, cast, terms, seed, generate) -> dict`, `write_atomic(path, data) -> None` and `main(argv=None) -> int`.

Order of checks in `main`: load and validate the book, then refuse to overwrite, then load the terms list and cast, then generate. That way nothing is requested and nothing is written when an earlier check fails (AC-6.1, AC-7.2). The model seed for each attempt is `seed + attempt`, so retries can produce different text while the whole run stays reproducible.

- [ ] (The failing tests already exist from Phase 0.) Run `uv run pytest tests/test_generate_script.py -q`. Expect FAIL (`ModuleNotFoundError: video_pipeline.generate_script`).
- [ ] Implement `video_pipeline/generate_script.py`:
```python
"""CLI: generate episodes/<slug>/episode_script.json from a book data file (spec 001)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from video_pipeline.contract import (
    BanterParseError,
    ConfigError,
    load_disallowed_terms,
    parse_banter,
    slugify,
    validate_script,
)
from video_pipeline.episode import (
    BookError,
    assemble_script,
    banter_plan_errors,
    load_book,
    plan_episode,
)
from video_pipeline.llm import (
    CLOUD_MODEL,
    LOCAL_MODEL,
    LLMError,
    LLMUnavailable,
    build_prompt,
    generate_cloud,
    generate_local,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
MAX_ATTEMPTS = 3
DEFAULT_SEED = 7
REVIEW_NOTICE = (
    "Review required: this script must be reviewed by a person before voice synthesis "
    "(age-suitability, no spoilers from later pages, no invented story events)."
)

Generate = Callable[[str, int], str]


class GenerationFailed(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"generation failed after {MAX_ATTEMPTS} attempts")
        self.errors = errors


def output_path(book: dict, episodes_dir: Path) -> Path:
    return episodes_dir / slugify(book["metadata"]["title"]) / "episode_script.json"


def load_cast(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"cast configuration not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"cast configuration is not valid JSON: {path} ({exc})") from None


def generate_episode_script(
    book: dict, cast: dict, terms: list[str], seed: int, generate: Generate
) -> dict:
    plan = plan_episode(book, seed)
    feedback: list[str] | None = None
    errors: list[str] = []
    for attempt in range(MAX_ATTEMPTS):
        raw = generate(build_prompt(book, cast, plan, feedback), seed + attempt)
        try:
            banter = parse_banter(raw)
        except BanterParseError as exc:
            errors = exc.errors
        else:
            script = assemble_script(book, cast, plan, banter)
            errors = banter_plan_errors(banter, plan) + validate_script(script, book, terms)
            if not errors:
                return script
        feedback = errors
    raise GenerationFailed(errors)


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m video_pipeline.generate_script",
        description="Generate episode_script.json for a book (local model by default).",
    )
    parser.add_argument("--book", type=Path, default=Path("book_data.json"))
    parser.add_argument("--mode", choices=("local", "cloud"), default="local")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true", help="overwrite an existing script")
    parser.add_argument("--episodes-dir", type=Path, default=Path("episodes"))
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--model", default=None, help="override the text model name")
    return parser.parse_args(argv)


def _backend(mode: str, model: str | None) -> Generate:
    if mode == "cloud":
        return lambda prompt, seed: generate_cloud(prompt, seed, model or CLOUD_MODEL)
    return lambda prompt, seed: generate_local(prompt, seed, model or LOCAL_MODEL)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        book = load_book(args.book)
        out = output_path(book, args.episodes_dir)
        if out.exists() and not args.force:
            print(
                f"error: {out} already exists; re-run with --force to overwrite it",
                file=sys.stderr,
            )
            return 1
        terms = load_disallowed_terms(args.config_dir / "disallowed_terms.txt")
        if not terms:
            print("warning: the disallowed-terms list is empty; no terms will be checked")
        cast = load_cast(args.config_dir / "cast.json")
        script = generate_episode_script(book, cast, terms, args.seed, _backend(args.mode, args.model))
    except (BookError, ConfigError, LLMUnavailable, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except GenerationFailed as exc:
        print(f"error: {exc}; problems in the last attempt:", file=sys.stderr)
        for problem in exc.errors:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    write_atomic(out, script)
    print(f"wrote {out}")
    print(REVIEW_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
- [ ] Run `uv run pytest -q`. Expect all PASS (the integration test is deselected by default).
- [ ] Run `uv run ruff check . && uv run ruff format --check .`. Expect no errors.
- [ ] Commit: `feat(script): add generate_script CLI with retries and atomic writes`

---

## ~~Phase 6: Integration verification~~ (invalidated v2.0.0 — real local-model run replaced by the Codex provider integration check in the v2.0.0 phases)

**Implements:** All FRs | **Satisfies:** All ACs, NFR Performance (3 min, model released), AC-6.3 against the real model
**Files:** `tests/test_ollama_integration.py`, `CLAUDE.md` (add the commands), `.claude/rules/data-pipeline.md` (note that `episodes/` holds hand-editable scripts)
**Interfaces:** Consumes `main`, `CONFIG_DIR`, `validate_script`, `load_disallowed_terms` and `OLLAMA_URL`. Produces nothing new.

- [ ] Write `tests/test_ollama_integration.py`:
```python
"""Real local-model run on the pilot book. Run with: uv run pytest -m ollama -v"""

import json
import time
import urllib.request

import pytest

from tests.helpers import PILOT_BOOK, load_json
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.generate_script import CONFIG_DIR, main
from video_pipeline.llm import OLLAMA_URL

pytestmark = pytest.mark.ollama


def loaded_models() -> list:
    with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10) as response:
        return json.load(response)["models"]


def test_pilot_book_end_to_end_local(tmp_path):
    args = ["--book", str(PILOT_BOOK), "--episodes-dir", str(tmp_path)]
    started = time.monotonic()
    assert main(args) == 0
    elapsed = time.monotonic() - started
    out = tmp_path / "the-snail-mail-express" / "episode_script.json"
    terms = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
    assert validate_script(load_json(out), load_json(PILOT_BOOK), terms) == []
    assert elapsed < 180, f"took {elapsed:.0f} s"  # NFR Performance

    for _ in range(10):  # model released (keep_alive=0); unloading can lag a moment
        if not loaded_models():
            break
        time.sleep(0.5)
    assert loaded_models() == []

    first = out.read_bytes()
    assert main([*args, "--force"]) == 0
    assert out.read_bytes() == first  # AC-6.3 with the real model
```
- [ ] Pull the local model: `ollama pull qwen2.5:7b`, which is about 4.7 GB. Ask before running.
- [ ] Run `uv run pytest -m ollama -v`. Expect PASS. If validation fails on all 3 attempts, the error list shows which rule the model keeps breaking. Tighten the matching instruction in `build_prompt` and add a unit-test assertion for the new prompt text before re-running.
- [ ] Run the full suite: `uv run pytest -v`. Expect all PASS.
- [ ] Add to `CLAUDE.md` under Commands: `uv sync --inexact`, `uv run pytest`, `uv run pytest -m ollama`, `uv run python -m video_pipeline.generate_script [--book PATH] [--mode cloud] [--seed N] [--force]`. Also note that generated scripts land in `episodes/<slug>/` and are hand-editable. In `.claude/rules/data-pipeline.md`, add: "✓ `episodes/<slug>/episode_script.json` is generated but hand-editable; regenerating requires `--force`."
- [ ] Human review (AC-7.4): read the generated pilot script for age-suitability, spoilers and invented events.
- [ ] Commit: `feat(script): complete episode script generator (spec 001)`

---

## Quickstart Validation

```bash
uv sync --inexact
ollama pull qwen2.5:7b
uv run python -m video_pipeline.generate_script            # → wrote episodes/the-snail-mail-express/episode_script.json + review notice
uv run python -m video_pipeline.generate_script            # → error: … already exists; re-run with --force (exit 1, file unchanged)
uv run python -m video_pipeline.generate_script --force    # → identical file (same seed)
uv run python -m video_pipeline.generate_script --force --seed 8   # → a different, still-valid take
GEMINI_API_KEY=… uv run python -m video_pipeline.generate_script --force --mode cloud   # opt-in cloud
uv run pytest -v && uv run pytest -m ollama -v
```

## Spec coverage

| Spec item | Phase(s) |
|---|---|
| FR-1 Input | 2 (load_book, slugify), 5 (CLI `--book`) |
| FR-2 Contract | 1 (Banter), 3 (validate_script), 0 (config/cast.json) |
| FR-3 Narration | 2 (split_narration), 3 |
| FR-4 Q&A moments | 2 (schedule, speakers), 3, 4 (prompt), 5 (banter_plan_errors in loop) |
| FR-5 Tags | 1, 3 |
| FR-6 Camera | 2 (motions, zoom_target), 3 |
| FR-7 Modes, reproducibility, re-runs, retries | 4, 5 |
| NFR Performance / Security / Reliability / Cost | 6, 1+3+4, 5, 4+5 |
| Error Scenarios table | 2 (book errors), 4 (backend errors), 5 (terms, overwrite, retries, atomic write) |

## Plan Changelog

| Version | Phase | Change |
|---------|-------|--------|
| 2.0.0 | Phase 4 | Partly invalidated: local Ollama backend removed; Gemini API backend and prompt builder kept |
| 2.0.0 | Phase 5 | Partly invalidated: provider selection and retry loop to be rewritten for agent providers (FR-7) |
| 2.0.0 | Phase 6 | Invalidated: replaced by the provider and episode-tools phases (FR-7, FR-8), to be planned with sdd-plan |
