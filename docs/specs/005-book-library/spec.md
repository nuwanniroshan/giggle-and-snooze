# Spec 005: Book library and folder layout

**Status:** Approved
**Created:** 2026-11-09
**Branch:** `feat/005-book-library`
**Depends on:** specs 001 (episode script), 002 (voice synthesis), 003 (render), 004 (studio)
**Source:** `docs/plans/book-factory-stories.md`, Story 1

---

## Problem Statement

A book's files are spread over three places: the project root (`book_data.json`, `assets/illustrations/`, `assets/references/`), `episodes/<slug>/` (script, nooks), and `build/` (flat files named after the pilot title). The build scripts hardcode the project path and the pilot's title. Adding a second book means copying files into four places and editing code. The producer needs any number of books, each in one predictable folder, with its generated outputs in that same folder inside the project.

## Goals

- Every book lives in exactly one folder, `books/<slug>/`, holding its source and its generated outputs.
- Every command that works on a book takes the book's slug. No book title or book path is written into code.
- The pilot book moves into the new layout with its content unchanged.
- Generated outputs are git-ignored. Source files are committed.
- The studio lists every book folder and flags the ones it cannot use.

## Non-Goals

- Publishing outputs (spec 006).
- Creating books from AI ideas (spec 007) or per-book art styles (spec 008).
- Changing the content, styling or pacing of any book or video.
- Moving shared project settings: voice and cast config, render settings, kid-safety terms, shared sound effects, shared fallback nook. They stay at the project root.
- Removing the JSON upload in the studio. Spec 007 replaces it.
- Access from other machines or multiple users.

## Users and Context

**Primary users:** the producer, on the M1 Pro, using the terminal and the studio.
**Secondary users:** none.
**Usage context:** adding a book, rebuilding a book, switching between books.
**User mental model:** "a book is a folder."

## User Stories

### Story 1: One folder per book

**As a** producer
**I want** each book to be one folder holding its source and its outputs
**So that** I can find, copy and manage a book without searching the project.

**Acceptance criteria:**

- [ ] **AC-1.1** Given `books/the-snail-mail-express/book.json` exists When the book library is read Then the book is listed with the slug `the-snail-mail-express` and the title from its `book.json`
- [ ] **AC-1.2** Given a folder in `books/` has no `book.json` When the book library is read Then the folder is reported as a problem that names the missing file, and it is not listed as a book
- [ ] **AC-1.3** Given a folder in `books/` has a name with uppercase letters, spaces, dots or other characters outside lowercase letters, digits and hyphens When the book library is read Then the folder is reported as an invalid slug and is not used
- [ ] **AC-1.4** Given the producer creates a new folder `books/<new-slug>/` containing a valid `book.json` and no code has changed When the book library is read Then the new book is listed

### Story 2: Commands target one book by slug

**As a** producer
**I want** every book command to take a slug
**So that** the same tools produce any book.

**Acceptance criteria:**

- [ ] **AC-2.1** Given `books/the-snail-mail-express/` exists When the book build is run with `--book the-snail-mail-express` Then every output is written under `books/the-snail-mail-express/build/` and nowhere else in `books/`
- [ ] **AC-2.2** Given `--book` names a slug with no folder in `books/` When any book command is run Then it exits with a non-zero status, lists the available slugs, and writes no files
- [ ] **AC-2.3** Given `--book` is omitted When any book command is run Then it exits with a non-zero status and a usage message that lists the available slugs, and writes no files
- [ ] **AC-2.4** Given two books exist When the build is run for one of them Then the files of the other book are byte-identical before and after the run
- [ ] **AC-2.5** Given `--book` is given to a video command (script, voices, render, approve) When it runs Then it reads and writes only inside `books/<slug>/` and the shared project settings

### Story 3: The pilot moves without loss

**As a** producer
**I want** the existing pilot book moved into the new layout with no change to its content
**So that** nothing I have already made is lost.

**Acceptance criteria:**

- [ ] **AC-3.1** Given the pilot's files exist in their current locations When the migration is run Then `books/the-snail-mail-express/` contains `book.json` (renamed from `book_data.json`, content unchanged), the 20 pages, both covers, the map, the references and the episode script, and each file's content hash equals the original's hash
- [ ] **AC-3.2** Given the pilot has been migrated When the book build is run Then the 20-page digital PDF, the complete reader PDF, the 24-page KDP print interior and the EPUB each have the same page count, page sizes and extracted text as before the migration
- [ ] **AC-3.3** Given the migration has already run When it is run again Then it changes no file and reports that nothing needed to move
- [ ] **AC-3.4** Given a copy step fails during the migration When the migration stops Then every original file is still in its old location, unchanged

### Story 4: Source is committed, outputs are ignored

**As a** producer
**I want** generated outputs kept out of git
**So that** the repository stays small while source stays versioned.

**Acceptance criteria:**

- [ ] **AC-4.1** Given a book has been built and rendered When git status is checked Then nothing under `books/<slug>/build/` or the project-level `build/` folder is listed as untracked or modified
- [ ] **AC-4.2** Given a book's `book_data.json`, illustrations, nooks or episode script is edited When git status is checked Then the edited file is listed as modified

### Story 5: The studio lists every book

**As a** producer
**I want** the studio to list every book and flag the folders it cannot use
**So that** I can see which books are ready to work on.

**Acceptance criteria:**

- [ ] **AC-5.1** Given three valid book folders exist When the studio home page loads Then it lists three books, each with its slug and title
- [ ] **AC-5.2** Given one folder fails validation When the studio home page loads Then the other books are listed normally and the invalid folder is shown with the problem text from AC-1.2 or AC-1.3
- [ ] **AC-5.3** Given a book is selected When its stage status is shown Then the status is derived only from files inside that book's folder

---

## Functional Requirements

### FR-1: Book folder layout

Each book is the folder `books/<slug>/` with this layout:

```
books/<slug>/
  book.json             source: metadata, style bible, 20 pages (required)
  illustrations/        source: page_NN.png, cover_front.png, cover_back.png, map (optional)
  references/           source: art inputs for this book (optional)
  nooks/                source: intro_nook.png, outro_nook.png (optional)
  episode/              source: episode_script.json, script review mark, audio approval mark
  build/                generated: every output for this book (ignored by git)
```

**Must:**
- Hold every file that belongs to one book inside its folder.
- Keep human decisions (script review, audio approval) in `episode/`, which is committed. They are never stored only in `build/`.

**Must not:**
- Store a book's source or generated files outside its folder, except the shared project settings listed in FR-3.

### FR-2: Slug rules

A slug is the book's folder name.

**Must:**
- Use only lowercase letters, digits and single hyphens, and start with a letter or digit.
- Be unique, which a folder name already guarantees.

**Must not:**
- Contain uppercase letters, spaces, dots, slashes or any path separator.
- Be used to build a path outside `books/` (no `..` and no absolute paths).

### FR-3: Shared project settings

These stay at the project root and apply to every book:

- `config/` (cast, voices, render settings, disallowed terms)
- `assets/audio_sfx/` (shared sound effects)
- `assets/nook/` (shared fallback nook images)

**Must:** read these from fixed project-root locations.
**Must not:** copy them into a book folder.

### FR-4: Generated output locations

**Must:**
- Write each book's generated output under `books/<slug>/build/`.
- Write project-level tool output (studio job records and lock, shared sound-effect cache, voice samples) under `build/` at the project root.
- Write outputs atomically, so a failed run never replaces a good output with a partial one.

**Must not:**
- Write generated files into any folder of a book other than `build/`.

### FR-5: No book identity in code

**Must:** take every title, author, page count and page size from the book's `book.json`.
**Must not:** contain the pilot's title, its file names or its path in any code path. Tests and the migrated pilot's own files are the only exceptions.

### FR-6: Command interface

Every command that works on a book takes `--book <slug>`: the book build, the illustration composer, and the video stages (script, voices, approve, render).

**Must:** require `--book`, validate it against FR-2, and report the available slugs on error.
**Must not:** guess a book when `--book` is missing.

### FR-7: Pilot migration

A one-time command moves the pilot's files from their current locations into `books/the-snail-mail-express/`.

**Must:**
- Copy each file, verify its content hash against the original, and only then remove the original.
- Skip any file already at its new location with the same hash, so reruns change nothing.
- Report each file as moved, already in place, or failed.

**Must not:** delete any original file whose copy has not been verified.

### FR-8: Git ignore

**Must:** ignore `books/*/build/` and the project-level `build/` folder. Source files listed in FR-1 are not ignored.

### FR-9: Studio book list

**Must:**
- List valid books sorted by slug.
- Show each book's title from its `book.json`.
- Derive each book's stage status only from files inside its own folder.
- Show problem folders (AC-1.2, AC-1.3) with their reason, without hiding the valid books.

**Must not:** list a folder that fails validation as a usable book.

---

## Non-Functional Requirements

### Performance

- Reading the book library with 50 book folders completes in under 1 second.

### Security

- A slug that fails FR-2 is rejected before any file is read or written.
- Book commands read and write only inside `books/<slug>/`, the project-level `build/` folder and the shared settings in FR-3.

### Reliability

- A failed or interrupted command leaves the book folder in a consistent state: no partial output replaces a complete one.
- Human decisions in `episode/` survive any rebuild of `build/`.
- The migration never deletes an original before its copy is verified (AC-3.4).

---

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Book folder missing `book.json` | Listed as a problem naming the file; not a book (AC-1.2) |
| `book.json` invalid JSON or fails validation | Listed as a problem naming the file and field; other books unaffected |
| Folder name fails the slug rules | Listed as an invalid slug; not used (AC-1.3) |
| `--book` names a missing slug | Non-zero exit; available slugs listed; no files written (AC-2.2) |
| `--book` omitted | Non-zero exit; usage message with available slugs; no files written (AC-2.3) |
| Migration copy fails or hash mismatch | Migration stops; originals untouched; failed file reported (AC-3.4) |
| Migration rerun | Nothing changed; reported as already in place (AC-3.3) |
| Interrupted build | Previous complete output in `build/` kept; partial output discarded |
| Output folder cannot be written (disk full, permissions) | Command fails with the path it could not write; source untouched |

---

## Decisions

- **Single cut-over.** The pilot is migrated in one step. After AC-3.2 passes, the old locations are removed. No old path stays readable.
- **Source file is `book.json`.** Every book's source file has this name. The pilot's root `book_data.json` is renamed during the migration, with its content unchanged. The studio and build commands read `book.json` only.
- **`--book` is always required** (FR-6). No command picks a book on its own.

---

## Out of Scope (Future Considerations)

- Publish outputs and their version record (spec 006).
- Creating books from AI ideas, and per-book art styles and illustration generation (specs 007, 008).
- Per-book background music.
- Retiring the studio's JSON upload form (spec 007).
