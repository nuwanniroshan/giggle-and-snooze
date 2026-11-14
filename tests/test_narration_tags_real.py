"""Real Codex run: narration tags are valid, within limits and keep the book text (spec 028).

Run with: uv run pytest -m codex tests/test_narration_tags_real.py -v -s"""

import math
import re

import pytest

from tests.helpers import MINI_BOOK, load_json, make_root
from video_pipeline.contract import ALLOWED_TAGS
from video_pipeline.generate_script import main

pytestmark = pytest.mark.codex

TAG_RE = re.compile(r"<(\w+)>")


def test_real_codex_narration_tags_are_valid_and_keep_the_text(tmp_path):
    folder = make_root(tmp_path, MINI_BOOK)
    assert main(["--book", folder.slug, "--root", str(tmp_path)]) == 0
    script = load_json(folder.script_path)
    book = load_json(MINI_BOOK)
    pages = {p["page_number"]: p["text"] for p in book["pages"]}
    total = 0
    for page in script["pages"]:
        narration = [ln["text"] for ln in page["lines"] if ln["role"] == "narration"]
        tags = [t for text in narration for t in TAG_RE.findall(text)]
        assert set(tags) <= set(ALLOWED_TAGS)
        assert len(tags) <= 1, f"page {page['page_number']}"
        total += len(tags)
        stripped = " ".join(TAG_RE.sub("", " ".join(narration)).split())
        assert stripped == " ".join(pages[page["page_number"]].split())
    assert total <= math.ceil(0.5 * len(script["pages"]))
