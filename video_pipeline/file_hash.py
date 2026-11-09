"""sha256 of a file, cached by its stat (spec 004 review fixes, round 1).

The studio recomputes every stage's state on each page view, which hashes the same pictures,
tracks and records again and again. A file whose path, inode, size and modification time (ns) are
unchanged is taken to have unchanged bytes; an atomic replace gives a new inode, any write a new
mtime. A file whose stat changes while it is being hashed (a writer was busy) gets its digest
returned but not cached. The cache is per process and bounded.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

MAX_ENTRIES = 4096
_cache: dict[tuple[str, int, int, int], str] = {}
_lock = threading.Lock()


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _key(path: Path) -> tuple[str, int, int, int]:
    stat = os.stat(path)  # noqa: PTH116 - one stat call for the whole key
    return (os.fspath(Path(path).absolute()), stat.st_ino, stat.st_size, stat.st_mtime_ns)


def sha256_file(path: Path) -> str:
    """Hex sha256 of the file at `path` (FileNotFoundError when it is missing)."""
    key = _key(path)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    digest = _digest(path)
    try:
        unchanged = _key(path) == key
    except OSError:
        unchanged = False
    if not unchanged:
        return digest
    with _lock:
        if len(_cache) >= MAX_ENTRIES:
            _cache.clear()
        _cache[key] = digest
    return digest
