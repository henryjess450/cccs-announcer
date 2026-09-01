"""Saying the address over the PA while the announcer waits to be set up.

This one speaks to the whole school, so the guards matter more than the
feature. Every test here is about when it must NOT happen.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.netinfo import spoken_address, startup_announcement
from tests.conftest import wait_until


# -- what it says ----------------------------------------------------------

def test_the_address_is_said_one_digit_at_a_time():
    """Somebody is writing this down off a loudspeaker in a corridor."""
    assert spoken_address("10.0.0.106", 8080) == (
        "one zero, point, zero, point, zero, point, one zero six, "
        "port, eight zero eight zero"
    )


def test_zero_is_said_as_zero_not_oh():
    """'oh' is ambiguous next to letters when transcribing."""
    spoken = spoken_address("192.168.0.1", 8080)
    assert "oh" not in spoken.split()
    assert "zero" in spoken


def test_the_announcement_says_what_to_do_about_it():
    text = startup_announcement("179.0.0.254", 8080)
    assert "one seven nine" in text
    assert "sign in" in text.lower()
    # No digits survive: everything must be speakable.
    assert not any(character.isdigit() for character in text)


# -- when it runs, and when it must not ------------------------------------

def announcements_of_kind(services, kind):
    return [row for row in services.db.recent(limit=200) if row["kind"] == kind]


def test_it_speaks_while_the_announcer_is_waiting_to_be_set_up(config):
    """A brand-new install: nobody knows the address yet."""
    from app.main import Services
    fast = dataclasses.replace(
        config, announce_address_interval_seconds=10, announce_address_max_times=2)
    # Driven directly rather than through the app, to keep the test quick.
    services = Services(fast)
    services.player.start()
    try:
        services.bootstrap_admin()
        assert services.accounts.setup_pending() is True
        services.start_address_announcements()
        assert wait_until(lambda: announcements_of_kind(services, "startup"), timeout=15)

        spoken = announcements_of_kind(services, "startup")[0]["normalized_text"]
        assert "point" in spoken
        assert "sign in" in spoken.lower()
    finally:
        services.stop_address_announcements()
        services.player.shutdown()


def test_it_stays_silent_once_the_announcer_has_been_set_up(client, app):
    """The common case, and the one that matters: a school that has been
    running for a year must not start shouting its address after a reboot."""
    services = app.state.services
    assert services.accounts.setup_pending() is False

    services.start_address_announcements()
    assert announcements_of_kind(services, "startup") == []


def test_claiming_the_account_silences_it(fresh_client, app):
    """It must go quiet the moment somebody signs in, not at the next repeat."""
    import re
    services = app.state.services
    text = services.first_login_file.read_text(encoding="utf-8")
    password = re.search(r"Password: (\S+)", text).group(1)

    from tests.conftest import sign_in
    sign_in(fresh_client, "admin", password)
    response = fresh_client.post("/api/setup", json={
        "username": "hjess", "display_name": "Henry Jess",
        "current_password": password, "new_password": "my own real password",
    })
    assert response.status_code == 200
    assert services._stop_address.is_set()
    assert services.accounts.setup_pending() is False


def test_it_can_be_turned_off_entirely(config):
    from app.main import Services
    off = dataclasses.replace(config, announce_address_on_start=False)
    services = Services(off)
    services.bootstrap_admin()
    services.start_address_announcements()
    assert services._address_thread is None
    assert announcements_of_kind(services, "startup") == []


def test_it_says_nothing_with_no_network(config, monkeypatch):
    """Announcing an address that does not exist would be worse than silence."""
    from app.main import Services
    monkeypatch.setattr("app.main.primary_address", lambda: None)
    services = Services(config)
    services.bootstrap_admin()
    services.start_address_announcements()
    assert services._address_thread is None


def test_it_gives_up_rather_than_talking_over_lessons_all_day(config):
    """If nobody ever signs in, it must stop by itself."""
    from app.main import Services
    fast = dataclasses.replace(
        config, announce_address_interval_seconds=10, announce_address_max_times=2)
    services = Services(fast)
    services.player.start()
    try:
        services.bootstrap_admin()
        services.start_address_announcements()
        assert wait_until(
            lambda: len(announcements_of_kind(services, "startup")) >= 2, timeout=30)
        # The thread must finish on its own, without anything stopping it.
        assert wait_until(lambda: not services._address_thread.is_alive(), timeout=30)
        assert len(announcements_of_kind(services, "startup")) == 2
    finally:
        services.stop_address_announcements()
        services.player.shutdown()


def test_startup_announcements_go_through_the_normal_queue(config):
    """So they can never overlap a real announcement."""
    from app.main import Services
    fast = dataclasses.replace(
        config, announce_address_interval_seconds=10, announce_address_max_times=1)
    services = Services(fast)
    services.player.start()
    try:
        services.bootstrap_admin()
        services.start_address_announcements()
        assert wait_until(
            lambda: services.db.count_by_state().get("done", 0) >= 1, timeout=30)
        assert services.audio.overlap_detected is False
        assert services.audio.overlapping_pairs() == []
    finally:
        services.stop_address_announcements()
        services.player.shutdown()


def test_they_do_not_count_against_anyone_s_rate_limit(config):
    """They are not a person's announcements."""
    from app.main import Services
    services = Services(config)
    services.bootstrap_admin()
    services.db.enqueue(
        raw_text="(startup)", normalized_text="address", chime="two_tone_bell",
        user_name="Announcer (starting up)", kind="startup",
    )
    user = services.accounts.get_by_username("admin")
    from app.accounts import _user_from_row
    assert services.rate_limiter.check(_user_from_row(user)).allowed is True


@pytest.fixture
def fresh_client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        yield test_client
