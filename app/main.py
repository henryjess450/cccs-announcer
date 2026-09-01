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
from datetime import timedelta
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
from .chimes import ChimeLibrary
from .config import Config, ensure_directories, load_config
from .db import (
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PLAYING,
    STATE_QUEUED,
    Database,
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
from .security import generate_password, verify_password
from .singleton import InstanceLock
from .tts import build_tts_engine
from .tts.base import TTSError

log = logging.getLogger(__name__)

VERSION = "2.0.0-phase2"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# How often an idle SSE stream emits a comment frame to stay alive.
KEEPALIVE_SECONDS = 15.0

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


class SetupRequest(BaseModel):
    """First-run: claim the starting administrator account."""
    username: str
    display_name: str
    current_password: str
    new_password: str


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
            note = "  (must set a new password)" if user["must_change_password"] else ""
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

    # -- saying the address out loud --------------------------------------

    def start_address_announcements(self) -> None:
        """Say the address over the PA until the announcer has been set up.

        The point is that whoever installed this is standing in a corridor with
        no idea what address to type. Hearing it is faster than walking back to
        the machine.

        The guards matter more than the feature:

          * It only runs while the first administrator account is UNCLAIMED.
            That is a brand-new install. Once somebody signs in and sets the
            account up it stops immediately and never happens again -- not on
            the next reboot, not a year later.
          * It stops after a fixed number of repeats even if nobody ever signs
            in, so a forgotten machine cannot talk over lessons all day.
          * It goes through the normal queue like anything else, so it can
            never overlap a real announcement.
          * PA_ANNOUNCE_ADDRESS_ON_START=false turns it off entirely.
        """
        if not self.config.announce_address_on_start:
            return
        if not self.accounts.setup_pending():
            return

        address = primary_address()
        if address is None:
            log.info("Not speaking the address: this computer is not on a network")
            return

        text = startup_announcement(address, self.config.port)
        interval = max(10, self.config.announce_address_interval_seconds)
        limit = max(1, self.config.announce_address_max_times)

        def loop() -> None:
            said = 0
            while said < limit and not self._stop_address.is_set():
                # Re-checked every time round: the moment somebody claims the
                # account, this goes quiet.
                if not self.accounts.setup_pending():
                    log.info("Address announcements stopped: the announcer has been set up")
                    return
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
                self._stop_address.wait(interval)

            if said >= limit:
                log.warning(
                    "Stopped saying the address after %s times. The announcer is "
                    "still not set up -- sign in at %s", said, staff_url(self.config.port),
                )

        log.warning(
            "Saying the address over the PA every %ss until the announcer is set up "
            "(at most %s times)", interval, limit,
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

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def render_page(name: str) -> HTMLResponse:
        html = (STATIC_DIR / name).read_text(encoding="utf-8")
        return HTMLResponse(
            html.replace("__VERSION__", VERSION),
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
        log.info("%s changed their password", user.username)
        return {"changed": True}

    # -- compose-screen data -----------------------------------------------

    @app.get("/api/config")
    def api_config(request: Request) -> Dict[str, Any]:
        user = require_user(request)
        chime = services.chimes.get(services.config.default_chime)
        return {
            "max_chars": services.config.max_chars,
            "default_chime": services.config.default_chime,
            # The chime is fixed for the whole school, so the compose screen
            # shows no chooser. The list is still reported for the admin panel
            # and the deployment checklist.
            "chime_locked": True,
            "chime_label": chime.label if chime else services.config.default_chime,
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

    @app.post("/api/normalize")
    def api_normalize(body: NormalizeRequest, request: Request) -> Dict[str, Any]:
        require_user(request)
        result = normalize(body.text)
        return {
            "raw": result.raw,
            "normalized": result.normalized,
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

        result = normalize(body.text)
        if not result.normalized.strip():
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
                f"{result.normalized}".encode("utf-8")
            ).hexdigest()[:32]
            path = services.config.audio_cache_dir / f"preview-{digest}.wav"
            if not path.exists() or path.stat().st_size < 128:
                services.tts.synthesize(result.normalized, path)
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
        result = normalize(raw)
        if not result.normalized.strip():
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

        # One chime for the whole school, set by PA_DEFAULT_CHIME. Staff do not
        # choose a chime, so nothing in the request body may change it.
        chime_key = services.config.default_chime
        if chime_key and services.chimes.path_for(chime_key) is None:
            raise AppError(500, "The chime sound is missing. Tell IT.", "chime_missing")

        estimated = services.player.estimate_seconds(result.normalized, chime_key)
        item_id = services.db.enqueue(
            raw_text=raw,
            normalized_text=result.normalized,
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
                   "normalized": result.normalized},
        )
        services.publish_status()

        snapshot = services.build_snapshot()
        position = next((q["position"] for q in snapshot["queue"] if q["id"] == item_id), 0)
        seconds_until = next((q["seconds_until"] for q in snapshot["queue"] if q["id"] == item_id), 0.0)
        return {
            "id": item_id,
            "normalized": result.normalized,
            "warnings": result.warnings,
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
            chime=services.config.default_chime,
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
                must_change_password=True,
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
