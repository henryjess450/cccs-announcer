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

# Windows returns this when a program cannot start because a DLL it needs is
# missing (STATUS_DLL_NOT_FOUND). For Piper that is almost always the Microsoft
# Visual C++ runtime, which Windows 10 does not always have.
_STATUS_DLL_NOT_FOUND = 0xC0000135
_DLL_EXIT_CODES = {_STATUS_DLL_NOT_FOUND, _STATUS_DLL_NOT_FOUND - (1 << 32)}

VC_RUNTIME_MESSAGE = (
    "The announcement voice is missing a Windows component. Tell IT: "
    "install the Microsoft Visual C++ Runtime."
)
VC_RUNTIME_DETAIL = (
    "Piper could not start because the Microsoft Visual C++ runtime "
    "(MSVCP140.dll / VCRUNTIME140.dll) is not installed. Fix it by running, "
    "in PowerShell on the announcer machine:\n"
    "    iwr https://aka.ms/vs/17/release/vc_redist.x64.exe -OutFile "
    "\"$env:TEMP\\vc.exe\"\n"
    "    Start-Process -Wait \"$env:TEMP\\vc.exe\" -ArgumentList "
    "'/install','/quiet','/norestart'\n"
    "Then restart the announcer. Re-running scripts\\setup.ps1 also installs it."
)

# The DLLs Piper needs from that runtime.
_VC_RUNTIME_DLLS = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")

_dialogs_silenced = False


def _silence_windows_error_dialogs() -> None:
    """Stop Windows popping a modal error box when a child process fails.

    A missing DLL normally raises a dialog that sits on the PA machine's
    desktop waiting for someone to click OK. On a machine that is locked in a
    cupboard, nobody ever does -- and every announcement quietly wedges behind
    it. Child processes inherit this setting, so setting it once here stops the
    dialog and lets us handle the failure properly instead.
    """
    global _dialogs_silenced
    if _dialogs_silenced or os.name != "nt":
        return
    try:
        import ctypes
        SEM_FAILCRITICALERRORS = 0x0001
        SEM_NOGPFAULTERRORBOX = 0x0002
        SEM_NOOPENFILEERRORBOX = 0x8000
        ctypes.windll.kernel32.SetErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        )
        _dialogs_silenced = True
    except Exception:  # pragma: no cover - not reachable off Windows
        log.debug("Could not set the Windows error mode", exc_info=True)


def missing_vc_runtime() -> bool:
    """True on Windows when the Visual C++ runtime Piper needs is absent."""
    if os.name != "nt":
        return False
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return not any((system32 / name).exists() for name in _VC_RUNTIME_DLLS)


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
        # Checked before anything is queued, so /health shows this rather than
        # every announcement failing one at a time.
        if missing_vc_runtime():
            raise TTSError(VC_RUNTIME_MESSAGE, VC_RUNTIME_DETAIL)
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
        _silence_windows_error_dialogs()
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
            if completed.returncode in _DLL_EXIT_CODES:
                raise TTSError(VC_RUNTIME_MESSAGE, VC_RUNTIME_DETAIL)
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
