"""Announcements on a timetable, in the school's own timezone."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from app.schedules import (
    ScheduleError,
    decide,
    describe,
    format_time,
    next_occurrence,
    parse_time,
    school_zone,
)
from tests.conftest import wait_until

VANCOUVER = school_zone("America/Vancouver")


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


# -- reading what a person typed -------------------------------------------

@pytest.mark.parametrize("typed,expected", [
    ("3:10 PM", time(15, 10)), ("15:10", time(15, 10)), ("8 AM", time(8, 0)),
    ("12:00 AM", time(0, 0)), ("12:30 PM", time(12, 30)), ("08:45", time(8, 45)),
    ("3:10pm", time(15, 10)), ("3:10 p.m.", time(15, 10)),
])
def test_times_people_actually_type(typed, expected):
    assert parse_time(typed) == expected


@pytest.mark.parametrize("typed", ["", "lunchtime", "25:00", "3:70", "abc"])
def test_nonsense_times_are_refused_in_plain_language(typed):
    with pytest.raises(ScheduleError) as caught:
        parse_time(typed)
    assert "time" in str(caught.value).lower()


def test_times_are_shown_back_the_way_people_read_them():
    assert format_time(time(15, 10)) == "3:10 PM"
    assert format_time(time(0, 5)) == "12:05 AM"
    assert format_time(time(12, 0)) == "12:00 PM"


def test_the_schedule_is_described_in_one_readable_line():
    assert describe("weekdays", time(8, 45), [], None) == \
        "Every school day (Monday to Friday) at 8:45 AM"
    assert describe("weekly", time(14, 0), [0, 2], None) == \
        "Every Monday, Wednesday at 2:00 PM"
    assert describe("daily", time(7, 0), [], None) == "Every day at 7:00 AM"


# -- the part that matters: school time, not computer time -----------------

def test_a_daily_time_stays_put_across_the_spring_clock_change(config):
    """British Columbia changes its clocks twice a year. "3:10 PM every day"
    has to stay 3:10 PM on both sides of that, which means the UTC time it
    fires at must MOVE.
    """
    if VANCOUVER is None:
        pytest.skip("no timezone database on this machine")

    after = datetime(2026, 3, 7, 20, 0, tzinfo=timezone.utc)   # Sat before
    times = []
    for _ in range(4):
        after = next_occurrence(
            kind="daily", at=time(15, 10), days=[], on_date=None,
            after=after, zone=VANCOUVER,
        )
        times.append(after)

    local = [moment.astimezone(VANCOUVER) for moment in times]
    assert all(m.hour == 15 and m.minute == 10 for m in local), local

    # The UTC time is what shifts -- that is the clock change being handled.
    utc_hours = {m.astimezone(timezone.utc).hour for m in times}
    assert len(utc_hours) == 2, "the UTC time should move across the change"


def test_a_daily_time_stays_put_across_the_autumn_clock_change():
    if VANCOUVER is None:
        pytest.skip("no timezone database on this machine")
    after = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    for _ in range(5):
        after = next_occurrence(
            kind="daily", at=time(8, 45), days=[], on_date=None,
            after=after, zone=VANCOUVER,
        )
        local = after.astimezone(VANCOUVER)
        assert (local.hour, local.minute) == (8, 45)


def test_school_days_skip_the_weekend():
    if VANCOUVER is None:
        pytest.skip("no timezone database on this machine")
    friday = datetime(2026, 3, 20, 23, 30, tzinfo=timezone.utc)   # Fri afternoon PT
    nxt = next_occurrence(kind="weekdays", at=time(8, 45), days=[], on_date=None,
                          after=friday, zone=VANCOUVER)
    assert nxt.astimezone(VANCOUVER).weekday() == 0, "should jump to Monday"


def test_chosen_days_are_honoured():
    if VANCOUVER is None:
        pytest.skip("no timezone database on this machine")
    monday = datetime(2026, 3, 16, 20, 0, tzinfo=timezone.utc)
    nxt = next_occurrence(kind="weekly", at=time(14, 0), days=[2, 4], on_date=None,
                          after=monday, zone=VANCOUVER)
    assert nxt.astimezone(VANCOUVER).weekday() in (2, 4)


def test_a_one_off_in_the_past_never_runs_again():
    assert next_occurrence(
        kind="once", at=time(9, 0), days=[], on_date="2020-01-01",
        after=datetime.now(timezone.utc), zone=VANCOUVER,
    ) is None


def test_a_schedule_that_has_ended_stops():
    if VANCOUVER is None:
        pytest.skip("no timezone database on this machine")
    assert next_occurrence(
        kind="daily", at=time(9, 0), days=[], on_date=None,
        after=datetime(2026, 7, 1, tzinfo=timezone.utc), zone=VANCOUVER,
        ends_on="2026-06-30",
    ) is None


# -- missed runs -----------------------------------------------------------

def test_something_slightly_late_still_goes_out():
    """The player was mid-announcement, or the machine was busy."""
    now = datetime.now(timezone.utc)
    assert decide(now - timedelta(minutes=3), now, grace_minutes=10).fire is True


def test_a_weekend_of_missed_announcements_is_not_replayed_on_monday():
    """The worst possible Monday morning: the whole backlog at 8 AM, at the
    wrong times, to a building that has moved on."""
    now = datetime.now(timezone.utc)
    verdict = decide(now - timedelta(days=2), now, grace_minutes=10)
    assert verdict.fire is False
    assert "switched off" in verdict.skipped_reason


def test_something_not_yet_due_does_not_fire():
    now = datetime.now(timezone.utc)
    assert decide(now + timedelta(minutes=5), now, grace_minutes=10).fire is False


# -- through the API -------------------------------------------------------

def test_scheduling_an_announcement(client):
    response = client.post("/api/schedules", json={
        "text": "Buses are now loading.",
        "kind": "weekdays", "at_time": "15:10",
    })
    assert response.status_code == 201
    schedule = response.json()["schedule"]
    assert schedule["when"] == "Every school day (Monday to Friday) at 3:10 PM"
    assert schedule["at_time_label"] == "3:10 PM"
    assert schedule["next_run_label"]
    assert schedule["enabled"] is True


def test_a_schedule_that_could_never_run_is_refused(client):
    response = client.post("/api/schedules", json={
        "text": "Too late.", "kind": "once",
        "at_time": "09:00", "on_date": "2020-01-01",
    })
    assert response.status_code == 400
    assert response.json()["reason"] == "never_runs"


def test_certain_days_needs_the_days(client):
    response = client.post("/api/schedules", json={
        "text": "Which days?", "kind": "weekly", "at_time": "09:00", "days": [],
    })
    assert response.status_code == 400
    assert "days of the week" in response.json()["detail"]


def test_an_empty_schedule_is_refused(client):
    assert client.post("/api/schedules", json={
        "text": "   ", "kind": "daily", "at_time": "09:00",
    }).status_code == 400


def test_a_scheduled_announcement_obeys_the_character_limit(client):
    assert client.post("/api/schedules", json={
        "text": "x" * 501, "kind": "daily", "at_time": "09:00",
    }).status_code == 400


def test_staff_see_only_their_own_schedules(client, admin_client):
    client.post("/api/schedules", json={
        "text": "Dana's.", "kind": "daily", "at_time": "09:00"})
    admin_client.post("/api/schedules", json={
        "text": "Alex's.", "kind": "daily", "at_time": "09:30"})

    mine = client.get("/api/schedules").json()
    assert mine["scope"] == "you"
    assert [s["text"] for s in mine["schedules"]] == ["Dana's."]

    everyone = admin_client.get("/api/schedules").json()
    assert everyone["scope"] == "everyone"
    assert len(everyone["schedules"]) == 2


def test_staff_cannot_touch_someone_elses_schedule(client, admin_client):
    theirs = admin_client.post("/api/schedules", json={
        "text": "Not yours.", "kind": "daily", "at_time": "09:00"}).json()["schedule"]

    assert client.post(f"/api/schedules/{theirs['id']}/delete").status_code == 403
    assert client.post(
        f"/api/schedules/{theirs['id']}/enabled?enabled=false").status_code == 403


def test_an_edited_schedule_keeps_whose_it_is(client, admin_client):
    """An administrator fixing a typo must not become the author."""
    hers = client.post("/api/schedules", json={
        "text": "Buses.", "kind": "daily", "at_time": "09:00"}).json()["schedule"]

    admin_client.post(f"/api/schedules/{hers['id']}", json={
        "text": "Buses are now loading.", "kind": "daily", "at_time": "09:00"})

    updated = admin_client.get("/api/schedules").json()["schedules"][0]
    assert updated["text"] == "Buses are now loading."
    assert updated["user_name"] == "Dana Rowe"


def test_pausing_stops_it_being_due(client):
    made = client.post("/api/schedules", json={
        "text": "Pause me.", "kind": "daily", "at_time": "09:00"}).json()["schedule"]

    paused = client.post(
        f"/api/schedules/{made['id']}/enabled?enabled=false").json()["schedule"]
    assert paused["enabled"] is False
    assert paused["next_run_label"] is None

    resumed = client.post(
        f"/api/schedules/{made['id']}/enabled?enabled=true").json()["schedule"]
    assert resumed["enabled"] is True
    assert resumed["next_run_label"]


def test_scheduling_requires_signing_in(anon_client):
    assert anon_client.get("/api/schedules").status_code == 401
    assert anon_client.post("/api/schedules", json={
        "text": "Hello.", "kind": "daily", "at_time": "09:00"}).status_code == 401


# -- actually firing -------------------------------------------------------

def test_a_due_schedule_is_announced(client, app):
    services = app.state.services
    made = client.post("/api/schedules", json={
        "text": "Buses are now loading.", "kind": "daily", "at_time": "09:00",
    }).json()["schedule"]

    # Bring it forward to a moment ago.
    from app.db import utcnow
    due = (utcnow() - timedelta(minutes=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    services.db.update_schedule(made["id"], next_run_at=due)

    assert services.run_due_schedules() == 1
    assert wait_until(lambda: drained(services), timeout=60)

    latest = services.db.recent(limit=1)[0]
    assert latest["kind"] == "scheduled"
    assert latest["user_name"] == "Dana Rowe"
    assert "Buses are now loading" in latest["normalized_text"]
    assert latest["state"] == "done"


def test_a_fired_schedule_moves_on_to_its_next_time(client, app):
    services = app.state.services
    made = client.post("/api/schedules", json={
        "text": "Daily notice.", "kind": "daily", "at_time": "09:00",
    }).json()["schedule"]

    from app.db import utcnow
    due = (utcnow() - timedelta(minutes=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    services.db.update_schedule(made["id"], next_run_at=due)
    services.run_due_schedules()

    after = services.db.get_schedule(made["id"])
    assert after["next_run_at"] > due
    assert after["last_result"] == "sent"
    assert wait_until(lambda: drained(services), timeout=60)


def test_a_long_missed_schedule_is_skipped_and_says_so(client, app):
    services = app.state.services
    made = client.post("/api/schedules", json={
        "text": "Missed while the machine was off.", "kind": "daily",
        "at_time": "09:00",
    }).json()["schedule"]

    from app.db import utcnow
    stale = (utcnow() - timedelta(days=2)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    services.db.update_schedule(made["id"], next_run_at=stale)

    assert services.run_due_schedules() == 0
    after = services.db.get_schedule(made["id"])
    assert "switched off" in after["last_result"]
    assert after["enabled"] == 1          # still live for tomorrow
    assert after["next_run_at"] > stale


def test_a_one_off_turns_itself_off_after_it_runs(client, app):
    services = app.state.services
    from app.db import utcnow
    tomorrow = (utcnow() + timedelta(days=1)).date().isoformat()
    made = client.post("/api/schedules", json={
        "text": "Just this once.", "kind": "once",
        "at_time": "09:00", "on_date": tomorrow,
    }).json()["schedule"]

    due = (utcnow() - timedelta(minutes=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    services.db.update_schedule(made["id"], next_run_at=due)
    assert services.run_due_schedules() == 1

    after = services.db.get_schedule(made["id"])
    assert after["enabled"] == 0
    assert after["next_run_at"] is None
    assert wait_until(lambda: drained(services), timeout=60)


def test_scheduled_announcements_never_overlap_a_live_one(client, app):
    """They go through the same queue as everything else."""
    services = app.state.services
    from app.db import utcnow

    for index in range(3):
        made = client.post("/api/schedules", json={
            "text": f"Scheduled notice number {index}.", "kind": "daily",
            "at_time": "09:00",
        }).json()["schedule"]
        due = (utcnow() - timedelta(seconds=30)).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        services.db.update_schedule(made["id"], next_run_at=due)

    client.post("/api/announcements", json={"text": "A live one at the same moment."})
    assert services.run_due_schedules() == 3
    assert wait_until(lambda: drained(services), timeout=90)

    assert services.audio.overlap_detected is False
    assert services.audio.overlapping_pairs() == []


def test_a_one_off_cannot_repeat_even_if_its_stored_time_is_wrong(client, app):
    """A wrong clock or a hand-edited row must not turn a one-off into a
    daily. Nobody is expecting the second one."""
    services = app.state.services
    from app.db import utcnow

    far_future = (utcnow() + timedelta(days=30)).date().isoformat()
    made = client.post("/api/schedules", json={
        "text": "Only once, ever.", "kind": "once",
        "at_time": "09:00", "on_date": far_future,
    }).json()["schedule"]

    # Its date is a month away, but its next run says "a minute ago".
    due = (utcnow() - timedelta(minutes=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    services.db.update_schedule(made["id"], next_run_at=due)

    assert services.run_due_schedules() == 1
    after = services.db.get_schedule(made["id"])
    assert after["enabled"] == 0
    assert after["next_run_at"] is None
    assert services.run_due_schedules() == 0
    assert wait_until(lambda: drained(services), timeout=60)
