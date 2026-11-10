"""Motion plan schema, limits, validation and stale check (spec 009, Phase 1)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from video_pipeline.library import PROJECT_ROOT, BookFolder
from video_pipeline.motion_plan import (
    EFFECTS,
    MOTIONS,
    Limits,
    MotionPlan,
    PlanError,
    cut_fields_hash,
    load_limits,
    load_plan,
    picture_identity,
    plan_path,
    plan_problems,
    plan_state,
    save_plan,
)

LIMITS = Limits(
    max_layers=8,
    max_effects=3,
    max_rotate_deg=4.0,
    max_shift=0.02,
    min_period_s=2.0,
    max_period_s=12.0,
    max_breathe=0.03,
)
SIZE = (1024, 1024)


def make_layer(name: str = "tree", **over) -> dict:
    layer = {
        "name": name,
        "description": "the big tree",
        "box": [100, 100, 500, 600],
        "include_points": [[300, 300]],
        "exclude_points": [],
        "z": 1,
        "depth": 0.5,
        "pivot": [300, 600],
        "motions": [{"type": "sway", "amplitude": 2.0, "period_s": 6.0, "phase": 0.0}],
    }
    layer.update(over)
    return layer


def make_plan(**over) -> dict:
    """A valid plan dict for a 1024x1024 picture."""
    plan = {
        "page_number": 3,
        "picture": {"file": "page_03.png", "sha256": "ab" * 32, "width": 1024, "height": 1024},
        "provider": "codex",
        "written_at": "2026-11-10T10:00:00Z",
        "layers": [make_layer()],
        "effects": [{"type": "snow", "intensity": 0.5}],
    }
    plan.update(over)
    return plan


def problems(plan: dict) -> list[str]:
    return plan_problems(plan, LIMITS, SIZE)


def joined(plan: dict) -> str:
    return "\n".join(problems(plan))


def write_picture(folder: BookFolder, name: str = "page_03.png", colour: str = "red") -> Path:
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    path = folder.illustrations / name
    Image.new("RGB", SIZE, colour).save(path)
    return path


def write_plan_for(folder: BookFolder, picture: Path, **over) -> MotionPlan:
    data = make_plan(picture=picture_identity(picture).model_dump(), **over)
    plan = MotionPlan.model_validate(data)
    save_plan(plan_path(folder, 3), plan)
    return plan


# -- constants and limits ------------------------------------------------------------------------


def test_motion_and_effect_names() -> None:
    assert MOTIONS == ("sway", "bob", "rotate", "breathe", "shift")
    assert EFFECTS == (
        "rain",
        "snow",
        "fireflies",
        "falling_leaves",
        "water_ripple",
        "smoke",
        "mist",
        "glow",
    )


def test_load_limits_reads_project_config() -> None:
    limits = load_limits(PROJECT_ROOT / "config")
    assert limits == LIMITS


def test_animation_config_has_planning_and_cut_blocks() -> None:
    data = json.loads((PROJECT_ROOT / "config" / "animation.json").read_text(encoding="utf-8"))
    assert data["planning"] == {"attempts": 3, "time_limit_s": 600}
    assert data["cut"] == {"feather_px": 2}


# -- a valid plan, save and load -------------------------------------------------------------------


def test_valid_plan_has_no_problems() -> None:
    assert problems(make_plan()) == []


def test_save_then_load_gives_an_equal_model(tmp_path: Path) -> None:
    plan = MotionPlan.model_validate(make_plan())
    path = tmp_path / "animation" / "page_03.json"
    save_plan(path, plan)
    assert load_plan(path, LIMITS) == plan


def test_saved_file_is_two_space_indent_ending_in_newline(tmp_path: Path) -> None:
    plan = MotionPlan.model_validate(make_plan())
    path = tmp_path / "page_03.json"
    save_plan(path, plan)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert text.startswith('{\n  "page_number": 3,')


def test_plan_path_is_page_number_under_animation(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    assert plan_path(folder, 3) == tmp_path / "books" / "demo" / "animation" / "page_03.json"
    assert plan_path(folder, 12).name == "page_12.json"


# -- schema errors (AC-3.1, AC-3.2, AC-3.3, AC-3.4, AC-1.4) -----------------------------------------


def test_unknown_motion_type_names_field_and_value() -> None:
    plan = make_plan()
    plan["layers"][0]["motions"][0]["type"] = "wiggle"
    text = joined(plan)
    assert "layers[0].motions[0].type" in text
    assert "wiggle" in text


def test_unknown_effect_type_is_named() -> None:
    plan = make_plan(effects=[{"type": "lava", "intensity": 0.5}])
    text = joined(plan)
    assert "effects[0].type" in text
    assert "lava" in text


def test_unknown_key_is_refused() -> None:
    plan = make_plan()
    plan["layers"][0]["colour_me"] = 1
    assert "layers[0].colour_me" in joined(plan)


def test_box_outside_picture_names_field_values_and_size() -> None:
    plan = make_plan()
    plan["layers"][0]["box"] = [900, 10, 1100, 50]
    text = joined(plan)
    assert "layers[0].box" in text
    assert "1100" in text
    assert "1024x1024" in text


def test_box_must_have_positive_area() -> None:
    plan = make_plan()
    plan["layers"][0]["box"] = [500, 10, 100, 50]
    assert "layers[0].box" in joined(plan)


def test_point_and_pivot_outside_picture_are_named() -> None:
    plan = make_plan()
    plan["layers"][0]["include_points"] = [[2000, 5]]
    plan["layers"][0]["exclude_points"] = [[5, -1]]
    plan["layers"][0]["pivot"] = [1025, 5]
    text = joined(plan)
    assert "layers[0].include_points[0]" in text
    assert "layers[0].exclude_points[0]" in text
    assert "layers[0].pivot" in text


def test_effect_region_box_outside_picture_is_named() -> None:
    plan = make_plan(
        effects=[{"type": "rain", "intensity": 0.5, "region": {"box": [0, 0, 5000, 9]}}]
    )
    text = joined(plan)
    assert "effects[0].region.box" in text
    assert "1024x1024" in text


def test_rotate_amplitude_over_limit_is_reported_and_not_clamped(tmp_path: Path) -> None:
    plan = make_plan()
    plan["layers"][0]["motions"] = [{"type": "rotate", "amplitude": 9, "period_s": 6}]
    text = joined(plan)
    assert "max 4" in text
    assert "layers[0].motions[0].amplitude" in text
    path = tmp_path / "page_03.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(PlanError, match="max 4"):
        load_plan(path, LIMITS)
    assert json.loads(path.read_text(encoding="utf-8"))["layers"][0]["motions"][0]["amplitude"] == 9


def test_amplitude_caps_per_motion_type() -> None:
    cases = {
        "sway": 5.0,
        "rotate": 5.0,
        "bob": 0.05,
        "shift": 0.05,
        "breathe": 0.05,
    }
    for kind, amplitude in cases.items():
        plan = make_plan()
        motion = {"type": kind, "amplitude": amplitude, "period_s": 6.0}
        if kind == "shift":
            motion["direction"] = [1.0, 0.0]
        plan["layers"][0]["motions"] = [motion]
        assert "over the limit" in joined(plan), kind


def test_amplitude_at_the_limit_is_allowed() -> None:
    plan = make_plan()
    plan["layers"][0]["motions"] = [
        {"type": "rotate", "amplitude": 4.0, "period_s": 2.0},
        {"type": "breathe", "amplitude": 0.03, "period_s": 12.0},
    ]
    assert problems(plan) == []


def test_period_outside_range_is_reported() -> None:
    for period in (1.0, 13.0):
        plan = make_plan()
        plan["layers"][0]["motions"][0]["period_s"] = period
        text = joined(plan)
        assert "layers[0].motions[0].period_s" in text
        assert "2.0-12.0" in text


def test_shift_needs_a_direction() -> None:
    plan = make_plan()
    plan["layers"][0]["motions"] = [{"type": "shift", "amplitude": 0.01, "period_s": 6.0}]
    assert "layers[0].motions[0].direction" in joined(plan)


def test_duplicate_layer_names_are_named() -> None:
    plan = make_plan(layers=[make_layer("tree"), make_layer("tree", z=2)])
    text = joined(plan)
    assert "duplicate" in text
    assert "'tree'" in text


def test_effect_region_naming_a_missing_layer_is_named() -> None:
    plan = make_plan(effects=[{"type": "glow", "intensity": 0.4, "region": {"layer": "ghost"}}])
    text = joined(plan)
    assert "effects[0].region.layer" in text
    assert "ghost" in text


def test_effect_region_naming_a_real_layer_is_fine() -> None:
    plan = make_plan(effects=[{"type": "glow", "intensity": 0.4, "region": {"layer": "tree"}}])
    assert problems(plan) == []


def test_nine_layers_break_the_layer_limit() -> None:
    layers = [make_layer(f"thing_{i}", z=i) for i in range(9)]
    text = joined(make_plan(layers=layers))
    assert "9 layers" in text
    assert "max 8" in text


def test_eight_layers_are_fine() -> None:
    layers = [make_layer(f"thing_{i}", z=i) for i in range(8)]
    assert problems(make_plan(layers=layers)) == []


def test_four_effects_break_the_effect_limit() -> None:
    effects = [{"type": "snow", "intensity": 0.2} for _ in range(4)]
    text = joined(make_plan(effects=effects))
    assert "4 effects" in text
    assert "max 3" in text


def test_bad_layer_name_and_colour_are_refused() -> None:
    plan = make_plan(
        layers=[make_layer("Big Tree")],
        effects=[{"type": "glow", "intensity": 0.1, "colour": "red"}],
    )
    text = joined(plan)
    assert "layers[0].name" in text
    assert "effects[0].colour" in text


def test_a_plan_needs_at_least_one_layer() -> None:
    assert "layers" in joined(make_plan(layers=[]))


def test_problems_does_not_mutate_the_input() -> None:
    plan = make_plan()
    plan["layers"][0]["box"] = [900, 10, 1100, 50]
    before = copy.deepcopy(plan)
    problems(plan)
    assert plan == before


def test_size_argument_sets_the_picture_bounds() -> None:
    plan = make_plan()
    plan["layers"][0]["box"] = [100, 100, 500, 600]
    text = "\n".join(plan_problems(plan, LIMITS, (400, 400)))
    assert "layers[0].box" in text
    assert "400x400" in text


# -- load_plan errors ------------------------------------------------------------------------------


def test_load_plan_names_the_file_for_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "page_03.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanError) as err:
        load_plan(path, LIMITS)
    assert str(path) in str(err.value)


def test_load_plan_names_the_file_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="page_03.json"):
        load_plan(tmp_path / "page_03.json", LIMITS)


# -- plan_state (AC-3.5) ---------------------------------------------------------------------------


def test_plan_state_none_without_a_file(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    write_picture(folder)
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "none"
    assert state.plan is None


def test_plan_state_ok_when_picture_matches(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    picture = write_picture(folder)
    plan = write_plan_for(folder, picture)
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "ok"
    assert state.plan == plan


def test_plan_state_defaults_the_picture_name_from_the_page_number(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    picture = write_picture(folder)
    write_plan_for(folder, picture)
    assert plan_state(folder, {"page_number": 3}, LIMITS).state == "ok"


def test_plan_state_stale_when_picture_hash_differs(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    picture = write_picture(folder)
    write_plan_for(folder, picture)
    write_picture(folder, colour="blue")
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "stale"
    assert state.plan is not None


def test_plan_state_no_picture_when_picture_missing(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    picture = write_picture(folder)
    write_plan_for(folder, picture)
    picture.unlink()
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "no_picture"
    assert "page_03.png" in state.detail


def test_plan_state_invalid_carries_the_message(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    write_picture(folder)
    path = plan_path(folder, 3)
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "invalid"
    assert str(path) in state.detail


def test_plan_state_invalid_for_a_rule_break(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    write_picture(folder)
    path = plan_path(folder, 3)
    path.parent.mkdir(parents=True)
    bad = make_plan()
    bad["layers"][0]["motions"] = [{"type": "rotate", "amplitude": 9, "period_s": 6}]
    path.write_text(json.dumps(bad), encoding="utf-8")
    state = plan_state(folder, {"page_number": 3, "illustration_file": "page_03.png"}, LIMITS)
    assert state.state == "invalid"
    assert "max 4" in state.detail


def test_picture_identity_reads_hash_and_size(tmp_path: Path) -> None:
    folder = BookFolder("demo", tmp_path)
    picture = write_picture(folder)
    ident = picture_identity(picture)
    assert ident.file == "page_03.png"
    assert (ident.width, ident.height) == SIZE
    assert ident.sha256 == hashlib.sha256(picture.read_bytes()).hexdigest()


# -- cut_fields_hash (FR-9 input, AC-4.5) ------------------------------------------------------------


def cut_hash(plan: dict) -> str:
    return cut_fields_hash(MotionPlan.model_validate(plan))


def test_cut_hash_changes_when_a_box_point_or_name_changes() -> None:
    base = cut_hash(make_plan())
    changed_box = make_plan(layers=[make_layer(box=[100, 100, 501, 600])])
    changed_include = make_plan(layers=[make_layer(include_points=[[301, 300]])])
    changed_exclude = make_plan(layers=[make_layer(exclude_points=[[10, 10]])])
    changed_name = make_plan(layers=[make_layer("oak")])
    hashes = {
        cut_hash(changed_box),
        cut_hash(changed_include),
        cut_hash(changed_exclude),
        cut_hash(changed_name),
    }
    assert base not in hashes
    assert len(hashes) == 4


def test_cut_hash_ignores_motion_effect_z_depth_and_pivot() -> None:
    base = cut_hash(make_plan())
    same = make_plan(
        layers=[
            make_layer(
                z=9,
                depth=0.9,
                pivot=[10, 10],
                motions=[{"type": "bob", "amplitude": 0.01, "period_s": 3.0}],
                description="something else",
            )
        ],
        effects=[{"type": "rain", "intensity": 0.9}],
        provider="gemini",
    )
    assert cut_hash(same) == base


def test_cut_hash_ignores_layer_order_and_point_order() -> None:
    a = make_layer("a", include_points=[[1, 1], [2, 2]])
    b = make_layer("b")
    first = cut_hash(make_plan(layers=[a, b]))
    a_swapped = make_layer("a", include_points=[[2, 2], [1, 1]])
    second = cut_hash(make_plan(layers=[b, a_swapped]))
    assert first == second


# -- planning with a (fake) provider: Phase 2 ----------------------------------------------------


def _server_args(command: list[str]) -> list[str]:
    prefix = "mcp_servers.kiddo.args="
    setting = next(c for c in command if c.startswith(prefix))
    return json.loads(setting[len(prefix) :])


def _plan_body() -> dict:
    return {"layers": [make_layer()], "effects": [{"type": "snow", "intensity": 0.5}]}


class FakeCodex:
    """Plays the agent: saves the next payload for the page through the real plan tools.

    `payloads` maps a page number to the payloads its attempts save in turn; a page with no
    entry saves a valid plan; `None` saves nothing (as a provider failure would).
    """

    def __init__(self, payloads: dict[int, list] | None = None):
        self.payloads = {k: list(v) for k, v in (payloads or {}).items()}
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path, timeout_s=600, on_event=None, max_refused_saves=5):
        from video_pipeline import plan_mcp
        from video_pipeline.agents import AgentEvent, AgentResult

        self.commands.append(command)
        args = plan_mcp.parse_args(_server_args(command)[2:])  # drop "-m video_pipeline.plan_mcp"
        tools = plan_mcp.build_tools(args)
        queue = self.payloads.get(args.page)
        payload = queue.pop(0) if queue else (_plan_body() if queue is None else None)
        events = []
        if payload is not None:
            result = tools.save_motion_plan(payload)
            events.append(AgentEvent("tool-result", tool="save_motion_plan", data=result))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("{}\n", encoding="utf-8")
        reason = "" if payload is not None else "agent exited with status 1"
        return AgentResult(ok=not reason, events=events, log_path=log_path, reason=reason)


@pytest.fixture
def book_root(tmp_path: Path) -> BookFolder:
    from tests.helpers import make_root

    folder = make_root(tmp_path)
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    for number in range(1, 9):
        Image.new("RGB", SIZE, "green").save(folder.illustrations / f"page_{number:02d}.png")
    for name in ("cover_front.png", "cover_back.png", "map_whisperwood.png"):
        Image.new("RGB", SIZE, "blue").save(folder.illustrations / name)
    return folder


def _plan_book(folder: BookFolder, fake: FakeCodex, **kw) -> list:
    from video_pipeline.motion_plan import plan_book

    return plan_book(
        folder,
        LIMITS,
        root=folder.root,
        provider=fake,
        binary=Path("/fake/codex"),
        log=lambda line: None,
        **kw,
    )


def _plan_files(folder: BookFolder) -> list[str]:
    return sorted(p.name for p in (folder.folder / "animation").glob("*"))


def test_plan_book_writes_a_plan_per_story_page(book_root) -> None:  # AC-1.1
    outcomes = _plan_book(book_root, FakeCodex())
    assert [o.status for o in outcomes] == ["written"] * 8
    assert _plan_files(book_root) == [f"page_{n:02d}.json" for n in range(1, 9)]
    plan = load_plan(plan_path(book_root, 2), LIMITS)
    picture = book_root.illustrations / "page_02.png"
    assert plan.picture == picture_identity(picture) and plan.page_number == 2


def test_planning_agent_gets_the_picture_and_only_plan_tools(book_root) -> None:  # FR-4, FR-5
    fake = FakeCodex()
    _plan_book(book_root, fake, pages=[3])
    command = fake.commands[0]
    picture = str(book_root.illustrations / "page_03.png")
    assert command[command.index("-i") + 1] == picture
    assert command[command.index("--sandbox") + 1] == "read-only"
    args = _server_args(command)
    assert args[:2] == ["-m", "video_pipeline.plan_mcp"]
    assert args[args.index("--picture") + 1] == picture
    assert "1024x1024" in command[-1]
    assert list((book_root.build_dir / "agent").glob("*-plan-p03-1.jsonl"))


def test_existing_plans_are_kept_byte_for_byte(book_root) -> None:  # AC-1.2
    _plan_book(book_root, FakeCodex(), pages=[2])
    before = plan_path(book_root, 2).read_bytes()
    lines: list[str] = []
    from video_pipeline.motion_plan import plan_book

    fake = FakeCodex()
    outcomes = plan_book(
        book_root,
        LIMITS,
        root=book_root.root,
        pages=[2],
        provider=fake,
        binary=Path("/fake/codex"),
        log=lines.append,
    )
    assert outcomes[0].status == "kept" and not fake.commands
    assert plan_path(book_root, 2).read_bytes() == before
    assert any("kept" in line for line in lines)


def test_force_rewrites_and_keeps_a_backup(book_root) -> None:  # AC-1.3
    _plan_book(book_root, FakeCodex(), pages=[2])
    before = plan_path(book_root, 2).read_bytes()
    other = _plan_body()
    other["layers"][0]["name"] = "bush"
    outcomes = _plan_book(book_root, FakeCodex({2: [other]}), pages=[2], force=True)
    assert outcomes[0].status == "written"
    assert plan_path(book_root, 2).with_name("page_02.json.bak").read_bytes() == before
    assert load_plan(plan_path(book_root, 2), LIMITS).layers[0].name == "bush"


def test_refused_plans_are_retried_with_the_errors(book_root) -> None:  # FR-6 retry
    bad = _plan_body()
    bad["layers"][0]["motions"][0]["amplitude"] = 9.0
    fake = FakeCodex({4: [bad, _plan_body()]})
    outcomes = _plan_book(book_root, fake, pages=[4])
    assert outcomes[0].status == "written" and len(fake.commands) == 2
    assert "amplitude" in fake.commands[1][-1]


def test_one_failing_page_does_not_stop_the_others(book_root) -> None:  # AC-1.5
    outcomes = _plan_book(book_root, FakeCodex({5: [None, None, None]}))
    failed = [o for o in outcomes if o.status == "failed"]
    assert [o.page_number for o in failed] == [5]
    assert "status 1" in failed[0].reason
    assert len([o for o in outcomes if o.status == "written"]) == 7
    assert not plan_path(book_root, 5).exists()


def test_missing_picture_fails_only_that_page(book_root) -> None:
    (book_root.illustrations / "page_06.png").unlink()
    outcomes = _plan_book(book_root, FakeCodex())
    failed = [o for o in outcomes if o.status == "failed"]
    assert [(o.page_number, o.reason) for o in failed] == [(6, "page_06.png is missing")]


def test_single_page_touches_only_that_page(book_root) -> None:  # AC-1.6
    fake = FakeCodex()
    outcomes = _plan_book(book_root, fake, pages=[3])
    assert [o.page_number for o in outcomes] == [3] and len(fake.commands) == 1
    assert _plan_files(book_root) == ["page_03.json"]


def test_main_exits_one_and_names_the_failed_page(book_root, monkeypatch, capsys) -> None:
    from video_pipeline import motion_plan

    fake = FakeCodex({5: [None, None, None]})
    monkeypatch.setattr(motion_plan, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(motion_plan, "run_codex", fake)
    code = motion_plan.main(["--book", book_root.slug, "--root", str(book_root.root)])
    out = capsys.readouterr().out
    assert code == 1
    assert "page 05: failed" in out and "7 written, 0 kept, 1 failed" in out


def test_main_page_flag_and_success_exit(book_root, monkeypatch, capsys) -> None:
    from video_pipeline import motion_plan

    monkeypatch.setattr(motion_plan, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(motion_plan, "run_codex", FakeCodex())
    args = ["--book", book_root.slug, "--root", str(book_root.root), "--page", "3"]
    assert motion_plan.main(args) == 0
    assert _plan_files(book_root) == ["page_03.json"]


def test_main_without_provider_exits_one_and_writes_nothing(book_root, monkeypatch, capsys):
    from video_pipeline import motion_plan
    from video_pipeline.agents import AgentUnavailable

    def down():
        raise AgentUnavailable("install the ChatGPT desktop app")

    monkeypatch.setattr(motion_plan, "codex_status", down)
    assert motion_plan.main(["--book", book_root.slug, "--root", str(book_root.root)]) == 1
    assert "install the ChatGPT desktop app" in capsys.readouterr().err
    assert not (book_root.folder / "animation").exists()


@pytest.mark.codex
def test_real_plan_page_one(tmp_path: Path) -> None:
    """Plans pilot page 1 with the real Codex and checks the plan validates."""
    import shutil

    from tests.helpers import PILOT_BOOK, make_root
    from video_pipeline.motion_plan import load_limits, plan_book

    folder = make_root(tmp_path, PILOT_BOOK, slug="the-snail-mail-express")
    folder.illustrations.mkdir(parents=True, exist_ok=True)
    shutil.copy(PILOT_BOOK.parent / "illustrations" / "page_01.png", folder.illustrations)
    limits = load_limits(PROJECT_ROOT / "config")
    outcomes = plan_book(folder, limits, root=tmp_path, pages=[1])
    assert outcomes[0].status == "written", outcomes[0].reason
    plan = load_plan(plan_path(folder, 1), limits)
    assert 1 <= len(plan.layers) <= limits.max_layers
