"""A fake audio device for CI, laptops with no PA, and the concurrency tests.

Two jobs:

1. Let the whole pipeline run end to end on a machine with no sound card.
2. Actively police the single-playback rule. Opening a second session while one
   is open raises immediately, so the concurrency test fails loudly rather than
   quietly recording a race that a human has to notice in a log.

Playback consumes real wall-clock time by default (scaled by `speed`) so queue
ordering and stop behaviour are exercised the way they will really behave.
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .base import AudioBackend, AudioUnavailable, PlaybackSession
from .wavio import duration_seconds


@dataclass
class PlaybackRecord:
    """One session's occupancy of the device, in monotonic seconds."""
    started: float
    ended: float = 0.0
    files: List[str] = field(default_factory=list)
    completed: bool = True


class _MockSession(PlaybackSession):
    def __init__(self, backend: "MockAudioBackend", record: PlaybackRecord):
        self._backend = backend
        self._record = record
        self.samplerate = 44100
        self.channels = 2

    def _sleep(self, seconds: float, stop_event: Optional[threading.Event]) -> bool:
        """Sleep in slices so a stop is honoured with realistic latency."""
        remaining = seconds / self._backend.speed
        slice_seconds = 0.02
        while remaining > 0:
            if stop_event is not None and stop_event.is_set():
                self._record.completed = False
                return False
            time.sleep(min(slice_seconds, remaining))
            remaining -= slice_seconds
        return True

    def play_wav(self, path: Path, gain: float, stop_event: Optional[threading.Event] = None) -> bool:
        path = Path(path)
        self._record.files.append(path.name)
        if self._backend.fail_on_play:
            raise AudioUnavailable("The speaker system isn't responding. Tell IT.", "mock failure")
        try:
            seconds = duration_seconds(path)
        except Exception:
            seconds = 0.2
        return self._sleep(seconds, stop_event)

    def play_silence(self, seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
        return self._sleep(seconds, stop_event)


class MockAudioBackend(AudioBackend):
    name = "mock"

    def __init__(self, speed: float = 1.0, available: bool = True):
        #: Multiplier on playback speed. 20.0 makes a 4-second announcement take
        #: 0.2s, which keeps the test suite fast without removing real timing.
        self.speed = speed
        self.available = available
        self.fail_on_play = False
        self.records: List[PlaybackRecord] = []
        self._open_sessions = 0
        self._lock = threading.Lock()
        #: Set if two sessions were ever open at once. The tests assert on this.
        self.overlap_detected = False

    def describe(self) -> str:
        return "mock audio device (no sound is produced)"

    def check_available(self) -> None:
        if not self.available:
            raise AudioUnavailable("The speaker system isn't responding. Tell IT.", "mock unavailable")

    @contextlib.contextmanager
    def open_session(self):
        self.check_available()
        with self._lock:
            if self._open_sessions != 0:
                self.overlap_detected = True
                raise AssertionError(
                    "Two playback sessions were open at once. "
                    "The single-playback lock is broken."
                )
            self._open_sessions += 1
            record = PlaybackRecord(started=time.monotonic())
            self.records.append(record)
        try:
            yield _MockSession(self, record)
        finally:
            with self._lock:
                record.ended = time.monotonic()
                self._open_sessions -= 1

    # -- assertions used by the tests ---------------------------------------

    def overlapping_pairs(self):
        """Return any pair of playback windows that overlap in time."""
        ordered = sorted(self.records, key=lambda r: r.started)
        clashes = []
        for earlier, later in zip(ordered, ordered[1:]):
            if later.started < earlier.ended:
                clashes.append((earlier, later))
        return clashes
