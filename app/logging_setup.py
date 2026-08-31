"""Logging: human-readable on the console, JSON lines on disk, rotated.

JSON on disk because the audit trail and the failure history need to be
greppable and machine-readable three years from now. Human-readable on the
console because that is what someone stares at while wiring up the amplifier.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 10

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Anything passed via logger.info(..., extra={...}) rides along.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
                                           datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "announcer.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(JsonLineFormatter())
    root.addHandler(file_handler)

    # uvicorn installs its own handlers; make it use ours instead so everything
    # lands in one rotated file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
