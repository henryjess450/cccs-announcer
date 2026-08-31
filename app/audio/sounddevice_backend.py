"""Real audio output through PortAudio (the `sounddevice` package).

Why one persistent stream per announcement, opened and closed around the whole
chime->gap->speech sequence:

* Opening the device per announcement gives a natural liveness check. If the
  USB interface was unplugged or another app grabbed exclusive access, we find
  out before we mark the item as playing, and the item stays in the queue.
* Keeping ONE stream open across the whole sequence avoids an audible click
  between the chime and the speech, which you get if you close and reopen.
* Everything is resampled in Python to the stream's rate. WASAPI shared mode on
  Windows will not reliably convert rates for us, and a silent rate mismatch
  sounds like a chipmunk over a PA at full volume.

Blocking writes are used rather than a callback. The player thread is dedicated
to this, blocking is far easier to reason about, and stop latency is bounded by
one block (about 46 ms at 2048 frames / 44.1 kHz).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .base import AudioBackend, AudioUnavailable, PlaybackSession
from .wavio import UnsupportedAudioFile, match_channels, read_wav, resample

log = logging.getLogger(__name__)

# What staff see when the PA cannot be reached. No jargon, and it says who to tell.
FRIENDLY_UNAVAILABLE = "The speaker system isn't responding. Tell IT."


def _import_sounddevice() -> Any:
    """Imported lazily so the mock backend works on machines with no PortAudio."""
    try:
        import sounddevice  # type: ignore
    except Exception as exc:  # ImportError, OSError when the C library is missing
        raise AudioUnavailable(
            "The speaker system isn't set up on this computer. Tell IT.",
            f"sounddevice/PortAudio import failed: {exc!r}",
        ) from exc
    return sounddevice


class _SoundDeviceSession(PlaybackSession):
    def __init__(self, stream: Any, samplerate: int, channels: int, blocksize: int):
        self._stream = stream
        self._blocksize = blocksize
        self._aborted = False
        self.samplerate = samplerate
        self.channels = channels

    def _write_array(self, data: np.ndarray, stop_event: Optional[threading.Event]) -> bool:
        """Write in blocks, checking the stop flag between each one."""
        sd = _import_sounddevice()
        total = data.shape[0]
        position = 0
        while position < total:
            if stop_event is not None and stop_event.is_set():
                # abort() discards whatever PortAudio has already buffered, so
                # Stop actually stops instead of trailing off a second later.
                with contextlib.suppress(Exception):
                    self._stream.abort()
                self._aborted = True
                return False
            block = data[position:position + self._blocksize]
            try:
                self._stream.write(np.ascontiguousarray(block, dtype=np.float32))
            except sd.PortAudioError as exc:
                raise AudioUnavailable(
                    FRIENDLY_UNAVAILABLE,
                    f"PortAudio write failed mid-playback: {exc!r}",
                ) from exc
            position += block.shape[0]
        return True

    def play_wav(self, path: Path, gain: float, stop_event: Optional[threading.Event] = None) -> bool:
        if self._aborted:
            return False
        try:
            data, source_rate = read_wav(Path(path))
        except UnsupportedAudioFile as exc:
            raise AudioUnavailable(
                "One of the sound files is damaged. Tell IT.", str(exc)
            ) from exc
        data = resample(data, source_rate, self.samplerate)
        data = match_channels(data, self.channels)
        if gain != 1.0:
            data = data * float(gain)
        np.clip(data, -1.0, 1.0, out=data)
        return self._write_array(data, stop_event)

    def play_silence(self, seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
        if self._aborted or seconds <= 0:
            return not self._aborted
        frames = int(self.samplerate * seconds)
        silence = np.zeros((frames, self.channels), dtype=np.float32)
        return self._write_array(silence, stop_event)


class SoundDeviceBackend(AudioBackend):
    name = "sounddevice"

    def __init__(
        self,
        device_name: Optional[str] = None,
        samplerate: Optional[int] = None,
        channels: int = 2,
        blocksize: int = 2048,
    ):
        self.device_name = device_name
        self.requested_samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize

    # -- device resolution ---------------------------------------------------

    def _resolve_device(self) -> int:
        """Find the output device index.

        Matched by NAME substring, never by index. Device indexes are reordered
        by Windows across reboots and after USB re-enumeration; hard-coding one
        is the classic way this stops working at 3 AM with nobody watching.
        """
        sd = _import_sounddevice()
        try:
            devices = sd.query_devices()
        except Exception as exc:
            raise AudioUnavailable(FRIENDLY_UNAVAILABLE, f"query_devices failed: {exc!r}") from exc

        if not self.device_name:
            try:
                default_index = sd.default.device[1]
            except Exception:
                default_index = None
            if default_index is None or default_index < 0:
                raise AudioUnavailable(
                    "No speaker output is set up on this computer. Tell IT.",
                    "No default output device reported by PortAudio.",
                )
            return int(default_index)

        needle = self.device_name.strip().lower()
        for index, device in enumerate(devices):
            if device.get("max_output_channels", 0) <= 0:
                continue
            if needle in str(device.get("name", "")).lower():
                return index

        available = ", ".join(
            str(d.get("name")) for d in devices if d.get("max_output_channels", 0) > 0
        )
        raise AudioUnavailable(
            "The speaker system isn't connected. Tell IT.",
            f"No output device matching {self.device_name!r}. Available outputs: {available}",
        )

    def _device_samplerate(self, index: int) -> int:
        if self.requested_samplerate:
            return int(self.requested_samplerate)
        sd = _import_sounddevice()
        info = sd.query_devices(index)
        return int(info.get("default_samplerate") or 44100)

    def describe(self) -> str:
        try:
            index = self._resolve_device()
            sd = _import_sounddevice()
            info = sd.query_devices(index)
            return f"{info.get('name')} ({self._device_samplerate(index)} Hz, {self.channels} ch)"
        except AudioUnavailable as exc:
            return f"unavailable: {exc.detail or exc.message}"

    def check_available(self) -> None:
        self._resolve_device()

    # -- playback ------------------------------------------------------------

    @contextlib.contextmanager
    def open_session(self):
        sd = _import_sounddevice()
        index = self._resolve_device()
        samplerate = self._device_samplerate(index)
        channels = self.channels

        info = sd.query_devices(index)
        max_channels = int(info.get("max_output_channels", channels) or channels)
        if channels > max_channels:
            log.warning("Device supports %s channels, falling back from %s", max_channels, channels)
            channels = max_channels

        try:
            stream = sd.OutputStream(
                device=index,
                samplerate=samplerate,
                channels=channels,
                dtype="float32",
                blocksize=self.blocksize,
            )
            stream.start()
        except Exception as exc:
            raise AudioUnavailable(
                FRIENDLY_UNAVAILABLE,
                f"Could not open output device {index} at {samplerate} Hz: {exc!r}",
            ) from exc

        session = _SoundDeviceSession(stream, samplerate, channels, self.blocksize)
        try:
            yield session
        finally:
            with contextlib.suppress(Exception):
                if not session._aborted:
                    stream.stop()
            with contextlib.suppress(Exception):
                stream.close()
