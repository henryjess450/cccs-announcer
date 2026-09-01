"""The chime library, and each person's choice of sound."""

from __future__ import annotations

import wave
from io import BytesIO


from app.chimes import BUILTIN_DESCRIPTIONS, BUILTIN_LABELS, BUILTIN_ORDER, ChimeLibrary
from tests.conftest import wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


# -- the library -----------------------------------------------------------

def test_every_offered_chime_actually_exists(chime_dir):
    library = ChimeLibrary(chime_dir)
    available = {chime.key for chime in library.available()}
    for key in BUILTIN_ORDER:
        assert key in available, f"{key} is offered but has no sound file"


def test_every_offered_chime_has_a_name_and_a_description():
    """Twelve sounds is too many to choose between by playing all of them."""
    for key in BUILTIN_ORDER:
        assert BUILTIN_LABELS.get(key), f"{key} has no name"
        assert BUILTIN_DESCRIPTIONS.get(key), f"{key} has no description"


def test_the_end_tone_is_not_something_staff_can_pick(chime_dir):
    """It is an output setting, not a choice."""
    library = ChimeLibrary(chime_dir)
    assert "end_tone" not in [chime.key for chime in library.available()]
    assert "end_tone" not in BUILTIN_ORDER


def test_there_are_long_attention_getting_chimes(chime_dir):
    """A single short beep is easy to talk over. Several of these need to be
    long enough to stop a corridor."""
    library = ChimeLibrary(chime_dir)
    long_ones = [c for c in library.available() if c.seconds >= 2.0]
    assert len(long_ones) >= 5, "not enough long chimes to choose from"
    assert max(c.seconds for c in library.available()) >= 3.5


def test_no_chime_is_so_long_that_it_delays_an_emergency(chime_dir):
    """Every announcement waits for its chime to finish first."""
    library = ChimeLibrary(chime_dir)
    for chime in library.available():
        assert chime.seconds <= 6.0, f"{chime.key} is {chime.seconds}s long"


def test_all_the_sounds_are_readable_audio(chime_dir):
    library = ChimeLibrary(chime_dir)
    for chime in library.available():
        with wave.open(str(chime.path), "rb") as handle:
            assert handle.getnframes() > 0


# -- listening before choosing --------------------------------------------

def test_the_list_is_offered_with_names_lengths_and_descriptions(client):
    body = client.get("/api/chimes").json()
    assert len(body["chimes"]) >= 10
    first = body["chimes"][0]
    assert first["label"] and first["description"] and first["seconds"] > 0
    assert body["default_chime"] == "two_tone_bell"
    assert body["chosen"] is None


def test_a_chime_can_be_listened_to_before_it_is_chosen(client):
    response = client.get("/api/chimes/fanfare/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(BytesIO(response.content), "rb") as handle:
        assert handle.getnframes() > 0


def test_listening_never_reaches_the_speakers(client, app):
    """Like Preview: it plays in the browser, not over the PA."""
    services = app.state.services
    before = len(services.audio.records)
    for key in ("fanfare", "westminster", "school_bell"):
        assert client.get(f"/api/chimes/{key}/audio").status_code == 200
    assert len(services.audio.records) == before
    assert services.db.count_by_state() == {}


def test_a_made_up_chime_is_refused(client):
    assert client.get("/api/chimes/airhorn/audio").status_code == 404


def test_a_chime_name_cannot_walk_out_of_the_folder(client):
    for attempt in ("..%2F..%2F..%2Fetc%2Fpasswd", "..", "a%2Fb"):
        assert client.get(f"/api/chimes/{attempt}/audio").status_code in (400, 404)


def test_listening_requires_signing_in(anon_client):
    assert anon_client.get("/api/chimes/fanfare/audio").status_code == 401


# -- choosing --------------------------------------------------------------

def test_choosing_a_sound_changes_what_that_person_announces_with(client, app):
    services = app.state.services
    assert client.post("/api/my-chime", json={"chime": "fanfare"}).json()["chime"] == "fanfare"

    client.post("/api/announcements", json={"text": "With my own sound."})
    assert wait_until(lambda: drained(services), timeout=60)

    played = [name for record in services.audio.records for name in record.files]
    assert "fanfare.wav" in played
    assert "two_tone_bell.wav" not in played


def test_one_persons_choice_does_not_change_anyone_elses(client, admin_client, app):
    services = app.state.services
    client.post("/api/my-chime", json={"chime": "school_bell"})

    client.post("/api/announcements", json={"text": "Mine."})
    assert wait_until(lambda: drained(services), timeout=60)
    admin_client.post("/api/announcements", json={"text": "Theirs."})
    assert wait_until(lambda: drained(services), timeout=60)

    chimes = [row["chime"] for row in services.db.recent(limit=10)]
    assert "school_bell" in chimes
    assert "two_tone_bell" in chimes


def test_clearing_a_choice_puts_them_back_on_the_school_default(client, app):
    services = app.state.services
    client.post("/api/my-chime", json={"chime": "fanfare"})
    assert client.post("/api/my-chime", json={"chime": None}).json()["chime"] is None

    client.post("/api/announcements", json={"text": "Back to the default."})
    assert wait_until(lambda: drained(services), timeout=60)
    assert services.db.recent(limit=1)[0]["chime"] == "two_tone_bell"


def test_a_made_up_choice_is_refused(client):
    response = client.post("/api/my-chime", json={"chime": "airhorn"})
    assert response.status_code == 400
    assert response.json()["reason"] == "no_such_chime"


def test_a_choice_cannot_walk_out_of_the_chime_folder(client, app):
    response = client.post("/api/my-chime", json={"chime": "../../../../etc/passwd"})
    assert response.status_code == 400
    assert app.state.services.accounts.get_by_username("dana")["chime"] is None


def test_choosing_requires_signing_in(anon_client):
    assert anon_client.post("/api/my-chime", json={"chime": "fanfare"}).status_code == 401


def test_the_choice_survives_signing_out_and_back_in(client, anon_client):
    from tests.conftest import STAFF_PASSWORD, sign_in
    client.post("/api/my-chime", json={"chime": "westminster"})
    client.post("/api/logout")

    user = sign_in(anon_client, "dana", STAFF_PASSWORD)
    assert user["chime"] == "westminster"
    assert anon_client.get("/api/config").json()["my_chime"] == "westminster"


# -- chosen while setting a password --------------------------------------

def test_a_sound_can_be_chosen_while_choosing_a_password(anon_client, app):
    """Where staff meet it: the screen they already have to fill in."""
    from tests.conftest import sign_in
    services = app.state.services
    services.accounts.create_user(
        username="newbie", display_name="Sam New", password="issued password here",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "newbie", "issued password here")

    response = anon_client.post("/api/password", json={
        "current_password": "issued password here",
        "new_password": "my own chosen password",
        "chime": "arrival",
    })
    assert response.status_code == 200
    assert response.json()["chime"] == "arrival"
    assert anon_client.get("/api/config").json()["my_chime"] == "arrival"


def test_a_bad_sound_does_not_block_the_password_change(anon_client, app):
    """The password is the important half. A rejected sound must say so
    without leaving them unable to sign in properly."""
    from tests.conftest import sign_in
    services = app.state.services
    services.accounts.create_user(
        username="newbie2", display_name="Sam Two", password="issued password here",
        role="staff", must_change_password=True,
    )
    sign_in(anon_client, "newbie2", "issued password here")

    response = anon_client.post("/api/password", json={
        "current_password": "issued password here",
        "new_password": "my own chosen password",
        "chime": "airhorn",
    })
    assert response.status_code == 400
    assert response.json()["reason"] == "no_such_chime"
    # The password did change, so they are not locked out of anything.
    assert anon_client.post(
        "/api/announcements", json={"text": "Works."}
    ).status_code == 201


def test_the_picker_is_on_the_password_screen(client):
    page = client.get("/").text
    assert 'id="setup-chimes"' in page
    assert "Your announcement sound" in page
    assert 'id="change-chime"' in page
