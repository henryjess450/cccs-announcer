"""Refuse to run two copies of the announcer against the same data folder.

Why this exists: the queue lives in SQLite, and the single-playback guarantee
relies on there being exactly one player thread. Two PROCESSES pointed at the
same database means two player threads, and they will talk over each other on
the PA -- the one failure this system must never have.

This is not hypothetical. On Windows, someone double-clicking the start script
a second time, or IT starting it by hand while the Scheduled Task copy is
already running, produces exactly that. The lock turns a silent, intermittent,
almost-undiagnosable audio overlap into an immediate, obvious refusal to start.

The lock is an exclusive lock on a file in the data folder, held for the life
of the process. The operating system releases it if the process dies for any
reason, including a power cut, so there is no stale lock file to clean up.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


log = logging.getLogger(__name__)


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AlreadyRunning(
                "The announcer is already running on this computer.\n"
                "Only one copy may run at a time -- two copies would talk over each "
                "other on the speakers.\n"
                f"Lock file: {self.path}\n"
                "If you are sure nothing else is running, restart the computer."
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._handle = handle
        log.info("Holding the single-instance lock (pid %s)", os.getpid())

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()
            self._handle = None
