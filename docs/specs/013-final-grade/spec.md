# Spec 013: Final grade (colour, sharpness, sound polish, fades)

**Status:** Approved
**Version:** 1.0.0
**Created:** 2026-11-12
**Last Updated:** 2026-11-12
**Branch:** `feat/013-final-grade`
**Depends on:** spec 003 (render), spec 012 (bookend clips and blended joins)
**Evidence:** `manual-edits/pip/` (original vs enhanced copy of the Pip episode, with measurements)

---

## Problem Statement

A hand-enhanced copy of the Pip episode looked richer and sounded more even than the tool's own render: more contrast and colour, a little sharpening, a light vignette, fades at the start and end, and one loudness for the whole video. The producer has to do this by hand outside the tool. The tool's render should be able to produce the enhanced result itself, the same way every time, for every book.

## Goals

- Every episode can get a final grade: colour correction, light sharpening, vignette, start and end fades, and one overall loudness.
- The grade is a set of named settings with a default look taken from the hand-enhanced copy, and each setting can be changed or switched off.
- A book can override the settings (for example a different look for a night scene book).
- The grade is a final step: it never changes the story sections, voices or cache of earlier stages, and a regrade is fast.

## Non-Goals

- Per-page or per-scene grading.
- New caption styles or sizes (captions looked small; a separate story).
- Background music, new sound effects, or changing the ambience or voice levels from spec 012.
- Automatic grading chosen by looking at the picture.

## Users and Context

The producer renders episodes on one Mac. The pilot render is about 3 min and 104 MB; the hand-enhanced copy took one extra pass and was 158 MB.

## User Stories

### Story 1: Colour and detail grade

**As a** producer **I want** the render to correct contrast, saturation and tone and to sharpen lightly **So that** pictures look as rich as in my hand-enhanced copy.

- [ ] **AC-1.1** Given the default grade When an episode is rendered Then the picture has contrast 1.05, saturation 1.12 and gamma 1.02 applied, and a frame of the result differs from the ungraded render by these amounts (measured on a test card frame within 2 percent)
- [ ] **AC-1.2** Given a grade with a different value for contrast, saturation, gamma, brightness or colour balance (shadows, midtones, highlights) When the episode is rendered Then that value is used and the others stay at their defaults
- [ ] **AC-1.3** Given a sharpening amount of 0 When the episode is rendered Then no sharpening is applied and the picture matches an ungraded render apart from the other grade settings
- [ ] **AC-1.4** Given the default grade When an episode is rendered Then the picture has a light darkening at the corners (vignette) and the centre of the frame is unchanged within 1 percent
- [ ] **AC-1.5** Given the grade is switched off When an episode is rendered Then the output is identical to a render before this spec

### Story 2: Fades at the very start and end

**As a** producer **I want** the video to fade in and out **So that** it does not start or stop abruptly.

- [ ] **AC-2.1** Given the default grade When an episode is rendered Then the first frame is black and fully visible after 0.6 s, the picture is fully faded to black at the last frame, and the fade out lasts 1.5 s
- [ ] **AC-2.2** Given the same When the sound is measured Then it fades in over 0.4 s and out over 1.5 s with no click at either end
- [ ] **AC-2.3** Given fade lengths of 0 When an episode is rendered Then no fade is applied at that end
- [ ] **AC-2.4** Given an episode shorter than the two fades together When it is rendered Then the fades are shortened so they do not overlap and the render succeeds

### Story 3: One loudness for the whole video

**As a** producer **I want** the whole episode set to one loudness **So that** the clips, story and outro sound equally loud.

- [ ] **AC-3.1** Given the default grade When an episode is rendered Then its integrated loudness is -16 LUFS within 1 LU and its true peak is at most -1.5 dBFS
- [ ] **AC-3.2** Given the Pip episode When it is rendered with the default grade Then its loudness range is at most 7 LU (it was 13.0 LU)
- [ ] **AC-3.3** Given a loudness target set to another value When an episode is rendered Then that target is met within 1 LU
- [ ] **AC-3.4** Given the sound grade is switched off When an episode is rendered Then the sound is identical to a render before this spec

### Story 4: Per-book overrides and a fast regrade

**As a** producer **I want** to set a different look for one book and to regrade without re-rendering the story **So that** I can try looks quickly.

- [ ] **AC-4.1** Given a book with its own grade settings When it is rendered Then those settings override the shared ones, and other books keep the shared settings
- [ ] **AC-4.2** Given an episode already rendered When only a grade setting changes and the episode is rendered again Then no page, intro, outro or transition section is re-rendered and only the final grading step runs
- [ ] **AC-4.3** Given an episode already rendered with the same grade When it is rendered again Then nothing is re-encoded
- [ ] **AC-4.4** Given a rendered episode When the render record is read Then it lists the grade settings used and the measured loudness and peak
- [ ] **AC-4.5** Given the studio's video page When an episode has been graded Then the page shows the grade settings used

## Functional Requirements

- **FR-1** The shared defaults live in the shared render settings and match the hand-enhanced copy: contrast 1.05, saturation 1.12, gamma 1.02, sharpening light, vignette light, picture fade in 0.6 s and out 1.5 s, sound fade in 0.4 s and out 1.5 s, loudness -16 LUFS, true peak -1.5 dBFS, loudness range 7 LU, high-pass at 70 Hz, light compression.
- **FR-2** A book may carry its own grade settings in `book.json`; any setting it omits takes the shared value.
- **FR-3** Settings are validated on load: unknown names, out-of-range values and negative fades are refused with the file and field named.
- **FR-4** The grade is applied to the finished joined video (after the blended joins of spec 012) in one pass, and is cached by the checksum of its input and its settings.
- **FR-5** The grade keeps the video's size, frame rate, colour space tags and audio format, and the length changes by no more than one frame.
- **FR-6** A `--no-grade` option and a settings switch render without the grade.

## Non-Functional Requirements

- Grading a 3-minute episode takes no more than 4 minutes on the M1 Pro (the hand pass took about 2 minutes).
- Output stays playable on YouTube and Apple devices (yuv420p, BT.709, fast start).

## Error Scenarios

| Scenario | Expected behaviour |
|---|---|
| Unknown setting name | Render stops before encoding naming the file and field (FR-3) |
| Value out of range | Same, naming the allowed range |
| Loudness target cannot be met without exceeding the peak limit | The peak limit wins; the record shows the measured loudness |
| Grading step fails | The ungraded video is kept and the error shows FFmpeg's message |

## Open Questions

Resolved at approval (2026-11-12):

- The grade applies to the whole video: logo reveal, opening clip, story, outro and ending clip.
- A book's own grade settings live in its `book.json` under a `grade` key (FR-2); there is no separate file and the blueprint does not drive the grade.
- Settings files are enough for the first version: no studio editing controls (AC-4.5 still shows the settings used).
- Constraint for the plan: the grade is built from FFmpeg's own filters (colour correction, sharpening, vignette, fades, loudness normalisation), not a separate tool.

## Out of Scope

- Studio editing controls (unless chosen above), per-page grading, caption changes, music.
