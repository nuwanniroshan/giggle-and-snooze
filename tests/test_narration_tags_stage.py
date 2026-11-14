"""Narration tags and the later stages (spec 028 AC-3.3, AC-3.4)."""

import json

from tests.helpers import MINI_BOOK, load_json, make_banter
from tests.voice_helpers import VOICES_PATH
from video_pipeline import episode
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import Banter
from video_pipeline.episode_tools import CONFIG_DIR
from video_pipeline.file_hash import script_sha256

CFG = sv.load_voice_config(VOICES_PATH)
TAG = {"page_number": 1, "sentence": 1, "position": "start", "tag": "gasp"}


def build(narration_tags):
    book = load_json(MINI_BOOK)
    plan = episode.plan_episode(book, 7)
    data = make_banter(book, plan).model_dump(exclude_none=True)
    data["narration_tags"] = narration_tags
    cast = load_json(CONFIG_DIR / "cast.json")
    return episode.assemble_script(book, cast, plan, Banter.model_validate(data))


def write(path, script):
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_a_narration_tag_changes_the_script_hash(tmp_path):  # AC-3.3
    plain = write(tmp_path / "plain.json", build([]))
    tagged = write(tmp_path / "tagged.json", build([TAG]))
    assert script_sha256(plain) != script_sha256(tagged)


def test_only_the_tagged_narration_line_gets_a_new_clip_key():  # AC-3.4
    plain = {j.line_id: j for j in sv.plan_jobs(build([]), CFG, {})}
    tagged = {j.line_id: j for j in sv.plan_jobs(build([TAG]), CFG, {})}
    assert plain.keys() == tagged.keys()
    changed = [lid for lid in plain if plain[lid].key != tagged[lid].key]
    assert len(changed) == 1
    assert plain[changed[0]].role == "narration"
    assert "<gasp>" in tagged[changed[0]].spoken
    assert all(plain[lid].spoken == tagged[lid].spoken for lid in plain if lid not in changed)
