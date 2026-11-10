# Spec 006: Add a book from an AI idea

**Status:** Approved
**Created:** 2026-11-09
**Branch:** `feat/006-idea-to-book`
**Depends on:** specs 001 (script generation, provider runner), 004 (studio), 005 (book folders)
**Source:** `docs/plans/book-factory-stories.md`, Story 2 (text part). The plan's "007" label is this spec; spec 006 is taken by the next free number. Art style and illustrations are spec 008.

---

## Problem Statement

Adding a book needs a hand-written `book.json` and a set of illustrations, which the producer uploads through the studio. The producer wants to start from an idea instead: ask for a few story ideas, or type one, pick the one they like, and receive a complete book draft to review before anything else is produced.

## Goals

- The producer can get 3 to 5 story ideas on request, or improve an idea they type.
- After the producer picks an idea, a full book draft (text and per-page illustration prompts) is written automatically.
- The producer reviews and edits the draft text before any image or audio work starts.
- An approved draft becomes a book folder under `books/<slug>/` (spec 005) that the existing stages can use.
- The JSON upload is removed from Add book.

## Non-Goals

- Per-book art style and illustration generation (spec 008).
- Choosing the cast, voices or video style per book. Every book uses the existing cast.
- Publishing, or any change to the script, voice and video stages.
- Writing books in languages other than English.
- Editing the book's structure (number of pages, layouts) in the browser. Only page text is editable.

## Users and Context

**Primary users:** the producer, on the M1 Pro, in the studio's Add book page.
**Secondary users:** none.
**Usage context:** planning the next release. The producer may try several ideas before picking one.
**User mental model:** "I describe a story, the studio writes it, I read it, and then it becomes a book."

## User Stories

### Story 1: Suggest story ideas

**As a** producer
**I want** the studio to suggest a few story ideas
**So that** I can choose a story without starting from a blank page.

**Acceptance criteria:**

- [ ] **AC-1.1** Given the Add book page is open When the producer chooses "Suggest ideas" Then between 3 and 5 ideas are shown, each with a title, a premise of one to two sentences, an age range and a core lesson
- [ ] **AC-1.2** Given ideas are shown When the producer chooses "Suggest more" Then a new set replaces the list, and none of the previous titles appears again in that set
- [ ] **AC-1.3** Given the text provider is unavailable When the producer chooses "Suggest ideas" Then no idea is shown, the page names the reason, and nothing is saved
- [ ] **AC-1.4** Given the provider returns ideas that break the rules in FR-5 on every attempt When the attempts are used up Then the page shows the problems found, and nothing is saved

### Story 2: Improve an idea I type

**As a** producer
**I want** to type my own idea and get a stronger version
**So that** my rough idea becomes a clear premise I can pick.

**Acceptance criteria:**

- [ ] **AC-2.1** Given the producer has typed an idea of 10 to 500 characters When they choose "Improve idea" Then one improved candidate is shown with a title, a premise of one to two sentences, an age range and a core lesson
- [ ] **AC-2.2** Given the typed idea is empty or shorter than 10 characters When the producer chooses "Improve idea" Then the page says the idea is too short and nothing is sent to the provider
- [ ] **AC-2.3** Given a candidate is shown When the producer chooses "Improve again" Then the new candidate replaces the old one and the typed idea is kept
- [ ] **AC-2.4** Given the typed idea asks for content that breaks FR-5 When the producer chooses "Improve idea" Then the page says which part cannot be used, and no candidate is shown

### Story 3: Pick an idea and get a book draft

**As a** producer
**I want** a complete draft written from the idea I pick
**So that** I can read the whole story before anything else is made.

**Acceptance criteria:**

- [ ] **AC-3.1** Given an idea is shown When the producer chooses "Write this book" with a maximum page count between 8 and 32 (default 20) Then a draft is created, the page shows it as "writing", and the text is generated in the background
- [ ] **AC-3.2** Given the text is generated When the draft is checked Then it has at least 8 pages and no more than the maximum the producer chose, numbered consecutively from 1, and every page has a title, text, layout, and an illustration prompt. The story is written to fit the maximum; the draft may have fewer pages than the maximum
- [ ] **AC-3.2a** Given the producer enters a maximum page count outside 8 to 32 When they choose "Write this book" Then the page says the allowed range and nothing is created
- [ ] **AC-3.3** Given a generated page breaks FR-5 When the draft is checked Then the text is regenerated up to the attempt limit; if it still fails, the draft is marked "failed" and lists the problems
- [ ] **AC-3.4** Given a draft is ready for review When the producer edits a page's text and saves Then the new text is checked against FR-5, and a save that fails the check changes nothing
- [ ] **AC-3.5** Given a draft is ready for review When the producer chooses "Create book" Then the draft becomes `books/<slug>/book.json` with the slug from the title, and the draft is removed
- [ ] **AC-3.6** Given a book with the same slug already exists When the producer chooses "Create book" Then the studio refuses, names the existing book, and keeps the draft
- [ ] **AC-3.7** Given a draft is being written When the studio is restarted Then the draft is listed as "interrupted" with its idea, and the producer can write it again from the same idea
- [ ] **AC-3.8** Given a draft exists When the producer chooses "Discard" Then the draft and its generation log are deleted after a confirmation

### Story 4: The upload is gone

**As a** producer
**I want** Add book to offer only the idea flow
**So that** I am not asked for a JSON file.

**Acceptance criteria:**

- [ ] **AC-4.1** Given the Add book page is opened When it loads Then no file upload field is shown
- [ ] **AC-4.2** Given a request that posts a book file to the add-book route When it is received Then it is refused and nothing is saved

---

## Functional Requirements

### FR-1: Idea suggestions

The system asks the text provider for 3 to 5 ideas per request. Each idea has: a title (3 to 60 characters), a premise (one to two sentences), an age range from the supported list in FR-6, and a core lesson (one sentence).

**Must:** return only ideas that pass FR-5.
**Must not:** repeat a title that was shown earlier in the same "Suggest more" sequence.

### FR-2: Idea improvement

The system asks the text provider to turn the producer's typed idea into one candidate with the same fields as FR-1.

**Must:** keep the producer's core characters and setting unless they break FR-5.
**Must not:** change the age range the producer chose, if one was chosen.

### FR-3: Book draft

A draft is a story written from the picked candidate, with at least 8 pages and no more than the maximum page count the producer chose (8 to 32, default 20). Each page has:

- `page_number` (1 to the page count, consecutive)
- `title` (short)
- `text` (read-aloud narration, short sentences, one to four sentences per page)
- `layout` (one of the layouts the book production already supports)
- `prompt` (a visual description of the page, used by spec 008)
- `illustration_file` (`page_NN.png`)

The draft also has `metadata` (title, author, age range, blurb), the characters, and a `style_bible` placeholder left for spec 008.

**Must:** make the draft match the book data rules that already apply to every book (spec 005 and the book validation in `episode.load_book`).
**Must not:** write any image or audio file.

### FR-4: Draft storage and lifecycle

A draft lives in `drafts/<draft-id>/` at the project root, with its JSON, its generation log and its state. Drafts are git-ignored: only a finished book (`books/<slug>/`) is committed. A draft is in one of these states: `writing`, `ready`, `failed`, `interrupted`, `created`.

**Must:** keep a draft across studio restarts.
**Must:** keep `drafts/` git-ignored.
**Must:** delete a draft's generation log with it.
**Must not:** create a draft with `created` state; a created draft is removed once its book exists.

### FR-5: Content rules

Every generated line (idea, premise, lesson, page title and page text) is checked against:

- the disallowed-terms list in `config/disallowed_terms.txt` (the same list the script stage uses),
- the rule that the story is gentle for its chosen age range: no graphic harm, no death, no frightening threats the story does not resolve on the same page,
- the length limits in FR-1 and FR-3.

**Must:** refuse any line that fails, and say which line failed and why.
**Must not:** pass a failing line to the producer as a valid draft.

### FR-6: Supported age ranges

The age ranges offered are `Ages 3-6`, `Ages 4-7` and `Ages 5-8`. Any other value is refused.

### FR-7: Attempts and time limits

Each provider request is limited to a fixed number of attempts (default 3) and a time limit (default 5 minutes, set in a config file the producer can edit). A time-out counts as a failed attempt.

**Must:** report the number of attempts used on the page when a request fails.

### FR-8: Text provider

The only text provider is the Codex agent on the producer's ChatGPT login. The agent may only use the tools this feature provides. If Codex is unavailable, the request fails; there is no other provider.

### FR-9: Generation log

Each provider run keeps its log (its steps and tool calls, never credentials) in the draft's folder.

### FR-10: Studio pages

- **Add book:** "Suggest ideas", a text box for a typed idea, "Improve idea", "Improve again", "Write this book" per candidate, and the list of drafts with their states.
- **Write this book:** a maximum page count field (8 to 32, default 20) next to each candidate.
- **Draft page:** the pages with editable text, the state, the attempts used, "Create book" (enabled only when the draft is ready and every page is valid) and "Discard".

---

## Non-Functional Requirements

### Performance

- A suggestion request returns within the time limit in FR-7 or fails.
- The studio stays usable while a draft is being written: page loads and other drafts respond within 1 second.

### Security

- Draft folders and generation logs stay inside `drafts/`. A draft ID that would leave that folder is refused.
- Nothing from the producer's machine or the provider is sent anywhere except the provider run itself.

### Reliability

- A draft write is atomic: a crash leaves the previous state of the draft, never a half-written page list.
- "Create book" writes the book folder in one step; a failure leaves the draft intact and no partial book folder.

---

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Codex not installed or not logged in | Page names the missing provider; nothing saved (AC-1.3) |
| Provider output not valid JSON or breaks FR-5 | Retried up to the attempt limit; then the problems are shown (AC-1.4, AC-3.3) |
| Provider run exceeds the time limit | Counted as a failed attempt; draft marked `failed` if all attempts fail |
| Studio stopped during writing | Draft shown as `interrupted`; the producer can write again (AC-3.7) |
| Typed idea too short or too long | Refused before any provider run (AC-2.2) |
| Maximum page count outside 8 to 32 | Refused before any provider run (AC-3.2a) |
| "Create book" with a slug that exists | Refused; draft kept (AC-3.6) |
| Edited page text fails FR-5 | Save refused; previous text kept (AC-3.4) |
| Disk full while creating the book | Book folder not created; draft kept; error names the folder |

---

## Decisions

- **Page count:** the producer picks a maximum page count from 8 to 32 (default 20). The story fits that maximum and may be shorter.
- **Drafts are git-ignored** (`drafts/`). A finished book is committed as source in `books/<slug>/`.
- **Author:** set per book on the Add book page; the pilot's author is the default.

## Open Questions

None.

---

## Out of Scope (Future Considerations)

- Art style and illustration generation (spec 008).
- Background music per book.
- Editing page layouts or the number of pages in the browser.
- Other languages and other age ranges beyond FR-6.
