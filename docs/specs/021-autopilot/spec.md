# Spec 021: Autopilot from idea to finished book

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/021-autopilot`
**Depends on:** specs 004, 006, 009, 010, 011, 016, 017, 018, 019, 020

---

## Problem Statement

After Create book the producer must start and wait for each stage in turn: paint, cut layers, write the script, review it, make voices, listen and approve, render, build the editions. The producer wants the studio to carry a book from a chosen idea to a finished video and editions without stopping.

## Goals

- One switch runs every stage after the idea is chosen, in order, with no stops.
- The automatic script review and audio approval are recorded as automatic and visible, so a person can still check them later.
- A failure stops the run at that stage with the reason, and the run can be resumed.

## Non-Goals

- Publishing anywhere.
- Running stages in parallel.
- Removing the manual path: every stage keeps its own button.

## Users and Context

**Primary user:** the producer. Content safety rests on the automatic checks (disallowed and gentle terms, kid line limits, audio checks) while autopilot runs.

## User Stories

### Story 1: Turn autopilot on
- [ ] **AC-1.1** Given the Add book page When the producer chooses an idea with Autopilot on (default on, a checkbox) Then after the draft is written the book is created automatically, without the draft review stop.
- [ ] **AC-1.2** Given a book page When the producer presses Run autopilot Then the run starts from the first unfinished stage.
- [ ] **AC-1.3** Given autopilot is running When the producer presses Stop Then the current job stops and no later stage starts.

### Story 2: Every stage in order
- [ ] **AC-2.1** Given autopilot When each stage succeeds Then the next starts in this order: paint (with plans) → cut layers → script → automatic review mark → voices → automatic approval → render → build editions.
- [ ] **AC-2.2** Given a stage already done and up to date When autopilot reaches it Then it is skipped.
- [ ] **AC-2.3** Given the run finishes When the book page is shown Then it says "Finished by autopilot" with the time, and links the video and editions.

### Story 3: Automatic review and approval are marked
- [ ] **AC-3.1** Given autopilot marks the script reviewed When the reviewed mark is written Then it records `"by": "autopilot"`, and the script page shows "Reviewed automatically".
- [ ] **AC-3.2** Given autopilot approves the audio When the status is written Then it records `"by": "autopilot"`, and the audio page shows "Approved automatically".
- [ ] **AC-3.3** Given audio clips failed their checks after all attempts When voices finish Then autopilot does not approve and stops with the failing lines named.

### Story 4: Failures stop and resume
- [ ] **AC-4.1** Given a stage fails When autopilot sees it Then the run stops, the book page shows which stage failed and its log, and no later stage starts.
- [ ] **AC-4.2** Given a stopped or failed run When the producer presses Run autopilot Then it resumes from the first unfinished stage.
- [ ] **AC-4.3** Given the studio restarts during a run When it starts again Then the run is shown as interrupted and is not resumed by itself.

## Functional Requirements

- **FR-1** Autopilot must use the existing stage commands and the one-job rule.
- **FR-2** The run state (book, stage, state, timestamps, by) must be stored on disk under the studio's build folder.
- **FR-3** Manual buttons stay; a manual start while autopilot runs is refused as busy.
- **FR-4** The `--reviewed` gate and approval gate stay in the CLI; autopilot satisfies them by writing the marks, recorded as automatic.

## Non-Functional Requirements

- Stages run one at a time to fit 16 GB.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Codex unavailable | Stops at the first stage that needs it, reason shown. |
| Voice clip fails checks | No approval; stops at voices with lines named. |
| Studio killed | Run marked interrupted on restart. |

## Open Questions

None.

## Out of Scope

- Scheduling, batches of books, publishing.
