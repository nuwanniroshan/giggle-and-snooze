# Spec 017: Feelings in the episode script

**Status:** Approved
**Created:** 2026-11-13
**Branch:** `feat/017-script-emotions`
**Depends on:** specs 001 (episode script), 002 (voices), 004 (studio), 011 (blueprint)

---

## Problem Statement

Generated episode scripts sound flat. The writing brief allows the voice model's sound tags (`<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`) but never asks for them, so most scripts have zero to four in a whole episode. Lines also carry no sense of how they should be said, so an excited kid and a sleepy goodnight come out in the same voice. The producer wants every script to carry feelings: sound tags where they fit, and a feeling per line that changes how the line is spoken.

## Goals

- Every newly generated script contains a reasonable number of sound tags, spread across the episode.
- Every spoken line written by the model can carry a feeling from a fixed list, and that feeling audibly changes delivery.
- The producer can see and edit feelings in the studio script view.
- Existing scripts and approved audio keep working unchanged.

## Non-Goals

- New sound tags beyond the six the voice model supports.
- Changing the voice model or using a different model per feeling.
- Feelings on the book's own page text read by the storyteller (the story is read as written).
- Rewriting existing books' scripts.

## Users and Context

**Primary user:** the producer, generating and reviewing scripts in the studio or CLI.
**Context:** stage 1 (script) and stage 2 (voices). Kid-safety and review rules stay in force.

## User Stories

### Story 1: Scripts with sound tags
As the producer, I want generated scripts to include sound tags so that the kids and storyteller sound alive.

- [ ] **AC-1.1** Given a book with M Q&A moments When a script is generated Then the brief asks for sound tags where they fit and states the minimum and maximum count.
- [ ] **AC-1.2** Given a generated banter with fewer than the minimum tags (default: M) When it is checked Then it is refused with a message giving the count and the minimum, and the model is asked to fix it (same retry path as other banter problems).
- [ ] **AC-1.3** Given a generated banter with more than the maximum tags (default: 2×M) or more than one tag in a line When it is checked Then it is refused with the count or the line named.
- [ ] **AC-1.4** Given an existing or hand-edited script with fewer tags than the minimum When it is loaded, validated, voiced or rendered Then it is accepted: the minimum applies only to newly generated banter.
- [ ] **AC-1.5** The minimum and maximum come from config, not code.

### Story 2: A feeling per line
As the producer, I want each model-written line to carry a feeling so that delivery matches the moment.

- [ ] **AC-2.1** Given the feelings list in config (default: neutral, excited, curious, happy, calm, sleepy, whisper, surprised) When a script is generated Then the brief lists them and each intro, moment and outro line may carry an `emotion` from the list.
- [ ] **AC-2.2** Given a line with an `emotion` not in the list When the script is validated Then it is refused naming the line and the allowed feelings.
- [ ] **AC-2.3** Given a line without `emotion` When it is voiced Then it is spoken as `neutral`, and its audio is identical to today's (same clip cache key).
- [ ] **AC-2.4** Given a generated banter where every line is `neutral` or missing When it is checked Then it is refused asking for feelings that fit the moments (at least one non-neutral line per moment by default, from config).

### Story 3: Feelings change the voice
As the producer, I want feelings to change how a line sounds so that the difference is audible.

- [ ] **AC-3.1** Given config gives each feeling a speed factor, a pitch offset in semitones and a loudness change in dB When a line with that feeling is voiced Then those are applied on top of the role's pitch lift, and the measured duration, pitch and loudness differ from the neutral version in the configured direction.
- [ ] **AC-3.2** Given `neutral` (all zero/1.0) When a line is voiced Then no extra processing runs.
- [ ] **AC-3.3** Given a line's feeling is changed When voices run Then only that line's clip is re-made (the clip cache key includes the feeling) and the audio needs approval again as for any text change.
- [ ] **AC-3.4** Given a feeling's settings outside safe limits (speed 0.8–1.25, pitch −2 to +2 semitones, loudness −9 to +3 dB) When config is loaded Then it fails naming the feeling and field.
- [ ] **AC-3.5** Given a voiced line with a feeling When the audio checks run (spec 002 words-per-second, silence) Then the checks use the changed audio and still pass for configured defaults.

### Story 4: See and edit feelings in the studio
As the producer, I want to see and change feelings in the script view so that I can fix a wrong one before voicing.

- [ ] **AC-4.1** Given a script When the studio script view is shown Then each intro, moment and outro line shows its feeling (neutral when missing).
- [ ] **AC-4.2** Given the producer picks a different feeling for a line and saves When the script is saved Then the file stores it, validation runs as for text edits, and an invalid value is refused with the message.

## Functional Requirements

- **FR-1** The script contract must accept an optional per-line `emotion` on intro, moment and outro lines, limited to the configured list.
- **FR-2** Generation checks must enforce the tag minimum, tag maximum, one-tag-per-line limit and the non-neutral-per-moment minimum; loading and validating existing scripts must not.
- **FR-3** Voice synthesis must apply each feeling's speed, pitch and loudness after synthesis, before stitching, and include the feeling in the clip cache key only when it is not neutral.
- **FR-4** Feelings, their delivery settings and the tag counts must live in config.
- **FR-5** The studio script view must show and edit feelings.
- **FR-6** The script hash rule is unchanged except that a feeling change counts as a line change (needs review and voice approval again).

## Non-Functional Requirements

- No extra model loads: feelings are applied to the synthesized audio, not by re-prompting the voice model.
- A neutral-only script voices in the same time as today.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Model keeps writing too few tags | Refused up to the existing attempt limit; run fails with the last problems listed. |
| Unknown feeling in a hand edit | Studio save / CLI validation refuses, naming line and allowed list. |
| Bad feeling settings in config | Voices stage fails at start naming the feeling and field. |

## Open Questions

None.

## Out of Scope

- Feelings for page narration.
- Background music (separate future spec).
