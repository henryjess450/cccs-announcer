"""The text-to-speech interface.

Everything above this package deals in "give me a WAV file for this text".
Swapping Piper for a cloud engine means writing one class here and changing one
line in .env -- no changes to the queue, the player, or the web layer.
"""

from __future__ import annotations

import abc
from pathlib import Path


class TTSError(Exception):
    """Speech could not be produced.

    `message` is shown to staff and must be plain language. `detail` carries the
    engine's own error output and goes only to the log.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class TTSEngine(abc.ABC):
    name: str

    @abc.abstractmethod
    def describe(self) -> str:
        """Human-readable engine/voice description for /health and the admin UI."""

    @abc.abstractmethod
    def check_ready(self) -> None:
        """Raise TTSError if the engine cannot synthesize right now."""

    @abc.abstractmethod
    def synthesize(self, text: str, out_path: Path) -> Path:
        """Write a PCM WAV of `text` to `out_path` and return the path."""
