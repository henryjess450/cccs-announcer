"""Start the announcer.

    python run.py

Reads host/port from .env. This is the entry point the Windows Scheduled Task
runs; keep it simple and keep it working with a bare `python run.py`.
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import load_config
from app.main import create_app
from app.singleton import AlreadyRunning


def main() -> int:
    config = load_config()
    try:
        application = create_app(config)
    except AlreadyRunning as exc:
        # Printed rather than logged: this happens before logging is useful,
        # and the person who just double-clicked the script needs to see it.
        print(exc, file=sys.stderr)
        return 1
    uvicorn.run(
        application,
        host=config.host,
        port=config.port,
        # Our own logging config is installed inside create_app; do not let
        # uvicorn replace it or the JSON log on disk goes quiet.
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
