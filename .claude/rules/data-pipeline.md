# Book data and pipeline design

- ✓ `book_data.json` is canonical. Every PDF, EPUB, script, audio and video file is a regenerable artifact in `build/`.
- ✓ Validate book JSON and `episode_script.json` on load (pydantic or jsonschema) and fail fast with the file and field name.
- ✓ Use `snake_case` keys. Identify pages by `page_number`, not list position.
- ✓ Write JSON atomically (`*.tmp`, then `os.replace`) with `indent=2, ensure_ascii=False`.
- ✓ Make each stage idempotent behind one CLI entry, and skip work whose inputs haven't changed.
- ✓ Name outputs from book metadata (`build/<book_slug>/<stage>/...`), never a hardcoded title.
- ✓ `episodes/<slug>/episode_script.json` is generated but hand-editable; regenerating requires `--force`.
- ✗ Never hand-edit generated files in `build/`.
- ✗ Don't hardcode durations, titles or text in code. Read them from the JSON.
