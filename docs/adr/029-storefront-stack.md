# ADR-029-storefront-stack: Storefront stack, backend and payments

**Status:** Proposed (default chosen without review; confirm or override)
**Date:** 2026-11-15
**Feature:** 029-storefront-monorepo

## Context

The requested template, `vercel/shop` (MIT, Next.js 16, Tailwind 4, shadcn-style `components/ui`), is a Shopify storefront: catalog, cart, checkout and customer accounts all come from Shopify via Hydrogen, plus an Eve agent. We instead want Supabase for speed and need free books, one-off purchases and a membership. Supabase has auth, Postgres with RLS and private Storage, but no payments.

## Options Considered

### Option A: Vercel Shop UI shell + Supabase (auth, catalog, entitlements, files) + Stripe (checkout, subscriptions)

**Pros:** Keeps the template's design system, layout, SEO and answer-engine surface; Supabase covers auth, data and signed file URLs; Stripe Checkout and Billing handle one-off and subscription payments with webhooks that write entitlements.
**Cons:** The Shopify data layer (`lib/shopify`, `lib/cart`, `lib/auth`, `agent/`) is removed, not reused; we own the catalog and cart (cart is trivial for digital goods: single-item Buy).

### Option B: Keep Shopify as commerce, Supabase only for the reader

**Pros:** Template works as is; Shopify handles tax and checkout.
**Cons:** Digital delivery and membership need Shopify apps; two user systems; monthly Shopify cost; conflicts with "use Supabase".

### Option C: Build the store from scratch on Next.js + Supabase + Stripe

**Pros:** No dead template code.
**Cons:** Slower; loses the template's polish.

## Decision

**We chose Option A.** `apps/web` starts from `vercel/shop`, with Shopify and Eve code stripped. Data model (Supabase, RLS on): `books` (access: `free | members | paid`, price, Stripe ids, storage paths, published), `entitlements` (user, book, source: purchase|membership|gift), `memberships` (user, Stripe customer, status, period end), `profiles`. Files live in a private Storage bucket; the server issues short-lived signed URLs only after an entitlement check. Stripe webhooks (service role) are the only writers of `entitlements` and `memberships`. Access rule: free books readable by signed-in users; `members` books need an active membership; `paid` books need a purchase entitlement or an active membership if the book is also flagged member-included.

## Consequences

Stripe is a new vendor and key set (test mode first). Tax, receipts and refunds go through Stripe. If Shopify is wanted later, Option B can replace only the checkout module. Template code still tracks upstream loosely: we copy, not fork, so upstream updates are manual.
