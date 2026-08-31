"""Text-to-speech layer.

Swap the implementation with PA_TTS_ENGINE. Nothing above this package knows
which engine is in use.
"""

from __future__ import annotations

from ..config import Config
from .base import TTSEngine, TTSError
from .mock import MockTTSEngine
from .piper import PiperEngine

__all__ = ["TTSEngine", "TTSError", "MockTTSEngine", "PiperEngine", "build_tts_engine"]


def build_tts_engine(config: Config) -> TTSEngine:
    if config.tts_engine == "mock":
        return MockTTSEngine(chars_per_second=config.chars_per_second)
    if config.tts_engine == "piper":
        return PiperEngine(
            binary=config.piper_binary,
            model=config.piper_model,
            config_path=config.piper_config or "",
            length_scale=config.piper_length_scale,
            timeout_seconds=config.piper_timeout_seconds,
        )
    raise ValueError(f"Unknown PA_TTS_ENGINE {config.tts_engine!r}. Use 'piper' or 'mock'.")
