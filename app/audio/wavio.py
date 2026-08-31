"""Minimal WAV reading/writing on top of the standard library plus numpy.

Deliberately not using soundfile/librosa: those pull in libsndfile and a large
dependency tree, and everything this system plays is plain PCM WAV -- Piper
writes 16-bit PCM, and the built-in chimes are generated as 16-bit PCM.

If Phase 3 adds MP3 chime uploads, convert them to WAV at upload time rather
than teaching the playback path a second format. The playback path must stay as
simple as possible.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Tuple

import numpy as np


class UnsupportedAudioFile(Exception):
    pass


def read_wav(path: Path) -> Tuple[np.ndarray, int]:
    """Read a PCM WAV file into float32 samples in [-1, 1], shape (frames, channels)."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            samplerate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise UnsupportedAudioFile(f"{path.name} is not a readable WAV file ({exc}).") from exc

    if not frames:
        raise UnsupportedAudioFile(f"{path.name} contains no audio.")

    if width == 1:
        # 8-bit WAV is unsigned, centred on 128.
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        # 24-bit: sign-extend three little-endian bytes into int32.
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        packed = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        packed = np.where(packed & 0x800000, packed - 0x1000000, packed)
        data = packed.astype(np.float32) / 8388608.0
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise UnsupportedAudioFile(f"{path.name} uses an unsupported sample width ({width} bytes).")

    return data.reshape(-1, channels), samplerate


def write_wav(path: Path, data: np.ndarray, samplerate: int) -> None:
    """Write float32 samples in [-1, 1] as 16-bit PCM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    clipped = np.clip(data, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(data.shape[1])
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(pcm.tobytes())


def resample(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear resampling.

    Good enough for speech and chimes over a PA horn, and it avoids a scipy
    dependency. If someone later needs studio quality here, that is the moment
    to add a real resampler -- not before.
    """
    if source_rate == target_rate:
        return data
    frames, channels = data.shape
    target_frames = max(1, int(round(frames * target_rate / float(source_rate))))
    source_index = np.linspace(0.0, frames - 1.0, target_frames)
    out = np.empty((target_frames, channels), dtype=np.float32)
    grid = np.arange(frames, dtype=np.float32)
    for channel in range(channels):
        out[:, channel] = np.interp(source_index, grid, data[:, channel])
    return out


def match_channels(data: np.ndarray, channels: int) -> np.ndarray:
    """Fan mono out to N channels, or fold multichannel down to the target count."""
    current = data.shape[1]
    if current == channels:
        return data
    if current == 1:
        return np.repeat(data, channels, axis=1)
    if channels == 1:
        return data.mean(axis=1, keepdims=True)
    if current > channels:
        return data[:, :channels]
    padding = np.repeat(data[:, -1:], channels - current, axis=1)
    return np.concatenate([data, padding], axis=1)


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        return handle.getnframes() / float(rate) if rate else 0.0
