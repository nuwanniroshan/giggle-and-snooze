# Feature 002: Voice Synthesis

**Status:** Approved
**Version:** 1.0.0
**Created:** 2026-11-08
**Last Updated:** 2026-11-08
**Branch:** `feat/002-voice-synthesis`

## Changelog

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-11-08 | Initial draft |

---

## Problem Statement

A reviewed episode script (spec 001) is still only text. Before a video can be rendered, every line has to be spoken by the right character: the Storyteller and the two kids, Leo (~8) and Mia (~5). The voices must sound warm and human, because voice quality is a release blocker in the project foundation. This has to happen locally and at no cost, the same way for every new book, without anyone recording or editing audio by hand. The local voice model has no child voices, so the kids need youthful voices lifted in pitch, and every result needs both automatic checks and a person's listening review before rendering.

## Goals

- Turn a reviewed episode script into spoken audio with one command: a clip per line, plus a stitched track for the intro, each page and the outro.
- Give the three characters distinct, consistent, natural voices, with emotion tags performed rather than read aloud.
- Catch broken audio automatically (silence, clipping, dropped words) and regenerate it before a person has to listen.
- Make re-runs cheap: only lines whose text or voice settings changed are synthesized again.
- Let each book fix the pronunciation of its invented names without changing the script text.

## Non-Goals

- Background music, page-turn or ambience sound effects, audio ducking or final mixing (render stage, spec 003).
- Rendering video or images.
- Cloud text-to-speech, voice cloning, or training or fine-tuning voices.
- Editing the script's words. Synthesis speaks the script as it is.

## Users and Context

**Primary users:** The channel producer, who runs synthesis after reviewing a generated script, then listens to the result.
**Secondary users:** The render stage (spec 003), which reads the audio tracks and their timings.
**Usage context:** On the local Mac (M1 Pro, 16 GB), after `episodes/<book-slug>/episode_script.json` has been reviewed, before rendering. No other model runs at the same time. Usually offline once the voice model is downloaded.
**User mental model:** "Turn the approved script into the read-aloud soundtrack, then let me listen before it goes into the video."

## User Stories

### Story 1: Synthesize an episode with one command

**As a** producer
**I want** to turn a reviewed episode script into audio with one command
**So that** the render stage has a complete soundtrack for the book

**Acceptance criteria:**

- [ ] **AC-1.1** Given a valid, reviewed episode script When the producer runs synthesis Then one audio clip exists for every line in the script (intro, every page line, outro), and the command exits successfully
- [ ] **AC-1.2** Given a successful run When the outputs are inspected Then there is one stitched track for the intro, one for each page, and one for the outro, under `build/<book-slug>/audio/`
- [ ] **AC-1.3** Given a successful run When a track's timing data is inspected Then it lists, in script order, each line's speaker, role, start time and end time within the track, and the track's total duration
- [ ] **AC-1.4** Given a stitched track When the clip boundaries are measured Then the gap between consecutive lines is 0.4 s when the same speaker continues, 0.7 s when the speaker changes, and 1.0 s between the last narration line and the first Q&A line on a page (each ±0.05 s), using the configured pause values
- [ ] **AC-1.5** Given a book other than the pilot (a valid script for an 8–32-page book) When synthesis runs Then it succeeds with no code or configuration changes

### Story 2: Distinct, natural voices

**As a** young viewer
**I want** the Storyteller, Leo and Mia to each sound like themselves every time
**So that** I can follow who is talking

**Acceptance criteria:**

- [ ] **AC-2.1** Given the cast configuration When synthesis runs Then every line is spoken with the voice configured for its speaker's role, and the same role uses the same voice and settings for every line in the episode
- [ ] **AC-2.2** Given a kid role with a pitch lift configured When its clip is compared with the same line rendered without the lift Then the clip's pitch is raised by the configured amount (±1 semitone) and its duration changes by no more than 5%
- [ ] **AC-2.3** Given a line containing emotion tags (`<giggle>`, `<laugh>`, `<chuckle>`, `<gasp>`, `<sigh>`, `<yawn>`) When it is synthesized Then the tags are passed to the voice model as performance cues and are never spoken as words (verified by the human listening review, Story 4)
- [ ] **AC-2.4** Given the cast configuration When the episode is synthesized Then the Storyteller, Leo and Mia use three different voices

### Story 3: Automatic quality checks

**As a** producer
**I want** broken clips caught and redone automatically
**So that** I only spend listening time on clips that are technically sound

**Acceptance criteria:**

- [ ] **AC-3.1** Given a synthesized clip When it is checked Then it fails if it is empty or shorter than 0.3 s, if any sample reaches full scale (clipping), if it contains a silence longer than 1.5 s, if its speaking rate (words excluding tags ÷ speech duration) is outside the configured range, or if it was cut off (speech still under way in its final 0.2 s before trimming, e.g. because generation hit a length limit)
- [ ] **AC-3.2** Given a clip When it is saved Then leading and trailing silence is trimmed to at most 0.1 s
- [ ] **AC-3.3** Given a clip that fails a check When synthesis continues Then that line is synthesized again, up to 2 more attempts with a different random seed each time, and a passing attempt replaces it
- [ ] **AC-3.4** Given a line that still fails after 3 attempts When the run ends Then it exits non-zero, names every failing line (intro/outro or page number, line number, speaker) and the check that failed, and writes no stitched tracks and no review sheet
- [ ] **AC-3.5** Given a successful run When the quality results are inspected Then every clip has its measured duration, peak level, longest silence, speaking rate and number of attempts recorded

### Story 4: Listening review gate

**As a** producer
**I want** to listen to the whole soundtrack and approve it before rendering
**So that** nothing robotic, mispronounced or garbled reaches the video

**Acceptance criteria:**

- [ ] **AC-4.1** Given a successful run When it finishes Then it writes a review sheet listing every track and every clip with its text, speaker, duration and quality results, and one full-episode preview file joining the intro, every page and the outro in order
- [ ] **AC-4.2** Given a successful run When its approval status is inspected Then it is "pending review", and the output message says the audio must be listened to and approved before rendering
- [ ] **AC-4.3** Given audio pending review When the producer runs the approve command for that book Then its status becomes "approved" with the time of approval, provided the audio still matches the script it was made from
- [ ] **AC-4.4** Given approved audio When any clip is later re-synthesized (or the script changes) Then the status returns to "pending review"
- [ ] **AC-4.5** Given the script has changed since the audio was synthesized When the producer runs the approve command Then it is refused with a message saying synthesis must be re-run first

### Story 5: Per-book pronunciation fixes

**As a** producer
**I want** to give the spoken form of a book's invented names
**So that** "Bramwell" or "Whisperwood" are said correctly without changing the printed text

**Acceptance criteria:**

- [ ] **AC-5.1** Given a book with an optional pronunciation list mapping a word to its spoken respelling When a line containing that word (whole word, case-insensitive) is synthesized Then the voice model receives the respelling in its place
- [ ] **AC-5.2** Given a pronunciation list When synthesis completes Then the episode script, the review sheet's line text and the timing data still show the original words
- [ ] **AC-5.3** Given a book with no pronunciation list When synthesis runs Then it proceeds normally
- [ ] **AC-5.4** Given a pronunciation entry is added or changed When synthesis re-runs Then exactly the lines containing that word are re-synthesized

### Story 6: Fast, safe re-runs

**As a** producer
**I want** unchanged lines reused
**So that** fixing one line doesn't cost a full re-synthesis

**Acceptance criteria:**

- [ ] **AC-6.1** Given a previous successful run When synthesis runs again with no changes Then no line is synthesized again, the stitched tracks are identical to before, and the run reports that every clip was reused
- [ ] **AC-6.2** Given one line's text is changed in the script When synthesis re-runs Then only that line is synthesized again, and only the track containing it is rebuilt
- [ ] **AC-6.3** Given a speaker's voice, pitch lift or the voice model setting changes When synthesis re-runs Then every line of the affected speaker(s) is synthesized again
- [ ] **AC-6.4** Given the force option When synthesis runs Then every line is synthesized again, ignoring reusable clips
- [ ] **AC-6.5** Given a run is interrupted When synthesis runs again Then clips completed before the interruption are reused and no partial clip or track is used

### Story 7: Script gate

**As a** producer
**I want** synthesis to refuse scripts that are invalid or that I haven't reviewed
**So that** voice time isn't spent on a script that will change, and the spec 001 review rule is enforced

**Acceptance criteria:**

- [ ] **AC-7.1** Given an episode script that fails spec 001's validation When synthesis runs Then it exits non-zero listing the validation errors and synthesizes nothing
- [ ] **AC-7.2** Given a valid script When synthesis runs without the reviewed confirmation option Then it exits non-zero saying the script must be reviewed by a person first (spec 001 AC-7.4) and how to confirm it, and synthesizes nothing
- [ ] **AC-7.3** Given no episode script exists for the book When synthesis runs Then it exits non-zero naming the expected script path

## Functional Requirements

### FR-1: Input

**Must:**
- Read the book data file (default `book_data.json` in the project root) to find the book slug and the optional pronunciation list. Read the episode script from `episodes/<book-slug>/episode_script.json`.
- Read voices, pitch lifts, pause lengths and the speaking-rate range from project configuration, with the per-role voice taken from the script's `cast`.
- Validate the script against spec 001's contract before any synthesis (AC-7.1).

**Must not:**
- Hardcode any book title, character name, path or duration.

### FR-2: Voices

**Must:**
- Synthesize every line with its role's configured voice, using the local voice model. Load the model once per run and reuse it for every line.
- Apply each kid role's configured pitch lift (in semitones) without changing speaking speed by more than 5% (AC-2.2). The Storyteller has no lift by default.
- Pass the allowed emotion tags to the voice model as performance cues (AC-2.3). Remove any other angle-bracket text before synthesis.
- Apply the book's pronunciation list to the spoken text only (FR-5).

### FR-3: Quality checks and retries

**Must:**
- Check every clip as in AC-3.1 and trim it as in AC-3.2.
- Re-synthesize a failing line with a new random seed, up to 3 attempts in total (AC-3.3).
- Record the quality results for every clip (AC-3.5).

### FR-4: Tracks, timing and preview

**Must:**
- Stitch the clips of the intro, each page and the outro into one track each, in script order, with the configured pauses (AC-1.4).
- Write timing data for each track (AC-1.3).
- Write one full-episode preview by joining the tracks in order, with a 1.0 s gap between tracks.
- Use one audio format for every clip and track: mono, one fixed sample rate, uncompressed.

### FR-5: Pronunciation

**Must:**
- Accept an optional per-book list of word → spoken respelling, stored with the book's data. Matching is whole word and case-insensitive.
- Change only the text sent to the voice model, never the script, review sheet or timing text (AC-5.2).

### FR-6: Review status

**Must:**
- Mark every successful run "pending review" and record which script and settings it was made from (AC-4.2).
- Provide an approve command per book that marks the audio "approved" only when it still matches the current script (AC-4.3, AC-4.5).
- Reset the status to "pending review" whenever any clip is re-synthesized (AC-4.4).

### FR-7: Reuse and re-runs

**Must:**
- Identify each clip by its spoken text (after pronunciation), voice, pitch lift, voice model and model settings, and reuse a clip with the same identity (AC-6.1–6.3).
- Rebuild only the tracks whose clips or pauses changed (AC-6.2).
- Offer a force option that re-synthesizes everything (AC-6.4).
- Write every clip, track and status file atomically, so an interrupted run leaves no partial file in use (AC-6.5).

**Must not:**
- Run while another stage's model is using the machine's memory. Synthesis runs on its own, one line at a time.

## Non-Functional Requirements

### Performance

- A first run for the 20-page pilot episode (about 2,150 spoken words) finishes within 30 minutes on the target machine, excluding the one-time model download. The 2026-11-09 timing test measured about 0.65 s of generation per spoken word (about 24 minutes per episode), leaving headroom for retries.
- A re-run with no changes finishes in under 30 seconds.
- Peak memory stays under 8 GB (about 6 GB measured).

### Quality

- Voice naturalness is a release blocker: the listening review (Story 4) is the gate, and audio is never rendered unapproved.

### Security

- Only the configured local voice model is used. No audio or text leaves the machine.
- Model files are loaded only from the pinned model source, without running code shipped with the model.

### Reliability

- Every failure exits non-zero with a message naming the cause and, where relevant, the line.
- Outputs are regenerable artifacts under `build/`, never hand-edited.

### Cost

- Zero cloud cost. A one-time model download is the only network use.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Episode script missing | Exit non-zero naming the expected path; nothing synthesized |
| Script fails spec 001 validation | Exit non-zero listing the errors; nothing synthesized |
| Reviewed confirmation not given | Exit non-zero explaining the review rule and how to confirm; nothing synthesized |
| Voice model not installed or not downloaded | Exit non-zero saying which model is missing and how to get it; nothing synthesized |
| A role's voice missing from the cast or not supported by the model | Exit non-zero naming the role and voice; nothing synthesized |
| A line fails quality checks 3 times | Exit non-zero naming every failing line and check; earlier clips kept for reuse; no tracks, review sheet or status written |
| Pronunciation list malformed | Exit non-zero naming the book data file and the bad entry |
| Approve requested but the script changed since synthesis | Refuse; say synthesis must be re-run |
| Approve requested but no audio exists | Exit non-zero saying to run synthesis first |
| Run interrupted | Completed clips reused next time; no partial file used |
| Not enough disk space to write audio | Exit non-zero naming the output folder; previous outputs intact |

## Open Questions

None. Resolved during specification:
- First-run target is 30 minutes for a 20-page episode, from a timing test (about 0.65 s per spoken word, about 6 GB peak memory). The test also showed that long narration lines get cut off at the model's default length limit, which is why AC-3.1 checks for cut-off clips.
- Kids use the most youthful adult voices of the local model with a configurable pitch lift (no child voices are available).
- Output is a clip per line plus stitched intro, page and outro tracks. Sound effects and music belong to the render stage.
- Quality gate: automatic checks with retries, plus a required human listening approval.
- An optional per-book pronunciation list affects audio only.

## Out of Scope (Future Considerations)

- A voice-audition tool to compare voices and pitch lifts side by side.
- Per-line manual overrides of voice, speed or pauses.
- Sound effects, background music and mixing (spec 003).
- Cloud or cloned voices, other languages, and other voice models.
