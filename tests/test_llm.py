import io
import json
import urllib.error
import urllib.request

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, load_json
from video_pipeline.contract import normalize_ws
from video_pipeline.episode import plan_episode
from video_pipeline.llm import (
    GEMINI_KEY_ENV,
    LLMError,
    LLMUnavailable,
    build_brief,
    build_prompt,
    generate_cloud,
    generate_local,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def fake_urlopen(monkeypatch, payload=None, exc=None):
    requests = []

    def fake(request, timeout=None):
        requests.append(request)
        if exc is not None:
            raise exc
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return requests


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_build_prompt_lists_pages_plan_and_feedback():
    book = load_json(MINI_BOOK)
    plan = plan_episode(book, 7)
    prompt = build_prompt(book, load_json(CAST_PATH), plan, feedback=["intro too short"])
    assert '"The Lantern Bug Parade"' in prompt
    assert all(f"PAGE {p['page_number']}" in prompt for p in book["pages"])
    for number in plan.qa_speakers:
        assert f"- page {number}:" in prompt
    assert "REJECTED" in prompt and "- intro too short" in prompt
    assert "Leo (Age 8)" in prompt and "Mia (Age 5)" in prompt


def test_build_prompt_demands_verbatim_grounding():
    book = load_json(MINI_BOOK)
    prompt = build_prompt(book, load_json(CAST_PATH), plan_episode(book, 7))
    assert "checked by exact text search" in prompt
    assert "never summarize, rename or write your own label" in prompt
    assert "a phrase found only on another page is wrong" in prompt
    pages = {p["page_number"]: p for p in book["pages"]}
    for number in plan_episode(book, 7).qa_speakers:
        assert f"grounding from PAGE {number} only" in prompt
        page = pages[number]
        assert (
            f"(grounding from PAGE {number} only)\n"
            f"     PAGE {number} TEXT: {normalize_ws(page['text'])}\n"
            f"     PAGE {number} ILLUSTRATION: {normalize_ws(page['prompt'])}"
        ) in prompt


def test_generate_local_sends_reproducible_request(monkeypatch):
    requests = fake_urlopen(monkeypatch, {"message": {"content": '{"ok": 1}'}})
    assert generate_local("hello", 9) == '{"ok": 1}'
    body = json.loads(requests[0].data)
    assert requests[0].full_url == "http://127.0.0.1:11434/api/chat"
    assert body["options"] == {"seed": 9, "temperature": 0}
    assert body["keep_alive"] == 0 and body["stream"] is False
    assert body["format"]["title"] == "Banter"


def test_generate_local_missing_model(monkeypatch):
    fake_urlopen(monkeypatch, exc=http_error(404))
    with pytest.raises(LLMUnavailable, match="ollama pull qwen2.5:7b"):
        generate_local("hello", 1)


def test_generate_local_server_down(monkeypatch):
    fake_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
    with pytest.raises(LLMUnavailable, match="not reachable"):
        generate_local("hello", 1)


def test_generate_local_bad_reply(monkeypatch):
    fake_urlopen(monkeypatch, {"unexpected": True})
    with pytest.raises(LLMError, match="unexpected response"):
        generate_local("hello", 1)


def test_generate_cloud_requires_key_and_sends_nothing_without_it(monkeypatch):
    monkeypatch.delenv(GEMINI_KEY_ENV, raising=False)
    requests = fake_urlopen(monkeypatch, {})
    with pytest.raises(LLMUnavailable, match=GEMINI_KEY_ENV):
        generate_cloud("hello", 1)
    assert requests == []


def test_generate_cloud_sends_key_in_header_only(monkeypatch):
    monkeypatch.setenv(GEMINI_KEY_ENV, "secret-key")
    reply = {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}
    requests = fake_urlopen(monkeypatch, reply)
    assert generate_cloud("hello", 3) == '{"ok": 1}'
    request = requests[0]
    assert "secret-key" not in request.full_url
    assert request.get_header("X-goog-api-key") == "secret-key"
    assert json.loads(request.data)["generationConfig"]["seed"] == 3


def test_generate_cloud_errors_never_leak_key(monkeypatch):
    monkeypatch.setenv(GEMINI_KEY_ENV, "secret-key")
    fake_urlopen(monkeypatch, exc=http_error(403))
    with pytest.raises(LLMError) as excinfo:
        generate_cloud("hello", 1)
    assert "secret-key" not in str(excinfo.value)


def test_build_brief_is_the_prompt_without_reply_format():
    book = load_json(MINI_BOOK)
    cast, plan = load_json(CAST_PATH), plan_episode(book, 7)
    brief = build_brief(book, cast, plan)
    assert "Reply with JSON only" not in brief
    assert "grounding from PAGE" in brief and "CAST" in brief and "RULES" in brief
    prompt = build_prompt(book, cast, plan)
    assert prompt.startswith(brief)
    assert "Reply with JSON only" in prompt and '"title": "Banter"' in prompt
