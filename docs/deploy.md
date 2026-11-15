# Deploy guide (spec 029, S9)

Status: written, nothing deployed. Everything below that needs an account, key, DNS record or legal
sign-off is owner-only (see the last table). Test mode first: use Stripe test keys until launch.

Topology:

| Piece | Host | Domain | Source |
|---|---|---|---|
| Store | Vercel project `giggle-web` | `giggleandsnooze.com` (+ `www` redirect) | `apps/web` |
| Reader | Vercel project `giggle-reader` | `read.giggleandsnooze.com` | public Readest fork + `apps/reader-patches` |
| Store data | Supabase project "store" | `*.supabase.co` | `supabase/` |
| Reader data (optional) | Supabase project "reader", separate | `*.supabase.co` | Readest's own schema |
| Studio | local Mac only | none | root Python project |

## 1. Supabase production project (store)

1. Create a project (region near your Vercel region). Note the project URL, anon key and service-role key
   (Project Settings, API). The service-role key is a server secret: never a `NEXT_PUBLIC_` variable.
2. Apply the schema: `supabase link --project-ref <ref>` then `supabase db push`. This runs
   `supabase/migrations/20261115000000_storefront.sql`, which creates the tables, RLS, `can_read_book`,
   the profile trigger and the buckets `covers` (public) and `epubs` (private). Do not run `seed.sql` in
   production (sample rows without files).
3. Check Storage shows both buckets, `epubs` not public. Do not add policies on `epubs`.
4. Auth, Providers: enable Email (magic link; keep "confirm email" on) and Google (create an OAuth client in
   Google Cloud, redirect URI `https://<ref>.supabase.co/auth/v1/callback`, paste client id and secret).
5. Auth, URL Configuration:
   - Site URL: `https://giggleandsnooze.com`
   - Redirect URLs: `https://giggleandsnooze.com/auth/callback`, `https://www.giggleandsnooze.com/auth/callback`
     (keep `http://localhost:3000/auth/callback` only in the dev project). `read.giggleandsnooze.com` needs
     no entry here: the reader does not use the store's auth; it only receives a signed file URL.
6. Auth, SMTP: configure a real sender (the built-in mailer is heavily rate limited).
7. CORS for the signed EPUB URL: the reader (origin `https://read.giggleandsnooze.com`) fetches the signed
   Storage URL from the browser. Supabase Storage answers with `Access-Control-Allow-Origin: *` by default, so
   no proxy is needed. After deploy, verify once:
   `curl -sI -H 'Origin: https://read.giggleandsnooze.com' '<signed url>' | grep -i access-control`.
   If it is missing, route the EPUB through a small same-origin proxy on the reader's domain instead.
8. Publish a book: `uv run python -m video_pipeline.publish_store ...` (S8) with the production URL and
   service-role key in the shell environment, never in a file in the repo.

## 2. Stripe

1. Account in test mode first. Products, Add product: "Giggle & Snooze membership", recurring price (placeholder
   test price). Copy the price id (`price_...`) to `STRIPE_MEMBERSHIP_PRICE_ID`.
2. Paid books need no pre-created price: checkout uses `books.stripe_price_id` when set, else an inline price from
   `price_cents`.
3. Webhook: Developers, Webhooks, Add endpoint `https://giggleandsnooze.com/api/stripe/webhook`, events:
   `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`.
   Copy the signing secret to `STRIPE_WEBHOOK_SECRET`. The handler is idempotent (upserts) and returns 500 on a
   database error so Stripe retries. Card-only checkout is assumed: delayed payment methods would also need
   `checkout.session.async_payment_succeeded`. Refunds and disputes do not remove an entitlement yet (manual:
   delete the row in `entitlements`).
4. Customer portal: Settings, Billing, Customer portal, enable cancel subscription and payment method update.
   (A "Manage membership" button calling the portal is not built yet; until then cancellation goes through
   Stripe-sent receipts or the owner.)
5. Local test: `stripe listen --forward-to localhost:3000/api/stripe/webhook` and use its `whsec_` secret.
6. Going live: repeat with live keys, new webhook endpoint and secret; swap all three Stripe variables.

## 3. Vercel

### Store (`giggle-web`)

- Import the repo, Root Directory `apps/web`, Framework Next.js, Node.js 24.x, install command `pnpm install`
  (Vercel detects the workspace). Production branch: `main`.
- Environment variables (Production and Preview; use test keys in Preview):

| Variable | Scope | Value |
|---|---|---|
| `NEXT_PUBLIC_SITE_URL` | public | `https://giggleandsnooze.com` |
| `NEXT_PUBLIC_READER_URL` | public | `https://read.giggleandsnooze.com` |
| `NEXT_PUBLIC_SUPABASE_URL` | public | store project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | store anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | store service-role key |
| `STRIPE_SECRET_KEY` | secret | `sk_live_...` (or `sk_test_...`) |
| `STRIPE_WEBHOOK_SECRET` | secret | `whsec_...` of the endpoint above |
| `STRIPE_MEMBERSHIP_PRICE_ID` | secret | `price_...` |

  Mark the four secret ones Sensitive. `.env.example` at the repo root lists the same names.

### Reader (`giggle-reader`)

- Source: the public fork of Readest (AGPL-3.0) with `apps/reader-patches/0001-open-book-from-src.patch` committed
  on top (until the fork exists: `git submodule update --init --recursive`, then
  `git -C apps/reader apply ../reader-patches/*.patch`). Root Directory `apps/readest-app`, Node 24, run
  `pnpm install` at the fork root then `pnpm setup-vendors` before the web build (`build-web` has not been run
  yet; do it locally first).
- Env (Readest names, from `reader-notes.md`): `NEXT_PUBLIC_APP_PLATFORM=web`. Leave `NEXT_PUBLIC_POSTHOG_*`
  and Stripe variables unset. `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` point to a
  separate reader Supabase project or stay unset (reader fully local, progress in the browser). Never point the
  reader at the store's project: Readest's tables (`books`, ...) collide with the store's catalog and its RLS
  and storage layout would mix with entitlements.
- Branding and an allowlist for `src` (the Supabase host only) are listed as follow-ups in
  `apps/reader-patches/README.md`; do them before launch.

## 4. DNS

At the registrar (or move nameservers to Vercel):

| Name | Type | Value |
|---|---|---|
| `giggleandsnooze.com` (apex) | A | `76.76.21.21` (or the value Vercel shows for the project) |
| `www` | CNAME | `cname.vercel-dns.com` (set as redirect to the apex in Vercel) |
| `read` | CNAME | `cname.vercel-dns.com` (domain added to `giggle-reader`) |

Add each domain in the matching Vercel project (Settings, Domains); Vercel shows the exact records and issues
TLS. Email DNS (SPF/DKIM) for the Supabase SMTP sender goes in the same zone.

## 5. AGPL source link

The reader is AGPL-3.0, so every user of `read.giggleandsnooze.com` must be offered the corresponding source of
the exact deployed version. Required before launch:

- The fork is public and contains the patch, branding and any other change; the deployed commit is a tag.
- The reader shows a visible "Source code" link to that fork (About dialog and footer).
- The store footer or an About page may also link it. Keep the fork's `LICENSE` and upstream notices.
- Store, Python pipeline and books remain separately licensed (ADR-029-reader-readest-agpl).

## 6. Launch checklist

- [ ] Migration applied; `epubs` private, `covers` public; no `seed.sql` rows in production.
- [ ] `supabase/tests/access.sql` run against a scratch database with the same migration.
- [ ] Email and Google sign-in work; redirect URLs set; custom SMTP configured.
- [ ] Stripe live keys, product/price, webhook and portal set; a real small test purchase and a subscription
      recorded in `entitlements` / `memberships`; refund path decided.
- [ ] A signed-out visitor reads a free sample; a non-owner is refused the full EPUB; an owner and a member read it.
- [ ] CORS header present on a signed Storage URL for the reader origin.
- [ ] Reader forked, patched, branded, `src` allowlist, source link visible, `build-web` passes, tag deployed.
- [ ] DNS for apex, `www`, `read`; HTTPS valid; `www` redirects to apex.
- [ ] Terms, privacy, refunds pages reviewed by a lawyer; children's privacy (COPPA) review done.
- [ ] Analytics (if any) consent-free and minimal; Stripe tax/shipping not enabled (non-goals).
- [ ] At least one book published through S8, cover and EPUB loading in the store and the reader.
- [ ] Environment variables match the table above in Production; no secret in any `NEXT_PUBLIC_` variable.

## 7. Owner-only actions

| Action | Why only the owner |
|---|---|
| Create Supabase, Stripe, Vercel, Google Cloud, GitHub accounts and projects | Accounts, billing, identity |
| Generate and store API keys, service-role key, webhook secret | Secrets must not pass through the assistant |
| Buy or transfer `giggleandsnooze.com` and edit DNS | Registrar access |
| Configure SMTP sender and Google OAuth consent screen | Account verification |
| Publish the Readest fork publicly under the owner's GitHub | Outward-facing, AGPL commitment |
| Legal review of terms, privacy, refunds, COPPA, AGPL compliance | Professional judgement |
| Approve the store design (S3/S4/S10) and set real prices and member-included books | Product decision |
| Switch Stripe to live mode | Financial |
