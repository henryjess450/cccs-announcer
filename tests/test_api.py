"""The web layer: validation, status, live updates, and the audit trail."""

from __future__ import annotations

import json

from app.db import STATE_DONE
from tests.conftest import wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


# -- compose-screen data ---------------------------------------------------

def test_config_reports_the_chime_and_the_single_zone(client):
    body = client.get("/api/config").json()
    assert body["default_chime"] == "two_tone_bell"
    assert body["my_chime"] is None          # nothing chosen yet
    assert body["chime_label"] == "Two-tone bell"
    # The end tone is an output setting, never something staff pick.
    assert "end_tone" not in [chime["key"] for chime in body["chimes"]]
    assert body["zones"] == [{"key": "all", "label": "Whole building"}]
    assert body["max_chars"] == 500


def test_the_compose_page_still_has_no_chime_chooser(client):
    """The chime belongs to the account, chosen once. Putting it on the compose
    screen would be one more decision for somebody in a hurry."""
    page = client.get("/").text
    assert 'id="chime"' not in page


def test_the_page_and_its_assets_are_served_locally(client):
    assert "<title>CCCS Announcements</title>" in client.get("/").text
    # No CDN anywhere: school computers may have no internet at all.
    for path in ("/static/app.js", "/static/styles.css"):
        assert client.get(path).status_code == 200
    page = client.get("/").text
    assert "http://" not in page and "https://" not in page


def test_normalize_shows_what_will_actually_be_spoken(client):
    body = client.post("/api/normalize", json={"text": "Bus 12 at 2:15 in Rm 204"}).json()
    assert body["normalized"] == "Bus number twelve at two fifteen in room two oh four"
    assert body["speakable"] is True
    assert body["chars"] == len("Bus 12 at 2:15 in Rm 204")


# -- validation ------------------------------------------------------------

def test_an_empty_announcement_is_refused_in_plain_language(client):
    response = client.post("/api/announcements", json={"text": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "Type an announcement first."


def test_an_overlong_announcement_is_refused(client):
    response = client.post("/api/announcements", json={"text": "x" * 501})
    assert response.status_code == 400
    assert "500 characters" in response.json()["detail"]


def test_a_chime_in_the_request_is_ignored(client, app):
    """The chime comes from the account, never the request body -- a crafted or
    stale request must not be able to pick a different sound."""
    services = app.state.services
    for attempted in ("urgent", "fanfare", "../../../../etc/passwd", ""):
        body = client.post("/api/announcements", json={
            "text": "Chime override attempt.", "chime": attempted,
        }).json()
        assert services.db.get(body["id"])["chime"] == "two_tone_bell"
    assert wait_until(lambda: drained(services), timeout=60)


def test_announcements_use_the_school_default_until_someone_chooses(client, app):
    services = app.state.services
    client.post("/api/announcements", json={"text": "Routine notice."})
    client.post("/api/announcements", json={"text": "Urgent notice.", "priority": True})
    assert wait_until(lambda: drained(services), timeout=60)

    played = [name for record in services.audio.records for name in record.files]
    chimes = [name for name in played if not name.startswith("speech-")]
    assert chimes and set(chimes) == {"two_tone_bell.wav"}


def test_zones_other_than_the_whole_building_are_refused_for_now(client):
    response = client.post("/api/announcements", json={"text": "hello", "zone": "gym"})
    assert response.status_code == 400
    assert "whole-building" in response.json()["detail"]


def test_markup_never_reaches_the_stored_text(client, app):
    services = app.state.services
    body = client.post("/api/announcements", json={
        "text": "<speak>hello</speak> everyone",
    }).json()
    assert wait_until(lambda: drained(services), timeout=30)
    row = services.db.get(body["id"])
    assert "<" not in row["normalized_text"]
    # The raw text is kept verbatim for the audit log.
    assert "<speak>" in row["raw_text"]


# -- submission and the audit trail ----------------------------------------

def test_submitting_returns_the_spoken_text_and_the_queue_position(client, app):
    services = app.state.services
    first = client.post("/api/announcements", json={"text": "A " * 150}).json()
    second = client.post("/api/announcements", json={"text": "Bus 12 has arrived."}).json()

    assert second["normalized"] == "Bus number twelve has arrived."
    assert second["position"] >= 1
    assert second["seconds_until"] > 0
    assert wait_until(lambda: drained(services), timeout=60)
    assert services.db.get(first["id"])["state"] == STATE_DONE


def test_every_announcement_is_attributed_and_logged(client, app):
    services = app.state.services
    body = client.post("/api/announcements", json={"text": "Testing attribution."}).json()
    assert wait_until(lambda: drained(services), timeout=30)

    row = services.db.get(body["id"])
    assert row["user_name"]                    # never blank
    assert row["raw_text"] == "Testing attribution."
    assert row["normalized_text"] == "Testing attribution."
    assert row["created_at"] and row["started_at"] and row["finished_at"]
    assert row["duration_seconds"] > 0
    assert row["state"] == STATE_DONE
    assert row["zone"] == "all"


def test_the_audio_test_plays_a_chime_and_no_speech(client, app):
    services = app.state.services
    body = client.post("/api/test-audio", json={}).json()
    assert wait_until(lambda: drained(services), timeout=30)

    row = services.db.get(body["id"])
    assert row["kind"] == "test"
    assert row["state"] == STATE_DONE
    played = [name for record in services.audio.records for name in record.files]
    assert "two_tone_bell.wav" in played
    assert not any(name.startswith("speech-") for name in played)


# -- live status -----------------------------------------------------------

def test_status_reports_idle_then_the_queue(client, app):
    services = app.state.services
    idle = client.get("/api/status").json()
    assert idle["status"] == "idle"
    assert idle["queue_depth"] == 0

    client.post("/api/announcements", json={"text": "A " * 200})
    client.post("/api/announcements", json={"text": "Second one."})
    busy = client.get("/api/status").json()
    assert busy["queue_depth"] >= 1
    assert busy["queue_seconds"] > 0

    assert wait_until(lambda: drained(services), timeout=60)
    assert client.get("/api/status").json()["status"] == "idle"


def test_priority_is_visible_in_the_queue(client, app):
    services = app.state.services
    client.post("/api/announcements", json={"text": "A " * 200})
    client.post("/api/announcements", json={"text": "Early dismissal.", "priority": True})

    snapshot = client.get("/api/status").json()
    assert any(item["priority"] for item in snapshot["queue"])
    assert wait_until(lambda: drained(services), timeout=60)


def test_the_event_stream_sends_a_status_snapshot_immediately(app, seeded):
    """A browser opening the page must see the true state at once.

    Driven against the ASGI interface directly. TestClient cannot abandon an
    endless response, and httpx's ASGI transport buffers the whole body -- both
    hang on a stream that is endless by design.
    """
    import asyncio

    services = app.state.services
    user = services.accounts.get_by_username("dana")
    from app.accounts import _user_from_row
    token, _ = services.accounts.start_session(_user_from_row(user))
    cookie = f"{services.config.session_cookie_name}={token}".encode()

    async def read_first_frame():
        services.broadcaster.bind_loop(asyncio.get_running_loop())
        services.publish_status()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/api/events", "raw_path": b"/api/events",
            "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(b"host", b"pa"), (b"cookie", cookie)],
            "client": ("127.0.0.1", 1234),
            "server": ("pa", 80),
        }
        outbox: asyncio.Queue = asyncio.Queue()

        async def receive():
            # Stay connected until the test cancels the task.
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message):
            await outbox.put(message)

        task = asyncio.create_task(app(scope, receive, send))
        try:
            start = await asyncio.wait_for(outbox.get(), timeout=5)
            assert start["type"] == "http.response.start"
            assert start["status"] == 200
            headers = {k.decode(): v.decode() for k, v in start["headers"]}
            assert headers["content-type"].startswith("text/event-stream")

            body = b""
            while b"\n\n" not in body:
                message = await asyncio.wait_for(outbox.get(), timeout=5)
                body += message.get("body", b"")
            return body.decode()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    frame = asyncio.run(read_first_frame())
    assert frame.startswith("event: status\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["status"] in ("idle", "playing", "error")
    assert "queue" in payload and "audio" in payload and "tts" in payload


def test_the_broadcaster_caches_the_latest_snapshot_for_new_clients(services):
    from app.events import sse_message
    services.publish_status()
    assert services.broadcaster.latest["status"] == "idle"
    frame = sse_message(services.broadcaster.latest)
    assert frame.startswith("event: status\ndata: {")
    assert frame.endswith("\n\n")


# -- health ----------------------------------------------------------------

def test_health_is_ok_with_everything_working(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] is True
    assert body["audio"]["ok"] is True
    assert body["tts"]["ok"] is True
    assert body["database"]["writable"] is True
    assert body["queue_depth"] == 0


def test_the_page_is_never_cached_and_assets_are_versioned(client):
    """An upgrade must not leave a school desktop on last month's JavaScript."""
    response = client.get("/")
    assert "no-store" in response.headers["cache-control"]
    body = response.text
    assert "__VERSION__" not in body
    assert "/static/styles.css?v=" in body
    assert "/static/app.js?v=" in body


def test_the_error_banner_shares_the_sticky_header(client):
    """The banner must not slide under the header when the page scrolls.

    It is the loudest thing the system can say; hiding it defeats the point.
    """
    body = client.get("/").text
    sticky = body.index('class="stickytop"')
    assert sticky < body.index('class="topbar"') < body.index('id="banner"')


def test_test_mode_is_announced_on_screen(client, app):
    """A mock voice hums. Unannounced, that is indistinguishable from a fault."""
    snapshot = client.get("/api/status").json()
    assert snapshot["test_mode"]["active"] is True
    message = snapshot["test_mode"]["message"]
    assert "test tone" in message
    assert "not going out" in message
    assert app.state.services.tts.name == "mock"


def test_test_mode_is_silent_when_the_real_engines_are_in_use(services, monkeypatch):
    monkeypatch.setattr(services.tts, "name", "piper")
    monkeypatch.setattr(services.audio, "name", "sounddevice")
    snapshot = services.build_snapshot()
    assert snapshot["test_mode"]["active"] is False
    assert snapshot["test_mode"]["message"] == ""


def test_asset_urls_change_when_the_javascript_changes(client, app):
    """Version-based cache busting only works if somebody remembers to bump
    the version. When they forget, browsers serve last month's JavaScript
    against this month's page and it looks like a broken feature.
    """
    import re
    from app.main import STATIC_DIR

    def token(page: str) -> str:
        match = re.search(r"/static/app\.js\?v=([^\"']+)", page)
        assert match, page[:400]
        return match.group(1)

    before = token(client.get("/").text)
    assert before == token(client.get("/").text), "the token must be stable"

    script = STATIC_DIR / "app.js"
    original = script.read_bytes()
    try:
        script.write_bytes(original + b"\n// a change\n")
        after = token(client.get("/").text)
    finally:
        script.write_bytes(original)

    assert after != before, "changing app.js must change its cache-busting token"


def test_every_page_gets_the_same_fresh_asset_token(client, admin_client):
    import re

    def token(page: str) -> str:
        return re.search(r"/static/styles\.css\?v=([^\"']+)", page).group(1)

    assert token(client.get("/").text) == token(admin_client.get("/admin").text)
    assert "__VERSION__" not in client.get("/").text


def test_the_asset_token_follows_content_not_timestamps(client):
    """A git pull rewrites timestamps on everything it touches. If the token
    followed those, every update would re-download the CSS and JavaScript even
    when neither actually changed.
    """
    import os
    import re
    import time
    from app.main import STATIC_DIR

    def token() -> str:
        return re.search(r"/static/app\.js\?v=([^\"']+)", client.get("/").text).group(1)

    before = token()
    script = STATIC_DIR / "app.js"
    original = script.stat().st_mtime
    try:
        # Same bytes, new timestamp -- exactly what a pull does.
        os.utime(script, (time.time(), time.time()))
        assert token() == before
    finally:
        os.utime(script, (original, original))
