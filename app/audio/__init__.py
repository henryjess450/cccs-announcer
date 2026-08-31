"""Audio output layer.

Swap the implementation with PA_AUDIO_BACKEND. Nothing above this package knows
whether sound is really coming out.
"""

from __future__ import annotations

from ..config import Config
from .base import AudioBackend, AudioUnavailable, PlaybackSession
from .mock import MockAudioBackend
from .sounddevice_backend import SoundDeviceBackend

__all__ = [
    "AudioBackend",
    "AudioUnavailable",
    "PlaybackSession",
    "MockAudioBackend",
    "SoundDeviceBackend",
    "build_audio_backend",
]


def build_audio_backend(config: Config) -> AudioBackend:
    if config.audio_backend == "mock":
        return MockAudioBackend(speed=config.mock_speed)
    if config.audio_backend == "sounddevice":
        return SoundDeviceBackend(
            device_name=config.audio_device,
            samplerate=config.audio_samplerate,
            channels=config.audio_channels,
            blocksize=config.audio_blocksize,
        )
    raise ValueError(
        f"Unknown PA_AUDIO_BACKEND {config.audio_backend!r}. Use 'sounddevice' or 'mock'."
    )
