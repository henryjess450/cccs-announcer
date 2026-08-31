"""The two things that make an install simple: finding the address, and
finding the speech engine without being told where it is.
"""

from __future__ import annotations


import pytest

from app.config import find_piper_binary, find_piper_voice, load_config
from app.netinfo import _is_private, _is_usable, all_urls, hostname, lan_addresses, staff_url


# -- the address to give staff --------------------------------------------

def test_useless_addresses_are_never_offered():
    """Handing somebody 127.0.0.1 sends them in a circle."""
    for address in ("127.0.0.1", "0.0.0.0", "169.254.10.4", ""):
        assert _is_usable(address) is False
    assert _is_usable("192.168.1.42") is True


@pytest.mark.parametrize("address,private", [
    ("192.168.1.42", True), ("10.0.0.106", True), ("172.16.5.9", True),
    ("172.31.255.1", True), ("172.32.0.1", False), ("8.8.8.8", False),
])
def test_school_lan_ranges_are_recognised(address, private):
    assert _is_private(address) is private


def test_the_staff_url_is_a_real_url():
    url = staff_url(8080)
    assert url.startswith("http://")
    assert url.endswith(":8080")


def test_addresses_never_include_loopback():
    assert all("127." not in address for address in lan_addresses())


def test_all_urls_covers_every_address_found():
    assert len(all_urls(8080)) == len(lan_addresses())


def test_hostname_always_returns_something():
    assert hostname()


def test_the_address_survives_having_no_network(monkeypatch):
    """The banner must still print something sensible on an unplugged machine."""
    monkeypatch.setattr("app.netinfo._route_address", lambda: None)
    monkeypatch.setattr("app.netinfo._hostname_addresses", lambda: [])
    assert lan_addresses() == []
    assert staff_url(8080) == "http://localhost:8080"


def test_health_reports_the_address(client):
    """So IT can read it without walking to the PA machine."""
    address = client.get("/health").json()["address"]
    assert address["staff_url"].startswith("http://")
    assert address["hostname"]


# -- finding Piper ---------------------------------------------------------

def test_the_speech_engine_is_found_where_setup_puts_it(tmp_path):
    engine = tmp_path / "piper" / "piper.exe"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"not really an executable")
    assert find_piper_binary(tmp_path) == str(engine)


def test_the_voice_is_found_where_setup_puts_it(tmp_path):
    voices = tmp_path / "voices"
    voices.mkdir()
    model = voices / "en_US-lessac-medium.onnx"
    model.write_bytes(b"model")
    (voices / "en_US-lessac-medium.onnx.json").write_text("{}")
    assert find_piper_voice(tmp_path) == str(model)


def test_a_voice_without_its_config_file_is_ignored(tmp_path):
    """Piper needs the .onnx.json beside the model; half a voice is no voice."""
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "orphan.onnx").write_bytes(b"model")
    assert find_piper_voice(tmp_path) == ""


def test_nothing_installed_is_reported_as_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert find_piper_binary(tmp_path) == ""
    assert find_piper_voice(tmp_path) == ""


def test_an_explicit_setting_still_wins(tmp_path, monkeypatch):
    """Auto-discovery is a default, not a straitjacket."""
    monkeypatch.setenv("PIPER_BINARY", r"D:\somewhere\else\piper.exe")
    monkeypatch.setenv("PIPER_MODEL", r"D:\somewhere\else\voice.onnx")
    config = load_config(env_file=tmp_path / "none.env")
    assert config.piper_binary == r"D:\somewhere\else\piper.exe"
    assert config.piper_model == r"D:\somewhere\else\voice.onnx"


def test_the_announcer_runs_with_no_settings_file_at_all(tmp_path):
    """Every setting has a working default -- .env is optional."""
    config = load_config(env_file=tmp_path / "definitely-not-here.env")
    assert config.port == 8080
    assert config.default_chime == "two_tone_bell"
    assert config.max_chars == 500
    assert config.rate_limit_count == 5
    assert config.session_idle_minutes == 30
