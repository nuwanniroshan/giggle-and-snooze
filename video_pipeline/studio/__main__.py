"""`uv run python -m video_pipeline.studio [--port N]` starts the studio on 127.0.0.1."""

import sys

from video_pipeline.studio.app import main

if __name__ == "__main__":
    sys.exit(main())
