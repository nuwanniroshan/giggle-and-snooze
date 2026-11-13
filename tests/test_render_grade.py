"""Spec 013: the final grading pass inside the render (FFmpeg and its loudness reports faked)."""

import json

import pytest

from tests.helpers import MINI_BOOK, PILOT_BOOK, load_json
from tests.render_helpers import FakeFrameRunner, FakeRunner, cli_args, make_world
from video_pipeline import render_episode as re_

LOUDNORM = json.dumps(
    {
        "input_i": "-19.1",
        "input_tp": "-3.0",
        "input_lra": "13.0",
        "input_thresh": "-29.6",
        "target_offset": "0.5",
    }
)
SUMMARY = "Summary:\n  I:  -16.2 LUFS\n  LRA:  5.5 LU\n  Peak:  -1.8 dBFS\n"


class FakeMeasure:
    def __init__(self, fail=False):
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = fail

    def __call__(self, cmd, what):
        self.calls.append((what, cmd))
        if self.fail:
            raise re_.RenderError(f"{what}: simulated loudness failure")
        return LOUDNORM if what == "measure loudness" else SUMMARY


def set_grade(world, **values):
    path = world["config"] / "render.json"
    cfg = load_json(path)
    cfg["grade"] = values
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch, MINI_BOOK)
    set_grade(w, enabled=True)
    return w


def render(world, *extra, runner=None, measure=None):
    runner = runner if runner is not None else FakeRunner()
    measure = measure if measure is not None else FakeMeasure()
    code = re_.main(
        cli_args(world, *extra), run=runner, frame_run=FakeFrameRunner(), measure=measure
    )
    return code, runner, measure


def record_of(world):
    return load_json(world["video"] / f"{world['slug']}.json")


def test_one_grading_pass_after_the_join(world, capsys):  # FR-4
    code, runner, measure = render(world)
    assert code == 0
    whats = [w for w, _ in runner.calls]
    assert whats[-3:] == ["join", "mux", "grade"]
    assert [w for w, _ in measure.calls] == ["measure loudness", "measure result"]
    grade_cmd = runner.calls[-1][1]
    assert grade_cmd[grade_cmd.index("-i") + 1].endswith(f"{world['slug']}.ungraded.mp4")
    assert world["final"].exists() and (world["video"] / f"{world['slug']}.ungraded.mp4").exists()
    assert "linear=true" in grade_cmd[grade_cmd.index("-af") + 1]
    assert "grade: rendered" in capsys.readouterr().out


def test_record_lists_settings_and_measured_loudness(world):  # AC-4.4
    render(world)
    record = record_of(world)
    assert record["grade"]["contrast"] == 1.05 and record["grade"]["loudness_lufs"] == -16
    assert record["loudness"] == {
        "integrated_lufs": -16.2,
        "true_peak_dbfs": -1.8,
        "loudness_range": 5.5,
    }
    assert record["final_key"] and record["grade_key"] and record["ungraded_sha256"]


def test_same_grade_reencodes_nothing(world, capsys):  # AC-4.3
    render(world)
    capsys.readouterr()
    code, runner, measure = render(world)
    assert code == 0 and runner.calls == [] and measure.calls == []
    assert "up to date" in capsys.readouterr().out


def test_settings_change_reruns_only_the_grade(world):  # AC-4.2
    render(world)
    first = record_of(world)
    set_grade(world, enabled=True, contrast=1.2)
    code, runner, _ = render(world)
    assert code == 0 and runner.whats() == ["grade"]
    second = record_of(world)
    assert second["grade"]["contrast"] == 1.2
    assert second["grade_key"] != first["grade_key"]
    assert second["final_key"] == first["final_key"]
    assert second["ungraded_sha256"] == first["ungraded_sha256"]


def test_book_override_regrades_only_that_book(world):  # AC-4.1
    render(world)
    book = load_json(world["book"])
    book["grade"] = {"saturation": 1.3}
    world["book"].write_text(json.dumps(book, indent=2), encoding="utf-8")
    code, runner, _ = render(world)
    assert code == 0
    # the book file changed: the script hash is unaffected, so only the grade runs
    assert runner.whats() == ["grade"]
    assert record_of(world)["grade"]["saturation"] == 1.3
    assert record_of(world)["grade"]["contrast"] == 1.05


def test_invalid_book_grade_stops_before_encoding(world, capsys):  # FR-3
    book = load_json(world["book"])
    book["grade"] = {"contrast": 99}
    world["book"].write_text(json.dumps(book, indent=2), encoding="utf-8")
    code, runner, _ = render(world)
    assert code == 1 and runner.calls == []
    assert "book.json: grade.contrast" in capsys.readouterr().err


def test_no_grade_flag_renders_without_the_pass(world):  # FR-6, AC-1.5
    code, runner, measure = render(world, "--no-grade")
    assert code == 0 and "grade" not in runner.whats() and measure.calls == []
    assert runner.whats()[-1] == "mux"
    record = record_of(world)
    assert "grade" not in record and "grade_key" not in record
    assert not (world["video"] / f"{world['slug']}.ungraded.mp4").exists()


def test_disabled_grade_is_todays_render(world, tmp_path):  # AC-1.5, AC-3.4
    set_grade(world, enabled=False)
    code, runner, measure = render(world)
    assert code == 0 and runner.whats()[-2:] == ["join", "mux"] and measure.calls == []
    assert world["final"].read_bytes() == b"fake mux"


def test_switching_the_grade_off_restores_the_ungraded_video(world):
    render(world)
    set_grade(world, enabled=False)
    code, runner, _ = render(world)
    assert code == 0 and runner.calls == []  # copied from the kept ungraded video
    assert world["final"].read_bytes() == b"fake mux"
    assert "grade_key" not in record_of(world)
    assert not (world["video"] / f"{world['slug']}.ungraded.mp4").exists()
    set_grade(world, enabled=True)  # and back on: the plain video is the source
    code, runner, _ = render(world)
    assert code == 0 and runner.whats() == ["grade"]


def test_sound_off_skips_measuring_and_copies_sound(world):  # AC-3.4
    set_grade(world, enabled=True, sound=False)
    _, runner, measure = render(world)
    cmd = runner.calls[-1][1]
    assert [w for w, _ in measure.calls] == ["measure result"]
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_failing_grade_keeps_the_ungraded_video_and_shows_the_error(world, capsys):
    code, _, _ = render(world, runner=FakeRunner(fail_on="grade"))
    assert code == 1
    assert "grade: simulated ffmpeg failure" in capsys.readouterr().err
    assert (world["video"] / f"{world['slug']}.ungraded.mp4").exists()
    assert not world["final"].exists()
    record = record_of(world)
    assert "grade_key" not in record
    code, runner, _ = render(world)  # the retry only grades
    assert code == 0 and runner.whats() == ["grade"]


def test_failing_loudness_measurement_is_a_render_error(world, capsys):
    code, _, _ = render(world, measure=FakeMeasure(fail=True))
    assert code == 1 and "simulated loudness failure" in capsys.readouterr().err


def test_missing_ungraded_video_renders_in_full(world):
    render(world)
    (world["video"] / f"{world['slug']}.ungraded.mp4").unlink()
    set_grade(world, enabled=True, contrast=1.2)
    code, runner, _ = render(world)
    assert code == 0 and "join" in runner.whats() and runner.whats()[-1] == "grade"
    assert not runner.whats("section ")  # sections stay cached


def test_force_rerenders_everything_then_grades(world):
    render(world)
    _, runner, _ = render(world, "--force")
    assert runner.whats("section ") and runner.whats()[-1] == "grade"


def test_render_check_follows_the_grade(world):  # studio: AC-4.5 stale marking
    from video_pipeline.library import BookFolder

    folder = BookFolder(world["slug"], world["root"])
    assert re_.render_check(folder, world["root"]) == "not_rendered"
    render(world)
    assert re_.render_check(folder, world["root"]) == "up_to_date"
    set_grade(world, enabled=True, contrast=1.2)
    assert re_.render_check(folder, world["root"]) == "out_of_date"
    render(world)
    assert re_.render_check(folder, world["root"]) == "up_to_date"
    set_grade(world, enabled=False)
    assert re_.render_check(folder, world["root"]) == "out_of_date"
    render(world)
    assert re_.render_check(folder, world["root"]) == "up_to_date"


def test_second_book_keeps_shared_settings(tmp_path, monkeypatch):  # AC-4.1
    w = make_world(tmp_path, monkeypatch, PILOT_BOOK)
    set_grade(w, enabled=True)
    render(w)
    assert record_of(w)["grade"]["saturation"] == 1.12
