"""Shared test helpers: fixture paths and a valid stub of the model's banter."""

import json
from pathlib import Path

from video_pipeline.contract import Banter
from video_pipeline.episode import EpisodePlan

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_BOOK = FIXTURES / "mini_book.json"
PILOT_BOOK = ROOT / "book_data.json"
CAST_PATH = ROOT / "config" / "cast.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
