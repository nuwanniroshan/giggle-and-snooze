"""Spec 011 Phase 5: the whole blueprint flow with fakes for Codex, the painter and the agent,
and the legacy book (no blueprint) unchanged (AC-7.1, AC-7.2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.blueprint_helpers import valid_blueprint
from tests.helpers import CAST_PATH, PILOT_BOOK, ROOT, load_json, make_banter, make_root
from tests.test_story_tools import book, idea
from tests.test_studio_app import Studio
from tests.test_write_story import FakeCodex, use
from video_pipeline import generate_script, illustrate
from video_pipeline import write_story as ws
from video_pipeline.blueprint import blueprint_sha256, read_use
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.drafts import DraftStore
from video_pipeline.episode import plan_episode
from video_pipeline.episode_tools import CONFIG_DIR, EpisodeTools, write_atomic
from video_pipeline.file_hash import script_sha256
from video_pipeline.library import BookFolder
from video_pipeline.llm import build_brief
from video_pipeline.motion_plan import PlanOutcome
from video_pipeline.studio import books as sb


@pytest.fixture
def root(tmp_path: Path) -> Path:
    make_root(tmp_path)  # real config/
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "sound_library").symlink_to(ROOT / "assets" / "sound_library")
    # make_root also added a book folder; the flow below creates its own
    import shutil

    shutil.rmtree(tmp_path / "books")
    return tmp_path


def test_design_story_book_pictures_script_then_edit_marks_stale(root, monkeypatch):
    store = DraftStore(root)
    draft = store.new_draft(idea(), 8, "Test Author")
    use(monkeypatch, FakeCodex([valid_blueprint(), book(8)]))
    assert ws.run_blueprint(root, draft_id=draft["id"], then_book=True) == 0
    slug = store.create_book(draft["id"])
    folder = BookFolder(slug, root)
    bp_data = valid_blueprint()
    assert json.loads(folder.blueprint_path.read_text(encoding="utf-8")) == bp_data
    prefix = bp_data["art"]["style_prompt_prefix"]
    data = json.loads(folder.book_json.read_text(encoding="utf-8"))
    assert data["style_bible"]["style_prompt_prefix"] == prefix

    # pictures: fake painter records every prompt
    prompts: list[str] = []

    def paint(binary, prompt, reference, target):
        prompts.append(prompt)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), "green").save(target)

    monkeypatch.setattr(illustrate, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(illustrate, "paint_one", paint)
    monkeypatch.setattr(
        illustrate, "plan_page", lambda *a, **k: PlanOutcome(a[1]["page_number"], "written", "")
    )
    assert illustrate.main(["--book", slug, "--root", str(root)]) == 0
    assert prompts and all(prefix in p and "#1b2a49" in p for p in prompts)

    # script: fake agent
    def fake_generate(prompt, seed):
        tools = EpisodeTools.load(folder, 7, root / "config")
        return make_banter(tools.book, tools.plan).model_dump_json()

    monkeypatch.setattr(generate_script, "generate_cloud", lambda p, s, m: fake_generate(p, s))
    code = generate_script.main(["--book", slug, "--root", str(root), "--provider", "gemini-api"])
    assert code == 0
    script = json.loads(folder.script_path.read_text(encoding="utf-8"))
    terms = load_disallowed_terms(CONFIG_DIR / "disallowed_terms.txt")
    palette = set(bp_data["sound"]["ambience"])
    assert validate_script(script, data, terms, allowed_ambience=palette) == []
    assert script["intro"]["ambience"] and set(script["intro"]["ambience"]) <= palette
    assert all(set(p["ambience"]) <= palette and p["ambience"] for p in script["pages"])

    sha = blueprint_sha256(folder.blueprint_path)
    used = read_use(folder)
    assert used["art"] == sha and used["script"] == sha

    paths = sb.StudioPaths(root)
    studio_book = sb.get_book(paths, slug)
    assert sb.blueprint_status(paths, studio_book)["stale"] == []
    studio = Studio(root)
    status_before = sb.stage_status(paths, studio_book, studio.runner.latest)
    script_hash = script_sha256(folder.script_path)

    edited = valid_blueprint(**{"mood.summary": "Slow, hushed and very dreamy."})
    write_atomic(folder.blueprint_path, edited)
    info = sb.blueprint_status(paths, studio_book)
    assert [m["output"] for m in info["stale"]] == ["pictures", "episode script"]
    assert sb.stage_status(paths, studio_book, studio.runner.latest) == status_before
    assert script_sha256(folder.script_path) == script_hash


def test_legacy_pilot_golden_values_and_no_panel(tmp_path):  # AC-7.1, AC-7.2
    from video_pipeline.illustrate import plan_images

    golden = load_json(ROOT / "tests" / "fixtures" / "legacy_golden.json")
    book20 = load_json(PILOT_BOOK)
    cast = load_json(CAST_PATH)
    plan = plan_episode(book20, golden["seed"])
    assert {str(k): list(v) for k, v in plan.qa_speakers.items()} == golden["plan"]["qa_speakers"]
    assert list(plan.motions) == golden["plan"]["motions"]
    assert plan.notes == ()  # the only difference from the stored repr: spec 011's empty notes
    brief = build_brief(book20, cast, plan)
    assert hashlib.sha256(brief.encode("utf-8")).hexdigest() == golden["brief_sha256"]
    assert [list(x) for x in plan_images(book20)] == golden["image_plan"]

    folder = BookFolder("the-snail-mail-express", ROOT)
    assert not folder.blueprint_path.exists()
    info = sb.blueprint_status(sb.StudioPaths(ROOT), sb.get_book(sb.StudioPaths(ROOT), folder.slug))
    assert info == {"present": False, "error": None, "view": None, "stale": []}
