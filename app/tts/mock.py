"""A stand-in speech engine for CI and for developing on a laptop.

It produces a real WAV of a realistic length so queue timing, duration
measurement and stop behaviour all behave like production. The audio is a quiet
low tone rather than silence, so a developer can actually hear that the
pipeline reached the speakers.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..audio.wavio import write_wav
from .base import TTSEngine, TTSError

SAMPLE_RATE = 22050


class MockTTSEngine(TTSEngine):
    name = "mock"

    def __init__(self, chars_per_second: float = 13.5, fail: bool = False):
        self.chars_per_second = chars_per_second
        #: Flipped by tests that need to prove a TTS failure is surfaced, not swallowed.
        self.fail = fail

    def describe(self) -> str:
        return "mock voice (a test tone, not real speech)"

    def check_ready(self) -> None:
        if self.fail:
            raise TTSError("The announcement voice isn't working. Tell IT.", "mock failure")

    def synthesize(self, text: str, out_path: Path) -> Path:
        self.check_ready()
        seconds = max(0.4, len(text) / self.chars_per_second)
        t = np.linspace(0.0, seconds, int(SAMPLE_RATE * seconds), endpoint=False, dtype=np.float32)
        # Two quiet tones, amplitude-wobbled, so it is obviously not real speech.
        tone = 0.06 * np.sin(2 * np.pi * 210.0 * t) + 0.03 * np.sin(2 * np.pi * 320.0 * t)
        tone *= 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t)
        write_wav(Path(out_path), tone.astype(np.float32), SAMPLE_RATE)
        return Path(out_path)
