"""The Piper engine's failure paths.

Everything here is about a machine where Piper cannot start. The success path
is covered end to end by the rest of the suite through the mock engine; what
matters here is that a broken install produces a clear message instead of a
mystery.
"""

from __future__ import annotations

import subprocess

import pytest

from app.tts.base import TTSError
from app.tts.piper import (
    VC_RUNTIME_MESSAGE,
    PiperEngine,
    _DLL_EXIT_CODES,
    _silence_windows_error_dialogs,
    missing_vc_runtime,
)


@pytest.fixture
def engine(tmp_path):
    binary = tmp_path / "piper.exe"
    binary.write_bytes(b"not really an executable")
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"model")
    (tmp_path / "voice.onnx.json").write_text("{}")
    return PiperEngine(binary=str(binary), model=str(model))


# -- the Visual C++ runtime ------------------------------------------------

def test_the_runtime_check_does_nothing_off_windows():
    """Only Windows has this problem; the check must not fire elsewhere."""
    assert missing_vc_runtime() is False


def test_a_missing_runtime_is_caught_before_anything_is_queued(engine, monkeypatch):
    """/health has to show this, rather than every announcement failing one at
    a time with the same message."""
    monkeypatch.setattr("app.tts.piper.missing_vc_runtime", lambda: True)
    with pytest.raises(TTSError) as caught:
        engine.check_ready()
    assert caught.value.message == VC_RUNTIME_MESSAGE
    assert "Visual C++" in caught.value.message


def test_the_message_is_plain_and_the_fix_is_in_the_detail(engine, monkeypatch):
    monkeypatch.setattr("app.tts.piper.missing_vc_runtime", lambda: True)
    with pytest.raises(TTSError) as caught:
        engine.check_ready()

    # What a staff member reads: no DLL names, no jargon.
    assert "MSVCP140" not in caught.value.message
    assert ".dll" not in caught.value.message.lower()
    assert "Tell IT" in caught.value.message

    # What IT reads in the log: the actual cause and the exact fix.
    assert "MSVCP140.dll" in caught.value.detail
    assert "vc_redist.x64.exe" in caught.value.detail
    assert "setup.ps1" in caught.value.detail


def test_the_windows_dll_exit_code_maps_to_the_runtime_message(engine, monkeypatch):
    """Windows returns STATUS_DLL_NOT_FOUND when a program cannot start for a
    missing DLL. Reported raw, that is an unreadable negative number."""
    monkeypatch.setattr("app.tts.piper.missing_vc_runtime", lambda: False)

    class Failed:
        returncode = -1073741515          # 0xC0000135 as a signed int
        stdout = b""
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(TTSError) as caught:
        engine.synthesize("hello", engine.model + ".out.wav")
    assert caught.value.message == VC_RUNTIME_MESSAGE
    assert "-1073741515" not in caught.value.message


def test_both_signed_and_unsigned_forms_of_the_code_are_recognised():
    assert 0xC0000135 in _DLL_EXIT_CODES
    assert -1073741515 in _DLL_EXIT_CODES


def test_other_failures_still_report_pipers_own_error(engine, monkeypatch):
    monkeypatch.setattr("app.tts.piper.missing_vc_runtime", lambda: False)

    class Failed:
        returncode = 1
        stdout = b""
        stderr = b"could not load the voice model"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(TTSError) as caught:
        engine.synthesize("hello", engine.model + ".out.wav")
    assert caught.value.message != VC_RUNTIME_MESSAGE
    assert "could not load the voice model" in caught.value.detail


# -- no modal dialogs on an unattended machine -----------------------------

def test_silencing_error_dialogs_is_safe_to_call_anywhere():
    """On the PA machine a Windows error box sits waiting for a click that
    never comes, and every announcement wedges behind it. Off Windows this is
    a no-op and must not raise."""
    _silence_windows_error_dialogs()
    _silence_windows_error_dialogs()


# -- the ordinary missing-pieces messages ----------------------------------

def test_a_missing_binary_says_what_to_set(tmp_path):
    engine = PiperEngine(binary=str(tmp_path / "nope.exe"), model=str(tmp_path / "v.onnx"))
    with pytest.raises(TTSError) as caught:
        engine.check_ready()
    assert "PIPER_BINARY" in caught.value.detail
    assert "Tell IT" in caught.value.message


def test_a_missing_voice_says_so(tmp_path):
    binary = tmp_path / "piper.exe"
    binary.write_bytes(b"x")
    engine = PiperEngine(binary=str(binary), model=str(tmp_path / "absent.onnx"))
    with pytest.raises(TTSError) as caught:
        engine.check_ready()
    assert "voice file not found" in caught.value.detail


def test_a_voice_without_its_config_says_so(tmp_path):
    binary = tmp_path / "piper.exe"
    binary.write_bytes(b"x")
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"m")
    engine = PiperEngine(binary=str(binary), model=str(model))
    with pytest.raises(TTSError) as caught:
        engine.check_ready()
    assert "config not found" in caught.value.detail
