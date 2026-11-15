# ADR-029-monorepo-layout: Monorepo layout

**Status:** Proposed (default chosen without review; confirm or override)
**Date:** 2026-11-15
**Feature:** 029-storefront-monorepo

## Context

The repo is a Python pipeline (`video_pipeline/`, `scripts/`, `tests/`, `books/` at 7.7 GB, `config/`, `assets/`) whose path root is derived in `video_pipeline/library.py`, with 28 specs, CLAUDE.md and rules that name those paths. The studio must keep running locally. We now add a public Next.js store, a web reader and a Supabase backend. Moving the Python tree into `apps/studio/` would touch every doc, test fixture, launch config and the user's 46 uncommitted files for no runtime gain.

## Options Considered

### Option A: Keep the Python tree at the root; add `apps/*`, `packages/*`, `supabase/` beside it

**Pros:** Zero change to the studio, tests, paths or git history of books; the JS workspace is additive; easy to move later.
**Cons:** Root mixes Python and JS files; "monorepo" is polyglot by convention, not by folder.

### Option B: Move everything Python into `apps/studio/`

**Pros:** Symmetric layout, each app self-contained.
**Cons:** Rewrites root derivation, 28 specs, CLAUDE.md, `.claude/rules`, launch config and 7.7 GB of tracked/ignored book paths; high risk, merges badly with in-flight branches.

## Decision

**We chose Option A.** pnpm workspace (`pnpm-workspace.yaml`) with `apps/web` (Next.js store), `apps/reader` (Readest fork, see ADR-029-reader-readest-agpl), `packages/*` (shared TypeScript, e.g. generated Supabase types), and `supabase/` (migrations, config). The studio stays `uv run python -m video_pipeline.studio` at the root; root `package.json` only carries convenience scripts (`studio`, `web`, `reader`).

## Consequences

The studio and book scripts are untouched. A later move to `apps/studio/` stays possible as one mechanical change. Python and JS tooling coexist at the root, so `.gitignore` gains `node_modules/`, `.next/`, `.vercel/`.
