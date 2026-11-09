import errno
import json

import pytest

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from tests.voice_helpers import FakeEngine, identity_shift, make_script, write_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.tts import VoiceUnavailable

SLUG = "the-lantern-bug-parade"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(sv, "create_engine", lambda cfg: engine)
    monkeypatch.setattr(sv, "pitch_shift", identity_shift)
    script = make_script(MINI_BOOK)
    script_path = write_script(script, tmp_path / "episodes", SLUG)
    return {
        "tmp": tmp_path,
        "engine": engine,
        "script": script,
        "script_path": script_path,
        "audio": tmp_path / "build" / SLUG / "audio",
    }


def run(env, *extra, book=MINI_BOOK, command=None):
    args = [
        *([command] if command else []),
        "--book",
        str(book),
        "--episodes-dir",
        str(env["tmp"] / "episodes"),
        "--build-dir",
        str(env["tmp"] / "build"),
        *extra,
    ]
    return sv.main(args)


def test_synthesizes_the_book(env, capsys):  # AC-1.1, AC-1.2, AC-4.1, AC-4.2
    assert run(env, "--reviewed") == 0
    audio = env["audio"]
    tracks = sorted(p.stem for p in (audio / "tracks").glob("*.wav"))
    assert tracks == sorted(["intro", "outro", *[f"page-{n:02d}" for n in range(1, 9)]])
    assert (audio / "preview.wav").exists()
    review = (audio / "review.md").read_text()
    for page in env["script"]["pages"]:
        assert f"page-{page['page_number']:02d}-01" in review
    assert "intro-01" in review and "outro-01" in review
    status = load_json(audio / "status.json")
    assert status["status"] == "pending review" and status["approved_at"] is None
    out = capsys.readouterr().out
    assert "listen" in out.lower() and "approve" in out


def test_other_book_needs_no_changes(env, tmp_path):  # AC-1.5
    write_script(make_script(PILOT_BOOK), tmp_path / "episodes", "the-snail-mail-express")
    assert run(env, "--reviewed", book=PILOT_BOOK) == 0
    assert (tmp_path / "build" / "the-snail-mail-express" / "audio" / "preview.wav").exists()


def test_missing_script(env, capsys):  # AC-7.3
    env["script_path"].unlink()
    assert run(env, "--reviewed") == 1
    assert str(env["script_path"]) in capsys.readouterr().err


def test_invalid_script_is_refused(env, capsys):  # AC-7.1
    env["script_path"].write_text("{}", encoding="utf-8")
    assert run(env, "--reviewed") == 1
    assert "missing top-level key" in capsys.readouterr().err
    assert env["engine"].calls == []


def test_unreviewed_script_is_refused(env, capsys):  # AC-7.2
    assert run(env) == 1
    err = capsys.readouterr().err
    assert "reviewed by a person" in err and "--reviewed" in err
    assert env["engine"].calls == [] and not env["audio"].exists()


def test_unsupported_voice_is_named(env, capsys):
    env["script"]["cast"]["kid_older"]["voice"] = "robot"
    write_script(env["script"], env["tmp"] / "episodes", SLUG)
    assert run(env, "--reviewed") == 1
    assert "kid_older" in capsys.readouterr().err


def test_missing_model_is_reported(env, monkeypatch, capsys):  # Error scenario
    def unavailable(cfg):
        raise VoiceUnavailable("voice model x is not downloaded; run: ... download")

    monkeypatch.setattr(sv, "create_engine", unavailable)
    assert run(env, "--reviewed") == 1
    assert "not downloaded" in capsys.readouterr().err
    assert not (env["audio"] / "status.json").exists()


def test_failing_line_names_line_and_check(env, monkeypatch, capsys):  # AC-3.4
    page = next(p for p in env["script"]["pages"] if p["has_qa"])
    target = page["lines"][-1]["text"]
    engine = FakeEngine(bad={target: 99})
    monkeypatch.setattr(sv, "create_engine", lambda cfg: engine)
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert f"page {page['page_number']} line {len(page['lines'])} (storyteller)" in err
    assert "clipping" in err
    audio = env["audio"]
    assert not (audio / "tracks").exists() and not (audio / "review.md").exists()
    assert not (audio / "status.json").exists()


def test_rerun_reuses_everything(env, monkeypatch, capsys):  # AC-6.1
    assert run(env, "--reviewed") == 0
    capsys.readouterr()

    def no_engine(cfg):
        raise AssertionError("nothing should be synthesized")

    monkeypatch.setattr(sv, "create_engine", no_engine)
    assert run(env, "--reviewed") == 0
    assert "synthesized 0" in capsys.readouterr().out


def test_force_resynthesizes(env):  # AC-6.4
    assert run(env, "--reviewed") == 0
    first = len(env["engine"].calls)
    assert run(env, "--reviewed", "--force") == 0
    assert len(env["engine"].calls) == 2 * first


def test_approval_flow(env, capsys):  # AC-4.3, AC-4.4, AC-4.5
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0
    status = load_json(env["audio"] / "status.json")
    assert status["status"] == "approved" and status["approved_at"]

    assert run(env, "--reviewed") == 0  # nothing changed: approval kept
    assert load_json(env["audio"] / "status.json")["status"] == "approved"

    page = next(p for p in env["script"]["pages"] if p["has_qa"])
    page["lines"][-1]["text"] = "What a lovely page!"
    write_script(env["script"], env["tmp"] / "episodes", SLUG)
    capsys.readouterr()
    assert run(env, command="approve") == 1  # AC-4.5
    assert "re-run synthesis" in capsys.readouterr().err

    assert run(env, "--reviewed") == 0  # AC-4.4
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


def test_approve_without_audio(env, capsys):
    assert run(env, command="approve") == 1
    assert "run synthesis first" in capsys.readouterr().err


def test_pronunciations_reach_the_voice_only(env, tmp_path):  # AC-5.1, AC-5.2
    book = load_json(MINI_BOOK)
    book["metadata"]["pronunciations"] = {"Tilly": "Til-ee"}
    book_path = tmp_path / "book.json"
    book_path.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed", book=book_path) == 0
    spoken = " ".join(text for text, _, _ in env["engine"].calls)
    assert "Til-ee" in spoken and "Tilly" not in spoken
    review = (env["audio"] / "review.md").read_text()
    timing = (env["audio"] / "tracks" / "page-01.json").read_text()
    assert "Tilly" in review and "Til-ee" not in review
    assert "Tilly" in timing and "Til-ee" not in timing


def test_malformed_pronunciations_name_the_book(env, tmp_path, capsys):
    book = load_json(MINI_BOOK)
    book["metadata"]["pronunciations"] = {"Tilly": ""}
    book_path = tmp_path / "book.json"
    book_path.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed", book=book_path) == 1
    err = capsys.readouterr().err
    assert str(book_path) in err and "Tilly" in err


def test_disk_full_names_the_folder(env, monkeypatch, capsys):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(sv, "write_wav_atomic", full)
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert "disk space" in err and str(env["audio"]) in err


def test_download_command(env, monkeypatch, capsys):
    fetched = []
    monkeypatch.setattr(sv, "download_model", lambda settings: fetched.append(settings.repo))
    assert run(env, command="download") == 0
    assert fetched == ["mlx-community/orpheus-3b-0.1-ft-4bit"]
    assert "ready" in capsys.readouterr().out


def test_defaults_resolve_from_the_project_root():
    args = sv.parse_args([])
    assert args.book == sv.ROOT / "book_data.json"
    assert args.build_dir == sv.ROOT / "build"
    assert args.command == "synthesize"
