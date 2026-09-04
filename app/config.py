"""Configuration, loaded from a .env file plus the process environment.

We parse .env ourselves rather than pulling in python-dotenv. It is twenty
lines of well-understood code, and every dependency here is something a school
IT person has to reason about in three years.

Precedence: real environment variables win over .env, so a service wrapper can
override a setting without editing the file.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        # Strip matching surrounding quotes; leave inner content alone so a
        # Windows path like C:\Program Files\... survives intact.
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        values[key] = raw
    return values


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


# Accepts the plain true/false people expect as well as the named modes, so an
# older .env carrying PA_ANNOUNCE_ADDRESS_ON_START=true keeps working.
_ANNOUNCE_MODES = ("always", "setup", "once", "never")


def _announce_mode(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in _ANNOUNCE_MODES:
        return cleaned
    if cleaned in ("1", "true", "yes", "on"):
        return "always"
    return "never"


# ---------------------------------------------------------------------------
# Finding Piper without being told where it is.
#
# The installer puts Piper in <app>/piper and the voice in <app>/voices. If it
# is there, nobody has to edit a settings file at all -- which removes the two
# most-mistyped lines in the whole configuration.
# ---------------------------------------------------------------------------

def find_piper_binary(root: Path = PROJECT_ROOT) -> str:
    """Look where the installer puts it, then in the virtual environment, then
    on the PATH. Returns "" if it genuinely is not installed."""
    candidates = [
        root / "piper" / "piper.exe",
        root / "piper" / "piper",
        root / ".venv" / "Scripts" / "piper.exe",
        root / ".venv" / "bin" / "piper",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("piper") or ""


def find_piper_voice(root: Path = PROJECT_ROOT) -> str:
    """Use the voice in <app>/voices when there is exactly one obvious choice.

    A voice needs a matching .onnx.json beside it, so only complete pairs
    count. With several installed we take the first alphabetically and let
    PIPER_MODEL override -- guessing is better than refusing to speak.
    """
    voices_dir = root / "voices"
    if not voices_dir.is_dir():
        return ""
    complete = [
        path for path in sorted(voices_dir.glob("*.onnx"))
        if path.with_suffix(".onnx.json").exists()
        or Path(str(path) + ".json").exists()
    ]
    return str(complete[0]) if complete else ""


@dataclass(frozen=True)
class Config:
    # --- web ---
    host: str
    port: int

    # --- storage ---
    data_dir: Path
    db_path: Path
    chime_dir: Path
    audio_cache_dir: Path
    log_dir: Path
    log_level: str
    sound_dir: Path

    # --- TTS ---
    tts_engine: str            # "piper" | "mock"
    piper_binary: str
    piper_model: str
    piper_config: Optional[str]
    piper_length_scale: float
    piper_timeout_seconds: float

    # --- audio output ---
    audio_backend: str         # "sounddevice" | "mock"
    audio_device: Optional[str]  # substring match against the device name
    audio_samplerate: Optional[int]
    audio_channels: int
    audio_blocksize: int
    speech_gain: float
    chime_gain: float
    chime_gap_ms: int
    default_chime: str
    end_tone: Optional[str]

    # --- accounts and sessions ---
    session_idle_minutes: int
    session_max_hours: int
    session_cookie_name: str
    session_cookie_secure: bool
    login_max_failures: int
    login_lockout_seconds: int
    # The administrator account created automatically on first start, so
    # nobody needs a command line to get going. The password is generated
    # unless one is set here.
    bootstrap_username: str
    bootstrap_password: str

    # --- rate limiting ---
    rate_limit_count: int
    rate_limit_window_seconds: int

    # --- preview ---
    preview_max_concurrent: int

    # --- sound clips ---
    sound_max_seconds: float
    sound_max_mb: int
    ffmpeg_binary: str
    ytdlp_binary: str

    # --- scheduled announcements ---
    #: The school's own timezone. Everything staff type is in this; everything
    #: stored is UTC.
    timezone: str
    #: How late a scheduled announcement may be and still go out. Anything
    #: later is skipped: the announcer was off, and firing a backlog into a
    #: building that has moved on is worse than missing it.
    schedule_grace_minutes: int
    schedule_check_seconds: int

    # --- speaking the address at startup ---
    #   "always" every start, repeating until an administrator signs in
    #   "setup"  only while the first administrator account is unclaimed
    #   "once"   one announcement at every start, no repeats
    #   "never"  silent
    announce_address_mode: str
    announce_address_interval_seconds: int
    announce_address_max_times: int

    # --- composing ---
    max_chars: int
    chars_per_second: float    # fallback estimate before we have measurements

    # Refuse to start if another copy is already using this data folder.
    # Only turned off by the test suite, which runs many instances at once.
    single_instance: bool = True

    # Test-only: speeds up the mock audio backend so the suite runs quickly
    # while still exercising real timing. Ignored by the real backend.
    mock_speed: float = 1.0

    @property
    def is_mock_audio(self) -> bool:
        return self.audio_backend == "mock"


def load_config(env_file: Optional[Path] = None) -> Config:
    env_file = env_file or Path(os.environ.get("PA_ENV_FILE", PROJECT_ROOT / ".env"))
    file_values = parse_env_file(Path(env_file))

    def get(key: str, default: str = "") -> str:
        # Real environment wins so a service wrapper can override .env.
        return os.environ.get(key, file_values.get(key, default))

    def get_opt(key: str) -> Optional[str]:
        value = get(key).strip()
        return value or None

    data_dir = Path(get("PA_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()

    return Config(
        host=get("PA_HOST", "0.0.0.0"),
        port=int(get("PA_PORT", "8080")),
        data_dir=data_dir,
        db_path=Path(get("PA_DB_PATH", str(data_dir / "announcer.sqlite3"))).expanduser(),
        chime_dir=Path(get("PA_CHIME_DIR", str(data_dir / "chimes"))).expanduser(),
        audio_cache_dir=Path(get("PA_AUDIO_CACHE_DIR", str(data_dir / "cache"))).expanduser(),
        log_dir=Path(get("PA_LOG_DIR", str(data_dir / "logs"))).expanduser(),
        sound_dir=Path(get("PA_SOUND_DIR", str(data_dir / "sounds"))).expanduser(),
        log_level=get("PA_LOG_LEVEL", "INFO").upper(),
        tts_engine=get("PA_TTS_ENGINE", "piper").lower(),
        # Both fall back to auto-discovery, so a working install needs no
        # settings file at all.
        piper_binary=get("PIPER_BINARY") or find_piper_binary(),
        piper_model=get("PIPER_MODEL") or find_piper_voice(),
        piper_config=get_opt("PIPER_CONFIG"),
        piper_length_scale=float(get("PIPER_LENGTH_SCALE", "1.05")),
        piper_timeout_seconds=float(get("PIPER_TIMEOUT_SECONDS", "30")),
        audio_backend=get("PA_AUDIO_BACKEND", "sounddevice").lower(),
        audio_device=get_opt("PA_AUDIO_DEVICE"),
        audio_samplerate=int(get("PA_AUDIO_SAMPLERATE")) if get_opt("PA_AUDIO_SAMPLERATE") else None,
        audio_channels=int(get("PA_AUDIO_CHANNELS", "2")),
        audio_blocksize=int(get("PA_AUDIO_BLOCKSIZE", "2048")),
        speech_gain=float(get("PA_SPEECH_GAIN", "1.0")),
        chime_gain=float(get("PA_CHIME_GAIN", "0.45")),
        chime_gap_ms=int(get("PA_CHIME_GAP_MS", "500")),
        default_chime=get("PA_DEFAULT_CHIME", "two_tone_bell"),
        end_tone=get_opt("PA_END_TONE"),
        session_idle_minutes=int(get("PA_SESSION_IDLE_MINUTES", "30")),
        session_max_hours=int(get("PA_SESSION_MAX_HOURS", "12")),
        session_cookie_name=get("PA_SESSION_COOKIE", "pa_session"),
        session_cookie_secure=_bool(get("PA_SESSION_COOKIE_SECURE", "false")),
        login_max_failures=int(get("PA_LOGIN_MAX_FAILURES", "5")),
        login_lockout_seconds=int(get("PA_LOGIN_LOCKOUT_SECONDS", "300")),
        bootstrap_username=get("PA_BOOTSTRAP_USERNAME", "admin").strip() or "admin",
        bootstrap_password=get("PA_BOOTSTRAP_PASSWORD", ""),
        rate_limit_count=int(get("PA_RATE_LIMIT_COUNT", "5")),
        rate_limit_window_seconds=int(get("PA_RATE_LIMIT_WINDOW_SECONDS", "600")),
        preview_max_concurrent=int(get("PA_PREVIEW_MAX_CONCURRENT", "2")),
        sound_max_seconds=float(get("PA_SOUND_MAX_SECONDS", "300")),
        sound_max_mb=int(get("PA_SOUND_MAX_MB", "25")),
        ffmpeg_binary=get("PA_FFMPEG_BINARY", "ffmpeg"),
        ytdlp_binary=get("PA_YTDLP_BINARY", "yt-dlp"),
        timezone=get("PA_TIMEZONE", "America/Vancouver").strip() or "America/Vancouver",
        schedule_grace_minutes=int(get("PA_SCHEDULE_GRACE_MINUTES", "10")),
        schedule_check_seconds=int(get("PA_SCHEDULE_CHECK_SECONDS", "20")),
        announce_address_mode=_announce_mode(get("PA_ANNOUNCE_ADDRESS_ON_START", "always")),
        announce_address_interval_seconds=int(
            get("PA_ANNOUNCE_ADDRESS_INTERVAL_SECONDS", "60")),
        announce_address_max_times=int(get("PA_ANNOUNCE_ADDRESS_MAX_TIMES", "20")),
        max_chars=int(get("PA_MAX_CHARS", "500")),
        chars_per_second=float(get("PA_CHARS_PER_SECOND", "13.5")),
        single_instance=_bool(get("PA_SINGLE_INSTANCE", "true")),
        mock_speed=float(get("PA_MOCK_SPEED", "1.0")),
    )


def ensure_directories(config: Config) -> None:
    for directory in (config.data_dir, config.chime_dir, config.audio_cache_dir,
                      config.log_dir, config.sound_dir, config.db_path.parent):
        directory.mkdir(parents=True, exist_ok=True)
