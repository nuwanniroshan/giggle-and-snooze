# Reader notes (S7), 2026-11-15

Upstream Readest `a60b7ee`, Node 24, pnpm 11, Next 16.4.2 dev server on port 3001 (`.claude/launch.json` entry `reader`).

## Verdict

**Works.** After the patch in `apps/reader-patches/0001-open-book-from-src.patch`, opening
`http://localhost:3001/?src=<encoded EPUB URL>` imported `The_Snail_Mail_Express.epub` and landed in
the reader on the cover (`/reader/<hash>`), with no clicks. Fixed-layout EPUB 3 renders well: full-page
art per page, correct square aspect, text and scrim as designed, page turns by arrow key (cover 1/28,
belongs-to 2/28, story page 3 with art and words). Only a 3-page walk was done, not all 28.

## Upstream does not open from a URL

Readest has no `src` parameter. Web import is file picker, drag-drop, OPDS (`/opds?url=` browses a
catalog), or demo books (`appService.importBook(url)`, hard-coded list). The deep-link ingress hooks
(`useAppUrlIngress`, `useOpenShareLink`, clip) are Tauri-only. The patch adds the smallest path:
fetch -> `File` -> existing `importBooks` -> `setPendingNavigationBookIds` (existing navigation).

## Setup gotchas

- The nested submodule `packages/foliate-js` must be checked out (`git submodule update --init --recursive`), else every foliate import fails with 500.
- `pnpm install` in `apps/reader` must run after that (foliate-js is a workspace member that supplies `pdfjs-dist`), then `pnpm setup-vendors` in `apps/readest-app` (copies pdf.js assets to `public/vendor`, git-ignored).
- Dev: `dotenv -e .env.web -- next dev`. `.env.web` is tracked upstream; I did not read or change it (a deny rule blocked reading env files). The dev server ran with it unmodified, no secrets, no Supabase, so the reader is fully usable without a backend (library lives in browser storage).
- The EPUB host must send `Access-Control-Allow-Origin` for the reader origin (the signed Supabase Storage URL does by default; test used a local CORS server).

## Env vars (reader)

Used by the code: `NEXT_PUBLIC_APP_PLATFORM=web`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_POSTHOG_KEY/HOST` (leave unset), `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY_*` (unneeded), `NEXT_PUBLIC_SELF_HOSTED`, `NEXT_PUBLIC_OBJECT_STORAGE_TYPE`. None are needed for the handoff itself. Store side needs `NEXT_PUBLIC_READER_URL` only.

## Sharing our Supabase project

**No, use a separate project (or a separate Postgres schema, which Readest does not support).** Readest queries
`books`, `book_configs`, `book_notes`, `files`, `customers`, `payments`, `plans`, `book_shares`, `send_*`, `crosspoint_*`, `replicas`, `stat_*`
and IAP tables. Our migration also creates `books` (catalog), so names collide, and Readest's RLS and storage
layout would mix with our entitlements. Without Readest's backend, progress and notes stay local to the browser, which is acceptable for v1.
Later option: second Supabase project for Readest sync, with auth bridged to the store.

## Recommendations

1. Keep Readest (fixed-layout is good); foliate-js fallback is not needed.
2. Create the public fork now (AGPL), commit the patch there, repoint `.gitmodules`, drop `apps/reader-patches`.
3. Branding + "Source code" link + hide OPDS/Send/translation/auth prompts (see `apps/reader-patches/README.md`).
4. Restrict `src` to our storage host allowlist; the store already mints short-lived signed URLs. Note a re-import of the same file is deduped by hash, so repeat visits reuse the browser copy (revoked access is not enforced for already imported books; acceptable, or clear the library on sign-out).
5. Reader pane opens with the sidebar pinned on a 4:3 viewport; consider default sidebar closed for kids' layout.
6. Production: `build-web` was not run (dev only). Run it before S9.
