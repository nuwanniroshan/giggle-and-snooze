# Giggle & Snooze: Kiddo

A pipeline that turns an illustrated children's book into publishable outputs:

1. **Book production.** Print and digital editions (PDF, EPUB, web reader) built from `books/<slug>/book.json` and `books/<slug>/illustrations/`.
2. **Storytime video factory.** A 16:9 1080p YouTube read-aloud video, made from the same book. Covers the episode script, voices, animation, render and YouTube upload. A local web studio runs the stages.
3. **Storefront (monorepo).** A public store (`apps/web`), an eReader (`apps/reader`, a Readest fork), and Supabase (`supabase/`).

Pilot book: *The Snail Mail Express: A Whisperwood Hollow Story* (`books/the-snail-mail-express/`).

Design docs: [`PRD.md`](PRD.md), specs in `docs/specs/`, the deploy guide in [`docs/deploy.md`](docs/deploy.md). Project rules for Claude Code are in [`CLAUDE.md`](CLAUDE.md) and `.claude/rules/`.

---

## Contents

- [Requirements](#requirements)
- [Setup on a new machine](#setup-on-a-new-machine)
- [Moving from another machine: what to copy](#moving-from-another-machine-what-to-copy)
- [Everyday commands](#everyday-commands)
- [Studio (web UI)](#studio-web-ui)
- [Book production](#book-production)
- [Video pipeline](#video-pipeline)
- [Storefront and reader](#storefront-and-reader)
- [YouTube upload](#youtube-upload)
- [Tests](#tests)
- [Repository layout](#repository-layout)
- [Environment variables](#environment-variables)
- [Troubleshooting](#troubleshooting)

---

## Requirements

The video pipeline is built for **Apple Silicon (M1 Pro, 16 GB)** and runs stages one at a time to fit memory. Book production and the storefront run on any machine with the tools below, but the voice, animation and segmentation models need macOS on Apple Silicon.

| Tool | Version | Needed for | Install (macOS, Homebrew) |
|---|---|---|---|
| Git | recent | clone, submodule | `xcode-select --install` |
| Python | 3.12 (managed by uv) | everything Python | installed by `uv` (see below) |
| uv | recent | Python env and commands | `brew install uv` |
| FFmpeg | Homebrew build (no rubberband needed) | video render, pitch lift | `brew install ffmpeg` |
| Node.js | >= 22.12 (`.nvmrc` = 24) | storefront, reader, Supabase CLI | `brew install node@24` or `nvm install` |
| pnpm | version in `package.json` (`packageManager`) | JS workspace | `corepack enable` (uses the pinned version) |
| Docker | running | local Supabase stack only | Docker Desktop |
| ChatGPT app with Codex CLI | logged in with ChatGPT | default script, plan, camera, cover and YouTube text provider | install the ChatGPT app and sign in |
| Gemini API key | optional | `--provider gemini-api` only | Google AI Studio |

The Codex CLI ships inside the ChatGPT app at `/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex`. Set `KIDDO_CODEX_BIN` to use another binary.

Nothing needs a paid API key for the default path. Codex uses the ChatGPT plan quota.

---

## Setup on a new machine

Run these in a terminal, from the folder where the project should live.

### 1. Clone with the reader submodule

```bash
git clone <repo-url> kiddo
cd kiddo
git submodule update --init --recursive
```

`apps/reader` is a git submodule (Readest, AGPL-3.0, pinned to a commit). Skip the second command only if you do not need the eReader.

### 2. Install system tools

```bash
brew install uv ffmpeg node@24
corepack enable
```

Install Docker Desktop too if you will run the local Supabase stack (step 6).

### 3. Python environment

```bash
uv sync --inexact
```

`uv` creates `.venv` with Python 3.12 (it downloads 3.12 if missing) and installs the locked dependencies from `uv.lock`. `--inexact` keeps any extra packages already in `.venv`; drop it for a clean install.

Check it works:

```bash
uv run pytest
```

Unit tests skip the real models and providers (see [Tests](#tests)).

### 4. Download the local models (one time, several GB)

```bash
uv run python -m video_pipeline.synthesize_voices download   # Orpheus voice model (pinned)
uv run python -m video_pipeline.cut_layers download           # SAM 2.1 Core ML + LaMa ONNX
```

Models are stored in `$KIDDO_MODELS_DIR`, or `~/.cache/kiddo/models` if unset. Copy this folder from the old machine to skip the downloads (see [what to copy](#moving-from-another-machine-what-to-copy)).

### 5. Sign in to the providers

- **ChatGPT app:** open it and sign in. Codex uses this login.
- **Gemini (optional):** export `GEMINI_API_KEY` in your shell profile. Only needed with `--provider gemini-api`.
- **YouTube (optional):** see [YouTube upload](#youtube-upload).

Confirm Codex is reachable:

```bash
/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex --version
```

### 6. Storefront, Supabase and the reader (optional)

Only needed for `apps/web`, `supabase/` or `apps/reader`.

```bash
pnpm install                       # root workspace (apps/web, supabase CLI)
cp .env.example apps/web/.env.local  # then fill the values (see Environment variables)

pnpm db:start                      # local Supabase: needs Docker running
pnpm exec supabase status -o env   # copy the keys into apps/web/.env.local
```

Local Supabase ports: Postgres `54322`, API `54321`, Studio `54323`, Mailpit (magic-link emails) `54324`. Migrations and `supabase/seed.sql` apply on start. `pnpm db:reset` rebuilds the database.

Run the store:

```bash
pnpm web                           # http://localhost:3000
```

Run the reader:

```bash
cd apps/reader
pnpm install                       # once, inside the submodule (its own workspace)
cd ../..
pnpm reader                        # needs the reader's .env.web
```

### 7. Check the studio

```bash
./start.sh                         # or: uv run python -m video_pipeline.studio
```

Open http://127.0.0.1:8765. The studio binds to 127.0.0.1 only, so it is reachable from this Mac only.

---

## Moving from another machine: what to copy

The repo contains the book source, scripts, configs, sounds and fonts. It does **not** contain the items below. Copy them yourself over a trusted channel (encrypted drive, password manager, or AirDrop). Do not commit them, and do not paste the secrets into chat.

| Item | Where it lives | In git? | Needed? | If you skip it |
|---|---|---|---|---|
| Book builds and story drafts | `books/<slug>/build/`, `build/`, `drafts/` | no (git-ignored) | optional | Rebuild with the commands below. Drafts are not recoverable from git, so copy `drafts/` if you want them. |
| Model cache | `~/.cache/kiddo/models` (or `$KIDDO_MODELS_DIR`) | no | saves a multi-GB download | Run the two `download` commands. |
| YouTube OAuth files | `~/.config/kiddo/youtube/` (or `$KIDDO_YOUTUBE_DIR`): `client_secret.json`, `token.json`, `channel.json` | no, secret | only for uploads | Run `uv run python -m video_pipeline.youtube connect` again. Copy `client_secret.json` (from Google Cloud) as the same file. |
| Env files | `apps/web/.env.local`, `apps/reader/.env.web` | no, secret | only for storefront and reader | Recreate from `.env.example` and the Supabase keys. |
| Your own Claude Code memory | `~/.claude/projects/...` | no | nice to have | Start fresh; the project's `CLAUDE.md` carries the rules. |
| Shell variables | `~/.zshrc` (`GEMINI_API_KEY`, `KIDDO_*`, `SUPABASE_*`) | no, secret | as needed | Export them again. |
| Supabase service-role key, Stripe keys | your dashboards | no, secret | production only | See [`docs/deploy.md`](docs/deploy.md). Never store them in the repo. |

**Do not copy:** `.venv/` (rebuild with `uv sync`), `node_modules/` (rebuild with `pnpm install`), `.DS_Store`, `.pytest_cache/`, `.ruff_cache/`. These are machine-specific.

Quick check after the copy:

```bash
git status                          # should be clean apart from what you copied in
uv run pytest                       # should pass on the new machine
```

---

## Everyday commands

```bash
# Books
.venv/bin/python scripts/compose_missing_illustrations.py --book the-snail-mail-express
.venv/bin/python scripts/build_book.py --book the-snail-mail-express

# Video (stage 1: script, stage 2: voices, stage 3: render)
uv run python -m video_pipeline.generate_script --book <slug>
uv run python -m video_pipeline.synthesize_voices --book <slug> --reviewed
uv run python -m video_pipeline.synthesize_voices --book <slug> approve
uv run python -m video_pipeline.render_episode --book <slug>

# Studio
./start.sh
```

Read the rest of this file for each stage.

---

## Studio (web UI)

`video_pipeline/studio` is a local web app for the whole workflow: add a book from an idea, review and edit the episode script, approve voices, ground the camera, paint and cut illustrations, render, and upload.

```bash
./start.sh                    # http://127.0.0.1:8765
./start.sh --port 9000        # another port
```

Only one studio can run per project (a lock in `build/studio/studio.lock`). Stop long jobs from the studio, not by killing the terminal.

---

## Book production

Print and digital editions for each book, built into `books/<slug>/build/`.

```bash
# 1. Regenerate the composited illustrations (pages 3, 6, 7, 10, 11, 14, 15, 19 and map_whisperwood.png).
#    This overwrites those files.
.venv/bin/python scripts/compose_missing_illustrations.py --book the-snail-mail-express

# 2. Build every deliverable.
.venv/bin/python scripts/build_book.py --book the-snail-mail-express
```

Outputs in `books/<slug>/build/`:

- Front and back cover PNGs (300 DPI), KDP wrap cover PDF/PNG
- `<Title>_<N>Page_Digital_Edition.pdf` (8.5 x 8.5 in)
- Complete reader PDF
- `<Title>_KDP_<P>Page_Print_Interior.pdf` (with bleed)
- Fixed-layout EPUB 3 (fonts embedded)
- `index.html` web reader, with `build/reader/`. Open it from `build/` so the relative `../illustrations/` and `audio/tracks/` paths work.

Book-specific front and back matter comes from the book's optional `print_extras` object, over the generic templates in `config/book_extras.json`.

Build order: covers first, because the reader PDF and wrap cover read `build/*_Cover.png`.

---

## Video pipeline

The episode is produced in stages. Each stage is idempotent and skips work whose inputs have not changed. Every generated script and every voice needs human review before it is used.

### Stage 1: episode script

```bash
uv run python -m video_pipeline.generate_script --book <slug> [--provider codex|gemini-api] [--seed N] [--force]
```

Writes `books/<slug>/episode/episode_script.json`. It is hand-editable; regenerating needs `--force`. Agent logs go to `books/<slug>/build/agent/`.

To use the episode tools from the ChatGPT or Codex app as an MCP server:

```bash
uv run python -m video_pipeline.mcp_server --book <slug>
```

### Stage 2: voices

```bash
uv run python -m video_pipeline.synthesize_voices --book <slug> --reviewed [--force]   # speak the reviewed script
# listen to books/<slug>/build/audio/preview.wav, then:
uv run python -m video_pipeline.synthesize_voices --book <slug> approve
```

Refuses to run without `--reviewed`. Outputs in `books/<slug>/build/audio/`.

### Animation and layers (optional, per page)

```bash
uv run python -m video_pipeline.motion_plan --book <slug> [--page N] [--force]   # Codex writes the motion plan
uv run python -m video_pipeline.cut_layers --book <slug> [--page N] [--force]    # SAM cuts layers, LaMa fills the background
uv run python -m video_pipeline.ground_camera --book <slug> [--page N] [--force] # Codex names the Q&A subject, SAM masks it
```

Plans live in `books/<slug>/animation/page_NN.json` and camera targets in `books/<slug>/camera/page_NN.json`. Both are committed.

### Illustrations (repaint and cleanup)

```bash
uv run python -m video_pipeline.illustrate --book <slug> [--page N [--cut] | --cover front|back]
uv run python -m video_pipeline.illustrate --book <slug> --page N --cleanup [--yes]
```

Repainting keeps the old picture as `.bak`. Cleanup removes small artifacts without repainting the page.

### Autopilot

```bash
uv run python -m video_pipeline.autopilot --book <slug>
```

Paints, cuts, scripts, auto-reviews, synthesizes voices, auto-approves, renders and builds editions. It never uploads to YouTube.

### Stage 3: render

```bash
uv run python -m video_pipeline.render_episode --book <slug> [--sfx-dir DIR] [--force]
uv run python -m video_pipeline.render_episode --book <slug> --preview-page N   # one page section, no approval needed
```

Output: `books/<slug>/build/video/<slug>.mp4` (and `.json`, which records the chapters).

### Book blueprint and new books

```bash
uv run python -m video_pipeline.write_story ideas --run <run-id>
uv run python -m video_pipeline.write_story candidate --run <run-id>
uv run python -m video_pipeline.write_story book --draft <draft-id>
uv run python -m video_pipeline.write_story blueprint --draft <draft-id> [--then-book]
uv run python -m video_pipeline.write_story blueprint --book <slug>   # old blueprint kept as .bak
```

Drafts live in `drafts/` (git-ignored). Copy the folder to keep them.

### Sound library

`assets/sound_library/` holds CC0 ambience and sound effects with an `index.json`. Check it:

```bash
uv run python -m video_pipeline.sound_library list
uv run python -m video_pipeline.sound_library check
```

---

## Storefront and reader

- **Store** (`apps/web`, Next.js): `pnpm web` (dev), `pnpm web:build` (build). Uses Supabase auth, catalog and entitlements, and Stripe checkout and membership. Needs `apps/web/.env.local`.
- **Reader** (`apps/reader`, Readest submodule): `pnpm reader`. Changes live as patches in `apps/reader-patches/` until the public fork exists.
- **Publish a book to the store:**

  ```bash
  uv run python -m video_pipeline.publish_store --book <slug> --access free|members|paid [--price-cents N] [--publish]
  ```

  Uploads the built EPUB and cover, then upserts the catalog row. It is a draft unless `--publish`. Needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the environment (for local use, the local Supabase values).

- **Production deploy:** see [`docs/deploy.md`](docs/deploy.md). It covers Supabase, Stripe, Vercel, DNS, the AGPL source link and the launch checklist. Some steps need an owner account and are not automated.

---

## YouTube upload

One project-wide channel, through an OAuth desktop client.

1. In Google Cloud, create an OAuth client (type: Desktop) and enable the YouTube Data API v3. Download the JSON.
2. Save it as `client_secret.json` in `$KIDDO_YOUTUBE_DIR` or `~/.config/kiddo/youtube/`. The directory is created with 0600 permissions. Never put it in the repo.
3. Connect the channel:

   ```bash
   uv run python -m video_pipeline.youtube connect      # opens Google consent
   uv run python -m video_pipeline.youtube status
   ```

4. Write the text, then upload:

   ```bash
   uv run python -m video_pipeline.youtube text --book <slug> [--force]
   uv run python -m video_pipeline.youtube upload --book <slug> [--yes]   # always Private, made for kids
   uv run python -m video_pipeline.youtube check --book <slug>
   ```

`uploads.json` and `text.json` are committed. `token.json` is not. If the token stops working, the studio shows "Reconnect needed"; run `connect` again.

---

## Tests

```bash
uv run pytest                          # unit tests (fast, no models, no providers)
uv run pytest -m codex                 # real ChatGPT run via Codex (uses plan quota)
uv run pytest -m "ffmpeg or tts"       # real FFmpeg and Orpheus
uv run pytest -m models                # real segmentation and fill models
uv run pytest -m youtube               # real upload to the connected channel (opt-in)
```

RLS checks: run `supabase/tests/access.sql` against a scratch database (see [`docs/deploy.md`](docs/deploy.md)).

Run the tests for the files you changed while you work; run the full suite before a commit.

---

## Repository layout

```
books/<slug>/          book.json, illustrations/, references/, nooks/, episode/, animation/, camera/,
                       youtube/, blueprint.json (committed); build/ (generated, git-ignored)
video_pipeline/        Python package: stages, studio, MCP servers, publish_store, youtube
scripts/               book production (build_book.py, compose_missing_illustrations.py)
config/                shared settings (cast, voices, render, animation, camera, covers, youtube, publication)
assets/                fonts, sound library, SFX, nook images, references, reader/publication CSS and JS
tests/                 pytest suite
apps/web/              public store (Next.js)
apps/reader/           eReader submodule (Readest, AGPL-3.0)
apps/reader-patches/   patches applied to the reader
packages/              shared JS packages
supabase/              migrations, seed.sql, tests/access.sql
docs/                  deploy guide, specs, ADRs, lessons
drafts/                story drafts (git-ignored)
prototypes/            throwaway prototypes
```

Path rules: every book command takes `--book <slug>` (and `--root` for tests). Output names come from the book's title, never a hardcoded value. Read `config/` for tunables; do not hardcode them.

---

## Environment variables

Set in your shell profile (`~/.zshrc`) or per command. None of these belong in the repo.

| Variable | Used by | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `generate_script --provider gemini-api` | Gemini API key (optional) |
| `KIDDO_CODEX_BIN` | Codex provider | Path to the Codex binary (defaults to the ChatGPT app copy) |
| `KIDDO_MODELS_DIR` | `cut_layers`, `synthesize_voices` | Model cache folder (default `~/.cache/kiddo/models`) |
| `KIDDO_YOUTUBE_DIR` | `youtube` | OAuth files folder (default `~/.config/kiddo/youtube`) |
| `SUPABASE_URL` | `publish_store` | Supabase project URL (local: `http://127.0.0.1:54321`) |
| `SUPABASE_SERVICE_ROLE_KEY` | `publish_store` | Service-role key (server secret; local value from `supabase status`) |
| `HF_HUB_OFFLINE=1` | voices | Set by the pipeline; models load offline after download |

Storefront (`apps/web/.env.local`, copied from `.env.example`):

| Variable | Scope |
|---|---|
| `NEXT_PUBLIC_SITE_URL` | public |
| `NEXT_PUBLIC_READER_URL` | public |
| `NEXT_PUBLIC_SUPABASE_URL` | public |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public |
| `SUPABASE_SERVICE_ROLE_KEY` | secret (server only, never `NEXT_PUBLIC_`) |
| `STRIPE_SECRET_KEY` | secret (test key locally) |
| `STRIPE_WEBHOOK_SECRET` | secret (`whsec_...`, from `stripe listen` locally) |
| `STRIPE_MEMBERSHIP_PRICE_ID` | secret (`price_...`) |

Shared settings, not secrets, are in `config/*.json`. Sensible defaults are there; change them in the file, not in code.

---

## Troubleshooting

- **`uv run` fails with a Python version error:** run `uv python install 3.12`, then `uv sync` again.
- **Voices or segmentation fail to load:** the model is missing. Run the `download` command for it, and check `KIDDO_MODELS_DIR`.
- **`ffmpeg` not found:** `brew install ffmpeg`. Homebrew's build has no `drawtext` and no rubberband; the pipeline already works around both.
- **Codex stage fails:** open the ChatGPT app and sign in. Check `KIDDO_CODEX_BIN` points at the binary. Agent logs are in `books/<slug>/build/agent/`.
- **Studio says another studio is running:** only one is allowed per project. Stop the other one, or remove `build/studio/studio.lock` only if no studio process is running.
- **Render or voice stage refuses to run:** the script or voice is not approved. Review the script, then run `synthesize_voices ... approve`. Approval is deliberate; the pipeline does not auto-approve except through `autopilot`.
- **Supabase `db:start` fails:** Docker must be running. Free ports `54321`–`54324` if another project uses them.
- **Reader submodule is empty:** run `git submodule update --init --recursive`.
- **YouTube "Reconnect needed":** run `uv run python -m video_pipeline.youtube connect`.
- **Not on Apple Silicon:** the voice, animation and segmentation models are built for Apple Silicon (M1 Pro is the target). Other platforms are untested.
