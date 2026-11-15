# ADR-029-reader-readest-agpl: Web reader built on Readest

**Status:** Proposed (default chosen without review; confirm or override)
**Date:** 2026-11-15
**Feature:** 029-storefront-monorepo

## Context

The user asked to use Readest (github.com/readest/readest) as the eReader. Readest is a Next.js 16 + Tauri v2 app (`apps/readest-app`) that already has a web build, Supabase auth/sync and uses foliate-js (MIT) for rendering. It is licensed **AGPL-3.0**: anyone who runs a modified version as a network service must offer that version's source to its users. Our books are fixed-layout EPUB 3 from `build_book.py`; Readest's fixed-layout support must be verified on our files.

## Options Considered

### Option A: Fork Readest, deploy as a separate app at `read.giggleandsnooze.com`, publish the fork's source

**Pros:** Matches the request; full reader features (progress, notes, themes) for free; the store stays under our own licence because it is a separate program linked by URL and signed file URLs, not combined code.
**Cons:** Must publish the fork publicly and keep an AGPL source link in the reader; fork maintenance; large (370 MB) repo.

### Option B: Use only foliate-js (MIT) inside `apps/web`

**Pros:** No AGPL obligation; one deployable.
**Cons:** We build the reader UI ourselves, which the user explicitly wanted to avoid.

### Option C: Embed Readest code directly into `apps/web`

**Pros:** One deployable.
**Cons:** Makes the whole store a derivative work under AGPL; rejected.

## Decision

**We chose Option A.** `apps/reader` is a git submodule of a public fork of Readest, pinned to a commit, built as the web target and deployed separately. The store links to `https://read.giggleandsnooze.com/?book=<id>&token=<signed>` (exact handoff decided in the reader story); the reader fetches the EPUB only through a signed URL minted by `apps/web` after the entitlement check. The fork changes stay minimal (branding, a "Source code" link, a custom library source). Creating the public fork under the user's GitHub account is outward-facing and is left for the user to approve; until then the submodule points at upstream.

## Consequences

AGPL obligations apply to the reader only; the store, Python pipeline and books keep their own licences. The reader uses a separate Supabase project (or none, keeping its library in the browser): Readest's own tables (`books`, `files`, ...) collide with the store's catalog and its RLS and storage layout would mix with entitlements (see `reader-notes.md`). The handoff is `/?src=<signed URL>` through `apps/reader-patches`. If fixed-layout rendering is poor, fall back to Option B for the reader story only. Needs a legal sanity check before launch.
