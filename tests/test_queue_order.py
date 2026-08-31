"""Queue ordering, including priority insertion.

These run against the database directly -- no player, no audio -- because
ordering is a property of the claim query and should be provable on its own.
"""

from __future__ import annotations

from app.db import STATE_INTERRUPTED, STATE_PLAYING, STATE_QUEUED, Database


def make_db(tmp_path) -> Database:
    database = Database(tmp_path / "queue.sqlite3")
    database.initialize()
    return database


def add(database: Database, label: str, priority: int = 0) -> int:
    return database.enqueue(
        raw_text=label, normalized_text=label, chime="attention",
        user_name="tester", priority=priority,
    )


def drain(database: Database):
    order = []
    while True:
        item = database.claim_next()
        if item is None:
            return order
        order.append(item["raw_text"])


def test_plain_queue_is_strict_fifo(tmp_path):
    database = make_db(tmp_path)
    for label in ["a", "b", "c", "d"]:
        add(database, label)
    assert drain(database) == ["a", "b", "c", "d"]


def test_priority_jumps_ahead_of_waiting_normal_items(tmp_path):
    database = make_db(tmp_path)
    add(database, "normal-1")
    add(database, "normal-2")
    add(database, "URGENT", priority=1)
    add(database, "normal-3")
    assert drain(database) == ["URGENT", "normal-1", "normal-2", "normal-3"]


def test_priority_items_are_fifo_among_themselves(tmp_path):
    database = make_db(tmp_path)
    add(database, "normal")
    add(database, "urgent-1", priority=1)
    add(database, "urgent-2", priority=1)
    assert drain(database) == ["urgent-1", "urgent-2", "normal"]


def test_a_late_priority_item_still_beats_older_normal_ones(tmp_path):
    database = make_db(tmp_path)
    for index in range(5):
        add(database, f"normal-{index}")
    database.claim_next()  # first one is now playing
    add(database, "URGENT", priority=1)
    assert drain(database)[0] == "URGENT"


def test_claiming_marks_the_item_playing_and_stamps_a_start_time(tmp_path):
    database = make_db(tmp_path)
    item_id = add(database, "hello")
    claimed = database.claim_next()
    assert claimed["id"] == item_id
    assert claimed["state"] == STATE_PLAYING
    assert database.get(item_id)["started_at"]


def test_an_item_can_only_be_claimed_once(tmp_path):
    database = make_db(tmp_path)
    add(database, "only-one")
    assert database.claim_next() is not None
    assert database.claim_next() is None


def test_a_queued_item_can_be_cancelled_but_a_playing_one_cannot(tmp_path):
    database = make_db(tmp_path)
    queued_id = add(database, "waiting")
    assert database.cancel_queued(queued_id, "admin") is True
    assert database.get(queued_id)["stopped_by"] == "admin"

    playing_id = add(database, "in-flight")
    database.claim_next()
    assert database.cancel_queued(playing_id, "admin") is False


def test_release_puts_an_item_back_at_the_front_of_its_tier(tmp_path):
    # When the audio device is missing the item must return to the queue and
    # keep its place, not go to the back or vanish.
    database = make_db(tmp_path)
    first = add(database, "first")
    add(database, "second")
    claimed = database.claim_next()
    database.release_to_queue(claimed["id"], "speakers unavailable")
    assert database.get(first)["state"] == STATE_QUEUED
    assert drain(database) == ["first", "second"]


def test_a_crash_mid_announcement_is_recovered_as_interrupted(tmp_path):
    database = make_db(tmp_path)
    item_id = add(database, "was playing when the power went out")
    database.claim_next()
    assert database.recover_orphaned_items() == [item_id]
    row = database.get(item_id)
    assert row["state"] == STATE_INTERRUPTED
    assert "Interrupted" in row["error"]


# -- chime library --------------------------------------------------------

def test_a_chime_key_cannot_escape_the_chime_folder(tmp_path):
    """Phase 3 lets admins name chimes again; the guard has to hold now."""
    from app.chimes import ChimeLibrary

    library = ChimeLibrary(tmp_path)
    for attempted in ("../../../../etc/passwd", "..\\..\\windows\\win", "a/b", "."):
        assert library.path_for(attempted) is None
    assert library.path_for("") is None
    assert library.path_for(None) is None
