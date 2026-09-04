"""Who is allowed to do what.

The rule that matters: a teacher can stop their own announcement, and an
administrator can stop anyone's. Nobody else can silence somebody else.
"""

from __future__ import annotations

import pytest

from app.db import STATE_DONE
from tests.conftest import wait_until


# Just under the 500-character limit, so the announcement plays long enough
# for a Stop request to land while it is still going.
LONG = ("Attention staff and students. " * 17)[:495]


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


# -- stopping --------------------------------------------------------------

def test_staff_can_stop_their_own_announcement(client, app):
    services = app.state.services
    sent = client.post("/api/announcements", json={"text": LONG}).json()
    assert wait_until(lambda: services.player.current_id == sent["id"], timeout=10)

    response = client.post(f"/api/announcements/{sent['id']}/stop", json={})
    assert response.json()["stopped"] is True
    assert wait_until(lambda: services.db.get(sent["id"])["state"] == "stopped", timeout=10)
    assert services.db.get(sent["id"])["stopped_by"] == "Dana Rowe"


def test_staff_cannot_stop_someone_elses_announcement(client, admin_client, app):
    services = app.state.services
    theirs = admin_client.post("/api/announcements", json={"text": LONG}).json()
    assert wait_until(lambda: services.player.current_id == theirs["id"], timeout=10)

    response = client.post(f"/api/announcements/{theirs['id']}/stop", json={})
    assert response.status_code == 403
    assert response.json()["reason"] == "not_yours"
    assert "own announcements" in response.json()["detail"]

    # It kept playing.
    assert wait_until(lambda: services.db.get(theirs["id"])["state"] == STATE_DONE, timeout=60)
    assert services.db.get(theirs["id"])["stopped_by"] is None


def test_an_administrator_can_stop_anyones_announcement(client, admin_client, app):
    services = app.state.services
    theirs = client.post("/api/announcements", json={"text": LONG}).json()
    assert wait_until(lambda: services.player.current_id == theirs["id"], timeout=10)

    assert admin_client.post(
        f"/api/announcements/{theirs['id']}/stop", json={}
    ).json()["stopped"] is True
    assert wait_until(lambda: services.db.get(theirs["id"])["state"] == "stopped", timeout=10)
    assert services.db.get(theirs["id"])["stopped_by"] == "Alex Vance"


def test_the_panic_stop_is_admin_only(client, admin_client, app):
    services = app.state.services
    assert client.post("/api/stop", json={}).status_code == 403

    sent = client.post("/api/announcements", json={"text": LONG}).json()
    assert wait_until(lambda: services.player.current_id == sent["id"], timeout=10)
    assert admin_client.post("/api/stop", json={}).json()["stopped"] is True
    assert wait_until(lambda: drained(services), timeout=30)


def test_stopping_is_recorded_against_the_person_who_did_it(client, admin_client, app):
    services = app.state.services
    sent = client.post("/api/announcements", json={"text": LONG}).json()
    assert wait_until(lambda: services.player.current_id == sent["id"], timeout=10)
    admin_client.post(f"/api/announcements/{sent['id']}/stop", json={})
    assert wait_until(lambda: drained(services), timeout=30)

    events = services.accounts.recent_events()
    stop_events = [e for e in events if e["event"] == "announcement.stopped"]
    assert stop_events and stop_events[0]["username"] == "alex"


# -- admin endpoints -------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"username": "x", "display_name": "X"}),
    ("POST", "/api/admin/users/1", {"role": "admin"}),
    ("POST", "/api/admin/users/1/reset-password", {}),
    ("POST", "/api/admin/users/1/unlock", {}),
    ("GET", "/api/admin/security-events", None),
])
def test_staff_are_refused_every_admin_endpoint(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json()["reason"] == "not_admin"


def test_the_admin_page_is_not_served_to_staff(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_the_admin_page_is_served_to_administrators(admin_client):
    assert "Staff accounts" in admin_client.get("/admin").text


# -- account management ----------------------------------------------------

def test_an_administrator_can_create_an_account(admin_client, anon_client):
    created = admin_client.post("/api/admin/users", json={
        "username": "pnewman", "display_name": "Priya Newman", "role": "staff",
    })
    assert created.status_code == 201
    body = created.json()
    assert body["user"]["username"] == "pnewman"
    # Staff keep the password they are handed; nothing blocks them first.
    assert body["user"]["must_change_password"] is False
    # The issued password is returned exactly once, here.
    assert len(body["password"]) >= 12

    assert anon_client.post("/api/login", json={
        "username": "pnewman", "password": body["password"],
    }).status_code == 200


def test_duplicate_usernames_are_refused(admin_client):
    response = admin_client.post("/api/admin/users", json={
        "username": "dana", "display_name": "Someone Else",
    })
    assert response.status_code == 400
    assert "already an account" in response.json()["detail"]


def test_resetting_a_password_signs_that_person_out(client, admin_client, app):
    services = app.state.services
    assert client.get("/api/status").status_code == 200

    row = services.accounts.get_by_username("dana")
    reset = admin_client.post(f"/api/admin/users/{row['id']}/reset-password", json={})
    assert reset.status_code == 200

    assert client.get("/api/status").status_code == 401


def test_the_last_administrator_cannot_be_removed(admin_client, app):
    """Locking everyone out of administration is not a recoverable mistake."""
    services = app.state.services
    alex = services.accounts.get_by_username("alex")

    with pytest.raises(ValueError):
        services.accounts.set_role(int(alex["id"]), "staff")
    with pytest.raises(ValueError):
        services.accounts.set_active(int(alex["id"]), False)


def test_an_administrator_cannot_turn_off_their_own_account(admin_client, app):
    services = app.state.services
    dana = services.accounts.get_by_username("dana")
    services.accounts.set_role(int(dana["id"]), "admin")   # now two admins

    alex = services.accounts.get_by_username("alex")
    response = admin_client.post(f"/api/admin/users/{alex['id']}", json={"is_active": False})
    assert response.status_code == 400
    assert "your own account" in response.json()["detail"]


# -- the audit log ---------------------------------------------------------

def test_staff_see_only_their_own_announcements(client, admin_client, app):
    services = app.state.services
    client.post("/api/announcements", json={"text": "From Dana."})
    admin_client.post("/api/announcements", json={"text": "From Alex."})
    assert wait_until(lambda: drained(services), timeout=60)

    mine = client.get("/api/announcements").json()
    assert mine["scope"] == "you"
    assert {row["user_name"] for row in mine["announcements"]} == {"Dana Rowe"}


def test_administrators_see_everyones_announcements(client, admin_client, app):
    services = app.state.services
    client.post("/api/announcements", json={"text": "From Dana."})
    admin_client.post("/api/announcements", json={"text": "From Alex."})
    assert wait_until(lambda: drained(services), timeout=60)

    everyone = admin_client.get("/api/announcements").json()
    assert everyone["scope"] == "everyone"
    assert {row["user_name"] for row in everyone["announcements"]} == {"Dana Rowe", "Alex Vance"}


def test_the_log_keeps_the_raw_text_and_the_spoken_text(client, app):
    services = app.state.services
    client.post("/api/announcements", json={"text": "Bus 12 at 2:15 in Rm 204."})
    assert wait_until(lambda: drained(services), timeout=60)

    entry = client.get("/api/announcements").json()["announcements"][0]
    assert entry["raw_text"] == "Bus 12 at 2:15 in Rm 204."
    assert entry["normalized_text"] == "Bus number twelve at two fifteen in room two oh four."
    assert entry["user_name"] == "Dana Rowe"
    assert entry["state"] == STATE_DONE
    assert entry["duration_seconds"] > 0


# -- clearing the log ------------------------------------------------------

def test_an_administrator_can_clear_the_whole_log(admin_client, app):
    services = app.state.services
    admin_client.post("/api/announcements", json={"text": "One."})
    admin_client.post("/api/announcements", json={"text": "Two."})
    assert wait_until(lambda: drained(services), timeout=60)
    assert len(admin_client.get("/api/announcements").json()["announcements"]) == 2

    response = admin_client.post("/api/admin/announcements/purge", json={})
    assert response.status_code == 200
    assert response.json()["removed"] == 2
    assert admin_client.get("/api/announcements").json()["announcements"] == []


def test_clearing_never_removes_something_still_waiting(admin_client, app):
    """Deleting a row out from under the player would lose an announcement
    somebody is standing there waiting to hear."""
    services = app.state.services
    services.audio.available = False          # nothing can drain
    held = admin_client.post("/api/announcements", json={"text": "Still waiting."}).json()

    removed = admin_client.post("/api/admin/announcements/purge", json={}).json()["removed"]
    assert removed == 0
    assert services.db.get(held["id"]) is not None


def test_clearing_only_old_entries_keeps_the_recent_ones(admin_client, app):
    services = app.state.services
    admin_client.post("/api/announcements", json={"text": "Today."})
    assert wait_until(lambda: drained(services), timeout=60)

    # Nothing here is older than a day, so nothing should go.
    assert admin_client.post(
        "/api/admin/announcements/purge", json={"older_than_days": 1}
    ).json()["removed"] == 0
    assert len(admin_client.get("/api/announcements").json()["announcements"]) == 1

    # Backdate it and try again.
    services.db.connect().execute(
        "UPDATE announcements SET created_at = '2020-01-01T00:00:00Z'"
    )
    assert admin_client.post(
        "/api/admin/announcements/purge", json={"older_than_days": 1}
    ).json()["removed"] == 1


def test_staff_cannot_clear_the_log(client):
    """The log is what makes every announcement attributable. Someone who
    misused it must not be able to erase the evidence."""
    response = client.post("/api/admin/announcements/purge", json={})
    assert response.status_code == 403
    assert response.json()["reason"] == "not_admin"


def test_clearing_the_log_is_itself_recorded(admin_client, app):
    services = app.state.services
    admin_client.post("/api/announcements", json={"text": "Will be cleared."})
    assert wait_until(lambda: drained(services), timeout=60)
    admin_client.post("/api/admin/announcements/purge", json={})

    events = [e for e in services.accounts.recent_events() if e["event"] == "log.cleared"]
    assert events, "clearing the audit log left no trace"
    assert events[0]["username"] == "alex"
    assert "1 announcement" in events[0]["detail"]
    assert "everything" in events[0]["detail"]


def test_a_negative_range_is_refused(admin_client):
    response = admin_client.post(
        "/api/admin/announcements/purge", json={"older_than_days": -5}
    )
    assert response.status_code == 400


def test_the_compose_page_clear_only_hides(client):
    """The Clear on the compose page must not be able to delete anything --
    it is a per-computer view setting, not an admin action."""
    page = client.get("/").text
    assert 'id="clear-history"' in page
    script = client.get("/static/app.js").text
    assert "localStorage" in script
    # It must not call any delete or purge endpoint.
    assert "purge" not in script
