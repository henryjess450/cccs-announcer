"""Per-user submission limits.

The point is not to stop a determined misuser -- the audit log does that, since
every announcement is permanently attributed to a named account. The point is
to stop an accident: a stuck key, a double-click, a browser tab left replaying
a request. Those produce a burst that would otherwise tie up the PA for
minutes.

Counted against announcements the user CREATED in the window, whatever happened
to them afterwards. Counting only successful ones would let a broken PA turn
into unlimited submissions.

Administrators are exempt. In a real emergency the person running the building
should not meet a rate limiter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from .accounts import User
from .config import Config
from .db import Database, parse_iso, utcnow


@dataclass
class RateDecision:
    allowed: bool
    limit: int
    window_seconds: int
    used: int
    retry_after_seconds: int = 0
    message: str = ""


class RateLimiter:
    def __init__(self, database: Database, config: Config):
        self.db = database
        self.config = config

    # Settings live in the database so an admin can change them at runtime
    # (Phase 3), falling back to the .env values.
    def limit(self) -> int:
        return int(self.db.get_setting("rate_limit_count", str(self.config.rate_limit_count)))

    def window_seconds(self) -> int:
        return int(self.db.get_setting(
            "rate_limit_window_seconds", str(self.config.rate_limit_window_seconds)
        ))

    def check(self, user: Optional[User]) -> RateDecision:
        limit = self.limit()
        window = self.window_seconds()

        if user is not None and user.is_admin:
            return RateDecision(True, limit, window, used=0)
        if limit <= 0:
            return RateDecision(True, limit, window, used=0)

        cutoff = utcnow() - timedelta(seconds=window)
        cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")

        rows = self.db.connect().execute(
            "SELECT created_at FROM announcements "
            "WHERE user_id = ? AND kind = 'announcement' AND created_at >= ? "
            "ORDER BY created_at ASC",
            (user.id if user else None, cutoff_iso),
        ).fetchall()

        used = len(rows)
        if used < limit:
            return RateDecision(True, limit, window, used=used)

        # When the oldest submission in the window falls out, they get another.
        oldest = parse_iso(rows[0]["created_at"])
        retry_after = window
        if oldest is not None:
            retry_after = max(1, int((oldest + timedelta(seconds=window) - utcnow()).total_seconds()))

        return RateDecision(
            allowed=False,
            limit=limit,
            window_seconds=window,
            used=used,
            retry_after_seconds=retry_after,
            message=_message(limit, window, retry_after),
        )


def _message(limit: int, window: int, retry_after: int) -> str:
    minutes = max(1, window // 60)
    wait = (
        f"about {max(1, retry_after // 60)} minute{'s' if retry_after >= 120 else ''}"
        if retry_after >= 60 else f"{retry_after} seconds"
    )
    return (
        f"You have sent {limit} announcements in the last {minutes} minutes. "
        f"You can send another in {wait}. "
        "If this is urgent, ask the office to send it."
    )
