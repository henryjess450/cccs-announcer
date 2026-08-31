"""The audio output interface.

There is exactly ONE rule that matters in this package: while a PlaybackSession
is open, it owns the audio device, and only the player thread may open one. Two
announcements overlapping on the PA is the worst failure this system can have,
so the design makes it structurally impossible rather than relying on a lock
somewhere being held correctly.
"""

from __future__ import annotations

import abc
import threading
from pathlib import Path
from typing import Optional


class AudioUnavailable(Exception):
    """The speaker system could not be reached.

    Raised when the device is missing, in use by something else, or fails
    mid-stream. Callers must keep the announcement queued and surface a loud
    error -- never swallow this.

    `message` is what a front-office staff member reads on screen and must
    contain no jargon. `detail` is the technical cause and goes to the log only.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class PlaybackSession(abc.ABC):
    """An open, exclusive handle on the audio device.

    One session covers a whole announcement (chime -> gap -> speech -> end tone)
    so there is no device close/reopen click between the parts.
    """

    samplerate: int
    channels: int

    @abc.abstractmethod
    def play_wav(self, path: Path, gain: float, stop_event: Optional[threading.Event] = None) -> bool:
        """Play a WAV file. Returns True if it finished, False if stopped early."""

    @abc.abstractmethod
    def play_silence(self, seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
        """Emit silence. Returns True if it finished, False if stopped early."""


class AudioBackend(abc.ABC):
    name: str

    @abc.abstractmethod
    def describe(self) -> str:
        """Human-readable device description, shown in /health and the admin UI."""

    @abc.abstractmethod
    def check_available(self) -> None:
        """Raise AudioUnavailable if the device cannot be used right now."""

    @abc.abstractmethod
    def open_session(self) -> "PlaybackSessionContext":
        """Context manager yielding a PlaybackSession. Raises AudioUnavailable."""


class PlaybackSessionContext:
    """Typing shim: what open_session() returns is a context manager."""

    def __enter__(self) -> PlaybackSession:  # pragma: no cover - interface only
        raise NotImplementedError

    def __exit__(self, *exc_info) -> bool:  # pragma: no cover - interface only
        raise NotImplementedError
