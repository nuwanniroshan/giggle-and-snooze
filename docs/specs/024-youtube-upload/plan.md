# Plan 024: Connect a YouTube channel and upload episodes

**Spec:** docs/specs/024-youtube-upload/spec.md (Approved 2026-11-14)
**Created:** 2026-11-14

## Context
The pipeline ends at `books/<slug>/build/video/<slug>.mp4`; publishing is manual in YouTube Studio. Spec 024 adds a single project-wide YouTube channel connection, a Codex-written kid-safe title/description/tags per book, and a Private, made-for-kids upload from the studio and CLI, recorded beside the book.

Work goes on `feat/024-youtube-upload`. TDD throughout. Unit tests use a fake YouTube client and the existing `FakeCodex` pattern (tests/test_motion_plan.py, tests/test_write_story.py), so they never touch the network or Codex. Commit only when asked.

## Global Constraints
- At most one connected channel per project.
- Every upload is `privacyStatus: "private"` and `selfDeclaredMadeForKids: true`. Nothing changes an existing YouTube video's privacy, made-for-kids setting or metadata.
- The category comes from `config/youtube.json` by content kind: storybook = "27" (Education), nursery_rhyme = "10" (Music). A book may override it.
- Description = Codex body + chapter list (from the render) + `More stories: http://giggleandsnooze.com/` (the line comes from config). That is the only link allowed.
- Text limits: title 1–100 characters and contains the book title. Full description ≤ 5000 bytes. Tags as YouTube counts them ≤ 500. No `<` or `>`. No disallowed terms, no emails, no other links, no calls to action from the config list. At least 3 chapters, the first at 0:00, each ≥ 10 s.
- Codex: 3 attempts in total, no fallback provider.
- Secrets (client file and token) stay outside the repo in `$KIDDO_YOUTUBE_DIR` or `~/.config/kiddo/youtube/` (directory 0700, files 0600). They are never logged or rendered.
- Autopilot never uploads.

## Decisions (traceable to the spec)
- **Libraries:** add `google-auth-oauthlib` (OAuth with PKCE and a loopback redirect; FR-1) and `google-api-python-client` (resumable `MediaFileUpload` with `next_chunk` progress and resume; FR-4). Writing OAuth and resumable upload by hand is more code to get wrong. These are the only new dependencies.
- **One seam for the network:** the `YouTubeClient` Protocol has `channel()`, `upload(path, body, on_progress)`, `set_thumbnail(video_id, path)`, `video_exists(video_id)` and `revoke()`. `GoogleYouTube` is the only real implementation. Tests use `FakeYouTube`. *Anti-abstraction note:* this wrapper exists only so tests run with no network (spec NFR Reliability).
- **Chapter times:** the render record has no section timings, and channel/bookend clips shift the body. The render therefore adds `"chapters": [{"name", "start"}]` to `<slug>.json`, computed from `timeline.section_times` plus the opening clips' offset. This goes in the record only; `final_key` and caches are unchanged. A record without `chapters` refuses with "re-render to record chapter times (`render_episode --force`)". The pipeline builds the chapters itself, so a page shorter than 10 s joins the chapter before it, and `back-cover` joins `outro`.
- **Studio consent:** connecting from the studio uses the studio's own `GET /youtube/callback` on 127.0.0.1 as the loopback redirect. The pending `{state, code_verifier, started}` is held in memory for 300 s. The CLI uses `InstalledAppFlow.run_local_server(port=0, timeout_seconds=300)`.
- **Language:** `metadata.language` or "en" (as publication_web.py:235 already does). The series name "Giggle & Snooze" comes from `config/youtube.json`.

## Data (beside the book, committed; written with `episode_tools.write_atomic`)
- `books/<slug>/youtube/text.json`: `{title, description, tags[], language, category_id|null, written_by: "codex"|"producer", written_at}`. `description` is the body only. The full description is composed with the current chapters whenever it is checked, shown or uploaded. A rewrite keeps the old file as `text.json.bak`.
- `books/<slug>/youtube/uploads.json`: `{uploads: [{video_id, url, channel_id, channel_title, uploaded_at, final_key, video_sha256, text_sha256, category_id, privacy:"private", made_for_kids:true, warnings[], state:"uploaded"|"not_found", checked_at|null}]}`. Records are only appended.
- Secrets directory: `client_secret.json` (must hold an `"installed"` client), `token.json`, `channel.json` (`{id, title, connected_at}`).
- `config/youtube.json`: `series`, `site_line`, `site_link`, `categories`, `default_kind: "storybook"`, `scopes: [youtube.upload, youtube.readonly]`, `chunk_mb: 8`, `max_resumes: 5`, `consent_timeout_s: 300`, `codex: {attempts: 3, time_limit_s: 300}`, `cta_terms: [...]`, `thumbnail: {size: [1280,720], max_bytes: 2000000}`.

---

## Phase 1: Pure YouTube text, chapters and records
**Implements:** FR-3, FR-5, parts of FR-2/FR-4 (composition, record) | **Satisfies:** AC-2.3, AC-2.4 (checks), AC-4.1, AC-4.3 (stale logic)
**Files:** `video_pipeline/youtube.py` (new), `config/youtube.json` (new), `tests/test_youtube.py` (new)
**Interfaces:** Produces `load_settings(config_dir) -> YouTubeSettings`; `chapters_from_record(record, book) -> list[Chapter]`; `compose_description(body, chapters, settings) -> str`; `text_problems(text: dict, book, chapters, settings, terms) -> list[str]`; `tags_length(tags) -> int`; `category_for(book, text, settings) -> str`; `YouTubePaths(folder)` (`.text`, `.uploads`, `.thumbnail` = `build/youtube/thumbnail.jpg`); `read_text`, `write_text(path, text, keep_backup)`; `append_upload(path, entry)`, `last_upload(path)`, `upload_state(folder, render_record) -> "none"|"current"|"older_cut"|"not_found"`.

Goal: every rule in FR-3 and the record/state logic as pure, tested functions.
- Tests: `test_title_over_100_refused`; `test_title_must_contain_book_title`; `test_description_over_5000_bytes_counts_utf8`; `test_tags_length_quotes_tags_with_spaces`; `test_angle_brackets_refused_in_any_field`; `test_disallowed_term_refused` (uses `contract.find_disallowed`); `test_email_refused`; `test_link_refused_except_site_line`; `test_cta_term_refused`; `test_all_problems_reported_at_once`; `test_chapters_start_at_zero_with_page_titles`; `test_short_page_joins_previous_chapter`; `test_back_cover_joins_outro`; `test_fewer_than_3_chapters_refused`; `test_record_without_chapters_names_rerender`; `test_compose_ends_with_site_line`; `test_category_storybook_is_27_and_book_override_wins`; `test_write_text_keeps_bak`; `test_upload_state_older_cut_when_final_key_differs`.

## Phase 2: Render records chapter times
**Implements:** FR-3 (chapter source) | **Satisfies:** AC-2.3
**Files:** `video_pipeline/render_episode.py`, `video_pipeline/timeline.py`, `tests/test_render_episode.py` (or the existing timeline test file)
**Interfaces:** Consumes `timeline.section_times`. Produces `timeline.chapter_starts(sections, cfg, offset_s) -> list[dict]` and `record["chapters"]`.

Goal: `<slug>.json` holds `chapters: [{"name": section name, "start": seconds}]` in final-video time.
- `offset_s` = the sum of the opening clips' seconds (channel intro, `open` bookend) − `len(before) * blend_size(cfg) / cfg.fps`; 0 without clips. It is added in the `base_record` block (render_episode.py ~1632).
- Tests: `test_chapter_starts_without_clips_match_section_times`; `test_chapter_starts_shift_by_opening_clips_minus_blend`; `test_chapters_do_not_change_final_key`.

## Phase 3: Connection and the Google client
**Implements:** FR-1, FR-4 (send, resume, thumbnail) | **Satisfies:** AC-1.1, AC-1.3, AC-1.4, AC-1.5, AC-1.6, AC-1.7, AC-3.3, AC-3.4, AC-4.4
**Files:** `video_pipeline/youtube_api.py` (new), `pyproject.toml` + `uv.lock` (deps; marker `youtube`), `tests/test_youtube_api.py` (new)
**Interfaces:** Produces `secrets_dir() -> Path`; `client_status(dir) -> tuple[bool, str]`; `begin_consent(dir, redirect_uri, scopes) -> Pending(auth_url, state, verifier, started)`; `finish_consent(dir, pending, query: dict, now, client_factory) -> Channel` (raises `ConnectError`); `connect_cli(dir, scopes, timeout_s) -> Channel`; `load_credentials(dir) -> Credentials` (raises `ReconnectNeeded`); `disconnect(dir, client)`; `connected_channel(dir) -> Channel | None`; `YouTubeClient` Protocol; `GoogleYouTube(creds)`; `upload_video(client, path, body, settings, on_progress) -> str` (resumes ≤ `max_resumes` with backoff 1, 2, 4… s on 5xx or connection errors; raises `QuotaExceeded` on 403 `quotaExceeded`/`uploadLimitExceeded`; raises `UploadError(reason, field)` on other 4xx); `make_thumbnail(cover_png, out_jpg, settings)` (reuses `frames.canvas_parts`/`compose_canvas` styling at 1280×720, JPEG ≤ 2 MB).

Goal: consent, credential storage, revoke, upload, thumbnail and existence check behind a fakeable client.
- `finish_consent`: refuses a wrong or missing `state`, an `error=` reply, or an age > 300 s with "Not connected: <reason>". A channel list with no items gives "This Google account has no YouTube channel". It writes `token.json` and `channel.json` only after every check passes, creating the directory with 0700 and the files with 0600.
- Tests (fake flow and `FakeYouTube`): `test_no_client_file_names_path`; `test_client_file_must_be_installed_type`; `test_consent_url_has_pkce_state_and_only_two_scopes`; `test_state_mismatch_stores_nothing`; `test_denied_consent_stores_nothing`; `test_expired_consent_stores_nothing`; `test_account_without_channel_refused_stores_nothing`; `test_connected_writes_token_0600_and_channel`; `test_disconnect_revokes_and_deletes_even_if_revoke_fails`; `test_revoked_token_raises_reconnect_needed`; `test_upload_body_private_made_for_kids_category_language`; `test_upload_resumes_after_connection_error`; `test_upload_gives_up_after_5_resumes`; `test_quota_error_message`; `test_metadata_rejection_names_field`; `test_thumbnail_jpeg_under_2mb_1280x720`; `test_no_secret_in_exception_text`.
- Opt-in `@pytest.mark.youtube` test: uploads a 3 s private clip to the connected channel and deletes it afterwards.

## Phase 4: Codex writes the text (MCP save tool + CLI `text`)
**Implements:** FR-2 | **Satisfies:** AC-2.1, AC-2.2, AC-2.5, AC-2.6
**Files:** `video_pipeline/youtube_mcp.py` (new), `video_pipeline/youtube.py` (CLI `text` subcommand + `write_youtube_text`), `tests/test_youtube_mcp.py`, `tests/test_youtube_text.py` (new)
**Interfaces:** Consumes the Phase 1 checks and `agents.build_codex_command` / `run_codex` / `codex_status`. Produces `write_youtube_text(folder, root, *, force, provider=None) -> TextOutcome(status: "written"|"failed", problems)` and the CLI `python -m video_pipeline.youtube text --book S --root R [--force]`.

Goal: Codex drafts a book-specific title, body and tags. The save tool refuses them with every FR-3 failure at once, the attempts loop, and the result is saved atomically.
- `youtube_mcp.py` follows `plan_mcp.py`: `parse_args(--book-json --record --out --root)`, `build_tools(args) -> YouTubeTools`, `create_server(tools)` exposing only `save_youtube_text(title: str, description: str, tags: list[str]) -> {"saved", "errors"}`. It allows at most 3 saves. Language and category are server-fixed. It validates with `text_problems` against the composed description.
- Prompt: book metadata (title, subtitle, blurb, age_range), each page's title and text, the blueprint `mood` (when present), the series name, and the rules (own words for this book, no links, no calls to action, the limits). The attempts loop copies `write_story.generate`: it appends the refusal list to the next prompt. Logs go to `build/agent/<stamp>-youtube-<n>.jsonl`.
- The CLI refuses an existing `text.json` without `--force` ("YouTube text exists; use --force", AC-2.5), and refuses unless `render_check == "up_to_date"`.
- Tests: `test_server_exposes_only_save_youtube_text`; `test_save_refuses_with_all_problems`; `test_save_limit_3`; `test_text_written_with_language_and_codex_mark`; `test_failed_attempt_feeds_problems_into_next_prompt`; `test_three_failures_save_nothing`; `test_existing_text_needs_force_and_keeps_bak`; `test_refused_when_video_not_up_to_date`; `test_prompt_carries_this_books_pages_not_another`. Opt-in `@pytest.mark.codex` real run on the mini book.

## Phase 5: CLI connect / disconnect / upload / check
**Implements:** FR-1, FR-4 | **Satisfies:** AC-1.7, AC-3.2, AC-3.5 (record), AC-3.6, AC-3.7, AC-3.8, AC-4.1, AC-4.2, AC-4.4
**Files:** `video_pipeline/youtube.py` (CLI), `tests/test_youtube_cli.py` (new)
**Interfaces:** Produces `upload_book(folder, root, client, *, confirm: Callable[[str], bool], out) -> UploadEntry`, `check_book(folder, client) -> str`, and the CLI subcommands `connect | disconnect [--yes] | status | text | upload --book S [--yes] | check --book S`.

Goal: the same gates and behaviour as the studio, in a terminal.
- `upload_book` gates, in order:
  1. `connected_channel` and `load_credentials`, else "Reconnect needed" / "Connect a channel first".
  2. `render_check(...) == "up_to_date"`, else the state and reason.
  3. `text.json` exists and `text_problems == []`, else "Write YouTube text first" plus the problems.
  4. The confirmation text: channel, title, "Made for kids", "Private", file size, and "This makes a second video; the first is <url>" when there is a previous upload.

  Then it calls `upload_video`, prints `uploaded N%` lines, calls `make_thumbnail` + `set_thumbnail` (a failure becomes the warning `thumbnail not set: <reason>`), and appends the record (AC-4.1). It writes no record on any exception or on SIGTERM.
- `--yes` skips the prompt (the studio passes it after its own confirmation).
- Tests (with `FakeYouTube` injected through `--client fake` only in tests via monkeypatch): `test_upload_refused_without_connection`; `test_upload_refused_when_video_out_of_date_with_reason`; `test_upload_refused_without_valid_text`; `test_confirmation_lists_channel_title_private_made_for_kids_size`; `test_second_upload_confirmation_links_first`; `test_progress_lines_printed`; `test_record_has_all_ac_4_1_fields`; `test_thumbnail_refusal_is_warning_and_upload_recorded`; `test_failure_writes_no_record`; `test_check_marks_not_found_and_keeps_record`; `test_connect_prints_channel_title_and_id`.

## Phase 6: Studio — YouTube settings page and book panel
**Implements:** FR-6, FR-1 (studio consent), FR-4 (job) | **Satisfies:** AC-1.1–AC-1.6, AC-2.1, AC-2.4, AC-2.5, AC-3.1, AC-3.2, AC-3.5, AC-3.6, AC-3.7, AC-4.2, AC-4.3, AC-4.4
**Files:** `video_pipeline/studio/jobs.py`, `video_pipeline/studio/books.py`, `video_pipeline/studio/app.py`, `video_pipeline/studio/templates/youtube.html` (new), `video_pipeline/studio/templates/book.html`, `video_pipeline/studio/templates/base.html` (nav link), `tests/test_studio_youtube.py` (new)
**Interfaces:** Consumes Phases 1, 3, 4 and 5. Adds the stages `youtube_text` and `youtube_upload`.

Goal: everything the producer needs, in the studio.
- `jobs.py`: `STAGE_MODULES["youtube_text"] = STAGE_MODULES["youtube_upload"] = "video_pipeline.youtube"`. `stage_command` branches build `[..., "-m", "video_pipeline.youtube", "text"|"upload", "--book", slug, "--root", R, (+"--force" | +"--yes")]`. Both go in `BOOK_STAGES`.
- `books.py`: constants and labels ("YouTube text", "YouTube upload"); `youtube_panel(paths, book) -> dict` (text and its problems, composed description, `upload_state`, last upload, the upload button's disabled reason from AC-3.6/3.7, the connection state). The progress `percent` is the last `uploaded N%` in the job log.
- `app.py` routes:
  - `GET /youtube`: settings page (client status + path, channel title/id, Connect / Disconnect, "Reconnect needed").
  - `POST /youtube/connect`: 303 to Google; pending held on `Studio`.
  - `GET /youtube/callback`: calls `finish_consent` in the threadpool, then 303 to `/youtube` with a message.
  - `POST /youtube/disconnect`: requires `confirm=1`.
  - `POST /books/{slug}/youtube/text`: saves an edit through `text_problems`; a refusal is a 409 naming the field and reason; `written_by: "producer"`.
  - `POST /books/{slug}/run/youtube_text` and `/run/youtube_upload`: through `_run_stage`. `gate` holds the AC-3.6/3.7 reasons. Rewrite and upload need `confirm=1`, and the upload form shows the AC-3.1 summary.
  - `POST /books/{slug}/youtube/check`.

  The existing `SameSiteGuard` covers the POSTs. Callback errors render through `message.html` without echoing query values.
- `book.html`: a YouTube panel after `#stage-build` (text fields, Write/Rewrite text, Upload with the confirm checkbox and summary, "On YouTube (Private)" + channel + time + link, "YouTube has an older cut", "not found on YouTube", Check on YouTube, job log). Autopilot is unchanged (no step added).
- Tests (`Studio` helper, `Commands` fake, `FakeYouTube`, `KIDDO_YOUTUBE_DIR=tmp`): `test_settings_without_client_has_no_connect_button`; `test_connect_redirects_to_google_with_state`; `test_callback_wrong_state_refused_nothing_stored`; `test_callback_error_shows_not_connected`; `test_callback_no_channel_refused`; `test_callback_ok_shows_channel`; `test_disconnect_needs_confirm`; `test_reconnect_needed_shown`; `test_write_text_starts_youtube_text_job`; `test_rewrite_needs_confirm`; `test_text_edit_refused_names_field`; `test_upload_disabled_reasons`; `test_upload_needs_confirm_and_starts_job_with_yes`; `test_panel_shows_on_youtube_private_with_link`; `test_panel_shows_older_cut`; `test_cross_site_upload_post_refused`; `test_autopilot_has_no_youtube_step`; `test_no_token_in_any_page`.

## Phase 7: Docs and integration verification
**Implements:** all FRs | **Satisfies:** all ACs
**Files:** `CLAUDE.md` (commands + architecture line), `.claude/memory/steering/tech-stack.md`, `docs/specs/024-youtube-upload/quickstart.md`, `.gitignore` (nothing new: secrets live outside the repo)
- `uv run pytest tests/test_youtube*.py tests/test_studio_youtube.py tests/test_render_episode.py`, then the full `uv run pytest` and `uv run ruff check video_pipeline tests`.
- Studio preview: open `/youtube` without a client (no Connect); then with a client file → Connect → Google consent page appears (the producer signs in; I don't). Check the book page panel states with the fakes.
- Real end-to-end only if the user asks: the producer creates the Google Cloud desktop client and places `client_secret.json`, connects, re-renders the pilot (to record chapters), runs `youtube text`, then `youtube upload`, and confirms on YouTube that the video is Private, made for kids, Education, with chapters and the site line.

## Quickstart (for quickstart.md)
1. Google Cloud console → new project → enable "YouTube Data API v3" → OAuth consent screen (External, add yourself as a test user) → Credentials → OAuth client ID → *Desktop app* → download the JSON to `~/.config/kiddo/youtube/client_secret.json`.
2. `uv run python -m video_pipeline.youtube connect` (or studio → YouTube → Connect channel).
3. `uv run python -m video_pipeline.render_episode --book <slug> --force` (once, for chapter times), then `uv run python -m video_pipeline.youtube text --book <slug>`.
4. `uv run python -m video_pipeline.youtube upload --book <slug>`. Make the video public later in YouTube Studio (until Google audits the project, the API keeps it Private).
