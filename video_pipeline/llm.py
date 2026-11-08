"""Text-model backends: local Ollama (default) and opt-in cloud Gemini. One request per call."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from video_pipeline.contract import Banter, normalize_ws
from video_pipeline.episode import EpisodePlan

LOCAL_MODEL = "qwen2.5:7b"
OLLAMA_URL = "http://127.0.0.1:11434"
CLOUD_MODEL = "gemini-2.5-flash"
GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 300

_WHO = {
    ("kid_older",): "kid_older only",
    ("kid_younger",): "kid_younger only",
    ("kid_older", "kid_younger"): "both kid_older and kid_younger",
}


class LLMError(Exception):
    """The model request failed or returned something unusable."""


class LLMUnavailable(Exception):
    """The selected backend cannot be used: not running, model missing, or no credentials."""


def build_brief(book: dict, cast: dict, plan: EpisodePlan) -> str:
    """Everything a writer needs: book, cast, the planned Q&A moments and the rules (no reply format)."""
    meta = book["metadata"]
    title = meta["title"]
    cast_lines = "\n".join(f"- {role}: {c['name']}, {c['description']}" for role, c in cast.items())
    pages = "\n\n".join(
        f"PAGE {p['page_number']}\nTEXT: {normalize_ws(p['text'])}\n"
        f"ILLUSTRATION: {normalize_ws(p['prompt'])}"
        for p in book["pages"]
    )
    by_number = {p["page_number"]: p for p in book["pages"]}
    moments = "\n".join(
        f"- page {number}: {_WHO[tuple(sorted(kids))]} (grounding from PAGE {number} only)\n"
        f"     PAGE {number} TEXT: {normalize_ws(by_number[number]['text'])}\n"
        f"     PAGE {number} ILLUSTRATION: {normalize_ws(by_number[number]['prompt'])}"
        for number, kids in sorted(plan.qa_speakers.items())
    )
    return f"""You write the spoken banter for a cozy read-aloud storytime video of the children's \
book "{title}" ({meta.get("age_range", "young children")}). The Storyteller reads every page \
aloud; you write only the extra dialogue described below.

CAST
{cast_lines}

BOOK
{pages}

WRITE
1. "intro": 2-4 lines before the book opens, with at least one kid line and one storyteller line. \
One line must say the exact title "{title}".
2. "moments": exactly one Q&A moment for each page below, spoken right after that page is read:
{moments}
   Each moment has 2-5 lines. Only the listed kids speak (when both are listed, both speak). \
The last line is the storyteller answering.
   kid_older asks curious questions about the plot or guesses at mysteries. kid_younger reacts \
to funny or cute details in the illustration.
   "grounding" is 2-6 consecutive words copied character for character from that page's \
TEXT or ILLUSTRATION, pointing at what the moment is about. It is checked by exact text search, \
so never summarize, rename or write your own label. Copy it from the PAGE block with the \
same number as the moment: a phrase found only on another page is wrong, even a character name. \
Example: if a TEXT says "the little fox hid \
under a mossy log", then "under a mossy log" is right and "fox hiding" is wrong.
   Use only what has happened up to that page. Never reveal later pages.
3. "outro": 3-5 lines after the book closes, with at least one kid line. The last line is the \
storyteller inviting viewers to subscribe and come back for the next story.

RULES
- Kid lines are at most 25 words.
- The only allowed inline tags are <giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>. No other \
tags, markup or stage directions.
- Everything is gentle and suitable for young children. Do not invent story events that are \
not in the book.
"""


def build_prompt(
    book: dict, cast: dict, plan: EpisodePlan, feedback: list[str] | None = None
) -> str:
    """One-shot prompt (Gemini API): the brief plus the JSON reply format and any feedback."""
    prompt = (
        build_brief(book, cast, plan)
        + "\nREPLY\nReply with JSON only, matching this JSON schema:\n"
        + json.dumps(Banter.model_json_schema())
        + "\n"
    )
    if feedback:
        problems = "\n".join(f"- {error}" for error in feedback)
        prompt += f"\nYOUR PREVIOUS ANSWER WAS REJECTED. Fix these problems:\n{problems}\n"
    return prompt


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def generate_local(
    prompt: str, seed: int, model: str = LOCAL_MODEL, base_url: str = OLLAMA_URL
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "format": Banter.model_json_schema(),
        "stream": False,
        "options": {"seed": seed, "temperature": 0},
        "keep_alive": 0,
    }
    try:
        data = _post_json(f"{base_url}/api/chat", payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LLMUnavailable(
                f"local model '{model}' is not installed; run: ollama pull {model}"
            ) from None
        raise LLMError(f"local model request failed: HTTP {exc.code}") from None
    except TimeoutError:
        raise LLMError(f"local model did not answer within {TIMEOUT_S} s") from None
    except (urllib.error.URLError, ConnectionError):
        raise LLMUnavailable(
            f"local model server is not reachable at {base_url}; "
            "start Ollama (open the app or run: ollama serve)"
        ) from None
    try:
        return data["message"]["content"]
    except (KeyError, TypeError):
        raise LLMError("local model returned an unexpected response") from None


def generate_cloud(prompt: str, seed: int, model: str = CLOUD_MODEL) -> str:
    key = os.environ.get(GEMINI_KEY_ENV)
    if not key:
        raise LLMUnavailable(f"cloud mode needs the {GEMINI_KEY_ENV} environment variable")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "seed": seed,
        },
    }
    try:
        data = _post_json(GEMINI_URL.format(model=model), payload, {"x-goog-api-key": key})
    except urllib.error.HTTPError as exc:
        raise LLMError(f"cloud model request failed: HTTP {exc.code}") from None
    except TimeoutError:
        raise LLMError(f"cloud model did not answer within {TIMEOUT_S} s") from None
    except (urllib.error.URLError, ConnectionError):
        raise LLMUnavailable("cloud model is not reachable; check the network connection") from None
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("cloud model returned an unexpected response") from None
