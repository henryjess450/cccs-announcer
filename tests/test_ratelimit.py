"""Per-user submission limits.

The limit exists to stop an accident -- a stuck key, a double-click, a tab
left replaying a request -- from tying up the PA. The audit log, not the
limiter, is what deters deliberate misuse.
"""

from __future__ import annotations

from app.ratelimit import RateLimiter
from tests.conftest import sign_in, wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


def test_staff_are_stopped_after_the_limit(client, app):
    services = app.state.services
    limit = services.rate_limiter.limit()

    for index in range(limit):
        response = client.post("/api/announcements", json={"text": f"Notice {index}."})
        assert response.status_code == 201, response.text

    blocked = client.post("/api/announcements", json={"text": "One too many."})
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["reason"] == "rate_limited"
    assert body["retry_after_seconds"] > 0
    # Plain language, and it says what to do instead.
    assert "ask the office" in body["detail"].lower()
    assert wait_until(lambda: drained(services), timeout=60)


def test_the_blocked_announcement_is_not_queued(client, app):
    services = app.state.services
    for index in range(services.rate_limiter.limit()):
        client.post("/api/announcements", json={"text": f"Notice {index}."})
    client.post("/api/announcements", json={"text": "This must not be queued."})

    assert wait_until(lambda: drained(services), timeout=60)
    texts = [row["raw_text"] for row in services.db.recent(limit=50)]
    assert "This must not be queued." not in texts


def test_administrators_are_exempt(admin_client, app):
    """In an emergency, the person running the building must not meet a limiter."""
    services = app.state.services
    for index in range(services.rate_limiter.limit() + 4):
        response = admin_client.post("/api/announcements", json={"text": f"Notice {index}."})
        assert response.status_code == 201
    assert wait_until(lambda: drained(services), timeout=90)


def test_the_limit_is_per_person_not_global(client, app):
    """One busy teacher must not stop everyone else announcing."""
    from fastapi.testclient import TestClient

    services = app.state.services
    services.accounts.create_user(
        username="sam", display_name="Sam Ortiz", password="sam a long password",
        role="staff", must_change_password=False,
    )
    other = TestClient(app)
    sign_in(other, "sam", "sam a long password")

    for index in range(services.rate_limiter.limit()):
        assert client.post(
            "/api/announcements", json={"text": f"Dana {index}."}
        ).status_code == 201
    assert client.post("/api/announcements", json={"text": "Dana again."}).status_code == 429

    # A different staff account is unaffected.
    assert other.post(
        "/api/announcements", json={"text": "Sam is fine."}
    ).status_code == 201
    assert wait_until(lambda: drained(services), timeout=90)


def test_a_rate_limited_attempt_is_recorded(client, app):
    services = app.state.services
    for index in range(services.rate_limiter.limit() + 1):
        client.post("/api/announcements", json={"text": f"Notice {index}."})

    events = [e["event"] for e in services.accounts.recent_events()]
    assert "announcement.rate_limited" in events
    assert wait_until(lambda: drained(services), timeout=60)


def test_failed_announcements_still_count(client, app):
    """Otherwise a broken PA turns into unlimited submissions."""
    services = app.state.services
    services.audio.available = False

    for index in range(services.rate_limiter.limit()):
        assert client.post(
            "/api/announcements", json={"text": f"Held {index}."}
        ).status_code == 201
    assert client.post("/api/announcements", json={"text": "One more."}).status_code == 429


def test_a_zero_limit_turns_the_limiter_off(config, app):
    services = app.state.services
    services.db.set_setting("rate_limit_count", "0")
    limiter = RateLimiter(services.db, config)
    user = services.accounts.create_user(
        username="busy", display_name="Busy Person", password="a long enough password",
        must_change_password=False,
    )
    for _ in range(20):
        services.db.enqueue(
            raw_text="x", normalized_text="x", chime="two_tone_bell",
            user_name=user.display_name, user_id=user.id,
        )
    assert limiter.check(user).allowed is True


def test_the_limit_can_be_changed_without_a_restart(client, app):
    """Phase 3 exposes this in the admin panel; the mechanism works now."""
    services = app.state.services
    services.db.set_setting("rate_limit_count", "2")

    assert client.post("/api/announcements", json={"text": "One."}).status_code == 201
    assert client.post("/api/announcements", json={"text": "Two."}).status_code == 201
    assert client.post("/api/announcements", json={"text": "Three."}).status_code == 429
    assert wait_until(lambda: drained(services), timeout=60)


def test_the_wait_is_reported_in_plain_language(client, app):
    services = app.state.services
    for index in range(services.rate_limiter.limit() + 1):
        response = client.post("/api/announcements", json={"text": f"Notice {index}."})
    detail = response.json()["detail"]
    assert "minutes" in detail
    assert "429" not in detail and "rate" not in detail.lower()
    assert wait_until(lambda: drained(services), timeout=60)
