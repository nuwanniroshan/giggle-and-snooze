# Data Model: Episode Script Generator

## Entities

### EpisodeScript (`episodes/<book_slug>/episode_script.json`)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| book_slug | string | = slugify(book title) | Folder name and identifier |
| title | string | = book `metadata.title` | Book title |
| aspect_ratio | string | `"16:9"` | Output frame |
| resolution | [int, int] | `[1920, 1080]` | Output size |
| cast | object | keys exactly `storyteller`, `kid_older`, `kid_younger`; each has `name`, `voice`, `description` (+ `engine`) | Copied from `config/cast.json` |
| intro | Bookend | 2–4 lines, ≥1 kid + ≥1 storyteller, mentions title, effect `zoom_in_center` | Before the book opens |
| pages | Page[] | one per book page, `page_number` 1..N in order | Narration and Q&A per page |
| outro | Bookend | 3–5 lines, ≥1 kid, last = storyteller subscribe invitation, effect `zoom_out_warm_fade` | After the book closes |

**Indexes:** none (a file).
**Relationships:** one EpisodeScript per book; `pages[i].page_number` ↔ book `pages[i].page_number`.
**Spec requirement:** FR-2

### Page

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| page_number | int | 1..N | Matches the book page |
| has_qa | bool | true on Q&A schedule pages only | Whether kids chime in |
| ffmpeg_camera.narration_motion | string | one of 5 motions | Camera while narrating |
| ffmpeg_camera.qa_zoom_target | {x, y, zoom} | Q&A pages only; x, y ∈ [0,1], zoom ∈ [1,2]; default 0.5/0.5/1.3 | Zoom during Q&A |
| qa_grounding | string | Q&A pages only; substring of page text or prompt | What the moment is about |
| lines | Line[] | narration first, then the Q&A moment (2–5 lines, ends with storyteller `answer`) | Spoken lines |

**Spec requirement:** FR-3, FR-4, FR-6

### Line

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| speaker | string | `storyteller` \| `kid_older` \| `kid_younger` | Who speaks |
| role | string | pages only: `narration` \| `question` \| `reaction` \| `answer` | Line purpose (absent in intro/outro) |
| text | string | non-empty; only tags `<giggle> <laugh> <chuckle> <gasp> <sigh> <yawn>`; kid lines ≤ 25 words; narration ≤ 2 sentences / 40 words unless one sentence | Spoken text |

**Spec requirement:** FR-2, FR-3, FR-5

### Banter (model reply, in memory only)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| intro | BanterLine[] | 2–4 | Intro dialogue |
| moments | Moment[] | one per planned Q&A page: `page_number`, `grounding`, 2–5 `lines` | Q&A dialogue |
| outro | BanterLine[] | 3–5 | Outro dialogue |

**Spec requirement:** FR-4, FR-7 (also the JSON schema sent to the model)

### Config files

| File | Format | Description |
|------|--------|-------------|
| `config/cast.json` | JSON object, three roles | Fixed recurring cast (FR-2) |
| `config/disallowed_terms.txt` | one term per line, `#` comments | Kid-safety term list (Security, AC-7.1–7.3) |

## Episode tools (added v2.0.0) (FR-8, MCP server `kiddo`)

| Tool | Input | Output |
|------|-------|--------|
| `get_episode_plan` | none | `{title, age_range, pages: [{page_number, text, illustration}], cast, qa_pages: [{page_number, kids}], brief, banter_schema}` |
| `validate_banter` | `banter` (object or JSON string: `{intro, moments, outro}`) | `{valid: bool, errors: [str]}` |
| `save_episode_script` | `banter` | `{saved: true, path, errors: []}` or `{saved: false, errors: [...]}`; at most 5 per run |

The server's command line fixes the book, seed, force flag, episodes and config directories (`--book --seed --force --episodes-dir --config-dir`).

## Agent run log

| File | Content |
|------|---------|
| `episodes/<slug>/agent/<YYYYmmdd-HHMMSS>-codex.jsonl` | Raw `codex exec --json` events: `thread.started`, `turn.started`, `item.started`/`item.completed` (`agent_message`, `mcp_tool_call`, `error`), `turn.completed` |
| `…-codex.stderr.log` | Codex stderr (diagnostics only, never credentials) |

Gitignored. Regenerated on each run.

## AgentEvent (in memory)

| Field | Type | Description |
|-------|------|-------------|
| kind | `say` \| `tool` \| `tool-result` \| `error` \| `done` | Event type |
| text | str | Message text, error message, or the tool result as JSON |
| tool | str | Tool name (`tool`, `tool-result`) |
| ok | bool | False when the tool call itself failed |
| data | object | Tool arguments (`tool`) or parsed tool result (`tool-result`) |
