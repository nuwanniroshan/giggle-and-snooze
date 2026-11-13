# Quickstart 024: YouTube upload
1. Google Cloud console → new project → enable "YouTube Data API v3" → OAuth consent screen (External, add yourself as a test user) → Credentials → OAuth client ID → *Desktop app* → download the JSON to `~/.config/kiddo/youtube/client_secret.json`.
2. `uv run python -m video_pipeline.youtube connect` (or studio → YouTube → Connect channel).
3. `uv run python -m video_pipeline.render_episode --book <slug> --force` (once, for chapter times), then `uv run python -m video_pipeline.youtube text --book <slug>`.
4. `uv run python -m video_pipeline.youtube upload --book <slug>`. Make the video public later in YouTube Studio (until Google audits the project, the API keeps it Private).
