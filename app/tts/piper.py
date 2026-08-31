"""Offline speech via Piper, invoked as a subprocess.

Why a subprocess rather than the Python bindings: the Piper release for Windows
is a self-contained folder (piper.exe plus its espeak-ng data) that IT can drop
on the machine and that does not care which Python is installed. That is one
less thing to break in three years.

Contract with the CLI:
    piper -m VOICE.onnx -c VOICE.onnx.json -f OUT.wav   [text on stdin]
Piper synthesizes one line of stdin at a time, so the text handed in here must
already be a single line -- normalize.py guarantees that.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .base import TTSEngine, TTSError

log = logging.getLogger(__name__)

FRIENDLY_FAILURE = "The announcement voice isn't working. Tell IT."


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(
        self,
        binary: str,
        model: str,
        config_path: str = "",
        length_scale: float = 1.0,
        timeout_seconds: float = 30.0,
        sentence_silence: float = 0.3,
    ):
        self.binary = binary
        self.model = model
        self.config_path = config_path
        self.length_scale = length_scale
        self.timeout_seconds = timeout_seconds
        self.sentence_silence = sentence_silence

    # -- readiness -----------------------------------------------------------

    def _resolve_binary(self) -> str:
        resolved = shutil.which(self.binary) or (self.binary if Path(self.binary).exists() else None)
        if not resolved:
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper executable not found at {self.binary!r} and not on PATH. "
                "Set PIPER_BINARY in .env to the full path of piper.exe.",
            )
        return resolved

    def describe(self) -> str:
        voice = Path(self.model).name if self.model else "(no voice configured)"
        return f"Piper, voice {voice}"

    def check_ready(self) -> None:
        self._resolve_binary()
        if not self.model:
            raise TTSError(
                FRIENDLY_FAILURE,
                "PIPER_MODEL is not set in .env. It must point to a .onnx voice file.",
            )
        model_path = Path(self.model)
        if not model_path.exists():
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper voice file not found: {model_path}",
            )
        # Piper needs the matching .onnx.json next to the model unless -c is given.
        if not self.config_path and not Path(str(model_path) + ".json").exists():
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper voice config not found: {model_path}.json "
                "(copy it alongside the .onnx file, or set PIPER_CONFIG).",
            )

    # -- synthesis -----------------------------------------------------------

    def synthesize(self, text: str, out_path: Path) -> Path:
        self.check_ready()
        binary = self._resolve_binary()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        command = [
            binary,
            "--model", self.model,
            "--output_file", str(out_path),
            "--length_scale", str(self.length_scale),
            "--sentence_silence", str(self.sentence_silence),
        ]
        if self.config_path:
            command[3:3] = ["--config", self.config_path]

        # Single line: Piper treats each stdin line as a separate utterance and
        # would overwrite the output file for every line but the last.
        payload = " ".join(text.split()) + "\n"

        try:
            completed = subprocess.run(
                command,
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                # Keep a console window from flashing up on the PA machine every
                # time an announcement is made.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper did not finish within {self.timeout_seconds}s: {exc!r}",
            ) from exc
        except OSError as exc:
            raise TTSError(FRIENDLY_FAILURE, f"Could not run Piper: {exc!r}") from exc

        if completed.returncode != 0:
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper exited {completed.returncode}: "
                f"{completed.stderr.decode('utf-8', 'replace').strip()[:2000]}",
            )
        if not out_path.exists() or out_path.stat().st_size < 128:
            raise TTSError(
                FRIENDLY_FAILURE,
                f"Piper produced no audio for {len(text)} characters of text. "
                f"stderr: {completed.stderr.decode('utf-8', 'replace').strip()[:2000]}",
            )
        return out_path
