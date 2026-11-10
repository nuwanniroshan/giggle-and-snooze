"""Shared test helpers: fixture paths and a valid stub of the model's banter."""

import json
import shutil
from pathlib import Path

from video_pipeline.contract import Banter, slugify
from video_pipeline.episode import EpisodePlan
from video_pipeline.episode_tools import CONFIG_DIR
from video_pipeline.library import BookFolder

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_BOOK = FIXTURES / "mini_book.json"
PILOT_BOOK = ROOT / "books" / "the-snail-mail-express" / "book.json"
CAST_PATH = ROOT / "config" / "cast.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_root(root: Path, book_path: Path = MINI_BOOK, slug: str | None = None) -> BookFolder:
    """A project root with the shared config and one book folder at books/<slug>/ (book.json)."""
    if not (root / "config").exists():
        shutil.copytree(CONFIG_DIR, root / "config")
    slug = slug or slugify(load_json(book_path)["metadata"]["title"])
    folder = BookFolder(slug, root)
    folder.folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(book_path, folder.book_json)
    return folder


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


def plan_signature(script: dict) -> list[tuple]:
    """Everything the seed decides on each page (AC-6.3): Q&A placement, kids, narration, motion."""
    return [
        (
            page["page_number"],
            page["has_qa"],
            page["ffmpeg_camera"]["narration_motion"],
            tuple(line["text"] for line in page["lines"] if line["role"] == "narration"),
            tuple(
                sorted(
                    {
                        line["speaker"]
                        for line in page["lines"]
                        if line["role"] != "narration" and line["speaker"] != "storyteller"
                    }
                )
            ),
        )
        for page in script["pages"]
    ]


def add_book_from_draft(root: Path, title: str = "The Moon Kite Race", pages: int = 8) -> str:
    """Add a book the way the studio does now (spec 006): a ready draft, then create_book."""
    from tests.test_story_tools import book as book_payload
    from tests.test_story_tools import idea as idea_payload
    from video_pipeline.drafts import DraftStore

    store = DraftStore(Path(root))
    draft = store.new_draft(idea_payload(title), pages=pages, author="Test Author")
    payload = book_payload(pages)
    payload["metadata"]["title"] = title
    store.update(draft["id"], state="ready", book=payload, attempts=1, problems=[])
    return store.create_book(draft["id"])
