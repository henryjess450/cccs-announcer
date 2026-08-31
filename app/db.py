"""SQLite storage. One file, no ORM, no migrations framework.

Threading model
---------------
Two kinds of thread touch this database: the web server's worker threads and
the single player thread. SQLite connections are not shareable across threads,
so every thread gets its own connection via `_local`.

WAL mode plus a generous busy_timeout means a web request writing an
announcement never blocks the player thread for a meaningful amount of time,
and vice versa.

The queue lives here rather than in memory on purpose: if the process dies
mid-announcement, the queued items are still on disk when it comes back, and
`recover_orphaned_items` makes the interrupted one visible instead of letting
it vanish.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 3

# Announcement lifecycle. Anything not in {queued, playing} is terminal.
STATE_QUEUED = "queued"
STATE_PLAYING = "playing"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_STOPPED = "stopped"
STATE_INTERRUPTED = "interrupted"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL,
    -- user_id stays NULL through Phase 1 (no auth). user_name always carries
    -- something printable so the audit log is never blank.
    user_id           INTEGER,
    user_name         TEXT    NOT NULL,
    kind              TEXT    NOT NULL DEFAULT 'announcement',  -- announcement | test
    raw_text          TEXT    NOT NULL,
    normalized_text   TEXT    NOT NULL,
    chime             TEXT,
    -- Zones are modelled from day one even though Phase 1 only ships 'all'.
    zone              TEXT    NOT NULL DEFAULT 'all',
    priority          INTEGER NOT NULL DEFAULT 0,   -- 0 normal, 1 priority
    state             TEXT    NOT NULL,
    estimated_seconds REAL,
    started_at        TEXT,
    finished_at       TEXT,
    duration_seconds  REAL,
    error             TEXT,
    stopped_by        TEXT
);

-- The queue read is the hottest query in the system: "next queued item,
-- priority first, then oldest".
CREATE INDEX IF NOT EXISTS idx_queue
    ON announcements (state, priority DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_created
    ON announcements (created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Migrations
#
# _SCHEMA above is the version 1 baseline and is safe to re-run (every
# statement is IF NOT EXISTS). Each entry below moves the database up one
# version and runs exactly once, tracked by the schema_version setting. Never
# edit a migration that has shipped -- add a new one.
# ---------------------------------------------------------------------------

_MIGRATIONS = {
    2: """
    -- Phase 2: accounts, sessions, and the security trail.
    CREATE TABLE IF NOT EXISTS users (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        username             TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        display_name         TEXT    NOT NULL,
        password_hash        TEXT    NOT NULL,
        role                 TEXT    NOT NULL DEFAULT 'staff',   -- staff | admin
        is_active            INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at           TEXT    NOT NULL,
        created_by           INTEGER,
        last_login_at        TEXT,
        -- Brute-force protection. Cleared on a successful sign-in.
        failed_logins        INTEGER NOT NULL DEFAULT 0,
        locked_until         TEXT
    );

    -- Sessions live server-side so deactivating an account takes effect at
    -- once, rather than whenever a self-contained token happens to expire.
    -- token_hash is the SHA-256 of the cookie value: reading this table does
    -- not hand anyone a usable session.
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash   TEXT    PRIMARY KEY,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token   TEXT    NOT NULL,
        created_at   TEXT    NOT NULL,
        last_seen_at TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL,
        ip           TEXT,
        user_agent   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions (expires_at);

    -- Sign-ins, lockouts, and account changes. Announcements have their own
    -- trail in the announcements table; this is everything else.
    CREATE TABLE IF NOT EXISTS security_events (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        at       TEXT NOT NULL,
        event    TEXT NOT NULL,
        username TEXT,
        user_id  INTEGER,
        ip       TEXT,
        detail   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_security_at ON security_events (at DESC);
    """,
    3: """
    -- Marks the administrator account the system creates for itself on first
    -- start. While this flag is set the account is still on its issued
    -- password and has not been claimed by a real person, which /health
    -- reports as degraded.
    ALTER TABLE users ADD COLUMN is_bootstrap INTEGER NOT NULL DEFAULT 0;
    """,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Read a timestamp written by now_iso(). Returns None on anything unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()

    # -- connection handling -------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def initialize(self) -> None:
        """Create the database if needed, then bring it up to date.

        Safe to run on every start. A fresh file gets the baseline plus every
        migration; an existing one gets only the migrations it has not seen.
        """
        conn = self.connect()
        conn.executescript(_SCHEMA)

        current = int(self.get_setting("schema_version", "1") or "1")
        for version in sorted(_MIGRATIONS):
            if version > current:
                conn.executescript(_MIGRATIONS[version])
                self.set_setting("schema_version", str(version))
                current = version
        self.set_setting("schema_version", str(max(current, SCHEMA_VERSION)))

    def is_writable(self) -> bool:
        """Used by /health. A read-only or full disk must surface loudly."""
        try:
            self.set_setting("_health_probe", now_iso())
            return True
        except sqlite3.Error:
            return False

    # -- settings ------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.connect().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.connect().execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- queue ---------------------------------------------------------------

    def enqueue(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        chime: Optional[str],
        user_name: str,
        user_id: Optional[int] = None,
        priority: int = 0,
        zone: str = "all",
        kind: str = "announcement",
        estimated_seconds: Optional[float] = None,
    ) -> int:
        cursor = self.connect().execute(
            "INSERT INTO announcements "
            "(created_at, user_id, user_name, kind, raw_text, normalized_text, chime, "
            " zone, priority, state, estimated_seconds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), user_id, user_name, kind, raw_text, normalized_text, chime,
             zone, 1 if priority else 0, STATE_QUEUED, estimated_seconds),
        )
        return int(cursor.lastrowid)

    def claim_next(self) -> Optional[Dict[str, Any]]:
        """Atomically take the next item and mark it playing.

        Only the player thread calls this, so in practice there is no contention
        -- but BEGIN IMMEDIATE makes the claim atomic regardless, so a future
        second consumer (or a stray admin script) cannot double-claim a row.
        Priority jumps the line; within a tier it is strict FIFO by insertion id.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM announcements WHERE state = ? "
                "ORDER BY priority DESC, id ASC LIMIT 1",
                (STATE_QUEUED,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE announcements SET state = ?, started_at = ? WHERE id = ?",
                (STATE_PLAYING, now_iso(), row["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        item = dict(row)
        item["state"] = STATE_PLAYING
        return item

    def release_to_queue(self, item_id: int, error: str) -> None:
        """Put a claimed item back.

        Used when the audio device is unavailable: the announcement must stay in
        the queue and be retried, never silently dropped.
        """
        self.connect().execute(
            "UPDATE announcements SET state = ?, started_at = NULL, error = ? WHERE id = ?",
            (STATE_QUEUED, error, item_id),
        )

    def finish(
        self,
        item_id: int,
        state: str,
        *,
        duration_seconds: Optional[float] = None,
        error: Optional[str] = None,
        stopped_by: Optional[str] = None,
    ) -> None:
        self.connect().execute(
            "UPDATE announcements SET state = ?, finished_at = ?, duration_seconds = ?, "
            "error = ?, stopped_by = ? WHERE id = ?",
            (state, now_iso(), duration_seconds, error, stopped_by, item_id),
        )

    def cancel_queued(self, item_id: int, by: str) -> bool:
        """Remove a not-yet-playing item. Returns False if it already started."""
        cursor = self.connect().execute(
            "UPDATE announcements SET state = ?, finished_at = ?, stopped_by = ? "
            "WHERE id = ? AND state = ?",
            (STATE_STOPPED, now_iso(), by, item_id, STATE_QUEUED),
        )
        return cursor.rowcount > 0

    def recover_orphaned_items(self) -> List[int]:
        """Mark anything left 'playing' by a crash as interrupted.

        Called once at startup. If the PA machine lost power mid-announcement we
        want that visible in the audit log, not a row stuck in 'playing' forever
        that makes the UI claim something is playing when nothing is.
        """
        conn = self.connect()
        rows = conn.execute("SELECT id FROM announcements WHERE state = ?", (STATE_PLAYING,)).fetchall()
        ids = [int(r["id"]) for r in rows]
        if ids:
            conn.execute(
                "UPDATE announcements SET state = ?, finished_at = ?, "
                "error = 'Interrupted: the announcer restarted while this was playing.' "
                "WHERE state = ?",
                (STATE_INTERRUPTED, now_iso(), STATE_PLAYING),
            )
        return ids

    # -- reads ---------------------------------------------------------------

    def get(self, item_id: int) -> Optional[Dict[str, Any]]:
        row = self.connect().execute("SELECT * FROM announcements WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def queued_items(self) -> List[Dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT * FROM announcements WHERE state = ? ORDER BY priority DESC, id ASC",
            (STATE_QUEUED,),
        ).fetchall()
        return [dict(r) for r in rows]

    def playing_item(self) -> Optional[Dict[str, Any]]:
        row = self.connect().execute(
            "SELECT * FROM announcements WHERE state = ? ORDER BY id ASC LIMIT 1",
            (STATE_PLAYING,),
        ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT * FROM announcements ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_by_state(self) -> Dict[str, int]:
        rows = self.connect().execute(
            "SELECT state, COUNT(*) AS n FROM announcements GROUP BY state"
        ).fetchall()
        return {r["state"]: int(r["n"]) for r in rows}

    # -- speaking-rate estimate ---------------------------------------------

    def record_speech_rate(self, chars: int, seconds: float) -> None:
        """Keep a rolling estimate of characters-per-second of finished speech.

        Used only to tell a waiting user "about 40 seconds". An exponentially
        weighted average means the estimate adapts if the voice or rate changes,
        without storing any history.
        """
        if chars <= 0 or seconds <= 0.2:
            return
        observed = chars / seconds
        stored = self.get_setting("speech_chars_per_second")
        current = float(stored) if stored else observed
        updated = (current * 0.8) + (observed * 0.2)
        self.set_setting("speech_chars_per_second", f"{updated:.4f}")

    def speech_rate(self, fallback: float) -> float:
        stored = self.get_setting("speech_chars_per_second")
        try:
            value = float(stored) if stored else fallback
        except (TypeError, ValueError):
            value = fallback
        return value if value > 1.0 else fallback
