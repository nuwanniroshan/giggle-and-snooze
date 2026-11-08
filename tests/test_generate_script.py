import json
import shutil

import pytest

from tests.helpers import CAST_PATH, MINI_BOOK, PILOT_BOOK, load_json, make_banter
from video_pipeline import generate_script
from video_pipeline.contract import load_disallowed_terms, validate_script
from video_pipeline.episode import plan_episode
from video_pipeline.llm import LLMUnavailable

TERMS = load_disallowed_terms(generate_script.CONFIG_DIR / "disallowed_terms.txt")


class StubModel:
    """Records every request; replies with valid banter (or scripted replies)."""

    def __init__(self, book: dict, seed: int, replies: list[str] | None = None) -> None:
        self.valid = make_banter(book, plan_episode(book, seed)).model_dump_json()
        self.replies = replies
        self.calls: list[tuple[str, int]] = []

    def __call__(self, prompt: str, seed: int, model: str | None = None) -> str:
        self.calls.append((prompt, seed))
        if self.replies is not None:
            return self.replies[min(len(self.calls), len(self.replies)) - 1]
        return self.valid


def never_called(*args, **kwargs):
    raise AssertionError("this backend must not be called")


def run(monkeypatch, tmp_path, book_path=MINI_BOOK, extra=(), local=None, cloud=None):
    monkeypatch.setattr(generate_script, "generate_local", local or never_called)
    monkeypatch.setattr(generate_script, "generate_cloud", cloud or never_called)
    args = ["--book", str(book_path), "--episodes-dir", str(tmp_path / "episodes"), *extra]
    return generate_script.main(args)


def out_path(tmp_path, slug="the-lantern-bug-parade"):
    return tmp_path / "episodes" / slug / "episode_script.json"


def test_writes_valid_script_in_default_mode(monkeypatch, tmp_path):  # AC-1.1, AC-1.2, AC-1.4
    book = load_json(MINI_BOOK)
    stub = StubModel(book, 7)
    assert run(monkeypatch, tmp_path, local=stub) == 0
    script = load_json(out_path(tmp_path))
    assert validate_script(script, book, TERMS) == []
    assert len(stub.calls) == 1


def test_pilot_book_needs_no_changes(monkeypatch, tmp_path):  # AC-1.3 (two different books)
    book = load_json(PILOT_BOOK)
    assert run(monkeypatch, tmp_path, PILOT_BOOK, local=StubModel(book, 7)) == 0
    script = load_json(out_path(tmp_path, "the-snail-mail-express"))
    assert validate_script(script, book, TERMS) == []
    assert sum(p["has_qa"] for p in script["pages"]) in (7, 8)


def test_existing_script_refused_without_force(monkeypatch, tmp_path, capsys):  # AC-6.1
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"hand edited")
    assert run(monkeypatch, tmp_path) == 1
    assert target.read_bytes() == b"hand edited"
    err = capsys.readouterr().err
    assert str(target) in err and "--force" in err


def test_force_overwrites(monkeypatch, tmp_path):  # AC-6.2
    book = load_json(MINI_BOOK)
    target = out_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert run(monkeypatch, tmp_path, extra=["--force"], local=StubModel(book, 7)) == 0
    assert validate_script(load_json(target), book, TERMS) == []


def test_same_seed_is_byte_identical(monkeypatch, tmp_path):  # AC-6.3
    book = load_json(MINI_BOOK)
    assert run(monkeypatch, tmp_path, extra=["--seed", "11"], local=StubModel(book, 11)) == 0
    first = out_path(tmp_path).read_bytes()
    extra = ["--seed", "11", "--force"]
    assert run(monkeypatch, tmp_path, extra=extra, local=StubModel(book, 11)) == 0
    assert out_path(tmp_path).read_bytes() == first


def test_other_seed_still_valid(monkeypatch, tmp_path):  # AC-6.4
    book = load_json(MINI_BOOK)
    assert run(monkeypatch, tmp_path, extra=["--seed", "3"], local=StubModel(book, 3)) == 0
    assert validate_script(load_json(out_path(tmp_path)), book, TERMS) == []


def test_cloud_mode_retries_with_one_request_per_attempt(monkeypatch, tmp_path):  # AC-6.5
    book = load_json(MINI_BOOK)
    valid = StubModel(book, 7).valid
    stub = StubModel(book, 7, replies=["{}", valid])
    assert run(monkeypatch, tmp_path, extra=["--mode", "cloud"], cloud=stub) == 0
    assert len(stub.calls) == 2
    assert "REJECTED" in stub.calls[1][0]  # validation errors were fed back
    assert validate_script(load_json(out_path(tmp_path)), book, TERMS) == []


def test_gives_up_after_three_invalid_attempts(monkeypatch, tmp_path, capsys):  # FR-7
    stub = StubModel(load_json(MINI_BOOK), 7, replies=["{}"])
    assert run(monkeypatch, tmp_path, local=stub) == 1
    assert len(stub.calls) == 3
    assert not out_path(tmp_path).exists()
    assert "intro" in capsys.readouterr().err  # last attempt's validation errors are listed


def test_local_unavailable_has_no_cloud_fallback(monkeypatch, tmp_path, capsys):  # FR-7
    def down(prompt, seed, model=None):
        raise LLMUnavailable("local model server is not reachable")

    assert run(monkeypatch, tmp_path, local=down) == 1
    assert "not reachable" in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_missing_terms_file_stops_before_any_request(monkeypatch, tmp_path, capsys):  # AC-7.2
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    assert run(monkeypatch, tmp_path, extra=["--config-dir", str(config)]) == 1
    assert str(config / "disallowed_terms.txt") in capsys.readouterr().err
    assert not out_path(tmp_path).exists()


def test_empty_terms_file_warns(monkeypatch, tmp_path, capsys):  # AC-7.3
    book = load_json(MINI_BOOK)
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CAST_PATH, config / "cast.json")
    (config / "disallowed_terms.txt").write_text("# nothing yet\n", encoding="utf-8")
    extra = ["--config-dir", str(config)]
    assert run(monkeypatch, tmp_path, extra=extra, local=StubModel(book, 7)) == 0
    assert "disallowed-terms list is empty" in capsys.readouterr().out


def test_success_prints_review_notice(monkeypatch, tmp_path, capsys):  # AC-7.4
    assert run(monkeypatch, tmp_path, local=StubModel(load_json(MINI_BOOK), 7)) == 0
    out = capsys.readouterr().out
    assert "reviewed by a person" in out and "before voice synthesis" in out


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b["pages"].pop(), "supported range is 8-32"),
        (lambda b: b["pages"][2].pop("prompt"), "page 3: 'prompt' is missing or empty"),
        (lambda b: b["pages"][4].update(page_number=9), "page 5: page_number must be 5"),
    ],
)
def test_invalid_book_writes_nothing(monkeypatch, tmp_path, capsys, mutate, message):
    book = load_json(MINI_BOOK)
    mutate(book)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(book), encoding="utf-8")
    assert run(monkeypatch, tmp_path, bad) == 1
    assert message in capsys.readouterr().err


def test_missing_book_file(monkeypatch, tmp_path, capsys):
    assert run(monkeypatch, tmp_path, tmp_path / "nope.json") == 1
    assert "book data file not found" in capsys.readouterr().err
