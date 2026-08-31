"""Two copies of the announcer must never share one data folder.

Two processes on one database means two player threads, which means two
announcements talking over each other on the PA.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from app.main import Services
from app.singleton import AlreadyRunning, InstanceLock

ROOT = Path(__file__).resolve().parent.parent


def test_a_second_lock_on_the_same_folder_is_refused(tmp_path):
    first = InstanceLock(tmp_path / "announcer.lock")
    first.acquire()
    try:
        with pytest.raises(AlreadyRunning) as caught:
            InstanceLock(tmp_path / "announcer.lock").acquire()
        # The message has to make sense to whoever double-clicked the icon.
        assert "already running" in str(caught.value)
        assert "talk over each other" in str(caught.value)
    finally:
        first.release()


def test_the_lock_is_reusable_once_released(tmp_path):
    lock = InstanceLock(tmp_path / "announcer.lock")
    lock.acquire()
    lock.release()
    second = InstanceLock(tmp_path / "announcer.lock")
    second.acquire()
    second.release()


def test_the_lock_is_released_when_the_process_dies(tmp_path):
    """No stale lock file to clean up after a power cut."""
    script = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r});"
        "from app.singleton import InstanceLock;"
        f"InstanceLock({str(tmp_path / 'announcer.lock')!r}).acquire();"
        "print('locked', flush=True)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
    assert result.returncode == 0

    # The child exited without releasing explicitly; the OS must have freed it.
    lock = InstanceLock(tmp_path / "announcer.lock")
    lock.acquire()
    lock.release()


def test_a_second_services_instance_is_refused(config):
    guarded = dataclasses.replace(config, single_instance=True)
    first = Services(guarded)
    try:
        with pytest.raises(AlreadyRunning):
            Services(guarded)
    finally:
        first.instance_lock.release()
