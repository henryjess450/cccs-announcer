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
BUILTIN_ORDER = [
    "westminster",
    "arrival",
    "fanfare",
    "school_bell",
    "double_chime",
    "xylophone",
    "sunrise",
    "two_tone_bell",
    "attention",
    "soft_alert",
    "alarm_pattern",
    "urgent",
]

BUILTIN_LABELS: Dict[str, str] = {
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
