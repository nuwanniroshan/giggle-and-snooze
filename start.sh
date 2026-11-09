#!/usr/bin/env bash
# Start the episode studio at http://127.0.0.1:8765 (pass --port N to change it).
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python -m video_pipeline.studio "$@"
