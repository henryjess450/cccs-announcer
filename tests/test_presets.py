"""Ready-made announcements, including the drills."""

from __future__ import annotations

import pytest

from app.presets import SEED_PRESETS, fill, label_for, slots_in
from tests.conftest import wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


# -- filling in the slots --------------------------------------------------

def test_slots_are_found_in_order_without_repeats():
    assert slots_in("Bus {number} has arrived. Bus {number}.") == ["number"]
    assert slots_in("{name} to {room} at {time}") == ["name", "room", "time"]
    assert slots_in("No slots here") == []


def test_a_slot_used_twice_is_filled_both_times():
    assert fill("Bus number {number}. Bus number {number}.", {"number": "12"}) == \
        "Bus number 12. Bus number 12."


def test_an_empty_slot_names_itself():
    """So the person is told which box to fill, not handed half a sentence."""
    with pytest.raises(KeyError) as caught:
        fill("{name}, report to the office.", {"name": "   "})
    assert caught.value.args[0] == "name"


def test_slot_labels_read_like_english():
    assert label_for("number") == "Bus number"
    assert label_for("grade_level") == "Grade level"


# -- what a new school starts with ----------------------------------------

def test_a_new_school_gets_presets_without_being_asked(client, admin_client):
    staff_sees = [p["title"] for p in client.get("/api/presets").json()["presets"]]
    assert "Bus has arrived" in staff_sees
    assert "Report to the office" in staff_sees

    # The drills are there too, for whoever is allowed to run them.
    admin_sees = [p["title"] for p in admin_client.get("/api/presets").json()["presets"]]
    assert any("Fire drill" in t for t in admin_sees)


def test_seeding_never_overwrites_what_a_school_has_changed(app):
    from app.presets import seed_if_empty
    services = app.state.services
    before = len(services.db.presets(include_disabled=True))
    assert seed_if_empty(services.db) == 0
    assert len(services.db.presets(include_disabled=True)) == before


# -- the drills ------------------------------------------------------------

DRILLS = [p for p in SEED_PRESETS if p["is_drill"]]


def test_every_drill_says_it_is_a_practice_at_the_start_and_the_end():
    """Somebody walking into a corridor halfway through has to hear the word
    before they act on it -- and again before they stop."""
    for drill in DRILLS:
        body = drill["body"].lower()
        assert body.startswith(("this is a practice", "all clear")), drill["title"]
        opening = body[:80]
        closing = body[-80:]
        if not body.startswith("all clear"):
            assert "practice" in opening, drill["title"]
            assert "practice" in closing, drill["title"]


def test_the_drills_cover_what_a_school_actually_practises():
    titles = " ".join(d["title"].lower() for d in DRILLS)
    for expected in ("fire", "earthquake", "lockdown", "hold and secure", "all clear"):
        assert expected in titles, f"no drill for {expected}"


def test_there_is_an_all_clear():
    """A drill without an all-clear leaves a building waiting."""
    assert any("all clear" in d["title"].lower() for d in DRILLS)


def test_drills_are_priority_so_they_jump_the_queue():
    for drill in DRILLS:
        assert drill["priority"] == 1, drill["title"]


def test_drills_are_administrators_only_by_default(client, app):
    """A practice lockdown should come from the office, not from whoever is
    nearest a keyboard."""
    visible = [p["title"] for p in client.get("/api/presets").json()["presets"]]
    assert not any("PRACTICE" in title for title in visible)


def test_administrators_can_see_the_drills(admin_client):
    visible = [p["title"] for p in admin_client.get("/api/presets").json()["presets"]]
    assert any("Fire drill" in title for title in visible)


def test_staff_are_refused_a_drill_even_by_its_number(client, admin_client):
    drill = next(p for p in admin_client.get("/api/presets").json()["presets"]
                 if p["is_drill"])
    response = client.post(f"/api/presets/{drill['id']}/use", json={"values": {}})
    assert response.status_code == 403
    assert response.json()["reason"] == "not_admin"


def test_announcing_a_drill_is_recorded_in_the_security_trail(admin_client, app):
    """A drill is rehearsed by the whole school. It belongs in more than the
    announcement log."""
    services = app.state.services
    drill = next(p for p in admin_client.get("/api/presets").json()["presets"]
                 if "Fire drill" in p["title"])

    assert admin_client.post(
        f"/api/presets/{drill['id']}/use", json={"values": {}}
    ).status_code == 201
    assert wait_until(lambda: drained(services), timeout=90)

    events = [e for e in services.accounts.recent_events()
              if e["event"] == "drill.announced"]
    assert events and "Fire drill" in events[0]["detail"]
    assert events[0]["username"] == "alex"


def test_a_drill_announcement_reaches_the_speakers_intact(admin_client, app):
    services = app.state.services
    drill = next(p for p in admin_client.get("/api/presets").json()["presets"]
                 if "Earthquake" in p["title"])
    admin_client.post(f"/api/presets/{drill['id']}/use", json={"values": {}})
    assert wait_until(lambda: drained(services), timeout=90)

    row = services.db.recent(limit=1)[0]
    assert row["kind"] == "drill"
    assert row["priority"] == 1
    assert "practice" in row["normalized_text"].lower()
    assert "Drop, cover, and hold on" in row["normalized_text"]
    assert row["state"] == "done"


# -- using an ordinary preset ---------------------------------------------

def test_using_a_preset_sends_the_filled_in_announcement(client, app):
    services = app.state.services
    preset = next(p for p in client.get("/api/presets").json()["presets"]
                  if p["title"] == "Bus has arrived")
    assert [s["name"] for s in preset["slots"]] == ["number"]

    response = client.post(f"/api/presets/{preset['id']}/use",
                           json={"values": {"number": "12"}})
    assert response.status_code == 201
    assert response.json()["normalized"] == \
        "Bus number twelve has arrived. Bus number twelve."
    assert wait_until(lambda: drained(services), timeout=60)
    assert services.db.recent(limit=1)[0]["user_name"] == "Dana Rowe"


def test_a_missing_slot_says_which_box_to_fill(client):
    preset = next(p for p in client.get("/api/presets").json()["presets"]
                  if p["title"] == "Bus has arrived")
    response = client.post(f"/api/presets/{preset['id']}/use", json={"values": {}})
    assert response.status_code == 400
    assert "bus number" in response.json()["detail"].lower()


def test_a_preset_still_obeys_the_rate_limit(client, app):
    """A shortcut for typing, not a way around anything."""
    services = app.state.services
    preset = next(p for p in client.get("/api/presets").json()["presets"]
                  if p["title"] == "Bus has arrived")
    for index in range(services.rate_limiter.limit()):
        assert client.post(f"/api/presets/{preset['id']}/use",
                           json={"values": {"number": str(index)}}).status_code == 201
    assert client.post(f"/api/presets/{preset['id']}/use",
                       json={"values": {"number": "99"}}).status_code == 429
    assert wait_until(lambda: drained(services), timeout=90)


def test_a_preset_uses_the_senders_own_sound_unless_it_sets_one(client, app):
    services = app.state.services
    client.post("/api/my-settings", json={"chime": "marimba"})
    preset = next(p for p in client.get("/api/presets").json()["presets"]
                  if p["title"] == "Bus has arrived")
    client.post(f"/api/presets/{preset['id']}/use", json={"values": {"number": "3"}})
    assert wait_until(lambda: drained(services), timeout=60)
    assert services.db.recent(limit=1)[0]["chime"] == "marimba"


def test_a_drill_uses_its_own_sound_whatever_the_sender_picked(admin_client, app):
    services = app.state.services
    admin_client.post("/api/my-settings", json={"chime": "marimba"})
    drill = next(p for p in admin_client.get("/api/presets").json()["presets"]
                 if "Lockdown" in p["title"])
    admin_client.post(f"/api/presets/{drill['id']}/use", json={"values": {}})
    assert wait_until(lambda: drained(services), timeout=90)
    assert services.db.recent(limit=1)[0]["chime"] == "alarm_pattern"


def test_presets_require_signing_in(anon_client):
    assert anon_client.get("/api/presets").status_code == 401
    assert anon_client.post("/api/presets/1/use", json={"values": {}}).status_code == 401


# -- managing them ---------------------------------------------------------

def test_staff_cannot_manage_presets(client):
    assert client.post("/api/admin/presets", json={
        "title": "Mine", "body": "Hello."}).status_code == 403


def test_an_administrator_can_add_one(admin_client):
    created = admin_client.post("/api/admin/presets", json={
        "title": "Assembly", "body": "Assembly in the gym at {time}.",
    })
    assert created.status_code == 201
    assert [s["name"] for s in created.json()["preset"]["slots"]] == ["time"]


def test_a_new_drill_is_made_administrators_only_automatically(admin_client):
    """Marking something a drill and leaving it open to everyone is a mistake
    nobody makes on purpose."""
    created = admin_client.post("/api/admin/presets", json={
        "title": "PRACTICE — Bus evacuation", "body": "This is a practice.",
        "is_drill": True, "admin_only": False,
    }).json()["preset"]
    assert created["is_drill"] is True
    assert created["admin_only"] is True
