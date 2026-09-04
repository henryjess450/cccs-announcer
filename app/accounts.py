"""Accounts, sessions, and the security trail.

Why sessions live in the database rather than in a signed self-contained token:
deactivating an account has to take effect immediately. A teacher who leaves at
lunchtime must not still be able to address the school at two o'clock because
their token had not expired yet. Server-side sessions can be revoked; signed
tokens cannot.

Only the SHA-256 of the session cookie is stored. Someone who can read the
database still cannot use the sessions in it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .db import Database, now_iso, parse_iso, utcnow
from .security import (
    generate_password,
    hash_password,
    new_token,
    token_fingerprint,
    verify_password,
)

log = logging.getLogger(__name__)

ROLE_STAFF = "staff"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STAFF, ROLE_ADMIN)

# A hash of a value nobody will ever type. Verified against when a username
# does not exist, so a wrong username costs the same time as a wrong password
# and cannot be distinguished by timing.
_DUMMY_HASH = hash_password("this account does not exist")


class AuthError(Exception):
    """Sign-in refused. `message` is shown to the person and says no more than
    it must -- never which half of the credentials was wrong."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason        # for the log, not the screen


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    #: True while this is the account the system made for itself on first
    #: start and no real person has claimed it yet.
    is_bootstrap: bool = False
    #: The chime this person's announcements play. None means the school
    #: default, so an account that has never chosen still works.
    chime: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "is_admin": self.is_admin,
            "must_change_password": self.must_change_password,
            "is_bootstrap": self.is_bootstrap,
            "chime": self.chime,
        }


def _user_from_row(row) -> User:
    return User(
        id=int(row["id"]),
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        must_change_password=bool(row["must_change_password"]),
        is_bootstrap=bool(row["is_bootstrap"]) if "is_bootstrap" in row.keys() else False,
        chime=(row["chime"] if "chime" in row.keys() else None) or None,
    )


class Accounts:
    def __init__(self, database: Database, config: Config):
        self.db = database
        self.config = config

    # -- security trail ------------------------------------------------------

    def record_event(
        self,
        event: str,
        *,
        username: Optional[str] = None,
        user_id: Optional[int] = None,
        ip: Optional[str] = None,
        detail: str = "",
    ) -> None:
        self.db.connect().execute(
            "INSERT INTO security_events (at, event, username, user_id, ip, detail) "
            "VALUES (?,?,?,?,?,?)",
            (now_iso(), event, username, user_id, ip, detail),
        )

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- users ---------------------------------------------------------------

    def count_users(self) -> int:
        return int(self.db.connect().execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def count_active_admins(self) -> int:
        return int(self.db.connect().execute(
            "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1", (ROLE_ADMIN,)
        ).fetchone()[0])

    def get(self, user_id: int) -> Optional[User]:
        row = self.db.connect().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row else None

    def get_by_username(self, username: str):
        return self.db.connect().execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()

    def list_users(self) -> List[Dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT id, username, display_name, role, is_active, must_change_password, "
            "is_bootstrap, chime, created_at, last_login_at, locked_until "
            "FROM users ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role: str = ROLE_STAFF,
        # Staff keep the password they are given. Forcing a change on first
        # sign-in was one more thing to go wrong between handing somebody a
        # password and them being able to announce, and an administrator who
        # can reset passwords can already act as anyone -- so the change was
        # not buying much. The gate still exists for the first-run
        # administrator account, which genuinely must be claimed.
        must_change_password: bool = False,
        created_by: Optional[int] = None,
    ) -> User:
        username = username.strip()
        display_name = display_name.strip() or username
        if not username:
            raise ValueError("A username is required.")
        if " " in username:
            raise ValueError("A username cannot contain spaces.")
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}.")
        if self.get_by_username(username) is not None:
            raise ValueError(f"There is already an account called {username!r}.")

        cursor = self.db.connect().execute(
            "INSERT INTO users (username, display_name, password_hash, role, is_active, "
            "must_change_password, created_at, created_by) VALUES (?,?,?,?,1,?,?,?)",
            (username, display_name, hash_password(password), role,
             1 if must_change_password else 0, now_iso(), created_by),
        )
        user = self.get(int(cursor.lastrowid))
        assert user is not None
        self.record_event("user.created", username=username, user_id=user.id,
                          detail=f"role={role}")
        return user

    def set_active(self, user_id: int, active: bool) -> None:
        """Deactivating also ends every session that account has open.

        Without that, a deactivated account keeps working until its session
        happens to expire -- which is exactly the window that matters.
        """
        if not active and self._is_last_active_admin(user_id):
            raise ValueError("This is the only administrator. Make someone else an "
                             "administrator first.")
        self.db.connect().execute(
            "UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id)
        )
        if not active:
            self.end_sessions_for_user(user_id)
        self.record_event("user.activated" if active else "user.deactivated", user_id=user_id)

    def set_role(self, user_id: int, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}.")
        if role != ROLE_ADMIN and self._is_last_active_admin(user_id):
            raise ValueError("This is the only administrator. Make someone else an "
                             "administrator first.")
        self.db.connect().execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        self.record_event("user.role_changed", user_id=user_id, detail=f"role={role}")

    def _is_last_active_admin(self, user_id: int) -> bool:
        row = self.db.connect().execute(
            "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or row["role"] != ROLE_ADMIN or not row["is_active"]:
            return False
        return self.count_active_admins() <= 1

    def set_password(self, user_id: int, password: str, *, must_change: bool) -> None:
        self.db.connect().execute(
            "UPDATE users SET password_hash = ?, must_change_password = ?, "
            "failed_logins = 0, locked_until = NULL WHERE id = ?",
            (hash_password(password), 1 if must_change else 0, user_id),
        )

    def reset_password(self, user_id: int, *, by: Optional[int] = None) -> str:
        """Admin action. Returns the new password to read out once; it is never
        stored in readable form and cannot be shown again.

        They keep this password -- they are not asked to change it.
        """
        password = generate_password()
        self.set_password(user_id, password, must_change=False)
        self.end_sessions_for_user(user_id)
        self.record_event("user.password_reset", user_id=user_id, detail=f"by={by}")
        return password

    def change_own_password(self, user_id: int, current: str, new: str) -> None:
        row = self.db.connect().execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or not verify_password(current, row["password_hash"]):
            raise AuthError("That current password is not right.", "bad_current_password")
        problem = password_problem(new)
        if problem:
            raise ValueError(problem)
        self.set_password(user_id, new, must_change=False)
        self.record_event("user.password_changed", user_id=user_id)

    # -- authentication ------------------------------------------------------

    def authenticate(self, username: str, password: str, *, ip: Optional[str] = None) -> User:
        row = self.get_by_username(username or "")

        if row is None:
            # Same work as a real check, so a wrong username and a wrong
            # password take the same time and cannot be told apart.
            verify_password(password or "", _DUMMY_HASH)
            self.record_event("login.failed", username=username, ip=ip, detail="no such account")
            raise AuthError("That username or password is not right.", "unknown_user")

        locked_until = parse_iso(row["locked_until"])
        if locked_until and locked_until > utcnow():
            minutes = max(1, int((locked_until - utcnow()).total_seconds() // 60) + 1)
            self.record_event("login.locked_out", username=row["username"],
                              user_id=row["id"], ip=ip)
            raise AuthError(
                f"This account is locked for about {minutes} more minute"
                f"{'s' if minutes != 1 else ''} after too many wrong passwords. "
                "An administrator can unlock it.",
                "locked_out",
            )

        if not verify_password(password or "", row["password_hash"]):
            self._register_failure(row, ip)
            raise AuthError("That username or password is not right.", "bad_password")

        if not row["is_active"]:
            # Checked after the password so a deactivated account cannot be
            # confirmed to exist by someone guessing usernames.
            self.record_event("login.deactivated", username=row["username"],
                              user_id=row["id"], ip=ip)
            raise AuthError(
                "This account has been turned off. Ask the office to turn it back on.",
                "inactive",
            )

        self.db.connect().execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? "
            "WHERE id = ?",
            (now_iso(), row["id"]),
        )
        self.record_event("login.ok", username=row["username"], user_id=row["id"], ip=ip)
        return _user_from_row(row)

    def _register_failure(self, row, ip: Optional[str]) -> None:
        failures = int(row["failed_logins"]) + 1
        locked_until = None
        if failures >= self.config.login_max_failures:
            locked_until = (
                utcnow() + timedelta(seconds=self.config.login_lockout_seconds)
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.db.connect().execute(
            "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
            (failures, locked_until, row["id"]),
        )
        self.record_event(
            "login.failed", username=row["username"], user_id=row["id"], ip=ip,
            detail=f"attempt {failures}" + (" (now locked)" if locked_until else ""),
        )

    def unlock(self, user_id: int) -> None:
        self.db.connect().execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", (user_id,)
        )
        self.record_event("user.unlocked", user_id=user_id)

    def set_chime(self, user_id: int, chime: Optional[str]) -> None:
        """Choose the sound this person's announcements play.

        None puts them back on the school default. The caller checks the chime
        actually exists -- this layer does not know about files.
        """
        self.db.connect().execute(
            "UPDATE users SET chime = ? WHERE id = ?", (chime or None, user_id)
        )

    # -- first run -----------------------------------------------------------

    def ensure_bootstrap_admin(self, password: Optional[str] = None):
        """Create the starting administrator, once, on a database with no users.

        Existing so nobody has to open a command prompt to get started: start
        the announcer, read the password off the screen, sign in, and set the
        account up in the browser.

        The password is GENERATED rather than a fixed well-known default. A
        shipped default password on a machine reachable from the school network
        is an open door for as long as it takes somebody to sign in for the
        first time. Whoever is standing at the PA machine can read a generated
        one just as easily; somebody on the far side of the network cannot
        guess it. Set PA_BOOTSTRAP_PASSWORD in .env if a fixed one is genuinely
        wanted.

        Returns (user, password) when it creates the account, or None when
        accounts already exist -- it never resets anything.
        """
        if self.count_users() > 0:
            return None

        chosen = password or self.config.bootstrap_password or generate_password(4)
        username = self.config.bootstrap_username
        cursor = self.db.connect().execute(
            "INSERT INTO users (username, display_name, password_hash, role, is_active, "
            "must_change_password, created_at, created_by, is_bootstrap) "
            "VALUES (?,?,?,?,1,1,?,NULL,1)",
            (username, "Administrator", hash_password(chosen), ROLE_ADMIN, now_iso()),
        )
        user = self.get(int(cursor.lastrowid))
        self.record_event("user.bootstrap_created", username=username,
                          user_id=user.id if user else None,
                          detail="created automatically on first start")
        return user, chosen

    def setup_pending(self) -> bool:
        """True while a bootstrap account is still unclaimed.

        /health reports this as degraded: an announcer nobody has taken
        ownership of is not finished being installed.
        """
        row = self.db.connect().execute(
            "SELECT COUNT(*) FROM users WHERE is_bootstrap = 1 AND is_active = 1"
        ).fetchone()
        return int(row[0]) > 0

    def complete_setup(
        self, user_id: int, *, username: str, display_name: str, password: str
    ) -> User:
        """Turn the bootstrap account into a real person's account.

        Renames it, sets a real password, and clears both the forced-change and
        bootstrap flags in one step, so there is no moment where the account is
        usable while still on its issued password.
        """
        current = self.get(user_id)
        if current is None:
            raise ValueError("That account no longer exists.")

        username = (username or "").strip()
        display_name = (display_name or "").strip()

        problem = username_problem(username)
        if problem:
            raise ValueError(problem)
        if not display_name:
            raise ValueError("A full name is required -- it is what the school sees.")
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)

        clash = self.get_by_username(username)
        if clash is not None and int(clash["id"]) != user_id:
            raise ValueError(f"There is already an account called {username!r}.")

        self.db.connect().execute(
            "UPDATE users SET username = ?, display_name = ?, password_hash = ?, "
            "must_change_password = 0, is_bootstrap = 0, failed_logins = 0, "
            "locked_until = NULL WHERE id = ?",
            (username, display_name, hash_password(password), user_id),
        )
        # Every other session for this account dies: if anyone else signed in
        # with the issued password while it was still valid, they are out now.
        self.db.connect().execute(
            "DELETE FROM sessions WHERE user_id = ?", (user_id,)
        )
        self.record_event("user.setup_completed", username=username, user_id=user_id,
                          detail=f"was {current.username!r}")
        updated = self.get(user_id)
        assert updated is not None
        return updated

    # -- sessions ------------------------------------------------------------

    def start_session(
        self, user: User, *, ip: Optional[str] = None, user_agent: Optional[str] = None
    ) -> Tuple[str, str]:
        """Returns (cookie_token, csrf_token). Only the hash of the cookie is stored."""
        token = new_token()
        csrf = new_token()
        expires = utcnow() + timedelta(hours=self.config.session_max_hours)
        self.db.connect().execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, "
            "last_seen_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?,?,?)",
            (token_fingerprint(token), user.id, csrf, now_iso(), now_iso(),
             expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
             ip, (user_agent or "")[:400]),
        )
        return token, csrf

    def load_session(self, token: Optional[str]):
        """Return (User, session_row) for a live session, or None.

        Enforces both limits: an absolute maximum age, and an idle window. The
        idle window is what stops an unattended logged-in computer in an empty
        classroom from being an open microphone.
        """
        if not token:
            return None
        fingerprint = token_fingerprint(token)
        row = self.db.connect().execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            return None

        now = utcnow()
        expires = parse_iso(row["expires_at"])
        last_seen = parse_iso(row["last_seen_at"])
        idle_limit = timedelta(minutes=self.config.session_idle_minutes)

        if expires is None or expires <= now:
            self._delete_session(fingerprint)
            return None
        if last_seen is None or (now - last_seen) > idle_limit:
            self._delete_session(fingerprint)
            self.record_event("session.idle_timeout", user_id=row["user_id"])
            return None

        user = self.get(int(row["user_id"]))
        if user is None or not user.is_active:
            self._delete_session(fingerprint)
            return None

        # Slide the idle window.
        self.db.connect().execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now_iso(), fingerprint)
        )
        return user, dict(row)

    def end_session(self, token: Optional[str]) -> None:
        if token:
            self._delete_session(token_fingerprint(token))

    def _delete_session(self, fingerprint: str) -> None:
        self.db.connect().execute("DELETE FROM sessions WHERE token_hash = ?", (fingerprint,))

    def end_sessions_for_user(self, user_id: int) -> int:
        cursor = self.db.connect().execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cursor.rowcount

    def purge_expired_sessions(self) -> int:
        cursor = self.db.connect().execute(
            "DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),)
        )
        return cursor.rowcount


def username_problem(username: str) -> Optional[str]:
    """Plain-language username rules."""
    username = (username or "").strip()
    if not username:
        return "A username is required."
    if len(username) < 2:
        return "That username is too short."
    if len(username) > 40:
        return "That username is too long."
    if any(character.isspace() for character in username):
        return "A username cannot contain spaces."
    if not all(c.isalnum() or c in "._-" for c in username):
        return "A username can only use letters, numbers, dots, dashes and underscores."
    return None


def password_problem(password: str) -> Optional[str]:
    """Plain-language password rules. Length is what actually matters, so that
    is what we ask for -- not a zoo of character classes people work around."""
    if not password or len(password) < 10:
        return "Passwords need to be at least 10 characters. A short phrase works well."
    if len(password) > 200:
        return "That password is too long."
    if password.strip() != password:
        return "Passwords cannot start or end with a space."
    return None
