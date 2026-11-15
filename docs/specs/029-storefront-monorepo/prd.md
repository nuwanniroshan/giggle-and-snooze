# PRD: Giggle & Snooze storefront, membership and reader (monorepo)

**Date:** 2026-11-15
**Feature:** 029-storefront-monorepo
**Status:** Draft from default recommendations; the owner reviews the build later.

## Problem

Books made by the studio (editions: print PDF, digital PDF, fixed-layout EPUB 3, web reader, plus the YouTube video) have no public place to be read or bought. The repo is a single Python project.

## Users & Context

- **Parents/readers** at giggleandsnooze.com: browse, read free books, buy books, subscribe for membership, read in a web eReader.
- **The publisher** (the user): makes books in the local studio (stays local, unchanged) and publishes chosen books to the store.
- Touches: new `pnpm-workspace.yaml`, `apps/web`, `apps/reader`, `packages/`, `supabase/`; a publish script from `books/<slug>/build/` to Supabase; `.gitignore`, `.claude/launch.json`, CLAUDE.md.

## Goals

- Repo is a polyglot monorepo; the Python studio runs exactly as before (ADR-029-monorepo-layout).
- A public store on the Vercel Shop template with Supabase and Stripe (ADR-029-storefront-stack).
- Access levels per book: free, members, paid; a membership unlocks all member-included books.
- Readers open entitled books in a Readest-based eReader (ADR-029-reader-readest-agpl).
- Domain layout: `giggleandsnooze.com` store, `read.giggleandsnooze.com` reader, studio local only.

## Non-Goals

- Moving the Python code or books into `apps/studio`.
- Print-on-demand ordering, physical shipping, tax setup, multi-currency (Stripe test mode only for now).
- Hosting the video (YouTube stays the video home).
- Moderation, reviews, gifting, multiple child profiles.

## Success Criteria

- `uv run python -m video_pipeline.studio` and the targeted tests still pass with no path change.
- `pnpm install && pnpm --filter web build` succeeds; the store lists published books from Supabase.
- A signed-out visitor can read a free book's sample; a signed-in free user reads free books; a purchaser or member reads paid books; others are refused by RLS and by the signed-URL endpoint (checked by a small SQL/API test).
- A Stripe test-mode purchase and subscription write the right entitlement through the webhook.
- Publishing one book from the studio output creates a catalog row and uploads the EPUB and cover.

## Architecture Decisions

- [ADR-029-monorepo-layout](../../adr/029-monorepo-layout.md): keep Python at the root; add `apps/*`, `packages/*`, `supabase/`.
- [ADR-029-storefront-stack](../../adr/029-storefront-stack.md): Vercel Shop UI shell + Supabase + Stripe; Shopify code removed.
- [ADR-029-reader-readest-agpl](../../adr/029-reader-readest-agpl.md): separate Readest fork, AGPL source published, signed-URL handoff.

## Stories

Order is build order. Each story is small enough for one sdd-specify cycle.

1. **S1 Monorepo skeleton.** pnpm workspace, root scripts, `.gitignore`, launch config, CLAUDE.md section. Done when `pnpm install` works and the studio is unaffected.
2. **S2 Supabase schema.** Migrations for `profiles`, `books`, `entitlements`, `memberships`, private `epubs`/`covers` storage, RLS, seed with 1 free + 1 paid book. Done when a SQL test shows RLS behaves per the access rule.
3. **S3 Store shell.** Copy Vercel Shop into `apps/web`, strip Shopify/Eve, brand it (Giggle & Snooze), home, library, book page, search; data from Supabase.
4. **S4 Auth.** Supabase Auth (email magic link + Google) in the store; account page.
5. **S5 Payments.** Stripe Checkout for one-off buys and a membership subscription, customer portal, webhook writing entitlements/memberships.
6. **S6 Access service.** `/api/books/[slug]/read` checks entitlement and returns a short-lived signed EPUB URL; free samples public.
7. **S7 Reader.** Readest fork submodule at `apps/reader`, web build, branding, AGPL source link, opens a book from a signed URL; verify fixed-layout rendering on a real EPUB.
8. **S8 Publish from studio.** `scripts/publish_book.py --book <slug>` uploads EPUB, cover, metadata from `book.json` and creates the catalog row (draft until marked published); optional studio button later.
9. **S9 Deploy and domain.** Vercel projects for web and reader, DNS for giggleandsnooze.com and read.giggleandsnooze.com, env docs, Supabase production project.
10. **S10 Launch hygiene.** Legal pages (terms, privacy, refunds, children's privacy/COPPA review), AGPL compliance check, minimal analytics.

## Out of Scope

Anything under Non-Goals, mobile apps from Readest's Tauri targets, and changes to the video or book-production pipeline.

## Open Questions (defaults taken)

- Store and reader Supabase: decided separate (see ADR-029-reader-readest-agpl and `reader-notes.md`).
- Membership price and which books are member-included: placeholder test prices.
- Public GitHub fork of Readest under the user's account: needs the user's approval.

## Build status (2026-11-15, first pass, uncommitted on `feat/029-storefront-monorepo`)

| Story | State |
|---|---|
| S1 skeleton | Done (workspace, `.nvmrc`, `.env.example`, root scripts, CLAUDE.md) |
| S2 schema | Done and verified in Postgres 15 with Supabase stubs: `supabase/tests/access.sql` and the RLS block checks passed. Review fix since: users can update only `profiles.display_name` (column grant); not re-run in Postgres |
| S3 store shell | Done pending owner design approval; typecheck and `next build` pass (Node 24) |
| S4 auth | Done pending design approval (email magic link and Google code paths); provider setup in `docs/deploy.md`, nothing configured |
| S5 payments | Checkout and webhook written and reviewed (webhook returns 500 on database errors so Stripe retries; stale cancelled subscriptions no longer overwrite a live one); not run against Stripe |
| S6 access | `/api/books/[slug]/read` written and reviewed, not run against a live project |
| S7 reader | Patch (`apps/reader-patches`) and `reader-notes.md` done; open-from-`src` verified on a real EPUB locally; **not forked**, not branded, no production build |
| S8 publish | `video_pipeline/publish_store.py` with 3 unit tests; not run against Supabase |
| S9 deploy | Docs done (`docs/deploy.md`); nothing deployed |
| S10 launch hygiene | Done pending design approval and legal review (legal pages exist); AGPL source link in the reader and analytics not done |
