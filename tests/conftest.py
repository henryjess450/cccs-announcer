"""Shared test fixtures.

Everything here runs against the mock TTS engine and the mock audio backend, so
the whole pipeline is exercised on a machine with no sound card -- CI, or a
laptop.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config, load_config  # noqa: E402
from app.main import Services, create_app  # noqa: E402
from scripts.make_chimes import generate  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    """Never let a developer's real .env or shell variables reach the tests."""
    for key in list(os.environ):
        if key.startswith(("PA_", "PIPER_")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def chime_dir(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("chimes")
    generate(directory)
    return directory


@pytest.fixture
def config(tmp_path: Path, chime_dir: Path) -> Config:
    base = load_config(env_file=tmp_path / "does-not-exist.env")
    return dataclasses.replace(
        base,
        data_dir=tmp_path,
        db_path=tmp_path / "test.sqlite3",
        chime_dir=chime_dir,
        audio_cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        log_level="WARNING",
        tts_engine="mock",
        audio_backend="mock",
        # 30x speed keeps the suite quick while still exercising real elapsed
        # time, real thread scheduling, and real stop latency.
        mock_speed=30.0,
        # The suite builds many instances; the real guard is tested explicitly
        # in tests/test_singleton.py.
        single_instance=False,
        # Off by default across the suite; the dedicated tests turn it on.
        announce_address_mode="never",
        chime_gap_ms=100,
        default_chime="two_tone_bell",
    )


@pytest.fixture
def services(config: Config) -> Services:
    return Services(config)


@pytest.fixture
def app(config: Config):
    return create_app(config)


# Accounts every test can rely on. Passwords are long because the real
# password rules apply to them too.
STAFF_PASSWORD = "dana rowe test password"
ADMIN_PASSWORD = "alex vance test password"


@pytest.fixture
def seeded(app):
    """One staff account and one administrator, both ready to sign in."""
    services = app.state.services
    services.accounts.create_user(
        username="dana", display_name="Dana Rowe", password=STAFF_PASSWORD,
        role="staff", must_change_password=False,
    )
    services.accounts.create_user(
        username="alex", display_name="Alex Vance", password=ADMIN_PASSWORD,
        role="admin", must_change_password=False,
    )
    return services


def sign_in(test_client, username: str, password: str):
    """Sign in and arm the client with the session's CSRF token.

    Mirrors what the real page does: the cookie rides along automatically, and
    the token comes back in a header the page sets on every write.
    """
    response = test_client.post(
        "/api/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    test_client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
    return response.json()["user"]


@pytest.fixture
def client(app, seeded):
    """Signed in as ordinary staff -- the least-privileged real user.

    This fixture owns the lifespan, so it starts the player thread. The other
    client fixtures share the same running app with their own cookie jars.
    """
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        sign_in(test_client, "dana", STAFF_PASSWORD)
        yield test_client


@pytest.fixture
def admin_client(app, client):
    """A second browser, signed in as an administrator."""
    from fastapi.testclient import TestClient
    other = TestClient(app)
    sign_in(other, "alex", ADMIN_PASSWORD)
    return other


@pytest.fixture
def anon_client(app, client):
    """A browser that has not signed in."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def anon_client_factory(app):
    """Make extra browsers against the already-running app.

    Unlike `anon_client` this does not depend on `client`, so it can be used
    with an app that has no seeded accounts.
    """
    from fastapi.testclient import TestClient

    def make():
        return TestClient(app)
    return make


def wait_until(predicate, timeout: float = 15.0, interval: float = 0.02) -> bool:
    """Poll until `predicate()` is true. Returns False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
