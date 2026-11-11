"""CLI: write ideas, an improved idea, or a book draft with the Codex agent (spec 006).

    python -m video_pipeline.write_story ideas --run ID [--root PATH]
    python -m video_pipeline.write_story candidate --run ID [--root PATH]
    python -m video_pipeline.write_story book --draft ID [--root PATH]
    python -m video_pipeline.write_story blueprint (--draft ID [--then-book] | --book SLUG)

Each run gives the agent only the story tools (FR-8). A submission counts only when the file the
tool saved passes the same rules again here; a refused attempt's problems go back to the agent.
The run's record (ideas, a candidate, or the draft's book) is written by this CLI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from video_pipeline.agents import (
    AgentUnavailable,
    build_codex_command,
    codex_status,
    run_codex,
)
from video_pipeline.blueprint import (
    Blueprint,
    BlueprintError,
    Context,
    load_blueprint,
    load_config,
    load_context,
    others_for,
)
from video_pipeline.contract import load_disallowed_terms
from video_pipeline.drafts import DraftError, DraftStore
from video_pipeline.episode_tools import write_atomic
from video_pipeline.library import PROJECT_ROOT, BookFolder
from video_pipeline.story_tools import (
    blueprint_check,
    book_errors,
    candidate_errors,
    ideas_errors,
    load_gentle_terms,
)

PAYLOAD_NAME = "payload.json"
LAYOUTS_TEXT = (
    "image_top_text_bottom, full_bleed_card_top_left, full_bleed_card_top_right, "
    "full_bleed_card_bottom_left, full_bleed_card_bottom_right"
)
AGE_TEXT = "Ages 3-6, Ages 4-7 or Ages 5-8"
GENTLE_RULES = (
    "Keep it gentle for young children: no death, no blood, no monsters, and no frightening "
    "threat the story does not resolve on the same page."
)
IDEAS_PROMPT = f"""You suggest story ideas for children's read-aloud picture books.
Use only the story tools.
1. Call submit_ideas with {{"ideas": [...]}} holding 3 to 5 different ideas. Each idea has
   "title" (3-60 characters), "premise" (1-2 sentences), "age_range" ({AGE_TEXT}) and "lesson"
   (one sentence).
2. {GENTLE_RULES}
3. If submit_ideas returns errors, fix every error and submit again.
4. Finish with one short line."""
CANDIDATE_PROMPT = f"""You improve one story idea for a children's read-aloud picture book.
Use only the story tools.
1. The producer's idea is in the message below. Keep its characters and setting; make the
   premise clear and warm.
2. Call submit_candidate with one object: "title" (3-60 characters), "premise" (1-2 sentences),
   "age_range" ({AGE_TEXT}, the one the idea suggests) and "lesson" (one sentence).
3. {GENTLE_RULES}
4. If submit_candidate returns errors, fix every error and submit again.
5. Finish with one short line.

The producer's idea:
"""
BOOK_PROMPT = f"""You write a children's read-aloud picture book from a chosen idea.
Use only the story tools.
1. The chosen idea is below. Write the book with at least 8 pages and at most the page maximum
   given below. A shorter book is fine; the story must fit the maximum.
2. Each page: "page_number" (1, 2, 3 ... in order), "title" (short), "text" (1 to 4 short
   sentences read aloud, with repetition and a clear cause and effect), "layout" (one of:
   {LAYOUTS_TEXT}), "prompt" (a visual description of the page for an illustrator), and
   "illustration_file" ("page_NN.png" with the page number).
3. Call submit_book with {{"metadata": {{"title", "subtitle", "age_range", "blurb",
   "dedication"}}, "pages": [...]}}. The age_range is one of {AGE_TEXT}.
4. {GENTLE_RULES} Warm ending.
5. If submit_book returns errors, fix every error and submit again.
6. Finish with one short line.

"""
BLUEPRINT_PROMPT = """You design the creative direction for a children's read-aloud picture book video.
Use only the story tools.
1. Call submit_blueprint with one object holding: "version" 1; "mood" (key, label, summary, roles
   for storyteller, kid_older, kid_younger each with pace slow|steady|brisk, energy
   hushed|calm|lively|bouncy and a one-sentence quirk); "art" (medium_key, medium_label, palette of
   exactly 5 {name, hex}, line, lighting, composition, style_prompt_prefix, avoid); "qa"
   (format_key, format_label, question_types 2-4, intro_hook, outro_hook, moments_min,
   moments_max, kid_notes); "sound" (ambience: library names, sfx: library names,
   music_direction); "animation" (intensity 0.25-2.0, effects); "rationale".
2. Keys are lowercase slugs. Make it clearly different from every existing blueprint listed
   below: a different art medium or mood, a different Q&A format, and a different palette.
3. Library sounds you may use: {sounds}. Effects you may use: {effects}.
4. Gentle for young children. If submit_blueprint returns errors, fix every error and submit
   again.
5. Finish with one short line.
"""
STORY_DIRECTION_SENTENCE = (
    "Describe each scene without naming a painting medium; the art direction is applied separately."
)


@dataclass
class Outcome:
    payload: object | None
    problems: list[str] = field(default_factory=list)
    attempts: int = 0


def read_limits(root: Path) -> tuple[int, float]:
    config = json.loads((Path(root) / "config" / "story.json").read_text(encoding="utf-8"))
    return int(config["attempts"]), float(config["time_limit_s"])


def story_server_args(
    mode: str, out: Path, pages: int, root: Path, subject: str | None = None
) -> list[str]:
    args = [
        "-m",
        "video_pipeline.story_mcp",
        "--mode",
        mode,
        "--out",
        str(out),
        "--pages",
        str(pages),
        "--root",
        str(root),
    ]
    if subject is not None:
        args += ["--subject", subject]
    return args


def validate_payload(
    mode: str, payload: object, pages: int, root: Path, subject: str | None = None
) -> list[str]:
    config = Path(root) / "config"
    if mode == "blueprint":
        ctx = load_context(root)
        exclude = tuple(subject.split(":", 1)) if subject else None
        return blueprint_check(
            payload, ctx, others_for(Path(root), ctx, exclude), load_config(config)
        )
    terms = load_disallowed_terms(config / "disallowed_terms.txt")
    gentle = load_gentle_terms(config / "gentle_terms.txt")
    if mode == "ideas":
        return ideas_errors(payload, terms, gentle)
    if mode == "candidate":
        return candidate_errors(payload, terms, gentle)
    return book_errors(payload, pages, terms, gentle)


def generate(
    *,
    mode: str,
    prompt: str,
    pages: int,
    folder: Path,
    root: Path,
    provider=None,
    subject: str | None = None,
) -> Outcome:
    """Run the agent until a valid payload is saved or the attempts run out (FR-7)."""
    attempts, time_limit = read_limits(root)
    binary = codex_status()  # AgentUnavailable is raised by the caller
    out = folder / PAYLOAD_NAME
    feedback: list[str] = []
    outcome = Outcome(payload=None)
    for attempt in range(1, attempts + 1):
        out.unlink(missing_ok=True)
        text = prompt
        if feedback:
            text += "\nThe last attempt was refused. Fix these problems and submit again:\n"
            text += "\n".join(f"- {problem}" for problem in feedback[:20])
        command = build_codex_command(
            binary,
            sys.executable,
            story_server_args(mode, out, pages, root, subject),
            {"PYTHONPATH": str(PROJECT_ROOT)},
            prompt=text,
        )
        log = folder / "agent" / f"{time.strftime('%Y%m%d-%H%M%S')}-{attempt}-codex.jsonl"
        result = (provider or run_codex)(command, log, timeout_s=time_limit)
        outcome.attempts = attempt
        if out.exists():
            try:
                payload = json.loads(out.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
            errors = (
                validate_payload(mode, payload, pages, root, subject)
                if payload is not None
                else ["the saved file is not valid JSON"]
            )
            if not errors:
                outcome.payload = payload
                return outcome
            feedback = errors
        else:
            feedback = [
                str(event.data["errors"][0])
                for event in result.events
                if event.kind == "tool-result"
                and isinstance(event.data, dict)
                and event.data.get("errors")
            ] or [result.reason or "the agent saved nothing"]
        outcome.problems = feedback
    return outcome


def run_ideas(root: Path, run_id: str, provider=None) -> int:
    store = DraftStore(root)
    try:
        folder = store.drafts_dir / "ideas" / run_id
        store.update_run(run_id, state="writing", attempts=0, payload=None, problems=[])
        outcome = generate(
            mode="ideas", prompt=IDEAS_PROMPT, pages=0, folder=folder, root=root, provider=provider
        )
    except AgentUnavailable as exc:
        store.update_run(run_id, state="failed", problems=[str(exc)])
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _finish_run(store, run_id, outcome)


def run_improve(root: Path, run_id: str, provider=None) -> int:
    store = DraftStore(root)
    record = store.read_run(run_id) or {}
    request = record.get("request") or ""
    try:
        folder = store.drafts_dir / "ideas" / run_id
        store.update_run(run_id, state="writing", attempts=0, payload=None, problems=[])
        outcome = generate(
            mode="candidate",
            prompt=CANDIDATE_PROMPT + request,
            pages=0,
            folder=folder,
            root=root,
            provider=provider,
        )
    except AgentUnavailable as exc:
        store.update_run(run_id, state="failed", problems=[str(exc)])
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return _finish_run(store, run_id, outcome)


def _finish_run(store: DraftStore, run_id: str, outcome: Outcome) -> int:
    if outcome.payload is None:
        store.update_run(
            run_id, state="failed", problems=outcome.problems, attempts=outcome.attempts
        )
        for problem in outcome.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    store.update_run(
        run_id, state="ready", payload=outcome.payload, problems=[], attempts=outcome.attempts
    )
    return 0


def run_book(root: Path, draft_id: str, provider=None) -> int:
    store = DraftStore(root)
    record = store.get(draft_id)
    if record is None:
        print(f"error: unknown draft {draft_id!r}", file=sys.stderr)
        return 1
    store.update(draft_id, state="writing", phase="story", attempts=0, problems=[], book=None)
    pages = int(record["pages"])
    prompt = (
        BOOK_PROMPT
        + f"Page maximum: {pages}.\nChosen idea:\n"
        + json.dumps(record["idea"], ensure_ascii=False, indent=2)
    )
    blueprint_file = store.blueprint_path(draft_id)
    if blueprint_file.is_file():
        try:
            prompt += "\n\n" + story_direction(load_blueprint(blueprint_file, load_context(root)))
        except BlueprintError as exc:
            store.update(draft_id, state="failed", problems=[str(exc)])
            print(f"error: {exc}", file=sys.stderr)
            return 1
    try:
        outcome = generate(
            mode="book",
            prompt=prompt,
            pages=pages,
            folder=store.draft_folder(draft_id),
            root=root,
            provider=provider,
        )
    except AgentUnavailable as exc:
        store.update(draft_id, state="failed", problems=[str(exc)])
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if outcome.payload is None:
        store.update(draft_id, state="failed", problems=outcome.problems, attempts=outcome.attempts)
        for problem in outcome.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    store.update(
        draft_id, state="ready", book=outcome.payload, problems=[], attempts=outcome.attempts
    )
    return 0


def story_direction(bp: Blueprint) -> str:
    """The creative direction the story writer follows (FR-5)."""
    return (
        f"Creative direction. Mood: {bp.mood.label}. {bp.mood.summary}\n"
        f"Art direction: {bp.art.style_prompt_prefix}\n{STORY_DIRECTION_SENTENCE}"
    )


def _others_text(others: dict[str, Blueprint]) -> str:
    if not others:
        return "\nThere are no other blueprints yet.\n"
    lines = ["\nExisting blueprints (yours must be clearly different from each):"]
    for label, bp in others.items():
        colours = " ".join(s.hex for s in bp.art.palette)
        lines.append(
            f"- {label}: mood {bp.mood.key}, art medium {bp.art.medium_key}, "
            f"Q&A format {bp.qa.format_key}, palette {colours}"
        )
    return "\n".join(lines) + "\n"


def blueprint_prompt(
    subject: dict, others: dict[str, Blueprint], ctx: Context | None = None
) -> str:
    """The design brief: the idea (or the book), the allowed sounds and effects, and every other
    blueprint to differ from (FR-4, AC-2.10)."""
    sounds = (
        ", ".join(
            f"{name} ({kind})" for name, kind in sorted(ctx.sounds.items()) if kind != "music"
        )
        if ctx
        else ""
    )
    effects = ", ".join(sorted(ctx.effects)) if ctx else ""
    text = BLUEPRINT_PROMPT.replace("{sounds}", sounds).replace("{effects}", effects)
    text += _others_text(others)
    return text + "\nThe book:\n" + json.dumps(subject, ensure_ascii=False, indent=2)


def _install_blueprint(target: Path, payload: object) -> None:
    """Keep the old file as `.bak`, then write the validated blueprint atomically."""
    if target.is_file():
        shutil.copyfile(target, target.with_name(target.name + ".bak"))
    write_atomic(target, payload)


def run_blueprint(
    root: Path,
    *,
    draft_id: str | None = None,
    slug: str | None = None,
    then_book: bool = False,
    provider=None,
) -> int:
    """Design a blueprint for a draft (optionally continuing with the story) or for a book."""
    root = Path(root)
    store = DraftStore(root)
    if draft_id is not None:
        record = store.get(draft_id)
        if record is None:
            print(f"error: unknown draft {draft_id!r}", file=sys.stderr)
            return 1
        subject = f"draft:{draft_id}"
        folder = store.draft_folder(draft_id)
        target = store.blueprint_path(draft_id)
        brief_subject = record["idea"]
        regenerating = record["state"] == "ready"
        if not regenerating:
            store.update(draft_id, state="designing", attempts=0, problems=[])
        store.update(draft_id, phase="design", design_problems=[])
    else:
        book_folder = BookFolder(slug, root)
        if not book_folder.book_json.is_file():
            print(f"error: unknown book {slug!r}", file=sys.stderr)
            return 1
        subject = f"book:{slug}"
        folder = book_folder.build_dir / "blueprint"
        target = book_folder.blueprint_path
        metadata = json.loads(book_folder.book_json.read_text(encoding="utf-8")).get("metadata", {})
        brief_subject = {k: metadata.get(k) for k in ("title", "subtitle", "blurb")}
        regenerating = True
    try:
        ctx = load_context(root)
        exclude = tuple(subject.split(":", 1))
        prompt = blueprint_prompt(brief_subject, others_for(root, ctx, exclude), ctx)
        outcome = generate(
            mode="blueprint",
            prompt=prompt,
            pages=0,
            folder=folder,
            root=root,
            provider=provider,
            subject=subject,
        )
    except (AgentUnavailable, BlueprintError) as exc:
        outcome = Outcome(payload=None, problems=[str(exc)])
    if outcome.payload is None:
        _design_failed(store, draft_id, regenerating, outcome)
        return 1
    _install_blueprint(target, outcome.payload)
    if draft_id is not None:
        store.update(draft_id, attempts=outcome.attempts, design_problems=[])
        if then_book:
            return run_book(root, draft_id, provider)
        if not regenerating:
            store.update(draft_id, state="interrupted")  # designed, story not written yet
    return 0


def _design_failed(
    store: DraftStore, draft_id: str | None, regenerating: bool, outcome: Outcome
) -> None:
    for problem in outcome.problems:
        print(f"  - {problem}", file=sys.stderr)
    if draft_id is None:
        return
    if regenerating:
        store.update(draft_id, design_problems=outcome.problems)
    else:
        store.update(
            draft_id,
            state="design_failed",
            problems=outcome.problems,
            attempts=outcome.attempts,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m video_pipeline.write_story")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    ideas = commands.add_parser("ideas")
    ideas.add_argument("--run", required=True)
    candidate = commands.add_parser("candidate")
    candidate.add_argument("--run", required=True)
    book = commands.add_parser("book")
    book.add_argument("--draft", required=True)
    design = commands.add_parser("blueprint")
    target = design.add_mutually_exclusive_group(required=True)
    target.add_argument("--draft")
    target.add_argument("--book")
    design.add_argument("--then-book", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "ideas":
            return run_ideas(args.root, args.run)
        if args.command == "candidate":
            return run_improve(args.root, args.run)
        if args.command == "blueprint":
            if args.then_book and not args.draft:
                print("error: --then-book needs --draft", file=sys.stderr)
                return 1
            return run_blueprint(
                args.root, draft_id=args.draft, slug=args.book, then_book=args.then_book
            )
        return run_book(args.root, args.draft)
    except DraftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
