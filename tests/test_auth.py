"""Sign-in, sessions, and the rules that keep an unattended computer from
becoming an open microphone.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.accounts import password_problem
from app.db import now_iso, utcnow
from app.security import hash_password, token_fingerprint, verify_password
from tests.conftest import STAFF_PASSWORD, sign_in


# -- password hashing ------------------------------------------------------

def test_passwords_are_hashed_not_stored():
    stored = hash_password("a good long password")
    assert "a good long password" not in stored
    assert stored.startswith("scrypt$")
    assert verify_password("a good long password", stored)
    assert not verify_password("a good long passwore", stored)


def test_the_same_password_hashes_differently_every_time():
    """Per-password salt: two people with the same password must not be
    visibly identical in the database."""
    assert hash_password("shared password") != hash_password("shared password")


def test_a_corrupt_password_record_fails_closed():
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "") is False


@pytest.mark.parametrize("password,ok", [
    ("short", False),
    ("nine char", False),
    ("ten chars!", True),
    (" leading space is out ", False),
    ("a perfectly reasonable passphrase", True),
])
def test_password_rules(password, ok):
    assert (password_problem(password) is None) is ok


# -- no anonymous anything -------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/config"),
    ("GET", "/api/status"),
    ("GET", "/api/events"),
    ("GET", "/api/announcements"),
    ("POST", "/api/announcements"),
    ("POST", "/api/normalize"),
    ("POST", "/api/preview"),
    ("POST", "/api/test-audio"),
    ("GET", "/api/admin/users"),
])
def test_everything_requires_signing_in(anon_client, method, path):
    response = anon_client.request(method, path, json={"text": "hello"})
    assert response.status_code == 401
    assert response.json()["reason"] == "signed_out"


def test_the_compose_page_redirects_to_sign_in(anon_client):
    response = anon_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_the_sign_in_page_is_reachable_without_signing_in(anon_client):
    response = anon_client.get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_health_stays_open_for_monitoring(anon_client):
    """IT has to be able to check this when nobody can sign in."""
    response = anon_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# -- signing in ------------------------------------------------------------

def test_signing_in_and_out(anon_client, app):
    user = sign_in(anon_client, "dana", STAFF_PASSWORD)
    assert user["display_name"] == "Dana Rowe"
    assert user["is_admin"] is False
    assert anon_client.get("/api/status").status_code == 200

    assert anon_client.post("/api/logout").status_code == 200
    assert anon_client.get("/api/status").status_code == 401


def test_a_wrong_password_says_nothing_useful(anon_client):
    response = anon_client.post("/api/login", json={"username": "dana", "password": "nope"})
    assert response.status_code == 401
    # Must not reveal whether the username exists.
    assert response.json()["detail"] == "That username or password is not right."


def test_an_unknown_username_gives_the_identical_message(anon_client):
    response = anon_client.post(
        "/api/login", json={"username": "nobody-here", "password": "nope"}
    )
    assert response.json()["detail"] == "That username or password is not right."


def test_usernames_are_case_insensitive(anon_client):
    assert anon_client.post(
        "/api/login", json={"username": "DANA", "password": STAFF_PASSWORD}
    ).status_code == 200


def test_too_many_wrong_passwords_locks_the_account(anon_client, app):
    for _ in range(5):
        anon_client.post("/api/login", json={"username": "dana", "password": "wrong"})

    # Even the right password is refused while locked.
    response = anon_client.post(
        "/api/login", json={"username": "dana", "password": STAFF_PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["reason"] == "locked_out"
    assert "locked" in response.json()["detail"]

    # An administrator can clear it.
    services = app.state.services
    row = services.accounts.get_by_username("dana")
    services.accounts.unlock(int(row["id"]))
    assert anon_client.post(
        "/api/login", json={"username": "dana", "password": STAFF_PASSWORD}
    ).status_code == 200


def test_a_successful_sign_in_clears_earlier_failures(anon_client, app):
    services = app.state.services
    for _ in range(3):
        anon_client.post("/api/login", json={"username": "dana", "password": "wrong"})
    sign_in(anon_client, "dana", STAFF_PASSWORD)
    row = services.accounts.get_by_username("dana")
    assert row["failed_logins"] == 0
    assert row["locked_until"] is None


# -- sessions --------------------------------------------------------------

def test_the_session_cookie_is_not_readable_by_scripts(anon_client):
    response = anon_client.post(
        "/api/login", json={"username": "dana", "password": STAFF_PASSWORD}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_only_the_hash_of_the_session_token_is_stored(anon_client, app):
    anon_client.post("/api/login", json={"username": "dana", "password": STAFF_PASSWORD})
    services = app.state.services
    token = anon_client.cookies.get(services.config.session_cookie_name)
    assert token

    rows = services.db.connect().execute("SELECT token_hash FROM sessions").fetchall()
    stored = [r["token_hash"] for r in rows]
    assert token not in stored
    assert token_fingerprint(token) in stored


def test_an_idle_session_expires(client, app):
    """An unattended logged-in computer must not stay an open microphone."""
    services = app.state.services
    assert client.get("/api/status").status_code == 200

    stale = (utcnow() - timedelta(minutes=services.config.session_idle_minutes + 5))
    services.db.connect().execute(
        "UPDATE sessions SET last_seen_at = ?",
        (stale.isoformat(timespec="seconds").replace("+00:00", "Z"),),
    )
    assert client.get("/api/status").status_code == 401


def test_a_session_past_its_maximum_age_expires_even_if_active(client, app):
    services = app.state.services
    services.db.connect().execute("UPDATE sessions SET expires_at = ?", (now_iso(),))
    assert client.get("/api/status").status_code == 401


def test_turning_off_an_account_signs_it_out_immediately(client, app):
    """The window between 'account disabled' and 'token expired' is exactly
    the window that matters."""
    services = app.state.services
    assert client.get("/api/status").status_code == 200

    row = services.accounts.get_by_username("dana")
    services.accounts.set_active(int(row["id"]), False)

    assert client.get("/api/status").status_code == 401
    assert client.post(
        "/api/login", json={"username": "dana", "password": STAFF_PASSWORD}
    ).status_code == 401


def test_signing_out_kills_that_session_only(client, admin_client):
    client.post("/api/logout")
    assert client.get("/api/status").status_code == 401
    assert admin_client.get("/api/status").status_code == 200


# -- CSRF ------------------------------------------------------------------

def test_a_write_without_the_token_is_refused(client):
    """A cookie alone must not be enough: browsers attach cookies to requests
    started by other sites."""
    del client.headers["X-CSRF-Token"]
    response = client.post("/api/announcements", json={"text": "Sent by another site."})
    assert response.status_code == 403
    assert response.json()["reason"] == "bad_csrf"


def test_a_write_with_the_wrong_token_is_refused(client):
    client.headers.update({"X-CSRF-Token": "not-the-right-token"})
    assert client.post("/api/announcements", json={"text": "hello"}).status_code == 403


def test_reads_do_not_need_the_token(client):
    del client.headers["X-CSRF-Token"]
    assert client.get("/api/status").status_code == 200


# -- forced password change ------------------------------------------------

def test_a_new_account_must_choose_a_password_before_announcing(anon_client, app):
    services = app.state.services
    services.accounts.create_user(
        username="newbie", display_name="Sam New", password="issued password here",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "newbie", "issued password here")

    blocked = anon_client.post("/api/announcements", json={"text": "Hello everyone."})
    assert blocked.status_code == 403
    assert blocked.json()["reason"] == "password_change_required"

    changed = anon_client.post("/api/password", json={
        "current_password": "issued password here",
        "new_password": "my own chosen password",
    })
    assert changed.status_code == 200

    assert anon_client.post(
        "/api/announcements", json={"text": "Hello everyone."}
    ).status_code == 201


def test_changing_a_password_needs_the_current_one(client):
    response = client.post("/api/password", json={
        "current_password": "not my password",
        "new_password": "a brand new password",
    })
    assert response.status_code == 400
    assert "not right" in response.json()["detail"]


def test_a_new_password_must_meet_the_rules(client):
    response = client.post("/api/password", json={
        "current_password": STAFF_PASSWORD,
        "new_password": "short",
    })
    assert response.status_code == 400
    assert "10 characters" in response.json()["detail"]


def test_the_old_password_stops_working_after_a_change(client, anon_client):
    client.post("/api/password", json={
        "current_password": STAFF_PASSWORD,
        "new_password": "a replacement password",
    })
    assert anon_client.post(
        "/api/login", json={"username": "dana", "password": STAFF_PASSWORD}
    ).status_code == 401
    assert anon_client.post(
        "/api/login", json={"username": "dana", "password": "a replacement password"}
    ).status_code == 200


# -- the security trail ----------------------------------------------------

def test_sign_ins_and_failures_are_recorded(anon_client, app):
    services = app.state.services
    anon_client.post("/api/login", json={"username": "dana", "password": "wrong"})
    sign_in(anon_client, "dana", STAFF_PASSWORD)
    anon_client.post("/api/logout")

    events = [e["event"] for e in services.accounts.recent_events()]
    assert "login.failed" in events
    assert "login.ok" in events
    assert "logout" in events


def test_the_trail_never_contains_a_password(anon_client, app):
    services = app.state.services
    anon_client.post("/api/login", json={"username": "dana", "password": STAFF_PASSWORD})
    blob = repr(services.accounts.recent_events())
    assert STAFF_PASSWORD not in blob


def test_health_is_degraded_with_no_administrator(anon_client, app):
    """A system nobody can administer is not healthy."""
    services = app.state.services
    row = services.accounts.get_by_username("alex")
    services.db.connect().execute("UPDATE users SET is_active = 0 WHERE id = ?", (row["id"],))
    body = anon_client.get("/health")
    assert body.status_code == 503
    assert body.json()["accounts"]["ok"] is False
