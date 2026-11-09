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
