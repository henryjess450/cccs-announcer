"""First-run setup: the administrator account the system makes for itself.

The point is that nobody needs a command line to get started. The constraint is
that a machine reachable from the school network must not sit there with a
password anyone could guess.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from app.accounts import Accounts, username_problem
from app.config import load_config
from app.db import Database
from tests.conftest import sign_in


@pytest.fixture
def fresh_client(app):
    """A client against an app with no accounts at all -- lifespan included,
    which is what creates the starting administrator."""
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        yield test_client


def issued_password(app) -> str:
    """Read the password out of the file the announcer writes on first start."""
    text = app.state.services.first_login_file.read_text(encoding="utf-8")
    match = re.search(r"Password: (\S+)", text)
    assert match, text
    return match.group(1)


# -- creation --------------------------------------------------------------

def test_an_administrator_account_is_created_on_first_start(fresh_client, app):
    services = app.state.services
    assert services.accounts.count_users() == 1
    row = services.accounts.get_by_username("admin")
    assert row is not None
    assert row["role"] == "admin"
    assert row["is_bootstrap"] == 1
    assert row["must_change_password"] == 1


def test_the_password_is_written_where_the_installer_can_find_it(fresh_client, app):
    path = app.state.services.first_login_file
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Username: admin" in text
    assert "Password: " in text


def test_the_password_is_generated_not_a_fixed_default(tmp_path):
    """A shipped default password is an open door for as long as it takes
    somebody to sign in for the first time."""
    passwords = set()
    for index in range(3):
        config = dataclasses.replace(
            load_config(env_file=tmp_path / "none.env"),
            db_path=tmp_path / f"boot{index}.sqlite3",
        )
        database = Database(config.db_path)
        database.initialize()
        _, password = Accounts(database, config).ensure_bootstrap_admin()
        passwords.add(password)
    assert len(passwords) == 3
    assert all(len(p) >= 16 for p in passwords)


def test_it_is_never_created_a_second_time(client, app):
    """The `client` fixture seeds real accounts before the lifespan runs."""
    services = app.state.services
    assert services.accounts.get_by_username("admin") is None
    assert services.accounts.ensure_bootstrap_admin() is None


def test_an_explicit_password_can_be_set_for_installs_that_want_one(tmp_path):
    config = dataclasses.replace(
        load_config(env_file=tmp_path / "none.env"),
        db_path=tmp_path / "boot.sqlite3",
        bootstrap_password="a chosen bootstrap password",
        bootstrap_username="setup",
    )
    database = Database(config.db_path)
    database.initialize()
    user, password = Accounts(database, config).ensure_bootstrap_admin()
    assert user.username == "setup"
    assert password == "a chosen bootstrap password"


# -- while it is unclaimed -------------------------------------------------

def test_health_is_degraded_until_the_account_is_claimed(fresh_client):
    response = fresh_client.get("/health")
    assert response.status_code == 503
    body = response.json()["accounts"]
    assert body["ok"] is False
    assert body["setup_pending"] is True
    assert "still on the password the system issued" in body["detail"]


def test_the_sign_in_page_says_what_to_do_without_saying_the_password(fresh_client, app):
    response = fresh_client.get("/api/setup-status")
    body = response.json()
    assert body["setup_pending"] is True
    assert body["username"] == "admin"
    # The username was always guessable. The password must never be served.
    assert issued_password(app) not in response.text
    assert issued_password(app) not in fresh_client.get("/login").text


def test_the_unclaimed_account_cannot_announce(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post("/api/announcements", json={"text": "Hello everyone."})
    assert response.status_code == 403
    assert response.json()["reason"] == "password_change_required"


def test_the_unclaimed_account_cannot_manage_accounts(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    assert fresh_client.get("/api/admin/users").status_code == 403


# -- claiming it -----------------------------------------------------------

def setup_payload(app, **overrides):
    payload = {
        "username": "hjess",
        "display_name": "Henry Jess",
        "current_password": issued_password(app),
        "new_password": "my own real password",
    }
    payload.update(overrides)
    return payload


def test_setting_it_up_renames_it_and_sets_a_real_password(fresh_client, app):
    services = app.state.services
    sign_in(fresh_client, "admin", issued_password(app))

    response = fresh_client.post("/api/setup", json=setup_payload(app))
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["username"] == "hjess"
    assert user["display_name"] == "Henry Jess"
    assert user["is_admin"] is True
    assert user["is_bootstrap"] is False
    assert user["must_change_password"] is False
    assert services.accounts.setup_pending() is False


def test_the_issued_password_stops_working(fresh_client, anon_client_factory, app):
    password = issued_password(app)
    sign_in(fresh_client, "admin", password)
    fresh_client.post("/api/setup", json=setup_payload(app, current_password=password))

    other = anon_client_factory()
    assert other.post(
        "/api/login", json={"username": "admin", "password": password}
    ).status_code == 401
    assert other.post(
        "/api/login", json={"username": "hjess", "password": "my own real password"}
    ).status_code == 200


def test_the_first_login_file_is_deleted(fresh_client, app):
    services = app.state.services
    assert services.first_login_file.exists()
    sign_in(fresh_client, "admin", issued_password(app))
    fresh_client.post("/api/setup", json=setup_payload(app))
    assert not services.first_login_file.exists()


def test_they_stay_signed_in_afterwards(fresh_client, app):
    """Setting up ends every session for the account, so a fresh one has to be
    issued or the person is thrown back to the sign-in page immediately."""
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post("/api/setup", json=setup_payload(app))
    fresh_client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})

    assert fresh_client.get("/api/status").status_code == 200
    assert fresh_client.post(
        "/api/announcements", json={"text": "Now it works."}
    ).status_code == 201


def test_anyone_else_signed_in_with_the_issued_password_is_thrown_out(
    fresh_client, anon_client_factory, app
):
    """Somebody on the network may have got in before IT did."""
    password = issued_password(app)
    intruder = anon_client_factory()
    sign_in(intruder, "admin", password)
    assert intruder.get("/api/me").status_code == 200

    sign_in(fresh_client, "admin", password)
    fresh_client.post("/api/setup", json=setup_payload(app, current_password=password))

    assert intruder.get("/api/me").status_code == 401


# -- validation ------------------------------------------------------------

def test_setup_needs_the_issued_password(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post(
        "/api/setup", json=setup_payload(app, current_password="guessing")
    )
    assert response.status_code == 400
    assert response.json()["reason"] == "bad_current_password"


@pytest.mark.parametrize("username,problem", [
    ("", "required"),
    ("a", "too short"),
    ("has space", "spaces"),
    ("bad!chars", "letters, numbers"),
])
def test_username_rules(username, problem):
    message = username_problem(username)
    assert message is not None and problem in message


def test_setup_rejects_a_bad_username(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post("/api/setup", json=setup_payload(app, username="has space"))
    assert response.status_code == 400
    assert "spaces" in response.json()["detail"]


def test_setup_rejects_a_weak_password(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post("/api/setup", json=setup_payload(app, new_password="short"))
    assert response.status_code == 400
    assert "10 characters" in response.json()["detail"]


def test_setup_requires_a_full_name(fresh_client, app):
    sign_in(fresh_client, "admin", issued_password(app))
    response = fresh_client.post("/api/setup", json=setup_payload(app, display_name="  "))
    assert response.status_code == 400
    assert "full name" in response.json()["detail"]


def test_setup_cannot_be_run_twice(fresh_client, app):
    # Captured before setup runs -- the file is deleted as part of it.
    payload = setup_payload(app)
    sign_in(fresh_client, "admin", payload["current_password"])
    first = fresh_client.post("/api/setup", json=payload)
    fresh_client.headers.update({"X-CSRF-Token": first.json()["csrf_token"]})

    again = fresh_client.post("/api/setup", json=payload)
    assert again.status_code == 400
    assert again.json()["reason"] == "already_set_up"


def test_an_ordinary_account_cannot_call_setup(client):
    response = client.post("/api/setup", json={
        "username": "sneaky", "display_name": "Sneaky",
        "current_password": "x", "new_password": "a long enough password",
    })
    assert response.status_code == 400
    assert response.json()["reason"] == "already_set_up"
