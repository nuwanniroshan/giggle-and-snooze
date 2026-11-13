from pathlib import Path

import pytest
from PIL import Image

from video_pipeline.illustrate import (
    IllustrateError,
    build_image_command,
    check_image,
    illustrate,
    plan_images,
)

BOOK = {
    "metadata": {"title": "Plops", "blurb": "Mina keeps rain."},
    "pages": [
        {"page_number": 2, "prompt": "two", "illustration_file": "page_02.png"},
        {"page_number": 1, "prompt": "one", "illustration_file": "page_01.png"},
    ],
}


def test_plan_orders_pages_then_covers():
    assert [s for s, _ in plan_images(BOOK)] == ["page_01", "page_02", "cover_front", "cover_back"]


def test_command_attaches_reference_and_prompt():
    cmd = build_image_command(Path("codex"), "a prompt", Path("ref.png"))
    assert cmd[cmd.index("-i") + 1] == "ref.png"
    assert "a prompt" in cmd[-1] and "./out.png" in cmd[-1]
    assert "-i" not in build_image_command(Path("codex"), "p", None)


def test_illustrate_skips_existing_and_references_first_image(tmp_path):
    (tmp_path / "page_01.png").write_bytes(b"x")
    calls = []
    done = illustrate(
        BOOK,
        tmp_path,
        Path("codex"),
        paint=lambda b, p, r, t: calls.append((p[:3], r, t.stem)),
        log=lambda _: None,
    )
    assert done == ["page_02", "cover_front", "cover_back"]
    assert all(ref == tmp_path / "page_01.png" for _, ref, _ in calls)


def test_check_image_rejects_junk_and_small(tmp_path):
    (tmp_path / "bad.png").write_bytes(b"nope")
    with pytest.raises(IllustrateError):
        check_image(tmp_path / "bad.png")
    Image.new("RGB", (100, 100)).save(tmp_path / "small.png")
    with pytest.raises(IllustrateError, match="too small"):
        check_image(tmp_path / "small.png")


# -- plans written by illustrate (spec 009, Phase 3: FR-13) ---------------------------------------


def _paint_real(record: list | None = None, colour: str = "green"):
    def paint(binary, prompt, reference, target):
        if record is not None:
            record.append(("paint", target.stem))
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 1024), colour).save(target)

    return paint


def test_planner_runs_after_each_painted_page_not_covers(tmp_path):  # AC-2.1, AC-2.2
    from video_pipeline.motion_plan import PlanOutcome

    events: list = []

    def plan(page):
        assert (tmp_path / f"page_{page['page_number']:02d}.png").is_file()
        events.append(("plan", page["page_number"]))
        return PlanOutcome(page["page_number"], "written")

    done = illustrate(
        BOOK, tmp_path, Path("codex"), paint=_paint_real(events), log=lambda _: None, plan=plan
    )
    assert done == ["page_01", "page_02", "cover_front", "cover_back"]
    assert events == [
        ("paint", "page_01"),
        ("plan", 1),
        ("paint", "page_02"),
        ("plan", 2),
        ("paint", "cover_front"),
        ("paint", "cover_back"),
    ]


def test_failed_plan_is_logged_and_painting_continues(tmp_path):  # AC-2.3
    from video_pipeline.motion_plan import PlanOutcome

    lines: list[str] = []
    done = illustrate(
        BOOK,
        tmp_path,
        Path("codex"),
        paint=_paint_real(),
        log=lines.append,
        plan=lambda page: PlanOutcome(page["page_number"], "failed", "agent saved nothing"),
    )
    assert done == ["page_01", "page_02", "cover_front", "cover_back"]
    assert (tmp_path / "page_01.png").is_file()
    assert any(line.strip() == "page_01: plan failed – agent saved nothing" for line in lines)
    assert any(line.strip() == "page_02: plan failed – agent saved nothing" for line in lines)


def test_kept_picture_gets_no_plan(tmp_path):  # AC-2.5
    from video_pipeline.motion_plan import PlanOutcome

    (tmp_path / "page_01.png").write_bytes(b"x")
    planned: list[int] = []

    def plan(page):
        planned.append(page["page_number"])
        return PlanOutcome(page["page_number"], "written")

    illustrate(BOOK, tmp_path, Path("codex"), paint=_paint_real(), log=lambda _: None, plan=plan)
    assert planned == [2]


def test_force_repaint_replans_and_keeps_old_plan_as_bak(tmp_path):  # AC-2.4
    import json

    from tests.helpers import make_root
    from tests.test_motion_plan import LIMITS, FakeCodex, _plan_body
    from video_pipeline.motion_plan import load_plan, plan_page, plan_path

    folder = make_root(tmp_path)
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    book["pages"] = [p for p in book["pages"] if p["page_number"] == 1]
    first = FakeCodex()

    def planner(provider):
        return lambda page: plan_page(
            folder,
            page,
            LIMITS,
            binary=Path("/fake/codex"),
            book=book,
            root=folder.root,
            force=True,
            provider=provider,
        )

    illustrate(
        book,
        folder.illustrations,
        Path("codex"),
        paint=_paint_real(),
        log=lambda _: None,
        plan=planner(first),
    )
    before = plan_path(folder, 1).read_bytes()
    other = _plan_body()
    other["layers"][0]["name"] = "bush"
    second = FakeCodex({1: [other]})
    illustrate(
        book,
        folder.illustrations,
        Path("codex"),
        force=True,
        paint=_paint_real(colour="red"),
        log=lambda _: None,
        plan=planner(second),
    )
    assert len(second.commands) == 1
    assert plan_path(folder, 1).with_name("page_01.json.bak").read_bytes() == before
    assert load_plan(plan_path(folder, 1), LIMITS).layers[0].name == "bush"


def test_main_plans_with_force_and_exits_1_naming_failed_pages(tmp_path, monkeypatch, capsys):
    from tests.helpers import make_root
    from video_pipeline import illustrate as module
    from video_pipeline.motion_plan import PlanOutcome

    folder = make_root(tmp_path)
    calls: list[tuple[int, bool]] = []

    def fake_plan_page(folder_, page, limits, *, binary, book, root, force=False, provider=None):
        calls.append((page["page_number"], force))
        status = "failed" if page["page_number"] == 2 else "written"
        return PlanOutcome(page["page_number"], status, "no plan" if status == "failed" else "")

    monkeypatch.setattr(module, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(module, "paint_one", _paint_real())
    monkeypatch.setattr(module, "plan_page", fake_plan_page)
    code = module.main(["--book", folder.slug, "--root", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 1
    assert calls and all(force for _, force in calls)
    assert "painted" in out.out
    assert "page_02" in out.err


@pytest.mark.parametrize(
    "error",
    [
        "AgentUnavailable",
        "OSError",
        "KeyError",
        "PlanError",
        "ValueError",
    ],
)
def test_main_planning_exception_fails_that_page_and_painting_goes_on(
    tmp_path, monkeypatch, capsys, error
):
    from tests.helpers import make_root
    from video_pipeline import illustrate as module
    from video_pipeline.agents import AgentUnavailable
    from video_pipeline.motion_plan import PlanError, PlanOutcome

    errors = {
        "AgentUnavailable": AgentUnavailable("codex went away"),
        "OSError": OSError("disk full"),
        "KeyError": KeyError("planning"),
        "PlanError": PlanError("plan.json: bad"),
        "ValueError": ValueError("bad config"),
    }
    folder = make_root(tmp_path)
    planned: list[int] = []

    def fake_plan_page(folder_, page, limits, *, binary, book, root, force=False, provider=None):
        planned.append(page["page_number"])
        if page["page_number"] == 2:
            raise errors[error]
        return PlanOutcome(page["page_number"], "written")

    monkeypatch.setattr(module, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(module, "paint_one", _paint_real())
    monkeypatch.setattr(module, "plan_page", fake_plan_page)
    code = module.main(["--book", folder.slug, "--root", str(tmp_path)])
    out = capsys.readouterr()
    assert code == 1
    assert (folder.illustrations / "cover_back.png").is_file()  # painting went on to the end
    assert len(planned) > 2  # pages after page 2 were planned too
    assert "page_02: plan failed" in out.out
    assert "page_02" in out.err


# -- character sheet in picture requests (spec 018: AC-2.1..2.3) ---------------------------------

JACK_LOOK = "A small boy with curly red hair, a green raincoat and yellow boots."
ZIB_LOOK = "A round friend with three eyes and a lilac scarf around its neck."
MOSS_LOOK = "A tall mossy tortoise with a tiny lantern hanging from its shell."


def sheet_book() -> dict:
    return {
        "metadata": {"title": "Jack's Dream", "blurb": "Jack meets Zib."},
        "style_bible": {
            "characters": [
                {"name": "Jack", "look": JACK_LOOK},
                {"name": "Zib", "look": ZIB_LOOK},
                {"name": "Moss", "look": MOSS_LOOK},
            ]
        },
        "pages": [
            {"page_number": 1, "prompt": "one", "characters_on_page": ["Jack"]},
            {"page_number": 2, "prompt": "two", "characters_on_page": ["Zib", "Jack"]},
            {"page_number": 3, "prompt": "three", "characters_on_page": []},
        ],
    }


def test_page_request_quotes_the_looks_of_its_characters():  # AC-2.1
    prompts = dict(plan_images(sheet_book(), style="STYLE TEXT"))
    request = prompts["page_02"]
    assert f"Jack: {JACK_LOOK}" in request and f"Zib: {ZIB_LOOK}" in request
    assert MOSS_LOOK not in request
    assert "two" in request and request.startswith("STYLE TEXT\n\n")
    assert ZIB_LOOK not in prompts["page_01"] and f"Jack: {JACK_LOOK}" in prompts["page_01"]


def test_cover_requests_quote_every_look():  # AC-2.2
    prompts = dict(plan_images(sheet_book()))
    for stem in ("cover_front", "cover_back"):
        for look in (JACK_LOOK, ZIB_LOOK, MOSS_LOOK):
            assert look in prompts[stem], stem


def test_page_without_characters_keeps_its_old_prompt():
    assert dict(plan_images(sheet_book()))["page_03"] == "three"
    assert dict(plan_images(sheet_book(), style="S"))["page_03"] == "S\n\nthree"


def _old_plan(book: dict, style: str | None) -> list[tuple[str, str]]:
    """The formula before spec 018: page prompt or cover text, with the style in front."""
    from video_pipeline.illustrate import cover_prompts

    pages = sorted(book["pages"], key=lambda p: p["page_number"])
    items = [(f"page_{p['page_number']:02d}", p["prompt"]) for p in pages]
    covers = cover_prompts(book)
    items += [("cover_front", covers["cover_front"]), ("cover_back", covers["cover_back"])]
    return [(s, f"{style}\n\n{p}") if style else (s, p) for s, p in items]


@pytest.mark.parametrize("style", [None, "A blueprint art direction."])
def test_books_without_a_sheet_get_identical_requests(style):  # AC-2.3
    from tests.helpers import PILOT_BOOK, load_json

    for book in (load_json(PILOT_BOOK), BOOK):
        assert plan_images(book, style) == _old_plan(book, style)


def test_painter_receives_the_character_block(tmp_path):  # AC-2.1 end to end
    prompts = {}
    illustrate(
        sheet_book(),
        tmp_path,
        Path("codex"),
        paint=lambda b, p, r, t: prompts.setdefault(t.stem, p),
        log=lambda _: None,
    )
    assert f"Zib: {ZIB_LOOK}" in prompts["page_02"]


# -- repaint one picture (spec 019, Phase 1: FR-1, AC-1.1, AC-1.4) -------------------------------


@pytest.fixture
def repaint_world(tmp_path, monkeypatch):
    """A book with every picture painted (each a distinct colour), and fakes for Codex, painting,
    planning and cutting. `events` records what ran, in order."""
    from tests.helpers import make_root
    from video_pipeline import cut_layers
    from video_pipeline import illustrate as module
    from video_pipeline.motion_plan import PlanOutcome

    folder = make_root(tmp_path)
    book = __import__("json").loads(folder.book_json.read_text(encoding="utf-8"))
    names = [f"page_{p['page_number']:02d}" for p in book["pages"]] + ["cover_front", "cover_back"]
    for i, stem in enumerate(names):
        folder.illustrations.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 576), (i * 10, 50, 50)).save(folder.illustrations / f"{stem}.png")
    events: list = []
    state = {"plan": "written", "cut": 0, "paint_error": None}

    def fake_paint(binary, prompt, reference, target):
        events.append(("paint", target.stem, reference.name if reference else None, prompt))
        if state["paint_error"]:
            target.write_bytes(b"half written")
            raise IllustrateError(state["paint_error"])
        Image.new("RGB", (1024, 576), "red").save(target)

    def fake_plan_page(folder_, page, limits, *, binary, book, root, force=False, provider=None):
        events.append(("plan", page["page_number"], force))
        reason = "no plan" if state["plan"] == "failed" else ""
        return PlanOutcome(page["page_number"], state["plan"], reason)

    def fake_cut(argv):
        events.append(("cut", list(argv)))
        return state["cut"]

    monkeypatch.setattr(module, "codex_status", lambda: Path("/fake/codex"))
    monkeypatch.setattr(module, "paint_one", fake_paint)
    monkeypatch.setattr(module, "plan_page", fake_plan_page)
    monkeypatch.setattr(cut_layers, "main", fake_cut)

    def run(*args):
        return module.main(["--book", folder.slug, "--root", str(tmp_path), *args])

    return folder, events, state, run


def _bytes_of(folder) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.illustrations.glob("*.png"))}


def test_page_repaint_paints_only_that_page_and_keeps_bak(repaint_world):  # AC-1.1
    folder, events, _state, run = repaint_world
    before = _bytes_of(folder)
    assert run("--page", "3") == 0
    assert [e[1] for e in events if e[0] == "paint"] == ["page_03"]
    after = _bytes_of(folder)
    assert after["page_03.png"] != before["page_03.png"]
    assert {k: v for k, v in after.items() if k != "page_03.png"} == {
        k: v for k, v in before.items() if k != "page_03.png"
    }
    assert (folder.illustrations / "page_03.png.bak").read_bytes() == before["page_03.png"]


def test_page_repaint_uses_page_one_as_reference(repaint_world):
    _folder, events, _state, run = repaint_world
    run("--page", "3")
    assert events[0][2] == "page_01.png"


def test_page_one_repaint_uses_the_next_page_as_reference(repaint_world):
    folder, events, _state, run = repaint_world
    (folder.illustrations / "page_02.png").unlink()
    run("--page", "1")
    assert events[0][1] == "page_01" and events[0][2] == "page_03.png"


def test_failed_repaint_restores_the_old_picture(repaint_world, capsys):  # error scenario
    folder, events, state, run = repaint_world
    before = (folder.illustrations / "page_03.png").read_bytes()
    state["paint_error"] = "page_03.png: no image was produced"
    assert run("--page", "3", "--cut") == 1
    assert (folder.illustrations / "page_03.png").read_bytes() == before
    assert "no image was produced" in capsys.readouterr().err
    assert [e[0] for e in events] == ["paint"]  # no plan, no cut


def test_page_repaint_plans_then_cuts_that_page_only(repaint_world):  # AC-1.1
    folder, events, _state, run = repaint_world
    assert run("--page", "3", "--cut") == 0
    assert [e[0] for e in events] == ["paint", "plan", "cut"]
    assert events[1] == ("plan", 3, True)
    assert events[2][1] == ["--book", folder.slug, "--page", "3", "--root", str(folder.root)]


def test_cut_failure_is_the_exit_code(repaint_world):
    _folder, _events, state, run = repaint_world
    state["cut"] = 2
    assert run("--page", "3", "--cut") == 2


def test_page_repaint_without_cut_only_plans(repaint_world):
    _folder, events, _state, run = repaint_world
    assert run("--page", "3") == 0
    assert [e[0] for e in events] == ["paint", "plan"]


def test_failed_plan_skips_the_cut_and_exits_1(repaint_world, capsys):
    folder, events, state, run = repaint_world
    state["plan"] = "failed"
    assert run("--page", "3", "--cut") == 1
    assert [e[0] for e in events] == ["paint", "plan"]
    assert "page_03" in capsys.readouterr().err
    assert (folder.illustrations / "page_03.png.bak").is_file()  # the new picture is kept


@pytest.mark.parametrize("side", ["front", "back"])
def test_cover_repaint_has_no_plan_or_cut(repaint_world, side):  # AC-1.4
    folder, events, _state, run = repaint_world
    before = _bytes_of(folder)
    assert run("--cover", side) == 0
    assert [e[:3] for e in events] == [("paint", f"cover_{side}", "page_01.png")]
    after = _bytes_of(folder)
    changed = [name for name in after if after[name] != before.get(name)]
    assert changed == [f"cover_{side}.png"]
    assert (folder.illustrations / f"cover_{side}.png.bak").read_bytes() == before[
        f"cover_{side}.png"
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["--page", "3", "--cover", "front"],
        ["--page", "3", "--force"],
        ["--cover", "back", "--force"],
        ["--cover", "side"],
    ],
)
def test_page_and_cover_and_force_are_exclusive(repaint_world, args):
    _folder, events, _state, run = repaint_world
    with pytest.raises(SystemExit) as exc:
        run(*args)
    assert exc.value.code == 2
    assert events == []


@pytest.mark.parametrize("args", [["--cut"], ["--cut", "--cover", "front"], ["--cut", "--force"]])
def test_cut_without_page_is_refused(repaint_world, args):
    _folder, events, _state, run = repaint_world
    with pytest.raises(SystemExit) as exc:
        run(*args)
    assert exc.value.code == 2
    assert events == []


def test_unknown_page_is_refused(repaint_world, capsys):
    folder, events, _state, run = repaint_world
    assert run("--page", "99") == 1
    assert "page 99 is not in the book" in capsys.readouterr().err
    assert events == []
    assert not list(folder.illustrations.glob("*.bak"))


def test_single_repaint_does_not_record_blueprint_use(repaint_world, monkeypatch):
    from video_pipeline import illustrate as module

    folder, _events, _state, run = repaint_world
    monkeypatch.setattr(module, "style_text", lambda _bp: "STYLE")
    monkeypatch.setattr(module, "load_blueprint", lambda *_a: object())
    monkeypatch.setattr(module, "load_context", lambda _root: None)
    folder.blueprint_path.write_text("{}", encoding="utf-8")
    recorded: list = []
    monkeypatch.setattr(module, "record_use", lambda *a: recorded.append(a))
    assert run("--page", "3") == 0
    assert run("--cover", "front") == 0
    assert recorded == []


def test_repaint_prompt_is_the_plan_images_prompt(repaint_world, monkeypatch):  # spec 018 looks
    import json

    from video_pipeline import illustrate as module

    folder, events, _state, run = repaint_world
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    book["style_bible"]["characters"] = [{"name": "Zib", "look": ZIB_LOOK}]
    for page in book["pages"]:
        page["characters_on_page"] = ["Zib"] if page["page_number"] == 3 else []
    folder.book_json.write_text(json.dumps(book), encoding="utf-8")
    monkeypatch.setattr(module, "style_text", lambda _bp: "STYLE")
    monkeypatch.setattr(module, "load_blueprint", lambda *_a: object())
    monkeypatch.setattr(module, "load_context", lambda _root: None)
    folder.blueprint_path.write_text("{}", encoding="utf-8")
    assert run("--page", "3") == 0
    expected = dict(plan_images(book, "STYLE"))["page_03"]
    assert events[0][3] == expected and ZIB_LOOK in expected


def test_select_images_picks_one_and_refuses_unknown():
    from video_pipeline.illustrate import select_images

    assert select_images(BOOK, None, "page_02") == ("page_02", "two")
    assert select_images(BOOK, "S", "cover_back")[0] == "cover_back"
    with pytest.raises(IllustrateError, match="page_07"):
        select_images(BOOK, None, "page_07")
