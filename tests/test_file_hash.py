"""The stat-keyed sha256 cache used by status checks (spec 004 review fixes, round 1)."""

import hashlib
import os

from video_pipeline import file_hash as fh
from video_pipeline import synthesize_voices as sv


def counting(monkeypatch):
    calls = []
    real = fh._digest

    def digest(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(fh, "_digest", digest)
    return calls


def test_hash_is_cached_while_the_file_is_unchanged(tmp_path, monkeypatch):
    calls = counting(monkeypatch)
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert fh.sha256_file(path) == expected
    assert fh.sha256_file(path) == expected
    assert calls == [path]


def test_hash_is_recomputed_when_size_or_mtime_changes(tmp_path, monkeypatch):
    calls = counting(monkeypatch)
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    fh.sha256_file(path)
    path.write_bytes(b"hello, world")  # new size
    assert fh.sha256_file(path) == hashlib.sha256(b"hello, world").hexdigest()
    stat = path.stat()
    path.write_bytes(b"HELLO, WORLD")  # same size, new mtime
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert fh.sha256_file(path) == hashlib.sha256(b"HELLO, WORLD").hexdigest()
    assert len(calls) == 3


def test_replaced_file_is_rehashed(tmp_path):
    path, other = tmp_path / "a.bin", tmp_path / "b.bin"
    path.write_bytes(b"aaaa")
    fh.sha256_file(path)
    other.write_bytes(b"bbbb")
    stat = path.stat()
    os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    other.replace(path)  # same size and mtime, new inode
    assert fh.sha256_file(path) == hashlib.sha256(b"bbbb").hexdigest()


def test_missing_file_raises(tmp_path):
    try:
        fh.sha256_file(tmp_path / "nope")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")


def test_pipeline_uses_the_cached_hash():
    assert sv.sha256_file is fh.sha256_file


def test_file_written_while_it_is_hashed_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / "busy.bin"
    path.write_bytes(b"aaaa")
    real = fh._digest

    def digest_during_a_write(p):
        result = real(p)
        path.write_bytes(b"bbbb")  # a writer lands after the bytes were read
        return result

    monkeypatch.setattr(fh, "_digest", digest_during_a_write)
    fh.sha256_file(path)
    key = os.fspath(path.absolute())
    assert not [k for k in fh._cache if k[0] == key]  # neither the old nor the new stat cached
    monkeypatch.setattr(fh, "_digest", real)
    assert fh.sha256_file(path) == hashlib.sha256(b"bbbb").hexdigest()


# --- script_sha256: ambience edits do not change the script hash (spec 007) -------------------

import json  # noqa: E402

import pytest  # noqa: E402

from video_pipeline.episode_tools import write_atomic  # noqa: E402


def _script() -> dict:
    return {
        "title": "T",
        "intro": {"lines": [{"speaker": "Storyteller", "text": "Hello"}]},
        "pages": [
            {"page_number": 1, "lines": [{"speaker": "Storyteller", "text": "One"}]},
            {"page_number": 2, "lines": [{"speaker": "Storyteller", "text": "Two"}]},
        ],
        "outro": {"lines": [{"speaker": "Storyteller", "text": "Bye"}]},
    }


def _write(tmp_path, data):
    path = tmp_path / "episode_script.json"
    write_atomic(path, data)
    return path


def test_script_hash_equals_file_hash_without_ambience(tmp_path):
    path = _write(tmp_path, _script())
    assert fh.script_sha256(path) == fh.sha256_file(path)


@pytest.mark.parametrize("where", ["intro", "outro", 0, 1])
def test_adding_ambience_keeps_the_script_hash(tmp_path, where):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    block = data[where] if isinstance(where, str) else data["pages"][where]
    block["ambience"] = ["rain", "wind"]
    write_atomic(path, data)
    assert fh.sha256_file(path) != before  # the raw bytes did change
    assert fh.script_sha256(path) == before


def test_ambience_on_every_block_and_edits_to_it_keep_the_hash(tmp_path):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    for block in (data["intro"], data["outro"], *data["pages"]):
        block["ambience"] = ["rain"]
    write_atomic(path, data)
    first = fh.script_sha256(path)
    data["pages"][0]["ambience"] = []
    write_atomic(path, data)
    assert first == before == fh.script_sha256(path)


def test_changing_text_changes_the_script_hash_even_with_ambience(tmp_path):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    data["pages"][1]["ambience"] = ["rain"]
    data["pages"][0]["lines"][0]["text"] = "Uno"
    write_atomic(path, data)
    assert fh.script_sha256(path) != before


def test_invalid_json_falls_back_to_the_raw_file_hash(tmp_path):
    path = tmp_path / "episode_script.json"
    path.write_text('{"ambience": [', encoding="utf-8")
    assert fh.script_sha256(path) == fh.sha256_file(path)
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert fh.script_sha256(path) == fh.sha256_file(path)


def test_a_script_saved_without_a_trailing_newline_also_ignores_ambience(tmp_path):
    path = tmp_path / "episode_script.json"
    path.write_text(json.dumps(_script(), indent=2, ensure_ascii=False), encoding="utf-8")
    before = fh.script_sha256(path)
    data = _script()
    data["intro"]["ambience"] = ["rain"]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    assert fh.script_sha256(path) == before


# --- script_sha256: the page animation override is ignored too (spec 010 FR-10) ---------------


def test_excluded_block_keys():
    assert fh.EXCLUDED_BLOCK_KEYS == ("ambience", "animation")


@pytest.mark.parametrize("value", [False, 1.5, 0.25])
def test_adding_changing_and_removing_animation_keeps_the_script_hash(tmp_path, value):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    data["pages"][0]["animation"] = value
    write_atomic(path, data)
    assert fh.sha256_file(path) != before
    assert fh.script_sha256(path) == before
    data["pages"][0]["animation"] = 2.0
    data["pages"][1]["animation"] = False
    write_atomic(path, data)
    assert fh.script_sha256(path) == before
    del data["pages"][0]["animation"]
    write_atomic(path, data)
    assert fh.script_sha256(path) == before
    del data["pages"][1]["animation"]
    write_atomic(path, data)
    assert fh.script_sha256(path) == fh.sha256_file(path) == before


def test_animation_and_ambience_together_keep_the_hash(tmp_path):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    data["intro"]["ambience"] = ["rain"]
    data["pages"][1]["animation"] = 0.5
    data["pages"][1]["ambience"] = ["wind"]
    write_atomic(path, data)
    assert fh.script_sha256(path) == before


def test_text_change_with_animation_changes_the_hash(tmp_path):
    path = _write(tmp_path, _script())
    before = fh.script_sha256(path)
    data = _script()
    data["pages"][1]["animation"] = False
    data["pages"][1]["lines"][0]["text"] = "Deux"
    write_atomic(path, data)
    assert fh.script_sha256(path) != before
