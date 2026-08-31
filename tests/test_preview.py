"""Preview: hear it here, before four hundred people hear it there.

The property that matters most is negative -- Preview must not reach the PA,
must not enter the queue, and must not touch the audio device at all.
"""

from __future__ import annotations

import wave
from io import BytesIO

from tests.conftest import wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


def test_preview_returns_playable_audio(client):
    response = client.post("/api/preview", json={"text": "Bus 12 at 2:15 in Rm 204."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"

    with wave.open(BytesIO(response.content), "rb") as handle:
        assert handle.getnframes() > 0
        assert handle.getframerate() > 0


def test_preview_never_reaches_the_speakers(client, app):
    """The whole point. Nothing may be sent to the PA, ever."""
    services = app.state.services
    before = len(services.audio.records)

    for _ in range(3):
        assert client.post("/api/preview", json={"text": "Testing the preview."}).status_code == 200

    assert len(services.audio.records) == before
    assert services.player.current_id is None


def test_preview_never_enters_the_queue(client, app):
    services = app.state.services
    client.post("/api/preview", json={"text": "This must not be announced."})

    assert services.db.count_by_state() == {}
    assert client.get("/api/status").json()["queue_depth"] == 0


def test_preview_speaks_the_normalized_text(client):
    """What you hear in the preview has to be what the school would hear."""
    long_form = client.post("/api/preview", json={"text": "Bus 12"})
    spelled_out = client.post("/api/preview", json={"text": "Bus number twelve"})
    # Same normalized text -> byte-identical audio, served from the same cache
    # entry. If preview synthesized the raw text these would differ.
    assert long_form.content == spelled_out.content


def test_preview_is_not_cached_by_the_browser(client):
    response = client.post("/api/preview", json={"text": "Hello."})
    assert "no-store" in response.headers["cache-control"]


def test_preview_refuses_empty_text(client):
    response = client.post("/api/preview", json={"text": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "Type an announcement first."


def test_preview_refuses_overlong_text(client):
    response = client.post("/api/preview", json={"text": "x" * 501})
    assert response.status_code == 400
    assert "500 characters" in response.json()["detail"]


def test_preview_requires_signing_in(anon_client):
    assert anon_client.post("/api/preview", json={"text": "Hello."}).status_code == 401


def test_preview_is_blocked_until_a_new_account_sets_a_password(anon_client, app):
    from tests.conftest import sign_in
    services = app.state.services
    services.accounts.create_user(
        username="fresh", display_name="Fresh Start", password="the issued password",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "fresh", "the issued password")
    response = anon_client.post("/api/preview", json={"text": "Hello."})
    assert response.status_code == 403
    assert response.json()["reason"] == "password_change_required"


def test_a_speech_failure_during_preview_says_so_plainly(client, app):
    services = app.state.services
    services.tts.fail = True
    response = client.post("/api/preview", json={"text": "Hello."})
    assert response.status_code == 503
    assert response.json()["reason"] == "tts_failed"
    assert "Tell IT" in response.json()["detail"]
    assert "Traceback" not in response.json()["detail"]


def test_preview_still_works_while_the_speakers_are_broken(client, app):
    """Preview does not use the audio device, so a dead PA must not stop it.

    This is exactly when someone wants to check pronunciation: while they wait
    for the speakers to come back.
    """
    services = app.state.services
    services.audio.available = False
    assert client.post("/api/preview", json={"text": "Still works."}).status_code == 200


def test_preview_is_recorded_as_a_preview_not_an_announcement(client):
    """A preview is not an announcement and must never appear in the log as one."""
    client.post("/api/preview", json={"text": "Just checking a name."})
    assert client.get("/api/announcements").json()["announcements"] == []


def test_previewing_does_not_count_against_the_rate_limit(client, app):
    """Checking pronunciation must not use up the ability to announce."""
    services = app.state.services
    for _ in range(services.rate_limiter.limit() + 3):
        assert client.post("/api/preview", json={"text": "Checking."}).status_code == 200
    assert client.post("/api/announcements", json={"text": "Real one."}).status_code == 201
    assert wait_until(lambda: drained(services), timeout=60)


def test_the_live_status_stream_is_also_blocked_until_the_password_is_set(anon_client, app):
    """The page must not open this stream yet -- a 403 here would show the
    staff member a "Connection lost" banner, which is not what is wrong."""
    from tests.conftest import sign_in
    services = app.state.services
    services.accounts.create_user(
        username="pending", display_name="Pending Person", password="the issued password",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "pending", "the issued password")
    response = anon_client.get("/api/events")
    assert response.status_code == 403
    assert response.json()["reason"] == "password_change_required"


def test_config_stays_reachable_so_the_password_screen_can_load(anon_client, app):
    from tests.conftest import sign_in
    services = app.state.services
    services.accounts.create_user(
        username="pending2", display_name="Pending Two", password="the issued password",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "pending2", "the issued password")
    assert anon_client.get("/api/config").status_code == 200
    assert anon_client.get("/api/me").status_code == 200
