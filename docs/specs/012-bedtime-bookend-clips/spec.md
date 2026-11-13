# Spec 012: Bedtime bookend clips

**Status:** Approved
**Version:** 3.0.1
**Created:** 2026-11-11
**Last Updated:** 2026-11-12
**Branch:** `feat/012-bedtime-bookend-clips`
**Depends on:** spec 001 (episode script), spec 003 (render), channel intro (`channel_intro` in `config/render.json`)

---

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-12 | Initial approved spec: clips joined with plain cuts |
| 2.0.0 | 2026-11-12 | MAJOR: FR-5 rewritten (cross-dissolve joins replace plain cuts); new Story 4 and FR-6/FR-7 (voice level and ambience level); the producer trimmed the clips themselves (opening about 1.2 s, ending about 17 s) |
| 3.0.1 | 2026-11-12 | PATCH: the start/end fades of FR-5 are the final grade's fade settings (spec 013); with the grade disabled there are no fades |
| 3.0.0 | 2026-11-12 | MAJOR: FR-5 rewritten again: joins are 1.5 s dissolves (was 0.8 s), and the video now fades in from black (0.8 s) and out to black and silence (2 s), which v2 forbade; new AC-5.5/5.6. The producer replaced both clips (opening 9.3 s, ending 10.0 s) because the 1.2 s opening felt too short |

## Problem Statement

Each episode opens with the channel logo reveal and then goes straight to the book cover, and it ends on the outro with no emotional close. The producer has two finished clips of a mother and two children in a bedroom (the 10 s "getting comfy and starting to read" clip and the 20 s "kids fall asleep, mother smiles" clip). They should frame every story like a real bedtime reading.

## Goals

- Episode order becomes: channel logo reveal → opening clip → book cover → story pages → (outro as today) → ending clip, with the clips' own sound.
- The two clips are shared channel assets used by every book.
- Rendering stays cached, and a missing or changed clip is reported or re-rendered correctly.

## Non-Goals

- Making or editing the clips themselves.
- Per-book clip choice or per-book override.
- Changing the logo reveal, the cover, the story pages or the outro content.
- Background music.

## Users and Context

The producer (single user) renders episodes through the studio or CLI on one Mac. The clips are 1280×720, 24 fps, H.264 with AAC audio (10 s and 20 s); episodes are 1920×1080, 30 fps.

## User Stories

### Story 1: Opening clip after the logo

**As a** producer **I want** the opening clip right after the logo reveal **So that** viewers see the family settle in before the book starts.

- [ ] **AC-1.1** Given a configured opening clip When an episode is rendered Then the final video plays the logo reveal, then the whole opening clip, then the book cover, in that order
- [ ] **AC-1.2** Given the opening clip When it is played in the episode Then its picture fills 1920×1080 at 30 fps without stretching (letterboxed or cropped per FR-3) and its own audio is heard at its original level
- [ ] **AC-1.3** Given no opening clip is configured When an episode is rendered Then the output is identical to a render before this spec

### Story 2: Ending clip

**As a** producer **I want** the kids-asleep clip at the end **So that** the episode closes on the mother smiling at them.

- [ ] **AC-2.1** Given a configured ending clip When an episode is rendered Then the whole ending clip plays after the last story or outro section and is the last thing in the video
- [ ] **AC-2.2** Given the ending clip When it is played Then it meets AC-1.2
- [ ] **AC-2.3** Given no ending clip is configured When an episode is rendered Then the output is identical to a render before this spec

### Story 3: Shared, cached, safe

**As a** producer **I want** the clips treated like the logo intro **So that** every book gets them and re-renders stay fast.

- [ ] **AC-3.1** Given the clips are configured When any book is rendered Then both are used without any per-book setting
- [ ] **AC-3.2** Given a book was rendered When it is rendered again with unchanged inputs Then the clips are not re-encoded
- [ ] **AC-3.3** Given a clip file changed on disk When the book is rendered Then the clip is re-encoded and the video's record lists the clip's new checksum
- [ ] **AC-3.4** Given a configured clip file does not exist When the render starts Then it stops before encoding with an error naming the missing path
- [ ] **AC-3.5** Given a clip has no audio track When it is rendered Then silence of the clip's length is used and the render succeeds
- [ ] **AC-3.6** Given a single-page preview render (`--preview-page`) When it is rendered Then no clip is included

## Functional Requirements

- **FR-1** The clip file paths are configured in the shared render settings; the files live under the project's shared brand assets, not under a book.
- **FR-2** The opening clip is joined after the channel intro and before the cover section; the ending clip is joined after the final section.
- **FR-3** A clip's picture is fit to the episode frame preserving aspect ratio; 16:9 sources fill it exactly.
- **FR-4** The video's record JSON lists each clip's checksum and length, and the episode's total length includes them.
- **FR-5** Each join between neighbouring parts (logo to opening clip, opening clip to cover, outro to ending clip) is a 1.5 s cross-dissolve (`channel_blend_seconds`): the picture dissolves and the sound cross-fades over the same 1.5 s. The whole video fades in from black over 0.8 s (sound fades in over the same time) and fades out to black and silence over its last 2.0 s. All three lengths are shared render settings; the start and end fades are the final grade's fade settings (spec 013), so with the grade disabled there are none.
- **FR-6** The story's voices (storyteller and both kids) are played louder: +6 dB over the previous mix, with the peak limit of -1 dBFS still holding. This applies to every book.
- **FR-7** The ambience level is 6 dB lower than before (`ambience_gain_db` from -14 to -20). This applies to every book.

### Story 4: Voice and ambience levels

**As a** producer **I want** the story voices louder and the ambience quieter **So that** the voices are as clear as the clips' own sound.

- [ ] **AC-4.1** Given an episode rendered with the same script and sound picks before and after this change When the voice track alone is compared Then the voices are 6 dB louder (within 0.5 dB) unless the -1 dBFS peak limit reduces them, and the master peak is never above -1 dBFS
- [ ] **AC-4.2** Given an episode with ambience When the master mix is built Then the ambience gain is -20 dB relative to the previous -14 dB setting, and nothing else in the mix changes
- [ ] **AC-4.3** Given the pilot book rendered after this change When its integrated loudness is measured over the story section Then it is within 3 LU of the clips' integrated loudness (clips measured -18 to -21 LUFS)

### Story 5: Blended joins

- [ ] **AC-5.1** Given the logo, an opening clip and a body When they are joined Then the join between the logo and the opening clip, and between the opening clip and the cover, are each 1.5 s cross-dissolves in picture and sound
- [ ] **AC-5.2** Given an ending clip When it is joined to the end of the body Then the join is a 1.5 s cross-dissolve in picture and sound, and the video's total length equals the sum of the part lengths minus 1.5 s per join
- [ ] **AC-5.3** Given a clip shorter than twice the dissolve length When it is joined on both sides Then the render succeeds, the clip is visible between its two dissolves, and the total length still follows AC-5.2
- [ ] **AC-5.4** Given only some parts are configured When the episode is rendered Then only the joins between present neighbours are dissolved and no join is added for an absent part
- [ ] **AC-5.5** Given any rendered episode When its first 0.8 s are measured Then frame 0 is black and silent, and picture brightness and sound level rise to full by 0.8 s
- [ ] **AC-5.6** Given any rendered episode When its last 2.0 s are measured Then picture and sound fall to black and silence, and the last frame is black and silent; the total length is unchanged by the fades

## Non-Functional Requirements

- Rendering the two clips adds no more than 30 s of encoding to an uncached render on the M1 Pro.
- Audio is normalised to the episode's sample rate and channel layout; no audible click at joins.

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Clip path missing | Render stops before encoding, error names the path (AC-3.4) |
| Clip unreadable as video | Render stops with the file name and FFmpeg's message |
| Clip has no audio | Silence used (AC-3.5) |

## Open Questions

Resolved at approval (2026-11-12):

- Joins are plain cuts (FR-5) — superseded in v2.0.0 and v3.0.0.
- The channel name stays "Giggle & Snooze" (the producer confirmed). The clips show "Wiggle & Snooze" on the wall; that is baked into the clips and is not changed by this spec.
- The 20 s clip plays whole.

## Out of Scope

- A studio control for choosing clips; per-book clips; trimming tools.
