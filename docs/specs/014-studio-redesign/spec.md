# Spec 014: Studio redesign (dark editing suite)

**Status:** Approved
**Version:** 1.0.0
**Created:** 2026-11-12
**Last Updated:** 2026-11-12
**Depends on:** spec 004 (studio), 006 (add a book), 009/010 (animation view), 011 (blueprint panel)

---

## Problem Statement

The studio works but looks like a settings page: small grey text in identical grey cards, a wall of
forms, and none of the books' own pictures. It is hard to see at a glance which books need what,
and the screens give the illustrations — the thing being made — no room.

## Goals

- A dark "editing suite" look where the covers and page art are the brightest thing on screen and
  one warm amber accent marks the next thing to do.
- Every book shows its cover: on the shelf (home), and in the book's own sidebar.
- Inside a book, a left sidebar holds the cover, the title and the five places (Overview, Script,
  Voices, Animation, Video) with a status dot each; the next step is highlighted.
- Each screen is laid out for its job: script lines read like a conversation beside their picture,
  audio tracks fold away, the video sits in a cinema frame, animation pages are a picture grid.
- Same routes, forms, field names, actions and wording the tests and CLI rely on. No behaviour change.

## Non-Goals

- New pipeline features, new routes that change data, or client-side frameworks.
- A light theme toggle (the studio is dark only; `color-scheme: dark`).
- External fonts or CDNs (the studio runs offline on 127.0.0.1).

## Functional Requirements

- **FR-1** Media gains a `thumb` kind: `/media/{slug}/thumb/{name}` serves a JPEG no wider than
  640 px of any file the `illustration` kind allows (same whitelist), cached in memory by path and
  modification time. Unknown names return 404.
- **FR-2** Home lists every book as a card with its front-cover thumbnail (a titled placeholder when
  the cover is missing), title, a four-step progress row (Script, Voices, Animation, Video) and the
  next action as the card's one button.
- **FR-3** Every book screen (overview, script, audio, animation, animation page, video) shares a
  sidebar layout with the cover, title and stage nav; the current screen is marked with
  `aria-current="page"`.
- **FR-4** The running-job notice stays (`id="running"`, Stop button, elapsed time) and is shown in
  the top bar region on every page.
- **FR-5** All existing text the tests assert, form actions, field names and media URLs stay.
- **FR-6** Layout works at 375 px wide (sidebar stacks above content, no horizontal page scroll).

## Acceptance Criteria

- **AC-1** `GET /media/<slug>/thumb/cover_front.png` returns `image/jpeg`, width ≤ 640.
- **AC-2** `GET /media/<slug>/thumb/not_listed.png` returns 404.
- **AC-3** Home HTML contains each book's thumb cover URL when the cover file exists.
- **AC-4** Book overview HTML contains the sidebar nav with `aria-current="page"` on Overview.
- **AC-5** All existing studio tests pass.
- **AC-6** Screens checked in the browser at 1440 px and 375 px without console errors.
