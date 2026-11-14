# Quickstart 024: YouTube upload

A producer's guide: connect the project's channel once, then for each book write the YouTube text and upload the rendered video. Every upload goes up **Private** and **made for kids**; you make it public yourself in YouTube Studio. Nothing here runs from autopilot.

## 1. Google Cloud setup (once)

1. Open the Google Cloud console and create a project (any name, e.g. "kiddo uploads").
2. **APIs & Services → Library**: enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen**: user type **External**; fill in the app name and your email; under **Test users**, add the Google account that owns the YouTube channel. Leave the app in *Testing*.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**: application type **Desktop app**. Download the JSON.
5. Save it as `~/.config/kiddo/youtube/client_secret.json` (or `$KIDDO_YOUTUBE_DIR/client_secret.json` if you set that variable). Keep this folder out of the repo; the pipeline writes its files there with mode 0600.

Check it: `uv run python -m video_pipeline.youtube status` (or the studio's **YouTube** page) shows the OAuth client as ready.

## 2. Connect the channel (once)

- Studio: **YouTube → Connect channel**, approve in Google, and you land back on the YouTube page with the channel's name and id.
- CLI: `uv run python -m video_pipeline.youtube connect` opens Google's consent page in your browser and waits for the answer.

Google asks for upload and read-only channel access. The token stays in the secrets folder (`token.json`, `channel.json`); no page or log shows it. If Google later stops accepting it (access revoked, or a *Testing* app's refresh token expired after 7 days), the YouTube page shows **Reconnect needed**: press Connect channel again. Uploads refuse to start until you do.

## 3. Re-render once for chapter times

Chapters come from the render record (`books/<slug>/build/video/<slug>.json`, key `chapters`). Videos rendered before spec 024 lack it, so re-render each one once:

```bash
uv run python -m video_pipeline.render_episode --book <slug> --force
```

## 4. Write the YouTube text

```bash
uv run python -m video_pipeline.youtube text --book <slug> [--force]
```

Codex writes the title, description body and tags (up to 3 attempts, each checked for length, kid safety and no calls to action) into `books/<slug>/youtube/text.json`. Review and edit it in the studio's book page (YouTube panel) or by hand; `--force` rewrites it and keeps the old one as `.bak`. The final description is your body + the chapter list + the site line from `config/youtube.json`. Category comes from the content kind (storybook = Education, 27).

## 5. Upload

- Studio: book page → **YouTube** panel → Upload (with confirmation). The panel shows the progress percentage, then the link.
- CLI: `uv run python -m video_pipeline.youtube upload --book <slug>` (asks first; `--yes` skips the question).

The upload is resumable, sets the cover as thumbnail (a failed thumbnail is only a warning), and appends the result to `books/<slug>/youtube/uploads.json` (commit it with `text.json`). Uploading again after a new render adds a second entry; the panel links the earlier one.

Later, `uv run python -m video_pipeline.youtube check --book <slug>` (or **Check** in the panel) confirms the last upload is still on YouTube and marks it `not_found` if it was deleted.

## 6. Make it public

Open YouTube Studio, review the video (made-for-kids is already set), and change visibility to Public or Unlisted. Until Google audits the API project, YouTube keeps API uploads Private anyway; that is expected.

## Quota

The default YouTube Data API quota is 10,000 units a day; one upload costs about 1,600, so plan on about **6 uploads a day**. When it runs out the upload stops with a quota message and nothing is recorded; try again after the daily reset (midnight Pacific time).

## Disconnect

- Studio: **YouTube → Disconnect** (tick the confirmation).
- CLI: `uv run python -m video_pipeline.youtube disconnect [--yes]`.

The token is revoked with Google (best effort) and the local `token.json` and `channel.json` are deleted. `client_secret.json` stays, so you can connect again (another channel, too). Upload records in the books are kept.

## Tests

`uv run pytest` never contacts Google. `uv run pytest -m youtube` runs the opt-in real upload of a short Private clip to the connected channel and deletes it again (it uses quota).
