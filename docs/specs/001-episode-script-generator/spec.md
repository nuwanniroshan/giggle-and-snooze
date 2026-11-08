# Feature 001: Episode Script Generator

**Status:** Approved
**Version:** 1.1.0
**Created:** 2026-11-08
**Last Updated:** 2026-11-08
**Branch:** `feat/001-episode-script-generator`

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-08 | Initial approved spec |
| 1.1.0 | 2026-11-08 | Spec review fixes: defined whitespace normalisation (AC-2.2); added narration line-splitting rule (FR-3, AC-2.4); split kid-safety into automated checks vs human review (FR-4, Security, AC-7.x); added missing/empty disallowed-terms list behaviour; zoom target is a best-effort estimate with a default (FR-6, AC-5.5) |

---

## Problem Statement

Each book has to become an episode script before any voices or video can be produced. The script needs the Storyteller's narration of every page, short interruptions where the kids ask questions, intro and outro dialogue, and camera cues for the renderer. Writing this by hand for every weekly or monthly book is slow and inconsistent, and it breaks the "one pipeline for every book" principle. The project needs a repeatable step that produces a valid, kid-safe episode script from any book data file at near-zero cost.

## Goals

- Produce a complete, valid episode script from a book data file with one command and no per-book code changes.
- Keep the narration exactly faithful to the book: every page in order, using the book's own text.
- Make the kid interruptions feel natural and varied: about every 2–3 pages, sometimes one kid and sometimes both, each tied to something on that page.
- Run fully offline by default at zero cloud cost; the cloud text model is opt-in for each run.
- Get the same script from the same inputs and settings, so a script can be reproduced and reviewed.

## Non-Goals

- Synthesizing audio, mixing music or sound effects, or rendering video (later pipeline stages).
- Generating intro/outro images or YouTube metadata (title, description, tags, timestamps).
- Editing or rewriting the book's story text.
- Any user interface beyond a command-line invocation.

## Users and Context

**Primary users:** The channel producer, who runs the pipeline for each new book (weekly or monthly).
**Secondary users:** The downstream pipeline stages (voice synthesis, video rendering) that read the episode script; the producer, who may hand-edit a script before rendering.
**Usage context:** On the local Mac after a book's data file and illustrations are final, before voice synthesis. Usually offline.
**User mental model:** "Point it at a book, get the read-aloud script." The producer thinks in terms of pages, the Storyteller, Leo (the older kid, ~8) and Mia (the younger kid, ~5), and the "Q&A moments" where the kids chime in.

## User Stories

### Story 1: Generate an episode script for a book

**As a** producer
**I want** to generate an episode script from a book data file with one command
**So that** the next pipeline stages have everything they need for that book

**Acceptance criteria:**

- [ ] **AC-1.1** Given a valid book data file and no existing script for that book When the producer runs the generator in default mode Then an episode script is written to `episodes/<book-slug>/episode_script.json` and the command exits successfully
- [ ] **AC-1.2** Given a valid book data file When the generator completes Then the saved script passes validation against the episode script contract (FR-2) with zero errors
- [ ] **AC-1.3** Given a book data file for a book other than the pilot book (different title, page count between 8 and 32) When the generator runs Then it produces a valid script with no code or configuration changes other than pointing it at the new file
- [ ] **AC-1.4** Given the generator runs in default mode When it completes Then no request was sent to any cloud service

### Story 2: Verbatim narration of every page

**As a** producer
**I want** the Storyteller to read each page exactly as written
**So that** the episode stays faithful to the published book

**Acceptance criteria:**

- [ ] **AC-2.1** Given a book with N pages When the script is generated Then the script has exactly N page entries, numbered and ordered the same as the book's pages
- [ ] **AC-2.2** Given any page When its Storyteller narration lines are joined in order with single spaces, with emotion tags removed, and both that result and the page text are whitespace-normalised (every run of whitespace, including line breaks, collapsed to one space; leading and trailing whitespace trimmed) Then the two strings are identical, character for character, including punctuation, quotes and dashes
- [ ] **AC-2.3** Given any page When the script is generated Then that page's narration lines come before any Q&A lines on that page
- [ ] **AC-2.4** Given any narration line When it is inspected with emotion tags removed Then it begins at a sentence start, ends at a sentence end, and contains at most 2 sentences and at most 40 words — except that a line holding a single sentence longer than 40 words is allowed

### Story 3: Natural, varied kid interruptions

**As a** young viewer
**I want** the kids to occasionally ask about what's on the page, sometimes one of them and sometimes both
**So that** the reading feels like a live, cozy storytime rather than a recording

**Acceptance criteria:**

- [ ] **AC-3.1** Given a book with N pages When the script is generated Then the number of pages with a Q&A moment is between ⌈N/3⌉ and ⌊N/2.5⌋ inclusive (7–8 for a 20-page book)
- [ ] **AC-3.2** Given the pages that have a Q&A moment When their page numbers are listed in order Then the first is page 3 or earlier, the last is within 3 pages of the final page, and every gap between consecutive Q&A pages is 2 or 3 pages
- [ ] **AC-3.3** Given a generated script When its Q&A moments are classified by which kids speak Then there is at least one moment with only Leo, at least one with only Mia, and at least one with both
- [ ] **AC-3.4** Given any Q&A moment When it is inspected Then it contains 2–5 lines, at least one kid line, and ends with a Storyteller answer line
- [ ] **AC-3.5** Given any Q&A moment When it is inspected Then it records a grounding phrase that appears word for word (case-insensitive) in that page's text or illustration description
- [ ] **AC-3.6** Given any kid line When its words are counted, excluding emotion tags Then it has at most 25 words

### Story 4: Intro and outro dialogue

**As a** viewer
**I want** a short welcome before the book opens and a warm goodbye after it closes
**So that** the episode feels like a complete show

**Acceptance criteria:**

- [ ] **AC-4.1** Given a generated script When the intro is inspected Then it has 2–4 lines, includes at least one kid line and one Storyteller line, and at least one line contains the book's title
- [ ] **AC-4.2** Given a generated script When the outro is inspected Then it has 3–5 lines, includes at least one kid line, and its final line is a Storyteller line inviting viewers to subscribe and come back for the next story

### Story 5: Camera hints for the renderer

**As a** producer
**I want** each page to carry camera directions
**So that** the renderer can animate pages without any manual setup

**Acceptance criteria:**

- [ ] **AC-5.1** Given any page When it is inspected Then it has a narration camera motion that is one of: `slow_push_in`, `slow_pull_out`, `pan_left`, `pan_right`, `static`
- [ ] **AC-5.2** Given any page with a Q&A moment When it is inspected Then it has a zoom target with `x` and `y` in [0.0, 1.0] and `zoom` in [1.0, 2.0]
- [ ] **AC-5.3** Given any page without a Q&A moment When it is inspected Then it has no zoom target
- [ ] **AC-5.4** Given the intro and the outro When inspected Then each has a camera effect: `zoom_in_center` for the intro and `zoom_out_warm_fade` for the outro
- [ ] **AC-5.5** Given a Q&A page whose illustration description gives no position cue for the grounding detail When the script is generated Then its zoom target is exactly `x = 0.5`, `y = 0.5`, `zoom = 1.3`

### Story 6: Safe re-runs and opt-in cloud

**As a** producer
**I want** existing scripts protected and cloud use to be my explicit choice
**So that** hand edits aren't lost and costs stay near zero

**Acceptance criteria:**

- [ ] **AC-6.1** Given a script already exists for the book When the producer runs the generator without the force option Then the existing file is unchanged (byte-identical), a message names the existing file and the force option, and the command exits with a non-zero status
- [ ] **AC-6.2** Given a script already exists When the producer runs the generator with the force option Then the file is replaced by a newly generated, valid script
- [ ] **AC-6.3** Given the same book data file, settings and seed in default (local) mode When the generator is run twice with force Then both output files are byte-identical
- [ ] **AC-6.4** Given the same inputs When the seed setting is changed Then the generator is allowed to produce a different script, which still passes every acceptance criterion above
- [ ] **AC-6.5** Given the producer selects cloud mode and cloud credentials are present When the generator runs Then it sends at most one generation request per attempt, and the script it writes passes the same validation as in local mode

### Story 7: Kid-safety gate

**As a** producer
**I want** obvious safety problems caught automatically and everything else flagged for my review
**So that** nothing unsuitable reaches voice recording without a person having checked it

**Acceptance criteria:**

- [ ] **AC-7.1** Given the disallowed-terms list contains a term When a generated line contains that term (case-insensitive, whole word) Then that attempt fails validation and the error names the line and the term
- [ ] **AC-7.2** Given the disallowed-terms list file does not exist When the generator runs Then it exits non-zero naming the expected file path, sends no generation request, and writes nothing
- [ ] **AC-7.3** Given the disallowed-terms list file exists but contains no terms When the generator runs Then generation proceeds and the run's output message states that the disallowed-terms list is empty
- [ ] **AC-7.4** Given a script is saved successfully When the generator finishes Then its output message states that the script must be reviewed by a person (age-suitability, no spoilers from later pages, no invented story events) before voice synthesis

## Functional Requirements

### FR-1: Input

The generator reads one book data file (format as in the existing `book_data.json`: `metadata`, `style_bible`, `pages` with `page_number`, `text` and illustration `prompt`).

**Must:**
- Accept the book data file path as an input; default to `book_data.json` in the project root.
- Derive the book slug from the book's title (lowercase, words joined by hyphens, punctuation removed; e.g. "The Snail Mail Express" → `the-snail-mail-express`).
- Use each page's text and illustration description as the only sources for kid questions and Storyteller answers.

**Must not:**
- Analyse illustration image files. Only the text descriptions are used.
- Hardcode any book title, page count, character name from a book, or file path.

### FR-2: Episode script contract

The output follows the `episode_script.json` contract in PRD.md §4.2, made exact here.

**Must:**
- Include `book_slug`, `title`, `aspect_ratio` (`"16:9"`), `resolution` (`[1920, 1080]`), `cast`, `intro`, `pages` and `outro`.
- Define `cast` with exactly three roles: `storyteller`, `kid_older` (Leo, age 8, curious, likes solving mysteries) and `kid_younger` (Mia, age 5, playful, notices funny visual details). Each role has a display name, a voice identifier and a description. These come from project configuration and are the same in every episode.
- Give every line a `speaker` (one of the three roles) and `text`. Page lines also have a `role`: `narration`, `question`, `reaction` or `answer`.
- Give every page `page_number`, `has_qa`, a camera block (FR-6) and `lines`. Pages with a Q&A moment also have `qa_grounding` (the grounding phrase from AC-3.5).
- Validate the complete script against this contract before saving it.

**Must not:**
- Save a script that fails validation, or a partial script.

### FR-3: Narration

**Must:**
- Narrate every page's full text, in book order, as one or more Storyteller `narration` lines (AC-2.2).
- Allow emotion tags (FR-5) to be inserted into narration lines without changing the words.
- Split a page's text into narration lines only at sentence ends. Each line holds at most 2 sentences and at most 40 words (emotion tags excluded). A single sentence longer than 40 words stays whole on its own line (AC-2.4).

**Must not:**
- Add, remove, reorder or substitute any word of the page text.

### FR-4: Q&A moments

**Must:**
- Place Q&A moments to satisfy AC-3.1 and AC-3.2.
- Vary participation across the episode: some moments have only Leo, some only Mia, and some both (AC-3.3). Which kids speak in each moment is decided by the generator from the seed, not by a fixed pattern.
- Keep Leo's lines in character (questions about the plot, guesses about mysteries) and Mia's lines in character (reactions to funny or cute visual details, giggles and gasps).
- End every moment with a Storyteller answer that stays consistent with the story so far and does not reveal events from later pages.

**Must not:**
- Contain any term from the project's disallowed-terms list (checked automatically, AC-7.1).
- Contain anything unsuitable for the book's stated age range, reveal events from later pages, ask viewers for personal information, or invent story events that are not in the book. These are checked by a person reviewing the script, not automatically (AC-7.4).

### FR-5: Emotion tags

**Must:**
- Use only these inline tags: `<giggle>`, `<laugh>`, `<chuckle>`, `<gasp>`, `<sigh>`, `<yawn>`.

**Must not:**
- Contain any other angle-bracket tag, markup or stage direction in line text.

### FR-6: Camera hints

**Must:**
- Give each page a narration motion from the allowed set (AC-5.1), and a zoom target only on Q&A pages (AC-5.2, AC-5.3).
- Treat the zoom target as a best-effort estimate of where the grounding detail sits, inferred only from the illustration description's position words (e.g. "left", "foreground", "top"). When the description gives no position cue, use the centre default `x = 0.5, y = 0.5, zoom = 1.3` (AC-5.5). The producer may hand-edit zoom targets in the saved script.
- Give the intro and outro the camera effects in AC-5.4.

### FR-7: Generation modes, reproducibility and re-runs

**Must:**
- Use the local text model by default, with no network access needed.
- Offer cloud mode only as an explicit per-run option.
- Take a seed setting (default fixed) and produce byte-identical output for identical inputs, settings and seed in local mode (AC-6.3).
- Refuse to overwrite an existing script unless the force option is given (AC-6.1, AC-6.2).
- When model output fails validation, retry generation up to 3 attempts in total, then fail.

**Must not:**
- Fall back from cloud to local mode, or from local to cloud mode, without being told to.

## Non-Functional Requirements

### Performance

- Local mode generates a 20-page script in at most 3 minutes on the target machine (M1 Pro, 16 GB), excluding the one-time model download.
- The generator releases the local model when it finishes so the next stage (voice synthesis) has the memory.

### Security

- Cloud credentials are read only from the environment, never from files in the repository, and are never written to logs, output or the script.
- Every generated line is checked against a project-level disallowed-terms list before saving; any match counts as a validation failure (FR-7 retries apply). The list file must exist (AC-7.2); an empty list is allowed but reported (AC-7.3).
- Automated checks cover only the disallowed-terms list and the structural rules in this spec. Age-suitability, spoilers and invented events are a human review gate before voice synthesis (AC-7.4).
- Model output is treated as untrusted: it is used only after it passes validation.

### Reliability

- The output file is either the complete previous file or the complete new file, never a partial write, even if the run is interrupted.
- Every failure exits with a non-zero status and a message naming the cause (and the page number where relevant).

### Cost

- Default mode costs nothing in cloud usage. Cloud mode makes one generation request per attempt (at most 3 per run), targeting under $0.01 per successful run.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Book data file missing or not valid JSON | Exit non-zero naming the file path; nothing written |
| A page lacks `text` or an illustration `prompt`, or page numbers are not 1..N in order | Exit non-zero naming the page and the missing or invalid field; nothing written |
| Book has fewer than 8 or more than 32 pages | Exit non-zero stating the supported range; nothing written |
| Local model not running or not installed | Exit non-zero saying the local model is unavailable and how to start it; no cloud fallback |
| Cloud mode selected but no credentials in the environment | Exit non-zero naming the missing environment variable; no local fallback |
| Model output fails validation (narration mismatch, bad tag, Q&A rule, disallowed term) 3 times | Exit non-zero listing the validation errors from the last attempt; nothing written |
| Disallowed-terms list file missing | Exit non-zero naming the expected file path; no generation request sent; nothing written |
| Disallowed-terms list file empty | Proceed; output message states the list is empty |
| Script already exists and force not given | Exit non-zero naming the file and the force option; file unchanged |
| Run interrupted mid-write | Previous file (if any) intact; no partial file left at the output path |

## Open Questions

None. Resolved during specification:
- Narration is verbatim (emotion tags allowed).
- Local mode is the default; cloud is opt-in per run.
- Kid cast is a fixed recurring pair (Leo ~8, Mia ~5); each Q&A moment has one or both kids, varied by seed.
- Existing scripts are kept unless forced.
- Output is reproducible for the same inputs, settings and seed in local mode.

## Out of Scope (Future Considerations)

- YouTube metadata generation (title, description, chapter timestamps, tags).
- Intro/outro keyframe image generation.
- 9:16 vertical format variants.
- Per-book cast overrides or additional characters.
- Automatic quality scoring of banter beyond the rule checks above; human review of the final episode remains the release gate.
