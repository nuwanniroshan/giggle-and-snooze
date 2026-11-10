# Spec 007: Sound library and ambience in episodes

**Status:** Approved
**Version:** 1.0.1 (PATCH: AC-4.1 wording; audio is mixed once for the whole episode, so only master audio and the final video are rebuilt, video clips are reused)
**Created:** 2026-11-10
**Branch:** `feat/007-sound-library`
**Depends on:** specs 003 (render), 005 (book folders)
**Supersedes:** the earlier draft of this spec (automatic download from Freesound). Dropped as too complex: the library is now collected by hand once.

---

## Problem Statement

Story videos have only voices and a page-turn sound. The producer wants rain, wind, fire, water, night sounds and calm music under the story so it feels alive. A set of free CC0 sounds is already stored in `assets/sound_library/` with an `index.json` that records author, source and license. Nothing in the pipeline uses them yet.

## Goals

- Pages (and the intro and outro) of an episode can name sounds from the library, and the rendered video plays them quietly under the voices.
- Rendering uses only files on disk, with no network.
- Adding a sound is one file plus one index entry, so the library grows by hand.
- Every sound in the library is CC0 and has a recorded source.

## Non-Goals

- Automatic download or search (Freesound API, Lotsofsounds).
- Choosing sounds with an AI model.
- Sounds that need attribution or forbid commercial use.
- Audio editing beyond loop, fade and level.
- A browser UI for the library or for tagging.
- Changing the voice tracks or the page-turn sound.

## Users and Context

**Primary users:** the producer building story videos on one Mac.
**Usage context:** the producer adds sound names to a page in the hand-editable `episode_script.json`, then renders.
**User mental model:** "put rain under pages 3–5".

## User Stories

### Story 1: Tag pages with sounds

**As a** producer
**I want** to list library sounds on a page
**So that** the video plays them while that page is shown

**Acceptance criteria:**

- [ ] **AC-1.1** Given a page whose `ambience` lists `rain_soft` When the episode is rendered Then rain is audible during that page's section, below the level of the voices
- [ ] **AC-1.2** Given a page with `ambience` listing two sounds When rendered Then both play together for that page
- [ ] **AC-1.3** Given an intro or outro with an `ambience` list When rendered Then the sounds play during that section
- [ ] **AC-1.4** Given an episode script with no `ambience` fields When it is rendered Then its audio is identical to today's output
- [ ] **AC-1.5** Given an `ambience` list that names a sound not in the library When the script is validated or rendered Then it fails before any encoding, naming the page and the sound

### Story 2: Smooth, quiet playback

**As a** producer
**I want** the sound bed to be gentle
**So that** it never fights the narration

**Acceptance criteria:**

- [ ] **AC-2.1** Given a sound shorter than its section When rendered Then it repeats to fill the section without a gap
- [ ] **AC-2.2** Given a sound that ends and restarts When rendered Then the join is crossfaded so there is no click
- [ ] **AC-2.3** Given two consecutive pages with different sounds When rendered Then the first fades out and the second fades in across the page change
- [ ] **AC-2.4** Given consecutive pages with the same sound When rendered Then it plays on without a fade or restart at the page change
- [ ] **AC-2.5** Given voices and ambience together When mixed Then the ambience is below the voices by the configured amount and the mix stays under the existing peak limit
- [ ] **AC-2.6** Given a sound of kind `sfx` (a short effect) in a page's list When rendered Then it plays once at the start of the page, not looped

### Story 3: Keep the library tidy

**As a** producer
**I want** a check that the library is consistent
**So that** every sound is licensed and present

**Acceptance criteria:**

- [ ] **AC-3.1** Given a library where every index entry has a file and a CC0 license When the check command runs Then it reports success
- [ ] **AC-3.2** Given an index entry whose file is missing, or an audio file with no index entry When the check runs Then it lists each problem and exits with a failure status
- [ ] **AC-3.3** Given an index entry whose license is not CC0 When the check runs Then it is reported as a problem
- [ ] **AC-3.4** Given the list command When it runs Then every sound is shown with its name, kind, length and author

### Story 4: Re-render only what changed

**As a** producer
**I want** a sound change to rebuild only its sections
**So that** renders stay fast

**Acceptance criteria:**

- [ ] **AC-4.1** Given an episode rendered before When one page's `ambience` list changes and it is rendered again Then the video section clips are reused and only the episode's audio and final video are rebuilt
- [ ] **AC-4.2** Given an episode rendered before When a library file's content changes Then every section that uses it is rebuilt

## Functional Requirements

### FR-1: Library

The library is the shared folder `assets/sound_library/` with one audio file per sound and an `index.json`. Each entry has a name, file, title, author, source URL, kind (`ambience`, `music` or `sfx`), length and license.

**Must:**
- Look sounds up by name.
- Treat only `ambience` and `music` entries as loopable, and `sfx` as play-once.
- Accept the existing files and index without changes.

**Must not:**
- Contact the network at any time.

### FR-2: Episode script field

Each of intro, page entries and outro may carry an optional `ambience` list of library names. The field is hand-editable like the rest of the script.

**Must:**
- Validate names against the library when the script is loaded.
- Treat a missing field as no ambience.
- Leave every existing script valid.

### FR-3: Mixing

**Must:**
- Loop, fade and level the sounds as the stories above describe, with the level, fade length and loop crossfade set in the render configuration.
- Keep the existing peak limit over the full mix.
- Be part of each section's cache identity, including the sound files' content.

**Must not:**
- Change voice tracks or the page-turn sound.

### FR-4: Library commands

**Must:**
- Provide `list` and `check` commands on the command line.

## Non-Functional Requirements

### Performance

- Adding ambience adds less than 10% to the audio mixing time of the existing pilot episode.

### Reliability

- Missing or unreadable sound files stop the render before encoding, with the sound name in the message.
- Unit tests use no real audio tool and no network.

### Security

- Sound files are decoded by the audio tool as data and never executed.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Ambience names an unknown sound | Fail at load and at render, naming page and sound |
| Index entry points to a missing file | Reported by `check`; render stops naming the sound |
| Sound file cannot be decoded | Render stops naming the sound; nothing partial is kept |
| Index file is unreadable or invalid | Fail naming the file and field |
| Two pages list the same sound with a gap between | Each page plays it independently |
| Section shorter than a play-once sound | The effect is cut at the section end with a short fade |

## Open Questions

- None.

## Out of Scope (Future Considerations)

- Automatic fetching from Freesound or other sources.
- AI-suggested ambience per page and a studio page to audition and tag sounds.
- Sidechain ducking and real background-music composition.
