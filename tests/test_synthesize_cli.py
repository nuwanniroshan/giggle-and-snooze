import errno
import json
import shutil

import pytest
import soundfile as sf

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json, make_root
from tests.voice_helpers import FakeEngine, identity_shift, make_script, write_script
from video_pipeline import synthesize_voices as sv
from video_pipeline.tts import VoiceUnavailable

SLUG = "the-lantern-bug-parade"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(sv, "create_engine", lambda cfg: engine)
    monkeypatch.setattr(sv, "pitch_shift", identity_shift)
    folder = make_root(tmp_path)
    script = make_script(MINI_BOOK)
    script_path = write_script(script, folder)
    return {
        "tmp": tmp_path,
        "folder": folder,
        "engine": engine,
        "script": script,
        "script_path": script_path,
        "audio": folder.build_dir / "audio",
    }


def run(env, *extra, slug=SLUG, command=None):
    args = [
        *([command] if command else []),
        "--book",
        slug,
        "--root",
        str(env["tmp"]),
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
    pilot = make_root(tmp_path, PILOT_BOOK, "the-snail-mail-express")
    write_script(make_script(PILOT_BOOK), pilot)
    assert run(env, "--reviewed", slug="the-snail-mail-express") == 0
    assert (pilot.build_dir / "audio" / "preview.wav").exists()


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
    write_script(env["script"], env["folder"])
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
    write_script(env["script"], env["folder"])
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
    env["folder"].book_json.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed") == 0
    spoken = " ".join(text for text, _, _ in env["engine"].calls)
    assert "Til-ee" in spoken and "Tilly" not in spoken
    review = (env["audio"] / "review.md").read_text()
    timing = (env["audio"] / "tracks" / "page-01.json").read_text()
    assert "Tilly" in review and "Til-ee" not in review
    assert "Tilly" in timing and "Til-ee" not in timing


def test_malformed_pronunciations_name_the_book(env, tmp_path, capsys):
    book = load_json(MINI_BOOK)
    book["metadata"]["pronunciations"] = {"Tilly": ""}
    env["folder"].book_json.write_text(json.dumps(book), encoding="utf-8")
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert SLUG in err and "Tilly" in err  # names the book, not a file path


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
    args = sv.parse_args(["--book", "moon-kite-race"])
    assert args.book == "moon-kite-race"
    assert args.root == sv.PROJECT_ROOT
    assert args.command == "synthesize"


# --- Review fixes -------------------------------------------------------------------------


def test_libsndfile_disk_full_names_the_folder(env, monkeypatch, capsys):  # review fix 4
    def full(*args, **kwargs):
        raise sf.LibsndfileError(0, "Error writing file: ")

    monkeypatch.setattr(sv.sf, "write", full)
    monkeypatch.setattr(
        sv.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10**9, 10**9, 1000)
    )
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert "disk space" in err and str(env["audio"]) in err


def test_other_libsndfile_errors_are_not_disk_full(env, monkeypatch):  # review fix 4
    def broken(*args, **kwargs):
        raise sf.LibsndfileError(0, "Format not recognised: ")

    monkeypatch.setattr(sv.sf, "write", broken)
    monkeypatch.setattr(
        sv.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10**12, 0, 10**12)
    )
    with pytest.raises(sf.LibsndfileError):
        run(env, "--reviewed")


def test_engine_error_exits_naming_the_line(env, monkeypatch, capsys):  # review fix 6
    target = env["script"]["pages"][1]["lines"][0]["text"]

    class Broken(FakeEngine):
        def synthesize(self, text, voice, seed):
            if text == target:
                raise RuntimeError("model exploded")
            return super().synthesize(text, voice, seed)

    monkeypatch.setattr(sv, "create_engine", lambda cfg: Broken())
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    page = env["script"]["pages"][1]["page_number"]
    assert f"error: page {page} line 1 (storyteller): RuntimeError: model exploded" in err


def test_approve_refuses_after_settings_change(env, capsys):  # review fix 10
    assert run(env, "--reviewed") == 0
    config = env["tmp"] / "config"
    voices = load_json(config / "voices.json")
    voices["pauses"]["same_speaker"] = 0.5
    (config / "voices.json").write_text(json.dumps(voices), encoding="utf-8")
    capsys.readouterr()
    assert run(env, command="approve") == 1
    err = capsys.readouterr().err
    assert "voice settings changed since the audio was synthesized; re-run synthesis first" in err
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


def test_review_sheet_is_written_before_status(env, monkeypatch):  # review fix 10
    def boom(*args, **kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(sv, "write_review", boom)
    with pytest.raises(OSError):
        run(env, "--reviewed")
    assert not (env["audio"] / "status.json").exists()


def test_approval_does_not_survive_an_interrupted_force(env, monkeypatch):  # AC-4.4
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0

    class Interrupted(FakeEngine):
        """Re-synthesizes different audio, then fails after a few clips."""

        def synthesize(self, text, voice, seed):
            if len(self.calls) >= 3:
                raise RuntimeError("interrupted")
            return super().synthesize(text, voice, seed) * 0.5

    monkeypatch.setattr(sv, "create_engine", lambda cfg: Interrupted())
    assert run(env, "--reviewed", "--force") == 1
    monkeypatch.setattr(sv, "create_engine", lambda cfg: FakeEngine())
    assert run(env, "--reviewed") == 0  # reuses the 3 new clips; nothing synthesized now
    status = load_json(env["audio"] / "status.json")
    assert status["status"] == "pending review" and status["approved_at"] is None


def test_approve_refuses_when_preview_changed(env, capsys):  # AC-4.4
    assert run(env, "--reviewed") == 0
    meta_path = env["audio"] / "preview.json"
    meta = load_json(meta_path)
    meta["preview_key"] = "0" * 32
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    capsys.readouterr()
    assert run(env, command="approve") == 1
    err = capsys.readouterr().err
    assert "the preview changed since synthesis finished; re-run synthesis first" in err
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


def test_approve_with_missing_script_names_it(env, capsys):
    assert run(env, "--reviewed") == 0
    env["script_path"].unlink()
    capsys.readouterr()
    assert run(env, command="approve") == 1
    assert f"episode script not found: {env['script_path']}" in capsys.readouterr().err


def test_synthesis_records_track_hashes(env):  # review r1 #1
    assert run(env, "--reviewed") == 0
    status = load_json(env["audio"] / "status.json")
    tracks = env["audio"] / "tracks"
    names = sorted(p.stem for p in tracks.glob("*.wav"))
    assert sorted(status["track_sha256"]) == names
    for name in names:
        assert status["track_sha256"][name] == {
            "wav": sv.sha256_file(tracks / f"{name}.wav"),
            "json": sv.sha256_file(tracks / f"{name}.json"),
        }


@pytest.mark.parametrize("suffix", ["wav", "json"])
def test_approve_refuses_an_edited_track(env, capsys, suffix):  # AC-2.2, review r1 #1
    assert run(env, "--reviewed") == 0
    path = env["audio"] / "tracks" / f"intro.{suffix}"
    if suffix == "wav":
        samples, rate = sf.read(path, dtype="float32")
        sf.write(path, samples * 0.5, rate)
    else:
        data = load_json(path)
        data["lines"][0]["text"] = "Unreviewed words"
        path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert run(env, command="approve") == 1
    assert "audio track intro changed since synthesis; re-run synthesis first" in (
        capsys.readouterr().err
    )
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


def test_approval_is_dropped_when_a_track_changed(env, monkeypatch):  # review r1 #1
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0
    status = load_json(env["audio"] / "status.json")
    status["track_sha256"]["intro"]["wav"] = "0" * 64
    (env["audio"] / "status.json").write_text(json.dumps(status), encoding="utf-8")
    assert run(env, "--reviewed") == 0
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"


@pytest.mark.parametrize("name", ["status.json", "preview.json"])
def test_approve_with_corrupt_record_is_readable(env, capsys, name):  # review r1 #3
    assert run(env, "--reviewed") == 0
    path = env["audio"] / name
    path.write_text("{not json", encoding="utf-8")
    capsys.readouterr()
    assert run(env, command="approve") == 1
    assert f"error: {path} is unreadable; re-run synthesis" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["status.json", "preview.json"])
def test_synthesis_recovers_from_a_corrupt_record(env, name):  # review r1 #3
    assert run(env, "--reviewed") == 0
    (env["audio"] / name).write_text("{not json", encoding="utf-8")
    assert run(env, "--reviewed") == 0
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"
    assert run(env, command="approve") == 0


def test_ambience_edit_keeps_the_approval_but_a_text_edit_drops_it(env, capsys):  # spec 007
    from video_pipeline.episode_tools import write_atomic

    write_atomic(env["script_path"], env["script"])
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0
    script = load_json(env["script_path"])
    script["intro"]["ambience"] = ["rain"]
    script["pages"][0]["ambience"] = ["wind"]
    script["outro"]["ambience"] = ["rain"]
    write_atomic(env["script_path"], script)
    capsys.readouterr()
    assert run(env, command="approve") == 0  # still valid: nothing to re-synthesize
    assert run(env, "--reviewed") == 0
    assert load_json(env["audio"] / "status.json")["status"] == "approved"

    script["pages"][0]["lines"][0]["text"] = "A changed narration line."
    write_atomic(env["script_path"], script)
    capsys.readouterr()
    assert run(env, command="approve") == 1
    assert "changed since the audio was synthesized" in capsys.readouterr().err


# --- spec 017: feelings ---------------------------------------------------------------------


def test_bad_feeling_settings_fail_at_start(env, capsys):  # AC-3.4, error scenarios
    path = env["tmp"] / "config" / "emotions.json"
    data = load_json(path)
    data["feelings"]["sleepy"]["gain_db"] = -20
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert "sleepy" in err and "gain_db" in err
    assert env["engine"].calls == []


def test_unknown_feeling_in_the_script_is_refused(env, capsys):  # AC-2.2
    env["script"]["intro"]["lines"][0]["emotion"] = "angry"
    write_script(env["script"], env["folder"])
    assert run(env, "--reviewed") == 1
    err = capsys.readouterr().err
    assert "intro line 1" in err and "'angry'" in err and "sleepy" in err
    assert env["engine"].calls == []


def test_feeling_edit_needs_approval_again(env):  # AC-3.3, FR-6
    assert run(env, "--reviewed") == 0
    assert run(env, command="approve") == 0
    calls = len(env["engine"].calls)
    env["script"]["outro"]["lines"][1]["emotion"] = "excited"
    write_script(env["script"], env["folder"])
    assert run(env, "--reviewed") == 0
    assert len(env["engine"].calls) == calls + 1  # only that line is spoken again
    assert load_json(env["audio"] / "status.json")["status"] == "pending review"
