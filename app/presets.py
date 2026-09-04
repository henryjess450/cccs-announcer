"""Ready-made announcements with fill-in slots.

Two jobs:

* Save typing on the things that get announced every day. "Bus number 12 has
  arrived" typed forty times a week is forty chances to mistype the number.
* Make the wording of a drill fixed, rehearsed, and identical every time. In an
  emergency, or a practice for one, nobody should be composing a sentence.

A slot is written {like_this} in the body. Everything else is said verbatim.
"""

from __future__ import annotations

import re
from typing import Dict, List

SLOT_RE = re.compile(r"\{([a-z][a-z0-9_]{0,30})\}")

# How each slot is labelled in the form. Anything not listed gets its own name
# tidied up, so a new preset works without touching this file.
SLOT_LABELS = {
    "number": "Bus number",
    "name": "Name",
    "text": "What to announce",
    "room": "Room",
    "time": "Time",
    "minutes": "Minutes",
    "location": "Location",
}


def slots_in(body: str) -> List[str]:
    """The slot names in a preset body, in order, without duplicates."""
    found: List[str] = []
    for match in SLOT_RE.finditer(body or ""):
        if match.group(1) not in found:
            found.append(match.group(1))
    return found


def label_for(slot: str) -> str:
    return SLOT_LABELS.get(slot, slot.replace("_", " ").capitalize())


def fill(body: str, values: Dict[str, str]) -> str:
    """Put the values into the slots.

    Raises KeyError naming the first slot left empty, so the person is told
    which box to fill rather than being handed a half-written announcement.
    """
    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        value = (values.get(name) or "").strip()
        if not value:
            raise KeyError(name)
        return value
    return SLOT_RE.sub(_replace, body or "").strip()


# ---------------------------------------------------------------------------
# What a new school starts with.
#
# The drill wording follows the shape every emergency-response guide uses:
# say it is a practice at the START, give the instruction twice, and say it is
# a practice again at the END. Somebody who walks into a corridor halfway
# through has to hear the word "practice" before they act on it.
#
# These are PRACTICE announcements. Real emergency wording belongs to the
# school's own safety plan -- it is a life-safety decision, not a default a
# piece of software should invent. Add real ones through the admin page, from
# the wording your district has already approved.
# ---------------------------------------------------------------------------

SEED_PRESETS = [
    {
        "title": "Bus has arrived",
        "body": "Bus number {number} has arrived. Bus number {number}.",
        "chime": None, "priority": 0, "is_drill": 0, "admin_only": 0,
        "sort_order": 10,
    },
    {
        "title": "Report to the office",
        "body": "{name}, please report to the main office. "
                "{name}, to the main office, please.",
        "chime": None, "priority": 0, "is_drill": 0, "admin_only": 0,
        "sort_order": 20,
    },
    {
        "title": "Attention staff",
        "body": "Attention staff. {text}",
        "chime": None, "priority": 0, "is_drill": 0, "admin_only": 0,
        "sort_order": 30,
    },
    {
        "title": "Buses are loading",
        "body": "Buses are now loading. Students taking the bus, please make "
                "your way to the bus loop.",
        "chime": None, "priority": 0, "is_drill": 0, "admin_only": 0,
        "sort_order": 40,
    },
    # -- drills ------------------------------------------------------------
    {
        "title": "PRACTICE — Fire drill",
        "body": (
            "This is a practice fire drill. This is a practice. "
            "Please leave the building now by your nearest exit and go to your "
            "assembly area. Walk, do not run. Teachers, please bring your class "
            "list and close the door behind you. "
            "This is a practice fire drill."
        ),
        "chime": "alarm_pattern", "priority": 1, "is_drill": 1, "admin_only": 1,
        "sort_order": 100,
    },
    {
        "title": "PRACTICE — Earthquake drill",
        "body": (
            "This is a practice earthquake drill. This is a practice. "
            "Drop, cover, and hold on. Drop, cover, and hold on. "
            "Get under a desk or table, cover your head, and hold on. "
            "Stay away from windows and shelves. Stay where you are until you "
            "hear the all clear. "
            "This is a practice earthquake drill."
        ),
        "chime": "alarm_pattern", "priority": 1, "is_drill": 1, "admin_only": 1,
        "sort_order": 110,
    },
    {
        "title": "PRACTICE — Lockdown drill",
        "body": (
            "This is a practice lockdown. This is a practice. "
            "Staff, secure your rooms now. Lock the door, turn off the lights, "
            "and move everyone away from windows and doors. "
            "Stay quiet and stay where you are until you hear the all clear. "
            "This is a practice lockdown."
        ),
        "chime": "alarm_pattern", "priority": 1, "is_drill": 1, "admin_only": 1,
        "sort_order": 120,
    },
    {
        "title": "PRACTICE — Hold and secure drill",
        "body": (
            "This is a practice hold and secure. This is a practice. "
            "Staff, bring everyone indoors and lock the outside doors. "
            "Classes carry on as normal inside the building. "
            "Nobody is to leave until you hear the all clear. "
            "This is a practice hold and secure."
        ),
        "chime": "alarm_pattern", "priority": 1, "is_drill": 1, "admin_only": 1,
        "sort_order": 130,
    },
    {
        "title": "PRACTICE — All clear",
        "body": (
            "All clear. All clear. The practice is over. "
            "Please return to your normal routine. Thank you, everyone."
        ),
        # A drill without an all-clear leaves a building waiting.
        "chime": "arrival", "priority": 1, "is_drill": 1, "admin_only": 1,
        "sort_order": 140,
    },
]


def seed_if_empty(database) -> int:
    """Give a new school something to work with. Never overwrites."""
    if database.presets(include_disabled=True):
        return 0
    for preset in SEED_PRESETS:
        database.add_preset(**preset)
    return len(SEED_PRESETS)
