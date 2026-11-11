"""A mini book world for the render tests: approved audio (spec 002 with the fake voice), small
synthetic pictures and a shrunken render config, all under tmp_path. FFmpeg is replaced by
`FakeRunner`, which writes a placeholder file to each command's output path."""

import json
import shutil
from pathlib import Path

from PIL import Image

from tests.helpers import load_json
from tests.voice_helpers import FakeEngine, identity_shift, make_script, write_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.contract import slugify
from video_pipeline.episode_tools import CONFIG_DIR
from video_pipeline.library import BookFolder


def picture(path: Path, colour=(120, 60, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (64, 64), colour)
    im.paste((230, 210, 40), (8, 8, 24, 24))
    im.save(path)
    return path


def illustration_name(page: dict) -> str:
    return page.get("illustration_file") or f"page_{int(page['page_number']):02d}.png"


def _shrink_render_config(path: Path) -> None:
    cfg = load_json(path)
    cfg.update(width=320, height=180, fps=10, upscale=2)
    cfg["captions"].update(font_size=16, margin=8, padding=6)
    cfg["backdrop"]["blur"] = 4
    cfg["video"]["preset"] = "ultrafast"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def make_world(tmp_path: Path, monkeypatch, book_path: Path, approve: bool = True) -> dict:
    """A project root under tmp_path with one book folder (books/<slug>/) whose audio is
    synthesized (fake voice) and optionally approved. Shared config and assets sit at the root."""
    book = load_json(book_path)
    slug = slugify(book["metadata"]["title"])
    root = tmp_path
    folder = BookFolder(slug, root)
    config, assets = root / "config", root / "assets"
    if not config.exists():
        shutil.copytree(CONFIG_DIR, config)
        _shrink_render_config(config / "render.json")
    folder.folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(book_path, folder.book_json)
    for index, page in enumerate(book["pages"]):
        picture(folder.illustrations / illustration_name(page), (40 + 9 * index, 80, 120))
    picture(folder.illustrations / "cover_front.png", (200, 40, 40))
    picture(folder.illustrations / "cover_back.png", (40, 200, 40))
    script_path = write_script(make_script(book_path), folder)
    monkeypatch.setattr(sv, "create_engine", lambda cfg: FakeEngine())
    monkeypatch.setattr(sv, "pitch_shift", identity_shift)
    common = ["--book", slug, "--root", str(root)]
    assert sv.main(["--reviewed", *common]) == 0
    if approve:
        assert sv.main(["approve", *common]) == 0
    return {
        "book": folder.book_json,
        "slug": slug,
        "root": root,
        "folder": folder,
        "config": config,
        "assets": assets,
        "build": root / "build",
        "script_path": script_path,
        "audio": folder.build_dir / "audio",
        "video": folder.build_dir / "video",
        "final": folder.build_dir / "video" / f"{slug}.mp4",
    }


def cli_args(world: dict, *extra: str) -> list[str]:
    return ["--book", world["slug"], "--root", str(world["root"]), *extra]


class FakeRunner:
    """Stands in for `run_ffmpeg`: records (what, cmd) and writes the output (last argument).

    Frame grabs get a tiny valid PNG; other outputs get placeholder bytes. `fail_on` makes the
    command whose `what` equals it raise RenderError instead.
    """

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail_on = fail_on

    def __call__(self, cmd: list[str], what: str) -> None:
        from video_pipeline.render_episode import RenderError

        self.calls.append((what, cmd))
        if what == self.fail_on:
            raise RenderError(f"{what}: simulated ffmpeg failure")
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".png":
            Image.new("RGB", (8, 8), (10, 20, 30)).save(out, format="PNG")
        else:
            out.write_bytes(b"fake " + what.encode())

    def whats(self, prefix: str = "") -> list[str]:
        return [what for what, _cmd in self.calls if what.startswith(prefix)]


class FakeFrameRunner:
    """Stands in for `run_ffmpeg_frames`: consumes every frame, records (what, cmd, frame count,
    distinct frame sizes) and writes placeholder bytes to the output (last argument)."""

    def __init__(self):
        self.calls: list[tuple[str, list[str], int, set[int]]] = []

    def __call__(self, cmd: list[str], frames, what: str) -> None:
        count, sizes = 0, set()
        for frame in frames:
            count += 1
            sizes.add(len(frame))
        self.calls.append((what, cmd, count, sizes))
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake animated " + what.encode())

    def whats(self, prefix: str = "") -> list[str]:
        return [call[0] for call in self.calls if call[0].startswith(prefix)]
