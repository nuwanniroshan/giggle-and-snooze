"""sha256 of a file, cached by its stat (spec 004 review fixes, round 1).

The studio recomputes every stage's state on each page view, which hashes the same pictures,
tracks and records again and again. A file whose path, inode, size and modification time (ns) are
unchanged is taken to have unchanged bytes; an atomic replace gives a new inode, any write a new
mtime. A file whose stat changes while it is being hashed (a writer was busy) gets its digest
returned but not cached. The cache is per process and bounded.
"""

from __future__ import annotations

import hashlib
import json
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


def script_sha256(path: Path) -> str:
    """Hash of an episode script that ignores the optional `ambience` lists (spec 007).

    Ambience does not affect the voices, so adding or editing it must not invalidate the audio
    approval or the review mark. A script without any `ambience` key (or one that cannot be
    parsed) hashes as its raw file bytes, so existing approvals stay valid. Otherwise the hash is
    of the script without those keys, serialized exactly as `episode_tools.write_atomic` writes
    it (indent 2, no ASCII escaping, the file's trailing newline), so a file written by that writer hashes
    the same before and after ambience is added.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return sha256_file(path)
    if not isinstance(data, dict):
        return sha256_file(path)
    blocks = [data.get("intro"), data.get("outro")]
    pages = data.get("pages")
    if isinstance(pages, list):
        blocks.extend(pages)
    blocks = [b for b in blocks if isinstance(b, dict)]
    if not any("ambience" in b for b in blocks):
        return sha256_file(path)
    for block in blocks:
        block.pop("ambience", None)
    # keep the file's own trailing newline (or lack of one), so a hand-saved script without it
    # also hashes the same before and after ambience is added
    text = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if raw.endswith("\n") else "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
