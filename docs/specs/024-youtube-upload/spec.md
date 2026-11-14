# Spec 024: Connect a YouTube channel and upload episodes

**Status:** Approved
**Created:** 2026-11-14
**Branch:** `feat/024-youtube-upload`
**Depends on:** specs 003 (render), 004/014 (studio), 005 (book folders), 011 (blueprint), 021 (autopilot), 022 (titled covers)

---

## Problem Statement

The pipeline ends at a finished `books/<slug>/build/video/<slug>.mp4`. To publish it the producer leaves the studio, opens YouTube Studio, uploads the file by hand, writes a title, description and tags, sets the children's-content declaration, picks a thumbnail and copies the video link back somewhere. That is slow, easy to get wrong (a missed "made for kids" declaration is a legal problem) and leaves no record in the book of what was published.

## Goals

- The producer connects one YouTube channel to the project once, from the studio, and sees which channel is connected.
- From a book's page (and the command line) the producer uploads the finished episode to that channel with a Codex-written, kid-safe, editable title, description and tags.
- Every upload is declared made for kids and starts Private.
- Each book records what was uploaded (video id, link, time, which render, which metadata) so the studio can show it and warn when the video has changed since.

## Non-Goals

- Connecting more than one channel at a time (disconnect, then connect another to switch).
- Uploading from autopilot (spec 021 stays "no publishing"); upload is always a deliberate action.
- Making a video Public, Unlisted or scheduled from the studio; the producer does that in YouTube Studio.
- Replacing the video file of an existing YouTube video (YouTube does not allow it); a new upload makes a new video.
- Playlists, end screens, cards, comments, analytics, Shorts / 9:16.
- Uploading editions (PDF, EPUB) anywhere.

## Users and Context

**Primary user:** the producer, on this Mac, in the studio at `http://127.0.0.1:8765` or in a terminal.
**Secondary users:** parents and children who watch the channel; they see only what the producer uploads and later makes public.
**Usage context:** after a book's video is rendered and up to date, usually once per book, occasionally again after a re-render.
**User mental model:** "Connect my channel" like any app that asks Google for permission; "Upload to YouTube" sends this book's video with words I have read; the book page then shows "On YouTube (Private)" with a link.

**Setup the producer does once, outside the studio:** create a Google Cloud project with the YouTube Data API enabled and a desktop OAuth client, and give the studio that client's JSON file. Until Google audits that project, YouTube keeps every API upload Private and locked; the spec's Private default matches this.

## User Stories

### Story 1: Connect the channel

**As a** producer
**I want** to connect my YouTube channel from the studio
**So that** later uploads go to it without signing in again

**Acceptance criteria:**

- [ ] **AC-1.1** Given no OAuth client file is set up When the producer opens the YouTube settings page Then it says no client is set up, names where the client JSON file goes, and offers no Connect button.
- [ ] **AC-1.2** Given an OAuth client file and no connection When the producer presses Connect channel Then the browser opens Google's consent page asking only for upload and read-only channel access, and after the producer allows it the settings page shows the channel's title and id.
- [ ] **AC-1.3** Given the producer denies consent or closes the consent page When the studio gets the result (or 5 minutes pass) Then the page shows "Not connected" with the reason, and no credentials are stored.
- [ ] **AC-1.4** Given the Google account has no YouTube channel When consent completes Then the connection is refused with "This Google account has no YouTube channel", and no credentials are stored.
- [ ] **AC-1.5** Given a connected channel When the producer presses Disconnect and confirms Then the stored credentials are revoked with Google (best effort) and deleted, and the page shows "Not connected".
- [ ] **AC-1.6** Given a connected channel whose access was revoked or expired at Google When the settings page or an upload checks it Then the studio shows "Reconnect needed" and the upload does not start.
- [ ] **AC-1.7** Given no studio When the producer runs the connect command in a terminal Then the same consent flow runs and the command prints the connected channel's title and id.

### Story 2: Write the YouTube words

**As a** producer
**I want** Codex to draft the title, description and tags from the book
**So that** I only need to read and adjust them

**Acceptance criteria:**

- [ ] **AC-2.1** Given a book with an up-to-date video When the producer presses Write YouTube text Then a Codex job drafts a title, description and tags from the book data and blueprint, and saves them as the book's YouTube text file.
- [ ] **AC-2.2** Given a draft When it is saved Then every check in FR-3 passes, or the draft is refused with every failure listed at once and Codex is asked again, up to 3 attempts in total; after 3 failures the job fails with the last list and nothing is saved.
- [ ] **AC-2.3** Given the saved text When the description is shown Then after Codex's book-specific body it carries chapter timestamps (intro, each story page by its title, outro) computed from the rendered video's own section timings, not written by Codex, the first at 0:00, and ends with the line "More stories: http://giggleandsnooze.com/".
- [ ] **AC-2.6** Given two books When their YouTube text is drafted Then each has its own title and description body written for that book; only the chapter list and the site line are added by the pipeline.
- [ ] **AC-2.4** Given the saved text When the producer edits a field in the studio and saves Then the same FR-3 checks run and a failing edit is refused with the field and the reason, leaving the file unchanged.
- [ ] **AC-2.5** Given a YouTube text file already exists When the producer presses Write YouTube text again Then the studio asks for confirmation and keeps the old file as a backup.

### Story 3: Upload the episode

**As a** producer
**I want** one button that uploads the book's video to my channel
**So that** publishing does not need YouTube Studio's upload page

**Acceptance criteria:**

- [ ] **AC-3.1** Given a connected channel, an up-to-date video and valid YouTube text When the producer presses Upload to YouTube Then the studio shows the channel name, title, the "Made for kids" and "Private" settings and the file size, and the upload starts only after the producer confirms.
- [ ] **AC-3.2** Given the upload runs When it is in progress Then the job shows the percentage sent, and Stop ends it without leaving a record.
- [ ] **AC-3.3** Given the upload finishes When YouTube accepts it Then the video is declared made for kids, Private, in the book's YouTube category (FR-5; storybooks default to Education), with the book's language, the title, description and tags from the text file, and the book's video cover as the thumbnail.
- [ ] **AC-3.4** Given the thumbnail is refused (for example the channel is not verified for custom thumbnails) When the video itself was accepted Then the upload is recorded as done with a warning "thumbnail not set: <reason>".
- [ ] **AC-3.5** Given the upload finishes When the book page is shown Then it says "On YouTube (Private)" with the channel name, the upload time and a link to the video.
- [ ] **AC-3.6** Given the video is not rendered, out of date or blocked When the producer looks at the upload button Then it is disabled with the reason (render state from `render_check`).
- [ ] **AC-3.7** Given no valid YouTube text When the producer looks at the upload button Then it is disabled with "Write YouTube text first".
- [ ] **AC-3.8** Given a terminal When the producer runs the upload command for a book Then it applies the same checks, prints progress, and prints the video link; without `--yes` it asks for confirmation first.

### Story 4: Know what is on YouTube

**As a** producer
**I want** the book to remember its upload
**So that** I do not upload twice by mistake and I know when YouTube has an old cut

**Acceptance criteria:**

- [ ] **AC-4.1** Given a finished upload When it is recorded Then the book's upload record holds the video id, link, channel id and title, upload time, the rendered video's final key and file sha256, the YouTube text file's sha256 and any warnings.
- [ ] **AC-4.2** Given a book already uploaded When the producer presses Upload to YouTube Then the confirmation says it will create a second video and links the first one.
- [ ] **AC-4.3** Given a book whose video was re-rendered after its upload When the book page is shown Then it says "YouTube has an older cut" next to the link.
- [ ] **AC-4.4** Given the recorded video was deleted on YouTube When the producer presses Check on YouTube Then the record is marked "not found on YouTube" (the record is kept, not deleted).

## Functional Requirements

### FR-1: One project-wide channel connection

**Must:**
- Hold at most one connected channel for the project.
- Ask Google only for the scopes needed to upload videos, set thumbnails and read the channel's own title and id.
- Keep the OAuth client file and the stored credentials outside the repository and outside any `build/` folder that is served, readable only by the current user.
- Complete the consent redirect on a loopback address on this Mac only.

**Must not:**
- Write tokens, client secrets or authorization codes to logs, job records, the studio's pages or committed files.
- Store credentials from a consent that failed, was denied, or belonged to an account with no channel.

### FR-2: Codex drafts the YouTube text

**Must:**
- Run Codex the same way other stages do (headless, its own validating save tool), with the book's metadata, page titles and texts, blueprint mood and the series name as input.
- Save to a hand-editable, committed file beside the book (with title, description body, tags, and the language), written atomically.
- Write a title and description body specific to each book (its title, characters, story and mood), never a shared template.
- Add the chapter list and the site line itself (FR-3), after Codex's description body; the site link is read from config, not hardcoded.

**Must not:**
- Fall back to another provider when Codex fails.
- Overwrite an existing text file without the producer's confirmation.

### FR-3: YouTube text checks

**Must:**
- Title: 1–100 characters, contains the book's title.
- Description, including the chapter list: at most 5000 bytes.
- Tags: each non-empty, together at most 500 characters as YouTube counts them.
- No `<` or `>` in any field.
- No term from `config/disallowed_terms.txt`, no email addresses, no links except the configured site link (`http://giggleandsnooze.com/`) on the pipeline-added last line, no requests to like, subscribe, comment or buy aimed at children.
- Chapter list: at least 3 chapters, first at 0:00, each at least 10 seconds long, in order.

### FR-4: Upload

**Must:**
- Refuse unless the channel is connected and valid, `render_check` is "up_to_date", and the YouTube text passes FR-3.
- Always set made-for-kids to true and privacy to Private.
- Send the file in a way that survives a dropped connection by resuming, not restarting.
- Run as a studio job (one stage at a time, stoppable, logged without secrets) and as a CLI command.
- Write the upload record (AC-4.1) atomically beside the book, committed.

**Must not:**
- Upload from autopilot.
- Change the privacy, made-for-kids setting or metadata of an existing YouTube video.

### FR-5: Category by content kind

**Must:**
- Read the YouTube category from config by content kind: `storybook` → Education (27); later kinds such as `nursery_rhyme` → Music (10).
- Let a book override the category in its YouTube text file.

**Must not:**
- Hardcode category ids in code.

### FR-6: Studio surfaces

**Must:**
- A YouTube settings page: client status, connected channel, Connect, Disconnect.
- On each book page: a YouTube panel with the text (editable), Write YouTube text, Upload to YouTube, upload state and link, older-cut warning, Check on YouTube.

## Non-Functional Requirements

### Performance

- Upload speed is limited by the network only; the studio stays responsive during an upload.
- Each upload costs about 1,600 of the project's default 10,000 daily API quota units (about 6 uploads a day); the studio shows a quota error as such (Error Scenarios).

### Security

- Studio routes that start a connection, upload or disconnect keep the studio's existing protections (127.0.0.1 only, cross-origin POSTs refused).
- The OAuth flow uses a state value and PKCE, and the redirect accepts only the expected state.
- Secrets never appear in test fixtures; tests use a fake YouTube.

### Reliability

- Unit tests run with no network and no Google account; a real-upload test is opt-in behind its own pytest marker and uploads a short private test clip.
- An interrupted studio (crash, restart) marks a running upload as interrupted, as other jobs are; no record is written for an unfinished upload.

## Error Scenarios

| Scenario | Expected Behavior |
|----------|-------------------|
| Client JSON missing or not a desktop OAuth client | Settings page and CLI name the file and what is wrong; Connect disabled |
| Consent denied, closed or timed out (5 min) | "Not connected: <reason>", nothing stored |
| Account has no YouTube channel | Refused (AC-1.4), nothing stored |
| Token expired / revoked | "Reconnect needed"; upload refused before sending any bytes |
| Daily quota exceeded | Job fails with "YouTube daily upload quota used up; try again after midnight Pacific time" |
| Network drops mid-upload | Resume up to 5 times with backoff, then fail with the last error; no record |
| YouTube rejects the metadata | Job fails with YouTube's reason and field; no record |
| Thumbnail refused | Upload recorded with a warning (AC-3.4) |
| Codex unavailable or 3 failed drafts | Text job fails with the reason / last failure list; nothing saved |
| Video re-rendered after upload | "YouTube has an older cut" (AC-4.3) |
| Recorded video deleted on YouTube | Marked "not found on YouTube" on Check (AC-4.4) |

## Open Questions

None. Resolved 2026-11-14:
- Category: storybooks use Education (27), since read-alouds with Q&A are learning content; nursery rhymes will use Music (10) when added (FR-5).
- Description: every book gets its own Codex-written title and description; no shared footer except the site line "More stories: http://giggleandsnooze.com/".

## Out of Scope (Future Considerations)

- Several channels, chosen per book.
- Changing privacy / scheduling publish time from the studio.
- Updating title, description or thumbnail of an already uploaded video.
- An autopilot switch that uploads as Private.
- Playlists per series, end screens, captions file upload, Shorts.
