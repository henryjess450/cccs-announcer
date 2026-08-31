"""The chime library: what sounds are available and where they live on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .audio.wavio import duration_seconds

# Built-in chimes, in the order they should appear in the dropdown.
# `end_tone` is excluded: it is an output setting, not something staff pick.
BUILTIN_ORDER = ["attention", "two_tone_bell", "soft_alert", "urgent"]

BUILTIN_LABELS: Dict[str, str] = {
    "attention": "Attention tone",
    "two_tone_bell": "Two-tone bell",
    "soft_alert": "Soft alert",
    "urgent": "Urgent",
    "end_tone": "End tone",
}


@dataclass
class Chime:
    key: str
    label: str
    path: Path
    seconds: float


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
        return Chime(key=key, label=BUILTIN_LABELS.get(key, key.replace("_", " ").title()),
                     path=path, seconds=seconds)

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
