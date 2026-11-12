# Plan 014: Studio redesign

1. **Thumbnails (FR-1, AC-1/2)** — failing tests in `tests/test_studio_app.py`, then a `thumb` kind
   in `Studio.media` that reuses `media_path(book, "illustration", name)`, resizes with Pillow
   (LANCZOS, RGB, JPEG q=82) and caches bytes keyed by `(path, mtime_ns)`.
2. **Cover helper (FR-2/3, AC-3/4)** — failing tests, then a `cover_url(book)` template global
   (thumb URL or None) and a `book_shell.html` layout with the sidebar (`book_links` macro becomes
   the vertical nav).
3. **Design system** — rewrite `tokens.css` (dark ground, surfaces, amber accent, status colours,
   rounded display face from the system), `base.css`, `studio.css`; fold `add_book.css` and
   `animation.css` into the new tokens.
4. **Screens** — rewrite home, book, script, audio, video, animation, animation page, add book,
   draft and message templates on the new layout, keeping every asserted string, form and URL.
5. **Verify** — full studio test run; browser pass at 1440 px and 375 px on every screen.
