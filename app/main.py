"""FastAPI application: the web layer.

Nothing in here plays audio. Submitting an announcement writes one row and pokes
the player thread; that separation is what makes the single-playback guarantee
hold no matter how many staff click Send at the same moment.

Database calls sit in plain `def` route handlers (not `async def`) so Starlette
runs them in its threadpool and a slow disk cannot stall the event loop or the
SSE streams.

Everything except the sign-in page, the health check, and the static assets
requires a signed-in account. There are no anonymous announcements: the audit
trail is the main thing preventing misuse, and it only works if every
announcement has a name on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .accounts import Accounts, AuthError, ROLE_ADMIN, ROLE_STAFF, ROLES
from .audio import build_audio_backend
from .audio.base import AudioUnavailable
from .auth import AppError, load_session, optional_user, require_admin, require_user
from .chimes import GROUPS as CHIME_GROUPS, ChimeLibrary
from .config import Config, ensure_directories, load_config
from .db import (
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PLAYING,
    STATE_QUEUED,
    Database,
    now_iso,
    parse_iso,
    utcnow,
)
from .events import Broadcaster, sse_message
from .logging_setup import configure_logging
from .netinfo import (
    all_urls,
    hostname,
    primary_address,
    public_address,
    staff_url,
    startup_announcement,
)
from .normalize import normalize
from .player import Player
from .ratelimit import RateLimiter
from .schedules import (
    KIND_ONCE,
    KINDS,
    ScheduleError,
    decide,
    describe,
    format_time,
    next_occurrence,
    parse_days,
    parse_time,
    school_zone,
)
from .security import generate_password, verify_password
from .singleton import InstanceLock
from .tts import build_tts_engine
from .tts.base import TTSError

log = logging.getLogger(__name__)

VERSION = "2.1.0"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# How often an idle SSE stream emits a comment frame to stay alive.
KEEPALIVE_SECONDS = 15.0

# Cache key for the CSS and JavaScript, recomputed whenever they change on
# disk. This used to be the application version, which only works if somebody
# remembers to bump it -- and when they forget, browsers keep serving last
# month's JavaScript against this month's page, which looks like a broken
# feature rather than a caching problem. A fingerprint of the files
# themselves cannot be forgotten.
_fingerprint_cache: Dict[str, str] = {}


def static_fingerprint() -> str:
    """A short token that changes whenever any served asset changes."""
    assets = sorted(
        path for path in STATIC_DIR.glob("*.*") if path.suffix in (".js", ".css")
    )
    try:
        # Timestamps are the cheap check for "has anything moved?", but the
        # token itself comes from the CONTENT. A git pull rewrites timestamps
        # on every file it touches; if the token followed those, every update
        # would re-download the CSS and JavaScript even when neither changed.
        key = "|".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in assets)
    except OSError:
        return VERSION

    cached = _fingerprint_cache.get(key)
    if cached is not None:
        return cached

    digest = hashlib.sha256()
    try:
        for path in assets:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError:
        return VERSION

    token = digest.hexdigest()[:12]
    # One entry is all we need; drop the older key.
    _fingerprint_cache.clear()
    _fingerprint_cache[key] = token
    return token


# Previews are discarded after this long. They are small, but there is no
# reason to keep them and no reason to let them accumulate forever.
PREVIEW_MAX_AGE_SECONDS = 2 * 60 * 60


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class NormalizeRequest(BaseModel):
    text: str = Field(default="")


class AnnouncementRequest(BaseModel):
    text: str
    priority: bool = False
    zone: str = "all"


class StopRequest(BaseModel):
    reason: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str
    #: Optional: staff choose their announcement sound on the same screen.
    chime: Optional[str] = None


class ScheduleRequest(BaseModel):
    """An announcement that goes out on a timetable, in school time."""
    text: str
    kind: str = "weekdays"
    at_time: str
    days: Optional[List[int]] = None
    on_date: Optional[str] = None
    starts_on: Optional[str] = None
    ends_on: Optional[str] = None
    priority: bool = False
    chime: Optional[str] = None
    enabled: bool = True


class SettingsRequest(BaseModel):
    """A person's own announcement settings. Anything left out is unchanged."""
    chime: Optional[str] = None
    clear_chime: bool = False
    announce_name: Optional[bool] = None
    spoken_name: Optional[str] = None


class SetupRequest(BaseModel):
    """First-run: claim the starting administrator account."""
    username: str
    display_name: str
    current_password: str
    new_password: str
    chime: Optional[str] = None


class NewUserRequest(BaseModel):
    username: str
    display_name: str
    role: str = ROLE_STAFF


class PurgeRequest(BaseModel):
    """Clear the announcement log. None means everything that has finished."""
    older_than_days: Optional[int] = None


class UserUpdateRequest(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None
    display_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class Services:
    def __init__(self, config: Config):
        self.config = config
        ensure_directories(config)
        configure_logging(config.log_dir, config.log_level)

        # Taken before anything else touches the database: two processes
        # sharing one queue means two player threads and overlapping audio.
        self.instance_lock = InstanceLock(config.data_dir / "announcer.lock")
        if config.single_instance:
            self.instance_lock.acquire()

        self.db = Database(config.db_path)
        self.db.initialize()
        self.accounts = Accounts(self.db, config)
        self.rate_limiter = RateLimiter(self.db, config)
        self.chimes = ChimeLibrary(config.chime_dir)
        self.tts = build_tts_engine(config)
        self.audio = build_audio_backend(config)
        self.broadcaster = Broadcaster()

        # Preview synthesis happens on web-request threads. This cap stops a
        # room full of people clicking Preview from starving the player thread
        # of CPU while a real announcement is being produced.
        self.preview_slots = threading.Semaphore(max(1, config.preview_max_concurrent))

        # Speaks the address over the PA while the announcer is waiting to be
        # set up. Stops the moment somebody claims the administrator account.
        self._address_thread: Optional[threading.Thread] = None
        self._stop_address = threading.Event()

        # Scheduled announcements. The zone is resolved once; everything that
        # converts between school time and UTC goes through app/schedules.py.
        self.zone = school_zone(config.timezone)
        if self.zone is None:
            log.warning(
                "Timezone %r could not be loaded -- scheduled announcements will "
                "use UTC, which is almost certainly the wrong time. On Windows "
                "this usually means the 'tzdata' package is missing.",
                config.timezone,
            )
        self._schedule_thread: Optional[threading.Thread] = None
        self._stop_schedules = threading.Event()

        self.player = Player(
            config=config,
            database=self.db,
            tts=self.tts,
            audio=self.audio,
            chimes=self.chimes,
            on_change=self.publish_status,
        )

    # -- status ------------------------------------------------------------

    def build_snapshot(self) -> Dict[str, Any]:
        health = self.player.health
        playing = self.db.playing_item()
        queued = self.db.queued_items()

        # While the speakers are unreachable the player keeps re-claiming the
        # next item, attempting it, and putting it back. During each attempt the
        # row is briefly in the 'playing' state -- but nothing is coming out of
        # the speakers, and telling staff "Now playing" would be a lie they act
        # on. Report it as held instead.
        held = None
        if playing is not None and not health.audio_ok:
            held, playing = playing, None

        queue: List[Dict[str, Any]] = []
        running_total = 0.0
        if playing is not None:
            # We do not know how much of the playing item is left, so the
            # estimate is deliberately conservative: assume all of it remains.
            running_total += playing.get("estimated_seconds") or self.player.estimate_seconds(
                playing["normalized_text"], playing.get("chime")
            )
        for position, item in enumerate(queued, start=1):
            seconds = item.get("estimated_seconds") or self.player.estimate_seconds(
                item["normalized_text"], item.get("chime")
            )
            queue.append({
                "id": item["id"],
                "position": position,
                # user_id travels with the item so each browser can work out
                # whether the Stop button is theirs. The snapshot is broadcast
                # to everyone, so it cannot be tailored per viewer.
                "user_id": item["user_id"],
                "user_name": item["user_name"],
                "text": item["normalized_text"],
                "priority": bool(item["priority"]),
                "zone": item["zone"],
                "seconds_until": round(running_total, 1),
                "estimated_seconds": round(seconds, 1),
            })
            running_total += seconds

        if not health.audio_ok:
            status = "error"
        elif playing is not None:
            status = "playing"
        else:
            status = "idle"

        # Recent failures are surfaced so a queued item that could not be spoken
        # is never just quietly gone from the screen.
        problems = [
            {
                "id": row["id"],
                "user_name": row["user_name"],
                "text": row["normalized_text"],
                "state": row["state"],
                "error": row["error"] or "This announcement did not play.",
                "created_at": row["created_at"],
            }
            for row in self.db.recent(limit=12)
            if row["state"] in (STATE_FAILED, STATE_INTERRUPTED)
        ][:5]

        # Running on a mock engine or a mock device produces a hum, or silence,
        # instead of an announcement. Without this notice that looks exactly
        # like a fault, and someone goes hunting for a broken amplifier. If the
        # system is not really able to announce, it has to say so.
        test_notes = []
        if self.tts.name == "mock":
            test_notes.append("the voice is a test tone, not real speech")
        if self.audio.name == "mock":
            test_notes.append("nothing is sent to the speakers")

        return {
            "status": status,
            "test_mode": {
                "active": bool(test_notes),
                "message": (
                    "Test mode — " + " and ".join(test_notes) +
                    ". Real announcements are not going out."
                ) if test_notes else "",
            },
            "held": None if held is None else {
                "id": held["id"],
                "user_name": held["user_name"],
                "text": held["normalized_text"],
            },
            "now_playing": None if playing is None else {
                "id": playing["id"],
                "user_id": playing["user_id"],
                "user_name": playing["user_name"],
                "text": playing["normalized_text"],
                "priority": bool(playing["priority"]),
                "started_at": playing["started_at"],
            },
            "queue": queue,
            "queue_depth": len(queue) + (1 if held is not None else 0),
            "queue_seconds": round(running_total, 1),
            "audio": {
                "ok": health.audio_ok,
                "message": health.audio_message,
                "device": self.audio.name,
            },
            "tts": {
                "ok": health.tts_ok,
                "message": health.tts_message,
                "engine": self.tts.name,
            },
            "problems": problems,
            "version": VERSION,
        }

    def publish_status(self) -> None:
        try:
            self.broadcaster.publish(self.build_snapshot())
        except Exception:
            log.exception("Could not build a status update")

    # -- first run ---------------------------------------------------------

    @property
    def first_login_file(self) -> Path:
        return self.config.data_dir / "FIRST-LOGIN.txt"

    def bootstrap_admin(self) -> None:
        """On an empty database, make the starting administrator account.

        The password is printed to the console and written to FIRST-LOGIN.txt
        in the data folder, because the person installing this is standing at
        the machine and the console may have scrolled by the time they look.
        The file is deleted the moment the account is claimed.
        """
        created = self.accounts.ensure_bootstrap_admin()
        if created is None:
            if not self.accounts.setup_pending():
                self.clear_first_login_file()
            return

        user, password = created
        message = (
            "\n"
            "  ===============================================================\n"
            "   FIRST-TIME SETUP\n"
            "\n"
            "   An administrator account has been created for you.\n"
            "\n"
            f"      Username:  {user.username}\n"
            f"      Password:  {password}\n"
            "\n"
            "   Open the announcer in a browser and sign in. You will be asked\n"
            "   to choose your own username, name and password straight away.\n"
            "\n"
            f"   This is also saved in:  {self.first_login_file}\n"
            "   That file is deleted as soon as you have signed in and set the\n"
            "   account up.\n"
            "  ===============================================================\n"
        )
        print(message, flush=True)
        log.warning("Created the first-run administrator account %r. "
                    "The announcer is not secure until it is set up.", user.username)
        try:
            self.first_login_file.write_text(
                "CCCS Announcer - first-time sign-in\n"
                "===================================\n\n"
                f"Username: {user.username}\n"
                f"Password: {password}\n\n"
                "Sign in at the announcer address and you will be asked to choose\n"
                "your own username, name and password.\n\n"
                "This file is deleted automatically once that is done.\n",
                encoding="utf-8",
            )
        except OSError:
            log.exception("Could not write %s", self.first_login_file)

    def admin_signin_lines(self) -> List[str]:
        """What to tell the person standing at the machine about signing in.

        Passwords are hashed and cannot be read back -- that is the point of
        hashing them. So there are only two honest things to print: the
        first-time password while it still exists, or the list of administrator
        usernames plus how to issue a new password.
        """
        lines: List[str] = []
        try:
            pending = self.accounts.setup_pending()
            admins = [
                user for user in self.accounts.list_users()
                if user["role"] == ROLE_ADMIN and user["is_active"]
            ]
        except Exception:
            return ["   (could not read the accounts)"]

        if pending and self.first_login_file.exists():
            password = ""
            for line in self.first_login_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("Password:"):
                    password = line.split(":", 1)[1].strip()
            lines += [
                "   FIRST-TIME SIGN-IN (this account has not been claimed yet):",
                "",
                f"        Username:  {self.config.bootstrap_username}",
                f"        Password:  {password}",
                "",
                "   Signing in asks you to choose your own name and password,",
                "   and these stop working straight away.",
            ]
            return lines

        if not admins:
            return [
                "   NO ADMINISTRATOR ACCOUNT. Nobody can manage the announcer.",
                "   Make one with:",
                "        .venv\\Scripts\\python.exe scripts\\manage_users.py add --admin",
            ]

        lines.append("   ADMINISTRATOR SIGN-IN:")
        lines.append("")
        for user in admins:
            note = "  (not set up yet)" if user["must_change_password"] else ""
            locked = "  (LOCKED)" if user["locked_until"] else ""
            lines.append(f"        {user['username']:<16} {user['display_name']}{note}{locked}")
        lines += [
            "",
            "   Passwords are not stored in a form anyone can read, so they",
            "   cannot be shown here. To issue a new one:",
            "",
            "        .venv\\Scripts\\python.exe scripts\\manage_users.py reset <username>",
        ]
        return lines

    def print_address_banner(self) -> None:
        """Tell whoever is standing at the machine what to write on the sticky note.

        This is the single most-asked question after an install, and hunting it
        down with ipconfig is exactly the kind of thing that stops a school IT
        person cold. No network calls -- it works with the internet unplugged.
        """
        port = self.config.port
        primary = staff_url(port)
        extras = [url for url in all_urls(port) if url != primary]

        lines = [
            "",
            "  ==============================================================",
            "   THE ANNOUNCER IS RUNNING",
            "",
            "   Staff open this address on their own computers:",
            "",
            f"        {primary}",
            "",
        ]
        if extras:
            lines.append("   Also reachable at: " + ", ".join(extras))
        lines += [
            f"   On this computer:  http://localhost:{port}",
            f"   By name:           http://{hostname()}:{port}",
            "",
        ]

        # People ask for the public address, so show it -- clearly labelled as
        # the one NOT to use. Short timeout and silent failure, because the PA
        # machine may have no internet at all and must still start.
        public = public_address()
        if public:
            lines += [
                f"   This school's internet address is {public}. That is NOT the",
                "   address staff use, and the announcer must not be reachable",
                "   there -- anything on the internet that can reach it can try",
                "   to talk to the whole school. Use the local address above.",
                "",
            ]
        else:
            lines += [
                "   The address above is local to the school network, which is",
                "   what you want. Do not forward the announcer to the internet.",
                "",
            ]

        lines += ["  --------------------------------------------------------------", ""]
        lines += self.admin_signin_lines()
        lines += [
            "",
            "   Keep this window open. Closing it stops announcements.",
            "  ==============================================================",
            "",
        ]
        if primary_address() is None:
            lines.insert(
                -1,
                "   NOTE: this computer does not appear to be on a network yet.\n"
                "   Plug in the network cable, then restart the announcer.",
            )
        print("\n".join(lines), flush=True)

    # -- scheduled announcements -------------------------------------------

    def compute_next_run(self, schedule: Dict[str, Any],
                         after: Optional[datetime] = None) -> Optional[str]:
        """When this schedule should next fire, as a UTC timestamp string."""
        moment = next_occurrence(
            kind=schedule["kind"],
            at=parse_time(schedule["at_time"]),
            days=parse_days(schedule.get("days")),
            on_date=schedule.get("on_date"),
            after=after or utcnow(),
            zone=self.zone,
            starts_on=schedule.get("starts_on"),
            ends_on=schedule.get("ends_on"),
        )
        if moment is None:
            return None
        return moment.astimezone(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")

    def run_due_schedules(self) -> int:
        """Queue anything that is due. Returns how many went out.

        Called on a timer, and safe to call at any moment: each schedule's next
        run is advanced before the announcement is queued, so a slow queue can
        never cause the same schedule to fire twice.
        """
        now = utcnow()
        fired = 0

        for schedule in self.db.due_schedules(now_iso()):
            due_at = parse_iso(schedule["next_run_at"])
            if due_at is None:
                self.db.update_schedule(schedule["id"], next_run_at=None, enabled=0)
                continue

            verdict = decide(due_at, now, self.config.schedule_grace_minutes)

            # Advance FIRST. If queueing throws, the schedule still moves on
            # rather than retrying every twenty seconds forever.
            #
            # "Once" is settled here rather than by working out whether its
            # date is still in the future. Relying on that comparison means a
            # wrong clock, or a hand-edited row, could make a one-off repeat --
            # and a one-off repeating is exactly the announcement nobody is
            # expecting.
            following = (
                None if schedule["kind"] == KIND_ONCE
                else self.compute_next_run(schedule, after=due_at)
            )
            self.db.update_schedule(
                schedule["id"],
                next_run_at=following,
                enabled=1 if following else 0,
                last_run_at=now_iso(),
                last_result="sent" if verdict.fire else verdict.skipped_reason,
            )

            if not verdict.fire:
                if verdict.skipped_reason != "not yet":
                    log.warning("Scheduled announcement %s skipped: %s",
                                schedule["id"], verdict.skipped_reason)
                continue

            try:
                owner = self.accounts.get(schedule["user_id"]) if schedule["user_id"] else None
                text = schedule["text"]
                if owner is not None and owner.announce_name:
                    text = f"Announcement from {owner.announced_as}. {text}"
                spoken = normalize(text).normalized
                chime = schedule["chime"] or (owner.chime if owner else None) \
                    or self.config.default_chime

                self.db.enqueue(
                    raw_text=schedule["text"],
                    normalized_text=spoken,
                    chime=chime,
                    user_name=schedule["user_name"],
                    user_id=schedule["user_id"],
                    priority=schedule["priority"],
                    zone=schedule["zone"],
                    kind="scheduled",
                    estimated_seconds=self.player.estimate_seconds(spoken, chime),
                )
                self.player.notify_new_item()
                fired += 1
                log.info("Scheduled announcement %s queued (%s)",
                         schedule["id"], schedule["user_name"])
            except Exception:
                log.exception("Could not queue scheduled announcement %s", schedule["id"])
                self.db.update_schedule(
                    schedule["id"],
                    last_result="could not be queued -- see the log",
                )

        if fired:
            self.publish_status()
        return fired

    def start_scheduler(self) -> None:
        def loop() -> None:
            while not self._stop_schedules.is_set():
                try:
                    self.run_due_schedules()
                except Exception:
                    # A scheduling fault must never take the announcer down.
                    log.exception("The schedule check failed")
                self._stop_schedules.wait(max(5, self.config.schedule_check_seconds))

        self._schedule_thread = threading.Thread(
            target=loop, name="pa-scheduler", daemon=True)
        self._schedule_thread.start()
        log.info("Scheduler started (school time = %s, checking every %ss)",
                 self.config.timezone, self.config.schedule_check_seconds)

    def stop_scheduler(self) -> None:
        self._stop_schedules.set()

    # -- saying the address out loud --------------------------------------

    def start_address_announcements(self) -> None:
        """Say the address over the PA so it can be heard from a corridor.

        PA_ANNOUNCE_ADDRESS_ON_START decides when:

          always  every start, repeating until an administrator signs in
          setup   only while the first administrator account is unclaimed
          once    one announcement at every start, no repeats
          never   silent

        Whichever mode, three limits always apply, because this talks to every
        classroom in the building:

          * it stops the moment an administrator signs in;
          * it gives up after PA_ANNOUNCE_ADDRESS_MAX_TIMES repeats, so a
            forgotten machine cannot talk over lessons all day;
          * it goes through the normal queue, so it can never overlap a real
            announcement.
        """
        mode = self.config.announce_address_mode
        if mode == "never":
            return
        if mode == "setup" and not self.accounts.setup_pending():
            return

        address = primary_address()
        if address is None:
            log.info("Not speaking the address: this computer is not on a network")
            return

        text = startup_announcement(address, self.config.port)
        interval = max(10, self.config.announce_address_interval_seconds)
        limit = 1 if mode == "once" else max(1, self.config.announce_address_max_times)

        def still_wanted() -> bool:
            if self._stop_address.is_set():
                return False
            if mode == "setup":
                return self.accounts.setup_pending()
            return True

        def loop() -> None:
            said = 0
            while said < limit and still_wanted():
                try:
                    self.db.enqueue(
                        raw_text=f"(startup) address {address}:{self.config.port}",
                        normalized_text=text,
                        chime=self.config.default_chime,
                        user_name="Announcer (starting up)",
                        kind="startup",
                        estimated_seconds=self.player.estimate_seconds(
                            text, self.config.default_chime),
                    )
                    self.player.notify_new_item()
                    self.publish_status()
                except Exception:
                    log.exception("Could not queue the startup address announcement")
                    return
                said += 1
                if said >= limit:
                    break
                self._stop_address.wait(interval)

            if said >= limit and limit > 1:
                log.info(
                    "Stopped saying the address after %s times. Sign in at %s",
                    said, staff_url(self.config.port),
                )

        log.warning(
            "Saying the address over the PA (mode=%s, every %ss, at most %s times). "
            "It stops as soon as an administrator signs in.",
            mode, interval, limit,
        )
        self._address_thread = threading.Thread(
            target=loop, name="pa-address-announcer", daemon=True)
        self._address_thread.start()

    def stop_address_announcements(self) -> None:
        self._stop_address.set()

    def clear_first_login_file(self) -> None:
        try:
            self.first_login_file.unlink(missing_ok=True)
        except OSError:
            pass

    # -- preview -----------------------------------------------------------

    def sweep_previews(self) -> None:
        cutoff = time.time() - PREVIEW_MAX_AGE_SECONDS
        try:
            for path in self.config.audio_cache_dir.glob("preview-*.wav"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config: Optional[Config] = None) -> FastAPI:
    services = Services(config or load_config())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.broadcaster.bind_loop(asyncio.get_running_loop())
        services.accounts.purge_expired_sessions()
        services.bootstrap_admin()
        services.player.start()
        services.publish_status()
        services.start_address_announcements()
        services.start_scheduler()
        log.info(
            "CCCS Announcer %s ready | audio=%s | tts=%s | accounts=%s | address=%s",
            VERSION, services.audio.describe(), services.tts.describe(),
            services.accounts.count_users(), staff_url(services.config.port),
        )
        services.print_address_banner()
        try:
            yield
        finally:
            log.info("Shutting down; waiting for the current announcement to finish")
            services.stop_address_announcements()
            services.stop_scheduler()
            services.player.shutdown()
            services.instance_lock.release()

    app = FastAPI(title="CCCS Announcer", version=VERSION, lifespan=lifespan)
    app.state.services = services

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # -- errors ------------------------------------------------------------

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        """Carry a machine-readable `reason` alongside the human message so the
        page can react (sign in again, change password, show the wait) without
        having to parse English."""
        payload = {"detail": exc.detail, "reason": exc.reason}
        payload.update(exc.extra)
        return JSONResponse(payload, status_code=exc.status_code)

    # -- helpers -----------------------------------------------------------

    def spoken_text_for(user, typed: str) -> str:
        """What will actually come out of the speakers.

        Preview and Send both go through this, so what somebody hears in their
        browser is exactly what the school hears -- including the "Announcement
        from ..." opening, if they have turned it on.
        """
        if user.announce_name:
            typed = f"Announcement from {user.announced_as}. {typed}"
        return normalize(typed).normalized

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def render_page(name: str) -> HTMLResponse:
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        return HTMLResponse(
            html.replace("__VERSION__", static_fingerprint()),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            services.config.session_cookie_name,
            token,
            httponly=True,                     # JavaScript can never read it
            samesite="lax",                    # not sent on cross-site POSTs
            secure=services.config.session_cookie_secure,
            max_age=services.config.session_max_hours * 3600,
            path="/",
        )

    # -- pages -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if optional_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return render_page("index.html")

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if optional_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return render_page("login.html")

    @app.get("/schedule", include_in_schema=False)
    def schedule_page(request: Request):
        if optional_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return render_page("schedule.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request):
        user = optional_user(request)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not user.is_admin:
            return RedirectResponse("/", status_code=303)
        return render_page("admin.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    # -- sign in / out -----------------------------------------------------

    @app.post("/api/login")
    def api_login(body: LoginRequest, request: Request, response: Response) -> Dict[str, Any]:
        try:
            user = services.accounts.authenticate(
                body.username, body.password, ip=client_ip(request)
            )
        except AuthError as exc:
            log.info("Sign-in refused for %r from %s (%s)",
                     body.username, client_ip(request), exc.reason)
            raise AppError(401, exc.message, exc.reason)

        token, csrf = services.accounts.start_session(
            user, ip=client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        set_session_cookie(response, token)
        if user.is_admin:
            # The address announcements exist to get an administrator signed
            # in. One just did.
            services.stop_address_announcements()
        log.info("%s signed in from %s", user.username, client_ip(request))
        return {"user": user.public(), "csrf_token": csrf}

    @app.post("/api/logout")
    def api_logout(request: Request, response: Response) -> Dict[str, Any]:
        # Deliberately does not use require_user: signing out must work even
        # from a session that has already gone stale.
        token = request.cookies.get(services.config.session_cookie_name)
        result = load_session(request)
        if result is not None:
            services.accounts.record_event(
                "logout", username=result[0].username, user_id=result[0].id,
                ip=client_ip(request),
            )
        services.accounts.end_session(token)
        response.delete_cookie(services.config.session_cookie_name, path="/")
        return {"signed_out": True}

    @app.get("/api/me")
    def api_me(request: Request) -> Dict[str, Any]:
        result = load_session(request)
        if result is None:
            raise AppError(401, "Please sign in again.", "signed_out")
        user, session = result
        return {"user": user.public(), "csrf_token": session["csrf_token"]}

    @app.get("/api/setup-status")
    def api_setup_status() -> Dict[str, Any]:
        """Whether the starting administrator account is still unclaimed.

        Open, with no sign-in, so the sign-in page can tell whoever is
        installing this what to do. It reveals the username but never the
        password -- the username was always guessable; the password is not.
        """
        pending = services.accounts.setup_pending()
        return {
            "setup_pending": pending,
            "username": services.config.bootstrap_username if pending else None,
        }

    @app.post("/api/setup")
    def api_setup(body: SetupRequest, request: Request, response: Response) -> Dict[str, Any]:
        """Claim the starting administrator account: new username, name, password.

        Everything changes in one step, so the account is never usable under a
        real person's name while still on its issued password.
        """
        user = require_user(request)
        if not user.is_bootstrap:
            raise AppError(
                400,
                "This account has already been set up.",
                "already_set_up",
            )

        row = services.accounts.get_by_username(user.username)
        if row is None or not verify_password(body.current_password, row["password_hash"]):
            raise AppError(400, "That current password is not right.", "bad_current_password")

        try:
            updated = services.accounts.complete_setup(
                user.id,
                username=body.username,
                display_name=body.display_name,
                password=body.new_password,
            )
        except ValueError as exc:
            raise AppError(400, str(exc), "invalid_setup")

        if body.chime is not None:
            chosen = body.chime.strip() or None
            if chosen is None or services.chimes.path_for(chosen) is not None:
                services.accounts.set_chime(updated.id, chosen)
                updated = services.accounts.get(updated.id)

        services.clear_first_login_file()
        services.stop_address_announcements()
        log.info("First-run administrator account set up as %r", updated.username)

        # complete_setup ended every session for this account, including this
        # one. Issue a fresh session so they stay signed in.
        token, csrf = services.accounts.start_session(
            updated, ip=client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        set_session_cookie(response, token)
        return {"user": updated.public(), "csrf_token": csrf}

    @app.post("/api/password")
    def api_change_password(body: PasswordRequest, request: Request) -> Dict[str, Any]:
        user = require_user(request)
        try:
            services.accounts.change_own_password(
                user.id, body.current_password, body.new_password
            )
        except AuthError as exc:
            raise AppError(400, exc.message, exc.reason)
        except ValueError as exc:
            raise AppError(400, str(exc), "weak_password")

        if body.chime is not None:
            chosen = body.chime.strip() or None
            if chosen is not None and services.chimes.path_for(chosen) is None:
                raise AppError(400, "That sound isn't available.", "no_such_chime")
            services.accounts.set_chime(user.id, chosen)

        log.info("%s changed their password", user.username)
        return {"changed": True, "chime": services.accounts.get(user.id).chime}

    # -- compose-screen data -----------------------------------------------

    @app.get("/api/config")
    def api_config(request: Request) -> Dict[str, Any]:
        user = require_user(request)
        chime = services.chimes.get(user.chime or services.config.default_chime)
        return {
            "max_chars": services.config.max_chars,
            "default_chime": services.config.default_chime,
            # The chime is fixed for the whole school, so the compose screen
            # shows no chooser. The list is still reported for the admin panel
            # and the deployment checklist.
            "chime_locked": False,
            "chime_label": chime.label if chime else services.config.default_chime,
            "my_chime": user.chime,
            "announce_name": user.announce_name,
            "announced_as": user.announced_as,
            "chimes": [
                {"key": c.key, "label": c.label, "seconds": round(c.seconds, 2)}
                for c in services.chimes.available()
            ],
            # Phase 1 ships one zone. The field exists so the UI does not change
            # shape when zones arrive.
            "zones": [{"key": "all", "label": "Whole building"}],
            "auth_enabled": True,
            "user": user.public(),
            "rate_limit": {
                "count": services.rate_limiter.limit(),
                "window_seconds": services.rate_limiter.window_seconds(),
                "exempt": user.is_admin,
            },
            "version": VERSION,
        }

    @app.get("/api/chimes")
    def api_chimes(request: Request) -> Dict[str, Any]:
        """Every sound staff can pick from, grouped, with a line about each."""
        user = require_user(request)
        return {
            "chimes": [
                {
                    "key": chime.key,
                    "label": chime.label,
                    "description": chime.description,
                    "seconds": round(chime.seconds, 2),
                    "group": chime.group,
                }
                for chime in services.chimes.available()
            ],
            "groups": [name for name, _ in CHIME_GROUPS],
            "default_chime": services.config.default_chime,
            "chosen": user.chime,
        }

    @app.get("/api/chimes/{key}/audio")
    def api_chime_audio(key: str, request: Request) -> Response:
        """The sound itself, so it can be listened to before it is chosen.

        Like Preview, this plays in the browser and never touches the PA.
        """
        require_user(request)
        path = services.chimes.path_for(key)
        if path is None:
            raise AppError(404, "That sound isn't available.", "no_such_chime")
        return Response(
            content=path.read_bytes(),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/my-settings")
    def api_my_settings(body: SettingsRequest, request: Request) -> Dict[str, Any]:
        """The announcement settings that belong to a person, not the school."""
        user = require_user(request)

        if body.clear_chime:
            services.accounts.set_chime(user.id, None)
        elif body.chime is not None:
            chosen = body.chime.strip() or None
            if chosen is not None and services.chimes.path_for(chosen) is None:
                raise AppError(400, "That sound isn't available.", "no_such_chime")
            services.accounts.set_chime(user.id, chosen)

        if body.announce_name is not None or body.spoken_name is not None:
            enabled = user.announce_name if body.announce_name is None else body.announce_name
            spoken = user.spoken_name if body.spoken_name is None else body.spoken_name
            try:
                services.accounts.set_announce_name(user.id, enabled, spoken)
            except ValueError as exc:
                raise AppError(400, str(exc), "bad_spoken_name")

        updated = services.accounts.get(user.id)
        log.info(
            "%s updated their settings (chime=%s, say name=%s)",
            user.username, updated.chime or "school default", updated.announce_name,
        )
        return {
            "chime": updated.chime,
            "announce_name": updated.announce_name,
            "spoken_name": updated.spoken_name,
            "announced_as": updated.announced_as,
            "example": normalize(
                f"Announcement from {updated.announced_as}. Buses are here."
            ).normalized,
        }

    @app.post("/api/normalize")
    def api_normalize(body: NormalizeRequest, request: Request) -> Dict[str, Any]:
        user = require_user(request)
        result = normalize(body.text)
        return {
            "raw": result.raw,
            "normalized": spoken_text_for(user, body.text),
            "warnings": result.warnings,
            "chars": len(body.text),
            "max_chars": services.config.max_chars,
            "speakable": bool(result.normalized.strip()),
        }

    @app.get("/api/status")
    def api_status(request: Request) -> Dict[str, Any]:
        require_user(request)
        return services.build_snapshot()

    # -- preview -----------------------------------------------------------

    @app.post("/api/preview")
    def api_preview(body: NormalizeRequest, request: Request) -> Response:
        """Synthesize the announcement and stream it back to this browser only.

        This never touches the audio device, never enters the queue, and never
        reaches the PA. It exists so somebody can hear how a surname or a room
        number comes out BEFORE four hundred people do.
        """
        user = require_user(request)

        spoken = spoken_text_for(user, body.text)
        if not spoken.strip():
            raise AppError(400, "Type an announcement first.", "empty")
        if len(body.text) > services.config.max_chars:
            raise AppError(
                400,
                f"That announcement is too long. Please keep it to "
                f"{services.config.max_chars} characters.",
                "too_long",
            )

        # Synthesis is CPU-heavy. Cap how many can run at once so a room full
        # of people previewing cannot slow down a real announcement.
        if not services.preview_slots.acquire(timeout=10):
            raise AppError(
                503,
                "The system is busy right now. Try Preview again in a moment.",
                "busy",
            )
        try:
            digest = hashlib.sha256(
                f"{services.tts.name}|{services.config.piper_length_scale}|"
                f"{spoken}".encode("utf-8")
            ).hexdigest()[:32]
            path = services.config.audio_cache_dir / f"preview-{digest}.wav"
            if not path.exists() or path.stat().st_size < 128:
                services.tts.synthesize(spoken, path)
            services.sweep_previews()
            audio = path.read_bytes()
        except TTSError as exc:
            log.error("Preview failed for %s: %s | %s", user.username, exc.message, exc.detail)
            raise AppError(503, exc.message, "tts_failed")
        finally:
            services.preview_slots.release()

        log.info("%s previewed %s characters", user.username, len(body.text))
        return Response(
            content=audio,
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'inline; filename="preview.wav"',
            },
        )

    # -- submit ------------------------------------------------------------

    @app.post("/api/announcements", status_code=201)
    def api_submit(body: AnnouncementRequest, request: Request) -> Dict[str, Any]:
        user = require_user(request)

        raw = body.text or ""
        if len(raw) > services.config.max_chars:
            raise AppError(
                400,
                f"That announcement is too long. Please keep it to "
                f"{services.config.max_chars} characters.",
                "too_long",
            )
        spoken = spoken_text_for(user, raw)
        if not spoken.strip():
            raise AppError(400, "Type an announcement first.", "empty")

        if body.zone != "all":
            raise AppError(
                400,
                "Only whole-building announcements are available right now.",
                "bad_zone",
            )

        decision = services.rate_limiter.check(user)
        if not decision.allowed:
            services.accounts.record_event(
                "announcement.rate_limited", username=user.username, user_id=user.id,
                ip=client_ip(request), detail=f"{decision.used} in {decision.window_seconds}s",
            )
            log.info("Rate limit hit by %s (%s in window)", user.username, decision.used)
            raise AppError(
                429, decision.message, "rate_limited",
                retry_after_seconds=decision.retry_after_seconds,
            )

        # The chime comes from the person's account, never from the request
        # body: a crafted or stale request must not be able to pick a
        # different sound. Accounts that have not chosen use the school
        # default from PA_DEFAULT_CHIME.
        chime_key = user.chime or services.config.default_chime
        if chime_key and services.chimes.path_for(chime_key) is None:
            raise AppError(500, "The chime sound is missing. Tell IT.", "chime_missing")

        estimated = services.player.estimate_seconds(spoken, chime_key)
        item_id = services.db.enqueue(
            # raw_text stays exactly what they typed, so the log shows what a
            # person wrote; normalized_text is what the school actually heard.
            raw_text=raw,
            normalized_text=spoken,
            chime=chime_key,
            user_name=user.display_name,
            user_id=user.id,
            priority=1 if body.priority else 0,
            zone=body.zone,
            estimated_seconds=estimated,
        )
        services.player.notify_new_item()
        log.info(
            "Announcement %s queued by %s (%s chars, priority=%s)",
            item_id, user.username, len(raw), body.priority,
            extra={"announcement_id": item_id, "username": user.username,
                   "normalized": spoken},
        )
        services.publish_status()

        snapshot = services.build_snapshot()
        position = next((q["position"] for q in snapshot["queue"] if q["id"] == item_id), 0)
        seconds_until = next((q["seconds_until"] for q in snapshot["queue"] if q["id"] == item_id), 0.0)
        return {
            "id": item_id,
            "normalized": spoken,
            "warnings": normalize(raw).warnings,
            "position": position,
            "ahead": max(0, position - 1) + (1 if snapshot["now_playing"] else 0),
            "seconds_until": seconds_until,
        }

    @app.post("/api/announcements/{item_id}/stop")
    def api_stop(item_id: int, request: Request,
                 body: Optional[StopRequest] = None) -> Dict[str, Any]:
        """Stop this announcement, whether it is playing or still waiting.

        Allowed for the person who sent it and for any administrator. Everyone
        else is refused -- a teacher must not be able to silence the office.
        """
        user = require_user(request)
        item = services.db.get(item_id)
        if item is None:
            raise AppError(404, "That announcement no longer exists.", "not_found")

        if not user.is_admin and item["user_id"] != user.id:
            raise AppError(
                403,
                "You can only stop your own announcements. An administrator can stop any.",
                "not_yours",
            )

        actor = user.display_name
        if item["state"] == STATE_PLAYING:
            if services.player.request_stop(actor, item_id=item_id):
                services.accounts.record_event(
                    "announcement.stopped", username=user.username, user_id=user.id,
                    ip=client_ip(request), detail=f"announcement {item_id} (playing)",
                )
                services.publish_status()
                return {"stopped": True, "was": "playing"}
        if item["state"] == STATE_QUEUED:
            if services.db.cancel_queued(item_id, actor):
                services.accounts.record_event(
                    "announcement.cancelled", username=user.username, user_id=user.id,
                    ip=client_ip(request), detail=f"announcement {item_id} (queued)",
                )
                log.info("Announcement %s cancelled from the queue by %s", item_id, user.username)
                services.publish_status()
                return {"stopped": True, "was": "queued"}
        return {"stopped": False, "was": item["state"]}

    @app.post("/api/stop")
    def api_stop_current(request: Request,
                         body: Optional[StopRequest] = None) -> Dict[str, Any]:
        """Admin-only panic button: stop whatever is playing, whoever sent it."""
        user = require_admin(request)
        stopped = services.player.request_stop(user.display_name)
        if stopped:
            services.accounts.record_event(
                "announcement.stopped", username=user.username, user_id=user.id,
                ip=client_ip(request), detail="stop-current",
            )
        services.publish_status()
        return {"stopped": stopped}

    @app.post("/api/test-audio", status_code=201)
    def api_test_audio(request: Request) -> Dict[str, Any]:
        """Play just a chime, so someone can verify the PA wiring.

        Goes through the normal queue rather than straight to the device, so it
        can never overlap a real announcement.
        """
        user = require_user(request)
        item_id = services.db.enqueue(
            raw_text="(audio test)",
            normalized_text="",
            chime=user.chime or services.config.default_chime,
            user_name=user.display_name,
            user_id=user.id,
            kind="test",
            estimated_seconds=services.chimes.seconds_for(services.config.default_chime) + 0.6,
        )
        services.player.notify_new_item()
        services.publish_status()
        return {"id": item_id}

    # -- history (the audit trail) -----------------------------------------

    @app.get("/api/announcements")
    def api_history(request: Request, limit: int = 50) -> Dict[str, Any]:
        """Recent announcements.

        Staff see their own; administrators see everyone's. The full
        searchable, filterable, exportable log arrives with the admin panel in
        Phase 3 -- this is the same data, unfiltered.
        """
        user = require_user(request)
        limit = max(1, min(int(limit), 500))
        rows = services.db.recent(limit=limit if user.is_admin else limit * 4)
        if not user.is_admin:
            rows = [r for r in rows if r["user_id"] == user.id][:limit]
        return {
            "announcements": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "user_name": r["user_name"],
                    "raw_text": r["raw_text"],
                    "normalized_text": r["normalized_text"],
                    "chime": r["chime"],
                    "zone": r["zone"],
                    "priority": bool(r["priority"]),
                    "state": r["state"],
                    "duration_seconds": r["duration_seconds"],
                    "error": r["error"],
                    "stopped_by": r["stopped_by"],
                }
                for r in rows
            ],
            "scope": "everyone" if user.is_admin else "you",
        }

    @app.post("/api/admin/announcements/purge")
    def api_purge_log(body: PurgeRequest, request: Request) -> Dict[str, Any]:
        """Clear finished announcements from the log.

        Administrators only, and the clearing is itself recorded in the
        security trail -- the log is the main thing preventing misuse, so
        emptying it must leave a mark saying who emptied it.
        """
        admin = require_admin(request)

        before = None
        described = "everything"
        if body.older_than_days is not None:
            days = int(body.older_than_days)
            if days < 0:
                raise AppError(400, "That is not a number of days.", "bad_range")
            cutoff = utcnow() - timedelta(days=days)
            before = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
            described = f"older than {days} day{'s' if days != 1 else ''}"

        removed = services.db.purge_announcements(before=before)
        services.accounts.record_event(
            "log.cleared", username=admin.username, user_id=admin.id,
            ip=client_ip(request), detail=f"{removed} announcement(s), {described}",
        )
        log.warning("%s cleared %s announcement(s) from the log (%s)",
                    admin.username, removed, described)
        services.publish_status()
        return {"removed": removed, "scope": described}

    @app.get("/api/admin/network")
    def api_network(request: Request) -> Dict[str, Any]:
        """What this computer is, and where. Shown on the admin dashboard."""
        require_admin(request)
        return {
            "staff_url": staff_url(services.config.port),
            "all_urls": all_urls(services.config.port),
            "hostname": hostname(),
            "port": services.config.port,
            # Looked up on request rather than at startup, with a short
            # timeout, because the PA machine may have no internet.
            "public_address": public_address(timeout=3.0),
            "audio_device": services.audio.describe(),
            "voice": services.tts.describe(),
            "version": VERSION,
        }

    # -- scheduled announcements -------------------------------------------

    def schedule_view(row: Dict[str, Any]) -> Dict[str, Any]:
        at = parse_time(row["at_time"])
        days = parse_days(row.get("days"))
        local_next = None
        if row.get("next_run_at"):
            moment = parse_iso(row["next_run_at"])
            if moment is not None and services.zone is not None:
                local = moment.astimezone(services.zone)
                local_next = local.strftime("%a %d %b, ") + format_time(local.time())
            elif moment is not None:
                local_next = moment.isoformat()
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "text": row["text"],
            "kind": row["kind"],
            "at_time": row["at_time"],
            "at_time_label": format_time(at),
            "days": days,
            "on_date": row["on_date"],
            "starts_on": row["starts_on"],
            "ends_on": row["ends_on"],
            "priority": bool(row["priority"]),
            "chime": row["chime"],
            "enabled": bool(row["enabled"]),
            "when": describe(row["kind"], at, days, row["on_date"]),
            "next_run_label": local_next,
            "last_run_at": row["last_run_at"],
            "last_result": row["last_result"],
        }

    def build_schedule_fields(body: ScheduleRequest, user) -> Dict[str, Any]:
        text = (body.text or "").strip()
        if not text:
            raise AppError(400, "Type what should be announced.", "empty")
        if len(text) > services.config.max_chars:
            raise AppError(
                400,
                f"That announcement is too long. Please keep it to "
                f"{services.config.max_chars} characters.",
                "too_long",
            )
        if not normalize(text).normalized.strip():
            raise AppError(400, "There is nothing speakable in that.", "empty")
        if body.kind not in KINDS:
            raise AppError(400, "Choose how often this should repeat.", "bad_kind")

        try:
            at = parse_time(body.at_time)
            days = parse_days(",".join(str(d) for d in (body.days or [])))
        except ScheduleError as exc:
            raise AppError(400, str(exc), "bad_schedule")

        if body.kind == "weekly" and not days:
            raise AppError(400, "Pick which days of the week this should run.",
                           "bad_schedule")
        if body.kind == "once" and not body.on_date:
            raise AppError(400, "Give the date this should run on.", "bad_schedule")

        if body.chime is not None and body.chime.strip():
            if services.chimes.path_for(body.chime.strip()) is None:
                raise AppError(400, "That sound isn't available.", "no_such_chime")

        return {
            "user_id": user.id,
            "user_name": user.display_name,
            "text": text,
            "chime": (body.chime or "").strip() or None,
            "priority": 1 if body.priority else 0,
            "zone": "all",
            "kind": body.kind,
            "at_time": f"{at.hour:02d}:{at.minute:02d}",
            "days": ",".join(str(d) for d in days) or None,
            "on_date": body.on_date or None,
            "starts_on": body.starts_on or None,
            "ends_on": body.ends_on or None,
            "enabled": 1 if body.enabled else 0,
        }

    @app.get("/api/schedules")
    def api_list_schedules(request: Request) -> Dict[str, Any]:
        """Staff see their own; administrators see everyone's."""
        user = require_user(request)
        rows = services.db.schedules(None if user.is_admin else user.id)
        return {
            "schedules": [schedule_view(row) for row in rows],
            "scope": "everyone" if user.is_admin else "you",
            "timezone": services.config.timezone,
            "timezone_ok": services.zone is not None,
        }

    @app.post("/api/schedules", status_code=201)
    def api_create_schedule(body: ScheduleRequest, request: Request) -> Dict[str, Any]:
        user = require_user(request)
        fields = build_schedule_fields(body, user)

        try:
            preview = dict(fields)
            next_run = services.compute_next_run(preview)
        except ScheduleError as exc:
            raise AppError(400, str(exc), "bad_schedule")
        if next_run is None:
            raise AppError(
                400,
                "That would never happen -- check the date and the days.",
                "never_runs",
            )

        schedule_id = services.db.add_schedule(next_run_at=next_run, **fields)
        log.info("%s scheduled an announcement (%s)", user.username, fields["kind"])
        services.accounts.record_event(
            "schedule.created", username=user.username, user_id=user.id,
            ip=client_ip(request), detail=f"schedule {schedule_id}",
        )
        return {"schedule": schedule_view(services.db.get_schedule(schedule_id))}

    @app.post("/api/schedules/{schedule_id}")
    def api_update_schedule(schedule_id: int, body: ScheduleRequest,
                            request: Request) -> Dict[str, Any]:
        user = require_user(request)
        existing = services.db.get_schedule(schedule_id)
        if existing is None:
            raise AppError(404, "That schedule no longer exists.", "not_found")
        if not user.is_admin and existing["user_id"] != user.id:
            raise AppError(403, "You can only change your own scheduled "
                                "announcements.", "not_yours")

        fields = build_schedule_fields(body, user)
        # Keep it attributed to whoever created it, even when an administrator
        # edits it -- the log has to stay honest about whose announcement it is.
        fields["user_id"] = existing["user_id"]
        fields["user_name"] = existing["user_name"]

        next_run = services.compute_next_run(dict(fields))
        if next_run is None and body.enabled:
            raise AppError(400, "That would never happen -- check the date and "
                                "the days.", "never_runs")
        services.db.update_schedule(schedule_id, next_run_at=next_run, **fields)
        return {"schedule": schedule_view(services.db.get_schedule(schedule_id))}

    @app.post("/api/schedules/{schedule_id}/enabled")
    def api_toggle_schedule(schedule_id: int, request: Request,
                            enabled: bool = True) -> Dict[str, Any]:
        user = require_user(request)
        existing = services.db.get_schedule(schedule_id)
        if existing is None:
            raise AppError(404, "That schedule no longer exists.", "not_found")
        if not user.is_admin and existing["user_id"] != user.id:
            raise AppError(403, "You can only change your own scheduled "
                                "announcements.", "not_yours")

        next_run = services.compute_next_run(existing) if enabled else None
        services.db.update_schedule(
            schedule_id, enabled=1 if enabled else 0, next_run_at=next_run)
        return {"schedule": schedule_view(services.db.get_schedule(schedule_id))}

    @app.post("/api/schedules/{schedule_id}/delete")
    def api_delete_schedule(schedule_id: int, request: Request) -> Dict[str, Any]:
        user = require_user(request)
        existing = services.db.get_schedule(schedule_id)
        if existing is None:
            raise AppError(404, "That schedule no longer exists.", "not_found")
        if not user.is_admin and existing["user_id"] != user.id:
            raise AppError(403, "You can only delete your own scheduled "
                                "announcements.", "not_yours")
        services.db.delete_schedule(schedule_id)
        services.accounts.record_event(
            "schedule.deleted", username=user.username, user_id=user.id,
            ip=client_ip(request), detail=f"schedule {schedule_id}",
        )
        return {"deleted": True}

    # -- admin: accounts ---------------------------------------------------

    @app.get("/api/admin/users")
    def api_list_users(request: Request) -> Dict[str, Any]:
        require_admin(request)
        return {"users": services.accounts.list_users()}

    @app.post("/api/admin/users", status_code=201)
    def api_create_user(body: NewUserRequest, request: Request) -> Dict[str, Any]:
        admin = require_admin(request)
        password = generate_password()
        try:
            user = services.accounts.create_user(
                username=body.username,
                display_name=body.display_name,
                password=password,
                role=body.role,
                created_by=admin.id,
            )
        except ValueError as exc:
            raise AppError(400, str(exc), "invalid_user")
        log.info("%s created account %s (%s)", admin.username, user.username, user.role)
        # The password is shown once, here, and never stored in readable form.
        return {"user": user.public(), "password": password}

    @app.post("/api/admin/users/{user_id}")
    def api_update_user(user_id: int, body: UserUpdateRequest,
                        request: Request) -> Dict[str, Any]:
        admin = require_admin(request)
        target = services.accounts.get(user_id)
        if target is None:
            raise AppError(404, "There is no account with that number.", "not_found")
        try:
            if body.role is not None:
                if body.role not in ROLES:
                    raise ValueError("Role must be 'staff' or 'admin'.")
                services.accounts.set_role(user_id, body.role)
            if body.is_active is not None:
                if not body.is_active and user_id == admin.id:
                    raise ValueError("You cannot turn off your own account.")
                services.accounts.set_active(user_id, body.is_active)
            if body.display_name is not None and body.display_name.strip():
                services.db.connect().execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (body.display_name.strip(), user_id),
                )
        except ValueError as exc:
            raise AppError(400, str(exc), "invalid_change")
        return {"user": services.accounts.get(user_id).public()}

    @app.post("/api/admin/users/{user_id}/reset-password")
    def api_reset_password(user_id: int, request: Request) -> Dict[str, Any]:
        admin = require_admin(request)
        if services.accounts.get(user_id) is None:
            raise AppError(404, "There is no account with that number.", "not_found")
        password = services.accounts.reset_password(user_id, by=admin.id)
        log.info("%s reset the password for account %s", admin.username, user_id)
        return {"password": password}

    @app.post("/api/admin/users/{user_id}/unlock")
    def api_unlock_user(user_id: int, request: Request) -> Dict[str, Any]:
        require_admin(request)
        if services.accounts.get(user_id) is None:
            raise AppError(404, "There is no account with that number.", "not_found")
        services.accounts.unlock(user_id)
        return {"unlocked": True}

    @app.get("/api/admin/security-events")
    def api_security_events(request: Request, limit: int = 100) -> Dict[str, Any]:
        require_admin(request)
        return {"events": services.accounts.recent_events(limit=max(1, min(int(limit), 500)))}

    # -- live status -------------------------------------------------------

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        # A GET, so no CSRF token is needed -- but it still requires a session.
        require_user(request)
        queue = services.broadcaster.subscribe()

        async def stream():
            # Poll on a short interval rather than blocking for the whole
            # keep-alive period, so a browser that closes its tab is noticed
            # within about a second instead of hanging a slot open for fifteen.
            last_ping = time.monotonic()
            try:
                if services.broadcaster.latest:
                    yield sse_message(services.broadcaster.latest)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                        yield sse_message(payload)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_ping >= KEEPALIVE_SECONDS:
                            # Keeps proxies from closing the stream and lets the
                            # browser notice a dead server.
                            yield ": keep-alive\n\n"
                            last_ping = time.monotonic()
            finally:
                services.broadcaster.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- health ------------------------------------------------------------

    @app.get("/health")
    def health() -> JSONResponse:
        """Deliberately open, with no sign-in.

        Monitoring systems need it, and it is the first thing DEPLOYMENT.md
        tells IT to check. It reports no announcement text and no personal
        data -- only whether the parts are working.
        """
        audio_ok, audio_detail = True, ""
        try:
            services.audio.check_available()
        except AudioUnavailable as exc:
            audio_ok, audio_detail = False, exc.detail or exc.message

        tts_ok, tts_detail = True, ""
        try:
            services.tts.check_ready()
        except TTSError as exc:
            tts_ok, tts_detail = False, exc.detail or exc.message

        db_writable = services.db.is_writable()
        counts = services.db.count_by_state()
        accounts_exist = services.accounts.count_active_admins() > 0
        setup_pending = services.accounts.setup_pending()
        accounts_ok = accounts_exist and not setup_pending
        healthy = audio_ok and tts_ok and db_writable and accounts_ok

        payload = {
            "status": "ok" if healthy else "degraded",
            "version": VERSION,
            "service": True,
            "audio": {
                "ok": audio_ok,
                "backend": services.audio.name,
                "device": services.audio.describe(),
                "detail": audio_detail,
            },
            "tts": {
                "ok": tts_ok,
                "engine": services.tts.name,
                "voice": services.tts.describe(),
                "detail": tts_detail,
            },
            "database": {"writable": db_writable, "path": str(services.config.db_path)},
            "accounts": {
                "ok": accounts_ok,
                "total": services.accounts.count_users(),
                "active_admins": services.accounts.count_active_admins(),
                "setup_pending": setup_pending,
                "detail": (
                    "No active administrator account. Nobody can manage the system."
                    if not accounts_exist else
                    "First-time setup is not finished: the starting administrator "
                    "account is still on the password the system issued."
                    if setup_pending else ""
                ),
            },
            "address": {
                "staff_url": staff_url(services.config.port),
                "all_urls": all_urls(services.config.port),
                "hostname": hostname(),
            },
            "queue_depth": counts.get(STATE_QUEUED, 0),
            "playing": counts.get(STATE_PLAYING, 0) > 0,
            "clients_connected": services.broadcaster.subscriber_count,
        }
        return JSONResponse(payload, status_code=200 if healthy else 503)

    return app
