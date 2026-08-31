"""The playback engine. This is the most important file in the system.

=============================================================================
THE SINGLE-PLAYBACK GUARANTEE
=============================================================================
Two announcements must never overlap on the PA. That is enforced structurally,
not with a lock that somebody has to remember to take:

    * Exactly ONE Player thread exists per process.
    * That thread is the ONLY code that ever calls AudioBackend.open_session().
    * Web requests cannot play audio. All they can do is INSERT a row.

So concurrency in the web layer can, at worst, produce extra queue rows. It
cannot produce two audio streams. The mock backend additionally raises if a
second session is ever opened while one is live, so the tests fail loudly if
someone later adds a second consumer.

=============================================================================
QUEUE SEMANTICS
=============================================================================
    * FIFO within a priority tier, priority tier first (see Database.claim_next).
    * Priority NEVER interrupts audio that is already playing. It plays next.
      A jarring cut-off mid-sentence is worse than waiting eight seconds.
    * Stop is the only thing that interrupts, and it is always attributed.

=============================================================================
FAILURE POLICY
=============================================================================
An announcement must never disappear quietly. Someone typed it and walked away
believing it would be heard.

    * Audio device unavailable -> the item goes BACK to 'queued', the player
      backs off and retries, and the UI shows a red banner. Nothing is lost.
    * TTS failure -> the item is marked 'failed' with a plain-language reason
      and shown in the UI. It is not retried forever, because a text the engine
      cannot speak will not become speakable on the third attempt.
    * Process death mid-announcement -> Database.recover_orphaned_items marks it
      'interrupted' at next startup so it appears in the audit log.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .audio.base import AudioBackend, AudioUnavailable
from .chimes import ChimeLibrary
from .config import Config
from .db import (
    STATE_DONE,
    STATE_FAILED,
    STATE_STOPPED,
    Database,
)
from .tts.base import TTSEngine, TTSError

log = logging.getLogger(__name__)

# Back-off between retries when the audio device is missing. Starts responsive
# (someone may be plugging the cable back in) and settles down so a genuinely
# disconnected PA does not spin the CPU or spam the log all night.
RETRY_DELAY_START = 2.0
RETRY_DELAY_MAX = 30.0


@dataclass
class PlayerHealth:
    audio_ok: bool = True
    tts_ok: bool = True
    audio_message: str = ""
    audio_detail: str = ""
    tts_message: str = ""
    tts_detail: str = ""


class Player:
    def __init__(
        self,
        config: Config,
        database: Database,
        tts: TTSEngine,
        audio: AudioBackend,
        chimes: ChimeLibrary,
        on_change: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.db = database
        self.tts = tts
        self.audio = audio
        self.chimes = chimes
        self._on_change = on_change or (lambda: None)

        self.health = PlayerHealth()

        self._thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._wake = threading.Event()

        # Guards _current_id / _stop_event / _stop_by. Held only for the few
        # microseconds needed to read or swap them -- never across playback.
        self._lock = threading.Lock()
        self._current_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._stop_by: Optional[str] = None
        self._retry_delay = RETRY_DELAY_START

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Player already started. There must be exactly one player thread.")
        interrupted = self.db.recover_orphaned_items()
        if interrupted:
            log.warning(
                "Recovered %s announcement(s) left playing by a previous run: %s",
                len(interrupted), interrupted,
            )
        self._thread = threading.Thread(target=self._run, name="pa-player", daemon=True)
        self._thread.start()
        log.info("Player thread started (audio=%s, tts=%s)", self.audio.name, self.tts.name)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._shutdown.set()
        self._stop_event.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def notify_new_item(self) -> None:
        """Called by the web layer after an INSERT so the player wakes at once."""
        self._wake.set()

    # -- stop ----------------------------------------------------------------

    def request_stop(self, by: str, item_id: Optional[int] = None) -> bool:
        """Cut off the announcement that is playing right now.

        `item_id` guards against the race where the user clicks Stop just as
        their announcement ends and the next one begins -- without it, they
        would silence somebody else's announcement.
        """
        with self._lock:
            current = self._current_id
            if current is None:
                return False
            if item_id is not None and item_id != current:
                return False
            self._stop_by = by
            self._stop_event.set()
        log.info("Stop requested for announcement %s by %s", current, by)
        return True

    @property
    def current_id(self) -> Optional[int]:
        with self._lock:
            return self._current_id

    # -- estimates -----------------------------------------------------------

    def estimate_seconds(self, normalized_text: str, chime_key: Optional[str]) -> float:
        """Rough duration used for the "2 ahead of you, about 40 seconds" hint."""
        rate = self.db.speech_rate(self.config.chars_per_second)
        speech = max(0.5, len(normalized_text) / rate)
        chime = self.chimes.seconds_for(chime_key)
        gap = self.config.chime_gap_ms / 1000.0 if chime else 0.0
        end = self.chimes.seconds_for(self.config.end_tone) if self.config.end_tone else 0.0
        # A little padding for device open and inter-item turnaround.
        return speech + chime + gap + end + 0.6

    # -- the loop ------------------------------------------------------------

    def _run(self) -> None:
        while not self._shutdown.is_set():
            try:
                item = self.db.claim_next()
            except Exception:
                # A database problem must not kill the player thread; if it does,
                # announcements stop forever with no obvious cause.
                log.exception("Could not read the queue; retrying shortly")
                self._shutdown.wait(1.0)
                continue

            if item is None:
                # Idle. Wake on a new submission, or poll once a second as a
                # backstop in case a notify was missed.
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue

            self._play_item(item)

    def _play_item(self, item: Dict[str, Any]) -> None:
        item_id = int(item["id"])
        text = item["normalized_text"]
        is_test = item.get("kind") == "test"

        with self._lock:
            self._current_id = item_id
            self._stop_event = threading.Event()
            self._stop_by = None
            stop_event = self._stop_event
        self._on_change()

        speech_path: Optional[Path] = None
        started = time.monotonic()
        try:
            # ---- 1. Synthesize first, before touching the device. ------------
            # Doing this up front means the device is held for the shortest
            # possible time, and a TTS failure never leaves a half-open stream.
            if text and not is_test:
                speech_path = self._synthesize(item_id, text)

            # ---- 2. Take the device for the whole sequence. ------------------
            with self.audio.open_session() as session:
                self._mark_audio_ok()
                completed = self._play_sequence(session, item, speech_path, stop_event)

            duration = time.monotonic() - started

            if not completed:
                with self._lock:
                    stopped_by = self._stop_by or "unknown"
                self.db.finish(item_id, STATE_STOPPED, duration_seconds=duration,
                               stopped_by=stopped_by)
                log.info("Announcement %s stopped by %s after %.1fs", item_id, stopped_by, duration)
            else:
                self.db.finish(item_id, STATE_DONE, duration_seconds=duration)
                if text and not is_test:
                    self.db.record_speech_rate(len(text), duration)
                log.info("Announcement %s played in %.1fs (%s)", item_id, duration, item["user_name"])

        except AudioUnavailable as exc:
            # The PA could not be reached. Keep the announcement -- somebody is
            # waiting to hear it -- and make the failure visible.
            self._mark_audio_failed(exc)
            self.db.release_to_queue(item_id, exc.message)
            log.error("Audio unavailable for announcement %s: %s | %s",
                      item_id, exc.message, exc.detail)
            self._backoff()

        except TTSError as exc:
            self.health.tts_ok = False
            self.health.tts_message = exc.message
            self.health.tts_detail = exc.detail
            self.db.finish(item_id, STATE_FAILED, error=exc.message)
            log.error("Speech failed for announcement %s: %s | %s",
                      item_id, exc.message, exc.detail)

        except Exception as exc:  # never let the player thread die
            self.db.finish(item_id, STATE_FAILED,
                           error="Something went wrong playing this announcement. Tell IT.")
            log.exception("Unexpected failure playing announcement %s: %r", item_id, exc)

        finally:
            if speech_path is not None:
                try:
                    speech_path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._current_id = None
                self._stop_by = None
            self._on_change()

    # -- the audible sequence -----------------------------------------------

    def _play_sequence(self, session, item, speech_path, stop_event) -> bool:
        """chime -> gap -> speech -> optional end tone.

        Returns False as soon as any part is cut short by Stop, so the remaining
        parts are skipped: pressing Stop during the chime must not still play
        the announcement.
        """
        chime_path = self.chimes.path_for(item.get("chime"))
        if chime_path is not None:
            if not session.play_wav(chime_path, self.config.chime_gain, stop_event):
                return False
            # The gap keeps the chime from running into the first word. Front
            # office staff report the speech as "cut off" without it.
            if not session.play_silence(self.config.chime_gap_ms / 1000.0, stop_event):
                return False

        if speech_path is not None:
            if not session.play_wav(speech_path, self.config.speech_gain, stop_event):
                return False

        if self.config.end_tone:
            end_path = self.chimes.path_for(self.config.end_tone)
            if end_path is not None and not session.play_wav(end_path, self.config.chime_gain, stop_event):
                return False
        return True

    # -- helpers -------------------------------------------------------------

    def _synthesize(self, item_id: int, text: str) -> Path:
        out_path = self.config.audio_cache_dir / f"speech-{item_id}.wav"
        path = self.tts.synthesize(text, out_path)
        self.health.tts_ok = True
        self.health.tts_message = ""
        self.health.tts_detail = ""
        return Path(path)

    def _mark_audio_ok(self) -> None:
        if not self.health.audio_ok:
            log.info("Audio device recovered")
            self._on_change()
        self.health.audio_ok = True
        self.health.audio_message = ""
        self.health.audio_detail = ""
        self._retry_delay = RETRY_DELAY_START

    def _mark_audio_failed(self, exc: AudioUnavailable) -> None:
        self.health.audio_ok = False
        self.health.audio_message = exc.message
        self.health.audio_detail = exc.detail

    def _backoff(self) -> None:
        delay = self._retry_delay
        self._retry_delay = min(RETRY_DELAY_MAX, self._retry_delay * 2)
        log.info("Retrying the speaker system in %.0fs", delay)
        self._shutdown.wait(delay)
