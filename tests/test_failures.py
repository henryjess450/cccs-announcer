"""Failures must be loud. Nothing may be silently swallowed.

Someone typed an announcement and walked away expecting to hear it. Every path
in here proves that when something breaks, the item is either still queued or
visibly marked failed -- never just gone.
"""

from __future__ import annotations

from app.db import STATE_DONE, STATE_FAILED, STATE_QUEUED
from tests.conftest import wait_until


def test_a_missing_audio_device_keeps_the_announcement_queued(services):
    services.audio.available = False
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="hold me", normalized_text="Please hold this.",
            chime="attention", user_name="tester",
        )
        services.player.notify_new_item()

        # The player tried, failed, and put it back.
        assert wait_until(lambda: services.player.health.audio_ok is False, timeout=10)
        assert services.db.get(item_id)["state"] == STATE_QUEUED
        assert "Tell IT" in services.player.health.audio_message
        assert services.build_snapshot()["status"] == "error"

        # Plug the PA back in: the held announcement plays without resubmitting.
        services.audio.available = True
        assert wait_until(lambda: services.db.get(item_id)["state"] == STATE_DONE, timeout=30)
        assert services.player.health.audio_ok is True
    finally:
        services.player.shutdown()


def test_a_device_failure_partway_through_still_keeps_the_item(services):
    services.audio.fail_on_play = True
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="x", normalized_text="Mid-playback failure.",
            chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.health.audio_ok is False, timeout=10)
        assert services.db.get(item_id)["state"] == STATE_QUEUED
    finally:
        services.player.shutdown()


def test_a_speech_failure_marks_the_item_failed_and_shows_it(services):
    services.tts.fail = True
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="x", normalized_text="This cannot be synthesized.",
            chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.db.get(item_id)["state"] == STATE_FAILED, timeout=10)

        row = services.db.get(item_id)
        assert "Tell IT" in row["error"]
        # No jargon reaches the person reading the screen.
        assert "Traceback" not in row["error"]

        snapshot = services.build_snapshot()
        assert any(problem["id"] == item_id for problem in snapshot["problems"])
    finally:
        services.player.shutdown()


def test_a_speech_failure_does_not_stop_later_announcements(services):
    """One bad item must not wedge the queue for the rest of the day."""
    services.tts.fail = True
    services.player.start()
    try:
        bad_id = services.db.enqueue(
            raw_text="bad", normalized_text="Broken.", chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.db.get(bad_id)["state"] == STATE_FAILED, timeout=10)

        services.tts.fail = False
        good_id = services.db.enqueue(
            raw_text="good", normalized_text="This one is fine.",
            chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.db.get(good_id)["state"] == STATE_DONE, timeout=20)
    finally:
        services.player.shutdown()


def test_the_player_thread_survives_an_unexpected_error(services):
    """A bug in one announcement must not take announcements down until reboot."""
    services.player.start()
    try:
        original = services.chimes.path_for

        def explode(key):
            raise RuntimeError("simulated bug in the chime lookup")

        services.chimes.path_for = explode
        bad_id = services.db.enqueue(
            raw_text="boom", normalized_text="Boom.", chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.db.get(bad_id)["state"] == STATE_FAILED, timeout=10)

        services.chimes.path_for = original
        good_id = services.db.enqueue(
            raw_text="ok", normalized_text="Still working.", chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.db.get(good_id)["state"] == STATE_DONE, timeout=20)
    finally:
        services.player.shutdown()


def test_health_reports_degraded_when_the_speakers_are_missing(client, app):
    app.state.services.audio.available = False
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["audio"]["ok"] is False
    assert body["database"]["writable"] is True


def test_health_reports_degraded_when_the_voice_is_missing(client, app):
    app.state.services.tts.fail = True
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["tts"]["ok"] is False


def test_a_held_announcement_is_never_reported_as_playing(services):
    """While the speakers are dead, "Now playing" would be a lie.

    The player keeps re-claiming the item to retry it, so the row is briefly in
    the 'playing' state on every attempt. Staff must see it as held.
    """
    services.audio.available = False
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="held", normalized_text="Waiting on the speakers.",
            chime="attention", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.health.audio_ok is False, timeout=10)

        # Check every snapshot across a full retry cycle, including the instants
        # when the row really is marked 'playing'.
        for _ in range(60):
            snapshot = services.build_snapshot()
            assert snapshot["now_playing"] is None, "a held item was shown as playing"
            assert snapshot["status"] == "error"
            assert snapshot["queue_depth"] >= 1, "a held item vanished from the count"

        snapshot = services.build_snapshot()
        if snapshot["held"] is not None:
            assert snapshot["held"]["id"] == item_id
    finally:
        services.player.shutdown()
