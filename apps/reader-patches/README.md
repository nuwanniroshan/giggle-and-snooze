# Reader patches (Readest fork)

`apps/reader` is a git submodule pinned to upstream Readest (`a60b7ee`). We have no fork yet, so our
changes live here as patches and are applied to the submodule working tree.

## Apply

```bash
git submodule update --init --recursive      # includes packages/foliate-js (needed to build)
git -C apps/reader apply ../reader-patches/*.patch
```

Check first with `git -C apps/reader apply --check ../reader-patches/*.patch`.
Undo with `git -C apps/reader checkout -- .`.

## Patches

- `0001-open-book-from-src.patch`: `/?src=<url-encoded https URL>` on the library page fetches the
  EPUB, imports it as if picked from disk, removes `src` from the URL and opens the reader on it.
  `importBooks` now also returns `importedHashes`. The URL host must allow CORS from the reader origin.

## Licence

Readest is AGPL-3.0. These patches and any fork built from them must be published under AGPL-3.0
(a public fork under our GitHub account, pinned by the submodule) before the reader is deployed.

## Follow-ups (not done)

- Create the public fork, commit these patches there, repoint `.gitmodules`.
- Branding: name, logo, title/meta ("Readest" strings, `public/` icons), theme colours.
- AGPL: a visible "Source code" link to the fork in the About dialog / footer.
- Hide what a children's reader does not need (OPDS, Send, translation, TTS upsell, sign-in nag, analytics: `NEXT_PUBLIC_POSTHOG_*` unset).
- Restrict `src` to an allowlist of our storage host.
- Upstream node: `pnpm setup-vendors` in `apps/readest-app` before the first build (output is git-ignored).
