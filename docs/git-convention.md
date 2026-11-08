---
branch_pattern: "^(feat|fix|docs|chore|refactor|test)/[0-9]+-[a-z0-9-]+$"
ticket_prefix: ""
commit_format: "<type>(<scope>): <message>"
allowed_types:
  - feat
  - fix
  - docs
  - chore
  - refactor
  - test
---

# Git Convention

This file is read by SDD skills to enforce branch naming and commit message standards.
To change these settings, edit this file directly.

## Examples

### Branch names
- `feat/002-episode-script-generator`
- `fix/003-xfade-offset-drift`

### Commit messages
- `feat(script): generate episode_script.json from book_data.json`
- `fix(render): normalise timebase before xfade`
