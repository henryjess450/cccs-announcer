"""The chime library: what sounds are available and where they live on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .audio.wavio import duration_seconds

# Built-in chimes, in the order staff see them. Longer, more distinctive ones
# first: a single short beep is easy to talk over, and what actually stops a
# corridor is a two-to-four second melody.
#
# `end_tone` is deliberately absent -- it is an output setting, not a choice.
# Grouped, because twenty sounds in one flat list is not a choice anybody can
# make. The group is what somebody is actually deciding between: "I need this
# heard over a noisy corridor" versus "this must not startle a class".
GROUPS = [
    ("Best for a noisy corridor", [
        "fanfare", "school_bell", "assembly", "bright_call",
        "westminster", "tubular_bells", "handbell",
    ]),
    ("Warm and friendly", [
        "arrival", "marimba", "celesta", "xylophone", "double_chime", "cascade",
    ]),
    ("Quiet, for classes in progress", [
        "gentle_gong", "sunrise", "soft_alert", "two_tone_bell", "attention",
    ]),
    ("Urgent", [
        "alarm_pattern", "urgent",
    ]),
]

BUILTIN_ORDER = [key for _, keys in GROUPS for key in keys]

# Which group each sound belongs to, for the picker.
GROUP_OF = {key: name for name, keys in GROUPS for key in keys}

BUILTIN_LABELS: Dict[str, str] = {
    "tubular_bells": "Tubular bells",
    "assembly": "Assembly call",
    "marimba": "Marimba",
    "celesta": "Celesta",
    "cascade": "Cascade",
    "handbell": "Handbell",
    "bright_call": "Bright call",
    "gentle_gong": "Gentle gong",
    "westminster": "Westminster clock",
    "arrival": "Arrival chime",
    "fanfare": "Fanfare",
    "school_bell": "School bell",
    "double_chime": "Double chime",
    "xylophone": "Xylophone",
    "sunrise": "Sunrise",
    "two_tone_bell": "Two-tone bell",
    "attention": "Attention tone",
    "soft_alert": "Soft alert",
    "alarm_pattern": "Alarm pattern",
    "urgent": "Urgent beeps",
    "end_tone": "End tone",
}

# One line each, so somebody choosing can tell them apart without playing all
# twelve. Written for a teacher, not an audio engineer.
BUILTIN_DESCRIPTIONS: Dict[str, str] = {
    "tubular_bells": "Three deep struck bells. Reads as a school rather than "
                     "a shop.",
    "assembly": "Five notes climbing then settling. Sounds like "
                "\u201ceverybody gather\u201d.",
    "marimba": "Warm wooden run. Carries over chatter without sounding like a "
               "warning.",
    "celesta": "Sparkling high notes. Attention without any hint of alarm \u2014 "
               "good for good news.",
    "cascade": "A tumbling run down six notes. Nobody mistakes it for the bell "
               "schedule.",
    "handbell": "Two bright strikes. Small, sharp, and carries a long way.",
    "bright_call": "Two rising pairs, repeated. Cuts through a corridor at "
                   "break time.",
    "gentle_gong": "One soft strike with a long tail. For the library, exam "
                   "halls, and quiet spaces.",
    "westminster": "The four-note school clock. Everyone already reads it as "
                   "\u201clisten up\u201d.",
    "arrival": "Warm three-note rise, like an airport or a shop. Friendly and "
               "hard to miss.",
    "fanfare": "Four notes climbing to a held top note. The best at cutting "
               "through a noisy corridor.",
    "school_bell": "Five strikes of the same bell. Anyone who missed the first "
                   "still hears the third.",
    "double_chime": "Ding-dong, twice. The pattern people already read as "
                    "\u201cannouncement\u201d.",
    "xylophone": "Bright and bouncy. Cheerful rather than alarming.",
    "sunrise": "A slow five-note climb. Calm, and long enough for a room to "
               "settle before the words start.",
    "two_tone_bell": "Short doorbell ding-dong. Quick and neutral.",
    "attention": "Two short rising tones. The plainest of the set.",
    "soft_alert": "Gentle and low-key, for notices that should not startle a "
                  "class.",
    "alarm_pattern": "Insistent high-low, six times. For things that genuinely "
                     "cannot wait.",
    "urgent": "Three fast beeps. Short and sharp.",
}


@dataclass
class Chime:
    key: str
    label: str
    path: Path
    seconds: float
    description: str = ""
    group: str = "Other"


class ChimeLibrary:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path_for(self, key: Optional[str]) -> Optional[Path]:
        if not key:
            return None
        # Reject anything that is not a plain name, so a chime key from a
        # request body can never walk out of the chime directory.
        if not key.replace("_", "").replace("-", "").isalnum():
            return None
        candidate = self.directory / f"{key}.wav"
        return candidate if candidate.is_file() else None

    def get(self, key: str) -> Optional[Chime]:
        path = self.path_for(key)
        if path is None:
            return None
        try:
            seconds = duration_seconds(path)
        except Exception:
            seconds = 0.0
        return Chime(
            key=key,
            label=BUILTIN_LABELS.get(key, key.replace("_", " ").title()),
            path=path,
            seconds=seconds,
            description=BUILTIN_DESCRIPTIONS.get(key, ""),
            group=GROUP_OF.get(key, "Other"),
        )

    def available(self) -> List[Chime]:
        """Built-ins first in a fixed order, then any extras found on disk."""
        found: List[Chime] = []
        seen = set()
        for key in BUILTIN_ORDER:
            chime = self.get(key)
            if chime:
                found.append(chime)
                seen.add(key)
        for path in sorted(self.directory.glob("*.wav")):
            key = path.stem
            if key in seen or key == "end_tone":
                continue
            chime = self.get(key)
            if chime:
                found.append(chime)
        return found

    def seconds_for(self, key: Optional[str]) -> float:
        chime = self.get(key) if key else None
        return chime.seconds if chime else 0.0
