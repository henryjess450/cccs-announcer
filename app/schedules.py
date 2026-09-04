"""Announcements that go out on a timetable.

School time, not computer time
------------------------------
Everything a person types is in the school's own timezone -- "3:10 PM" means
3:10 PM in Vancouver, in March and in July. Everything stored is UTC. The
conversion happens here and nowhere else.

That distinction is the whole reason this module exists. British Columbia
changes its clocks twice a year, and a bus announcement that drifts an hour in
March is worse than no bus announcement: nobody is expecting it, so nobody
notices it stopped being right.

Missed runs
-----------
If the announcer was switched off over the weekend, Monday morning must not
begin with every announcement it missed. Anything more than a short grace
period late is skipped, logged, and the schedule moves on to its next proper
time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Sequence

try:                                    # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - very old Python
    ZoneInfo = None  # type: ignore

# What a schedule can repeat on.
KIND_ONCE = "once"
KIND_DAILY = "daily"
KIND_WEEKDAYS = "weekdays"
KIND_WEEKLY = "weekly"
KINDS = (KIND_ONCE, KIND_DAILY, KIND_WEEKDAYS, KIND_WEEKLY)

# Monday is 0, matching datetime.weekday().
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
SCHOOL_DAYS = [0, 1, 2, 3, 4]

MAX_SEARCH_DAYS = 800   # a schedule that finds nothing in two years has ended


class ScheduleError(ValueError):
    """Something about the schedule does not make sense. The message is shown
    to the person who typed it, so it says what to do."""


def school_zone(name: str):
    """The school's timezone.

    Falls back to UTC rather than refusing to start: a wrong timezone makes
    announcements happen at the wrong time, but no timezone at all would stop
    the announcer running, which is worse.
    """
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def parse_time(value: str) -> time:
    """'15:10' or '3:10 PM' -> a time. People type both."""
    text = (value or "").strip().upper().replace(".", "")
    if not text:
        raise ScheduleError("Give a time, like 3:10 PM.")

    meridiem = None
    for suffix in ("AM", "PM"):
        if text.endswith(suffix):
            meridiem = suffix
            text = text[: -len(suffix)].strip()
            break

    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        raise ScheduleError(f"{value!r} is not a time. Try something like 3:10 PM.")

    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"{value!r} is not a time. Try something like 3:10 PM.")
    return time(hour=hour, minute=minute)


def format_time(value: time) -> str:
    """Back to something a person reads: '3:10 PM'."""
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {meridiem}"


def parse_days(value: Optional[str]) -> List[int]:
    if not value:
        return []
    days = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            day = int(part)
        except ValueError:
            raise ScheduleError("Pick which days of the week this should run.")
        if not 0 <= day <= 6:
            raise ScheduleError("Pick which days of the week this should run.")
        if day not in days:
            days.append(day)
    return sorted(days)


def describe(kind: str, at: time, days: Sequence[int], on_date: Optional[str]) -> str:
    """One line a person can check at a glance."""
    when = format_time(at)
    if kind == KIND_ONCE:
        return f"Once, on {on_date} at {when}"
    if kind == KIND_DAILY:
        return f"Every day at {when}"
    if kind == KIND_WEEKDAYS:
        return f"Every school day (Monday to Friday) at {when}"
    if kind == KIND_WEEKLY:
        if not days:
            return f"Weekly at {when}"
        names = ", ".join(DAY_NAMES[d] for d in days)
        return f"Every {names} at {when}"
    return when


def _as_local(moment: datetime, zone) -> datetime:
    return moment.astimezone(zone) if zone is not None else moment


def next_occurrence(
    *,
    kind: str,
    at: time,
    days: Sequence[int],
    on_date: Optional[str],
    after: datetime,
    zone,
    starts_on: Optional[str] = None,
    ends_on: Optional[str] = None,
) -> Optional[datetime]:
    """The next time this schedule should fire, as an aware UTC datetime.

    `after` is a UTC datetime; the result is strictly later than it. None means
    the schedule has no future occurrences and is finished.

    The search walks forward a day at a time and builds each candidate in LOCAL
    time before converting. That is what makes it correct across a clock change:
    "3:10 PM every day" stays 3:10 PM on both sides of it, even though the UTC
    offset moves.
    """
    if kind not in KINDS:
        raise ScheduleError(f"Unknown schedule type {kind!r}.")

    local_after = _as_local(after, zone)
    start_bound = date.fromisoformat(starts_on) if starts_on else None
    end_bound = date.fromisoformat(ends_on) if ends_on else None

    if kind == KIND_ONCE:
        if not on_date:
            raise ScheduleError("Give the date this should run on.")
        wanted = datetime.combine(date.fromisoformat(on_date), at)
        if zone is not None:
            wanted = wanted.replace(tzinfo=zone)
        candidate = wanted.astimezone(after.tzinfo) if zone is not None else wanted
        return candidate if candidate > after else None

    if kind == KIND_WEEKDAYS:
        wanted_days = SCHOOL_DAYS
    elif kind == KIND_WEEKLY:
        wanted_days = parse_days(",".join(str(d) for d in days))
        if not wanted_days:
            raise ScheduleError("Pick which days of the week this should run.")
    else:
        wanted_days = list(range(7))

    day = local_after.date()
    for _ in range(MAX_SEARCH_DAYS):
        if end_bound and day > end_bound:
            return None
        if (not start_bound or day >= start_bound) and day.weekday() in wanted_days:
            local = datetime.combine(day, at)
            if zone is not None:
                local = local.replace(tzinfo=zone)
            candidate = local.astimezone(after.tzinfo) if zone is not None else local
            if candidate > after:
                return candidate
        day += timedelta(days=1)
    return None


@dataclass
class DueDecision:
    """Whether a schedule should fire now, and why not if not."""
    fire: bool
    skipped_reason: str = ""


def decide(due_at: datetime, now: datetime, grace_minutes: int) -> DueDecision:
    """Should something scheduled for `due_at` still go out?

    Late by less than the grace period: yes -- the machine was busy, or the
    player was mid-announcement.

    Late by more: no. The announcer was off. Firing now would mean Monday
    morning opening with everything it missed over the weekend, at the wrong
    times, to a building that has moved on.
    """
    if due_at > now:
        return DueDecision(fire=False, skipped_reason="not yet")
    late = (now - due_at).total_seconds() / 60.0
    if late > grace_minutes:
        return DueDecision(
            fire=False,
            skipped_reason=(
                f"missed by {int(late)} minutes -- the announcer was probably "
                "switched off, so this one was skipped"
            ),
        )
    return DueDecision(fire=True)
