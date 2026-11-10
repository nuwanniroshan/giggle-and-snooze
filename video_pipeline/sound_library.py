"""Spec 007: the CC0 sound library (`assets/sound_library/`), its index, `list` and `check`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac"}
INDEX = "index.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "assets" / "sound_library"


class LibraryError(Exception):
    """The sound library index is missing or invalid, or a sound is unknown."""


class _Entry(BaseModel):
    file: str
    title: str
    author: str
    url: str
    kind: Literal["ambience", "music", "sfx"]
    seconds: float
    license: str | None = None


class _Index(BaseModel):
    license: str
    sounds: dict[str, _Entry]


@dataclass(frozen=True)
class Sound:
    name: str
    file: Path
    kind: str
    seconds: float
    author: str
    license: str


@dataclass(frozen=True)
class Library:
    root: Path
    sounds: dict[str, Sound]

    def get(self, name: str) -> Sound:
        try:
            return self.sounds[name.lower()]
        except KeyError:
            raise LibraryError(f"unknown sound {name!r}") from None


def load_library(root: Path) -> Library:
    """Read and validate `<root>/index.json`; fail fast naming the file and field."""
    root = Path(root)
    path = root / INDEX
    try:
        index = _Index.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"sound library index not found: {path}") from None
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LibraryError(f"{path}: {exc}") from None
    sounds = {
        name.lower(): Sound(
            name.lower(),
            root / entry.file,
            entry.kind,
            entry.seconds,
            entry.author,
            entry.license or index.license,
        )
        for name, entry in index.sounds.items()
    }
    return Library(root, sounds)


def check_library(root: Path) -> list[str]:
    """Return the library's problems: bad index, missing files, orphans, non-CC0 licences."""
    try:
        lib = load_library(root)
    except LibraryError as exc:
        return [str(exc)]
    problems = [
        f"{s.name}: file {s.file.name} is missing"
        for s in lib.sounds.values()
        if not s.file.is_file()
    ]
    problems += [
        f"{s.name}: license is not CC0 ({s.license})"
        for s in lib.sounds.values()
        if "CC0" not in s.license
    ]
    listed = {s.file.name for s in lib.sounds.values()}
    problems += [
        f"{p.name}: audio file has no index entry"
        for p in sorted(Path(root).iterdir())
        if p.suffix.lower() in AUDIO_SUFFIXES and p.name not in listed
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sound_library")
    parser.add_argument("command", choices=["list", "check"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    if args.command == "check":
        problems = check_library(args.root)
        for line in problems:
            print(line)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    try:
        lib = load_library(args.root)
    except LibraryError as exc:
        print(exc, file=sys.stderr)
        return 1
    for s in sorted(lib.sounds.values(), key=lambda s: (s.kind, s.name)):
        print(f"{s.name:18} {s.kind:9} {s.seconds:7.1f}s  {s.author}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
