"""The single most important property: two announcements never overlap.

The mock audio backend raises if a second playback session is ever opened while
one is live, and separately records every playback window so the tests can
check for overlap directly. Both checks are asserted here.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from app.db import STATE_DONE
from tests.conftest import wait_until

SPEECH_FILE = re.compile(r"speech-(\d+)\.wav")


def played_ids(backend):
    """Playback order, recovered from the per-item speech file names."""
    order = []
    for record in backend.records:
        for name in record.files:
            match = SPEECH_FILE.match(name)
            if match:
                order.append(int(match.group(1)))
    return order


def queue_is_empty(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


def test_twenty_simultaneous_submissions_never_overlap(admin_client, app):
    """Twenty submissions hitting Send at the same instant.

    This is the scenario the whole design exists to survive: many concurrent web
    requests, exactly one audio stream. Sent as an administrator because
    administrators are exempt from the rate limit -- the limiter is tested
    separately, and it must not be what makes this pass.
    """
    services = app.state.services
    backend = services.audio
    count = 20

    def submit(index: int):
        return admin_client.post("/api/announcements", json={
            "text": f"Announcement number {index} for the whole building.",
        })

    with ThreadPoolExecutor(max_workers=count) as pool:
        responses = list(pool.map(submit, range(count)))

    assert all(r.status_code == 201 for r in responses), \
        [r.status_code for r in responses]

    assert wait_until(lambda: queue_is_empty(services), timeout=60), \
        f"queue did not drain: {services.db.count_by_state()}"

    # 1. The backend never had two sessions open at once.
    assert backend.overlap_detected is False
    # 2. No two playback windows overlap in wall-clock time.
    assert backend.overlapping_pairs() == []
    # 3. Every announcement actually played -- none were dropped in the scramble.
    assert len(backend.records) == count
    assert services.db.count_by_state().get(STATE_DONE) == count


def test_concurrent_direct_enqueues_also_never_overlap(services):
    """Same guarantee, bypassing the web layer entirely."""
    services.player.start()
    try:
        count = 15

        def enqueue(index: int) -> int:
            item_id = services.db.enqueue(
                raw_text=f"item {index}",
                normalized_text=f"This is announcement {index}.",
                chime="attention",
                user_name="tester",
            )
            services.player.notify_new_item()
            return item_id

        with ThreadPoolExecutor(max_workers=count) as pool:
            list(pool.map(enqueue, range(count)))

        assert wait_until(lambda: queue_is_empty(services), timeout=60)
        assert services.audio.overlap_detected is False
        assert services.audio.overlapping_pairs() == []
        assert len(services.audio.records) == count
    finally:
        services.player.shutdown()


def test_priority_plays_next_but_does_not_interrupt(services):
    """Priority jumps the queue. It never cuts off audio already playing."""
    services.player.start()
    try:
        # A long announcement so there is a real window to interrupt.
        long_id = services.db.enqueue(
            raw_text="long", normalized_text="A " * 200, chime="attention",
            user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.current_id == long_id, timeout=10)

        normal_id = services.db.enqueue(
            raw_text="normal", normalized_text="Routine notice.", chime="attention",
            user_name="tester",
        )
        urgent_id = services.db.enqueue(
            raw_text="urgent", normalized_text="Early dismissal.", chime="urgent",
            user_name="tester", priority=1,
        )
        services.player.notify_new_item()

        assert wait_until(lambda: queue_is_empty(services), timeout=60)

        # The long one finished normally -- priority did not cut it off.
        assert services.db.get(long_id)["state"] == STATE_DONE
        assert services.db.get(long_id)["stopped_by"] is None
        # ...and the urgent one went before the normal one that was queued first.
        assert played_ids(services.audio) == [long_id, urgent_id, normal_id]
    finally:
        services.player.shutdown()


def test_stop_cuts_the_current_announcement_and_records_who(services):
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="long", normalized_text="A " * 400, chime="attention",
            user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.current_id == item_id, timeout=10)

        assert services.player.request_stop("Principal Vance", item_id=item_id) is True
        assert wait_until(lambda: services.db.get(item_id)["state"] == "stopped", timeout=10)

        row = services.db.get(item_id)
        assert row["stopped_by"] == "Principal Vance"
        # It was cut short, not left to run to the end.
        assert row["duration_seconds"] < 400 * 2 / 13.5 / services.config.mock_speed
    finally:
        services.player.shutdown()


def test_stop_for_the_wrong_id_does_not_silence_someone_else(services):
    """Guards the race where Stop is clicked just as the next item begins."""
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="mine", normalized_text="A " * 300, chime="attention",
            user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.current_id == item_id, timeout=10)

        assert services.player.request_stop("someone", item_id=item_id + 999) is False
        assert wait_until(lambda: services.db.get(item_id)["state"] == STATE_DONE, timeout=60)
    finally:
        services.player.shutdown()


def test_stopping_during_the_chime_skips_the_speech(services):
    """Stop must abandon the whole sequence, not just the part that is playing."""
    services.player.start()
    try:
        item_id = services.db.enqueue(
            raw_text="x", normalized_text="This should never be spoken.",
            chime="two_tone_bell", user_name="tester",
        )
        services.player.notify_new_item()
        assert wait_until(lambda: services.player.current_id == item_id, timeout=10)
        services.player.request_stop("tester", item_id=item_id)

        assert wait_until(lambda: services.db.get(item_id)["state"] == "stopped", timeout=10)
        record = services.audio.records[0]
        assert f"speech-{item_id}.wav" not in record.files
    finally:
        services.player.shutdown()


def test_a_queued_item_can_be_cancelled_before_it_plays(client, app):
    services = app.state.services
    first = client.post("/api/announcements", json={"text": "A " * 200}).json()
    second = client.post("/api/announcements", json={"text": "Cancel me please."}).json()

    response = client.post(f"/api/announcements/{second['id']}/stop", json={})
    assert response.json()["stopped"] is True

    assert wait_until(lambda: queue_is_empty(services), timeout=60)
    assert services.db.get(second["id"])["state"] == "stopped"
    assert services.db.get(first["id"])["state"] == STATE_DONE
    assert second["id"] not in played_ids(services.audio)
