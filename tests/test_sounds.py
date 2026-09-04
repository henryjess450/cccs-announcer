"""Sound clips played over the PA instead of speech."""

from __future__ import annotations

import numpy as np
import pytest

from app.audio.wavio import write_wav
from app.sounds import SoundError, SoundLibrary, safe_title
from tests.conftest import wait_until


def drained(services) -> bool:
    counts = services.db.count_by_state()
    return counts.get("queued", 0) == 0 and counts.get("playing", 0) == 0


def make_wav(path, seconds=2.0, rate=44100):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False, dtype=np.float32)
    tone = (np.sin(2 * np.pi * 440 * t) * 0.3).reshape(-1, 1)
    write_wav(path, tone, rate)
    return path.read_bytes()


# -- the library -----------------------------------------------------------

def test_a_wav_is_stored_and_measured(tmp_path):
    library = SoundLibrary(tmp_path / "sounds")
    name, seconds = library.add_bytes(make_wav(tmp_path / "in.wav", 2.0), "siren.wav")
    assert name.startswith("sound-") and name.endswith(".wav")
    assert 1.9 < seconds < 2.1
    assert library.path_for(name) is not None


def test_a_clip_that_would_hold_up_the_queue_is_refused(tmp_path):
    """Everything else waits behind it, including an emergency."""
    library = SoundLibrary(tmp_path / "sounds", max_seconds=1.0)
    with pytest.raises(SoundError) as caught:
        library.add_bytes(make_wav(tmp_path / "long.wav", 3.0), "long.wav")
    assert "minutes" in caught.value.message


def test_an_enormous_file_is_refused_before_it_is_written(tmp_path):
    library = SoundLibrary(tmp_path / "sounds", max_bytes=1024)
    with pytest.raises(SoundError) as caught:
        library.add_bytes(make_wav(tmp_path / "big.wav", 2.0), "big.wav")
    assert "too big" in caught.value.message
    assert list((tmp_path / "sounds").glob("sound-*")) == []


def test_something_that_is_not_audio_is_refused(tmp_path):
    library = SoundLibrary(tmp_path / "sounds")
    with pytest.raises(SoundError):
        library.add_bytes(b"#!/bin/sh\nrm -rf /", "payload.sh")


def test_an_empty_file_is_refused(tmp_path):
    library = SoundLibrary(tmp_path / "sounds")
    with pytest.raises(SoundError):
        library.add_bytes(b"", "nothing.wav")


@pytest.mark.parametrize("name", [
    "../../../../etc/passwd", "..\\..\\windows\\win.ini", "a/b.wav", ".hidden", "",
])
def test_a_stored_name_can_never_point_outside_the_folder(tmp_path, name):
    """Filenames come from the database, and a database is not a trust
    boundary -- one bad row must not read a file elsewhere on the machine."""
    library = SoundLibrary(tmp_path / "sounds")
    assert library.path_for(name) is None


def test_titles_are_cleaned_up():
    assert safe_title("  Air   raid \x00siren ") == "Air raid siren"
    assert safe_title("") == "Sound"


def test_a_link_needs_the_extra_programs(tmp_path):
    library = SoundLibrary(tmp_path / "sounds", ytdlp="definitely-not-installed")
    with pytest.raises(SoundError) as caught:
        library.add_from_link("https://example.com/watch?v=abc")
    assert "Tell IT" in caught.value.message
    assert "yt-dlp" in caught.value.detail


def test_something_that_is_not_a_link_is_refused(tmp_path):
    library = SoundLibrary(tmp_path / "sounds")
    with pytest.raises(SoundError) as caught:
        library.add_from_link("not a link at all")
    assert "link" in caught.value.message


# -- through the API -------------------------------------------------------

def upload(admin_client, tmp_path, title="Air raid siren", seconds=2.0):
    data = make_wav(tmp_path / "upload.wav", seconds)
    return admin_client.post(
        "/api/admin/sounds",
        files={"file": ("siren.wav", data, "audio/wav")},
        data={"title": title},
    )


def test_an_administrator_can_add_a_sound(admin_client, tmp_path):
    response = upload(admin_client, tmp_path)
    assert response.status_code == 201
    sound = response.json()["sound"]
    assert sound["title"] == "Air raid siren"
    assert 1.9 < sound["seconds"] < 2.1
    assert sound["added_by"] == "Alex Vance"


def test_staff_cannot_add_sounds(client, tmp_path):
    data = make_wav(tmp_path / "upload.wav")
    assert client.post(
        "/api/admin/sounds",
        files={"file": ("siren.wav", data, "audio/wav")},
        data={"title": "Mine"},
    ).status_code == 403


def test_a_sound_can_be_listened_to_before_it_is_played(admin_client, tmp_path):
    sound = upload(admin_client, tmp_path).json()["sound"]
    response = admin_client.get(f"/api/sounds/{sound['id']}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"


def test_listening_does_not_reach_the_speakers(admin_client, app, tmp_path):
    services = app.state.services
    sound = upload(admin_client, tmp_path).json()["sound"]
    before = len(services.audio.records)
    admin_client.get(f"/api/sounds/{sound['id']}/audio")
    assert len(services.audio.records) == before


def test_playing_a_sound_puts_the_file_through_the_speakers(admin_client, app, tmp_path):
    services = app.state.services
    sound = upload(admin_client, tmp_path).json()["sound"]

    assert admin_client.post(f"/api/sounds/{sound['id']}/play").status_code == 201
    assert wait_until(lambda: drained(services), timeout=60)

    row = services.db.recent(limit=1)[0]
    assert row["kind"] == "sound"
    assert row["state"] == "done"
    assert row["sound_file"]
    played = [name for record in services.audio.records for name in record.files]
    assert row["sound_file"] in played
    # Nothing was synthesised for it.
    assert not any(name.startswith("speech-") for name in played)


def test_a_sound_is_recorded_against_whoever_played_it(client, admin_client, app, tmp_path):
    services = app.state.services
    sound = upload(admin_client, tmp_path).json()["sound"]
    client.post(f"/api/sounds/{sound['id']}/play")
    assert wait_until(lambda: drained(services), timeout=60)
    assert services.db.recent(limit=1)[0]["user_name"] == "Dana Rowe"


def test_a_sound_goes_through_the_queue_like_everything_else(admin_client, app, tmp_path):
    """So a clip can never talk over an announcement."""
    services = app.state.services
    sound = upload(admin_client, tmp_path).json()["sound"]

    admin_client.post("/api/announcements", json={"text": "A live announcement."})
    admin_client.post(f"/api/sounds/{sound['id']}/play")
    admin_client.post("/api/announcements", json={"text": "Another one."})
    assert wait_until(lambda: drained(services), timeout=90)

    assert services.audio.overlap_detected is False
    assert services.audio.overlapping_pairs() == []


def test_a_sound_can_be_stopped_like_an_announcement(admin_client, app, tmp_path):
    services = app.state.services
    sound = upload(admin_client, tmp_path, seconds=30.0).json()["sound"]
    item = admin_client.post(f"/api/sounds/{sound['id']}/play").json()

    assert wait_until(lambda: services.player.current_id == item["id"], timeout=15)
    assert admin_client.post("/api/stop", json={}).json()["stopped"] is True
    assert wait_until(lambda: drained(services), timeout=30)
    assert services.db.get(item["id"])["state"] == "stopped"


def test_deleting_a_sound_removes_the_file(admin_client, app, tmp_path):
    services = app.state.services
    sound = upload(admin_client, tmp_path).json()["sound"]
    filename = services.db.get_sound(sound["id"])["filename"]
    assert services.sounds.path_for(filename) is not None

    assert admin_client.post(f"/api/admin/sounds/{sound['id']}/delete").status_code == 200
    assert services.sounds.path_for(filename) is None
    assert services.db.get_sound(sound["id"]) is None


def test_the_page_says_whether_links_can_be_fetched(admin_client):
    body = admin_client.get("/api/sounds").json()
    assert "can_fetch_links" in body
    assert body["max_mb"] == 25
    # Nothing offers a feature the machine cannot actually do.
    assert isinstance(body["can_fetch_links"], bool)


def test_sounds_require_signing_in(anon_client):
    assert anon_client.get("/api/sounds").status_code == 401
    assert anon_client.post("/api/sounds/1/play").status_code == 401
