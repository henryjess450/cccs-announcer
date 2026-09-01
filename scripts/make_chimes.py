"""Generate the built-in chime WAV files.

Run this once (the seed script does it for you). The chimes are generated
rather than shipped as binaries so they can be tweaked, reviewed in a diff, and
regenerated without hunting for a licence-clean sound file.

All chimes are 44.1 kHz 16-bit mono. They are intentionally recorded at a
moderate peak level, because PA_CHIME_GAIN attenuates them further at playback
time -- chimes are mixed below speech.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.wavio import write_wav  # noqa: E402

RATE = 44100


def _tone(frequency: float, seconds: float, harmonics=(1.0, 0.0, 0.0), decay: float = 0.0) -> np.ndarray:
    t = np.linspace(0.0, seconds, int(RATE * seconds), endpoint=False, dtype=np.float32)
    wave = np.zeros_like(t)
    for index, amplitude in enumerate(harmonics, start=1):
        if amplitude:
            wave += amplitude * np.sin(2 * np.pi * frequency * index * t)
    if decay:
        wave *= np.exp(-decay * t)
    return wave


def _envelope(signal: np.ndarray, attack: float = 0.012, release: float = 0.05) -> np.ndarray:
    """Soft attack and release. A hard edge on a PA horn produces an audible thump."""
    n = len(signal)
    attack_n = min(int(RATE * attack), n // 2)
    release_n = min(int(RATE * release), n // 2)
    env = np.ones(n, dtype=np.float32)
    if attack_n:
        env[:attack_n] = np.linspace(0.0, 1.0, attack_n, dtype=np.float32) ** 2
    if release_n:
        env[-release_n:] = np.linspace(1.0, 0.0, release_n, dtype=np.float32) ** 2
    return signal * env


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def _normalize(signal: np.ndarray, peak: float = 0.7) -> np.ndarray:
    highest = float(np.max(np.abs(signal))) or 1.0
    return (signal / highest) * peak


# ---------------------------------------------------------------------------
# Building blocks for the longer, melodic chimes.
#
# A single beep is easy to talk over and easy to ignore. What actually stops a
# corridor full of children is a short MELODY -- several notes in a recognisable
# pattern -- with a bright, bell-like timbre that cuts through noise. So the
# chimes below are two to four seconds long and built from tuned notes rather
# than plain tones.
# ---------------------------------------------------------------------------

# Note frequencies, equal temperament.
NOTES = {
    "G4": 392.00, "A4": 440.00, "B4": 493.88,
    "C5": 523.25, "D5": 587.33, "E5": 659.26, "F5": 698.46, "G5": 783.99,
    "A5": 880.00, "B5": 987.77, "C6": 1046.50, "D6": 1174.66, "E6": 1318.51,
    "G6": 1567.98,
}


def _bell(note: str, seconds: float, decay: float = 3.0, brightness: float = 0.5) -> np.ndarray:
    """One struck note: a fast attack and a long ring, like a tuned bell.

    Real bells carry strong overtones, which is what makes them audible over a
    noisy room. `brightness` scales how much of those overtones survive.
    """
    frequency = NOTES[note]
    t = np.linspace(0.0, seconds, int(RATE * seconds), endpoint=False, dtype=np.float32)
    wave = np.sin(2 * np.pi * frequency * t)
    wave += brightness * 0.50 * np.sin(2 * np.pi * frequency * 2.0 * t)
    wave += brightness * 0.28 * np.sin(2 * np.pi * frequency * 3.0 * t)
    wave += brightness * 0.14 * np.sin(2 * np.pi * frequency * 4.2 * t)
    wave *= np.exp(-decay * t)
    # A very short attack removes the click without softening the strike.
    attack = min(int(RATE * 0.004), len(wave))
    if attack:
        wave[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)
    return wave.astype(np.float32)


def _melody(notes, gap: float, ring: float, decay: float = 3.0,
            brightness: float = 0.5, tail: float = 0.0) -> np.ndarray:
    """Play notes `gap` seconds apart, each left ringing for `ring` seconds.

    Notes overlap on purpose: the previous note is still sounding when the next
    is struck, which is what makes a real chime sound like a chime rather than
    a list of beeps.
    """
    step = max(1, int(RATE * gap))
    length = step * (len(notes) - 1) + int(RATE * ring) + int(RATE * tail)
    out = np.zeros(length, dtype=np.float32)
    for index, note in enumerate(notes):
        struck = _bell(note, ring, decay=decay, brightness=brightness)
        start = index * step
        out[start:start + len(struck)] += struck
    return out


def attention() -> np.ndarray:
    """Two rising tones. Short and neutral."""
    return _normalize(np.concatenate([
        _envelope(_tone(784.0, 0.28, (1.0, 0.25, 0.08))),
        _silence(0.04),
        _envelope(_tone(1046.5, 0.40, (1.0, 0.25, 0.08))),
        _silence(0.10),
    ]))


def two_tone_bell() -> np.ndarray:
    """Classic ding-dong doorbell shape, with bell-like decay."""
    return _normalize(np.concatenate([
        _envelope(_tone(659.3, 0.55, (1.0, 0.45, 0.20), decay=4.0), attack=0.004, release=0.12),
        _envelope(_tone(523.3, 0.85, (1.0, 0.45, 0.20), decay=3.2), attack=0.004, release=0.20),
        _silence(0.10),
    ]))


def soft_alert() -> np.ndarray:
    """Gentle, low-urgency. For routine notices that should not startle."""
    body = _tone(523.3, 0.9, (1.0, 0.15, 0.0)) + 0.5 * _tone(659.3, 0.9, (1.0, 0.1, 0.0))
    return _normalize(np.concatenate([
        _envelope(body, attack=0.12, release=0.30),
        _silence(0.08),
    ]), peak=0.55)


def urgent() -> np.ndarray:
    """Three fast beeps. For priority announcements."""
    beep = _envelope(_tone(988.0, 0.13, (1.0, 0.3, 0.15)), attack=0.005, release=0.025)
    return _normalize(np.concatenate([
        beep, _silence(0.07), beep, _silence(0.07), beep, _silence(0.12),
    ]), peak=0.8)


# --------------------------------------------------------------------------
# The longer ones. These are the chimes that actually get a corridor to stop.
# --------------------------------------------------------------------------

def westminster() -> np.ndarray:
    """The four-note school-clock phrase. Instantly recognisable as 'listen'."""
    return _normalize(np.concatenate([
        _melody(["E5", "D5", "C5", "G4"], gap=0.46, ring=2.2, decay=2.0, brightness=0.55),
        _silence(0.15),
    ]))


def arrival() -> np.ndarray:
    """The rising three-note chime used in airports and shops. Warm, unmissable."""
    return _normalize(np.concatenate([
        _melody(["C5", "E5", "G5"], gap=0.34, ring=2.0, decay=2.4, brightness=0.45),
        _silence(0.15),
    ]))


def fanfare() -> np.ndarray:
    """Four notes climbing to a held top note. The most 'something is happening'
    of the set, and the best at cutting through a noisy corridor."""
    climb = _melody(["C5", "E5", "G5", "C6"], gap=0.20, ring=2.4, decay=1.7, brightness=0.75)
    return _normalize(np.concatenate([climb, _silence(0.15)]), peak=0.78)


def xylophone() -> np.ndarray:
    """Bright, bouncy four notes. Cheerful rather than alarming -- good for
    routine notices where 'urgent' would be the wrong signal."""
    return _normalize(np.concatenate([
        _melody(["C6", "G5", "E5", "C5"], gap=0.20, ring=1.5, decay=5.0, brightness=0.8),
        _silence(0.15),
    ]))


def school_bell() -> np.ndarray:
    """Five strikes of the same bell. Repetition is what carries down a
    corridor -- anyone who missed the first strike still hears the third."""
    return _normalize(np.concatenate([
        _melody(["A5"] * 5, gap=0.52, ring=2.0, decay=2.6, brightness=0.85),
        _silence(0.15),
    ]))


def double_chime() -> np.ndarray:
    """Ding-dong, twice. The pattern people already read as 'announcement'."""
    pair = ["E5", "C5"]
    return _normalize(np.concatenate([
        _melody(pair, gap=0.42, ring=1.6, decay=2.8, brightness=0.55),
        _silence(0.16),
        _melody(pair, gap=0.42, ring=2.0, decay=2.8, brightness=0.55),
        _silence(0.15),
    ]))


def sunrise() -> np.ndarray:
    """A slow five-note climb. Calm but long enough that a room settles before
    the words start -- good for the start of the day."""
    return _normalize(np.concatenate([
        _melody(["C5", "D5", "E5", "G5", "C6"], gap=0.30, ring=2.4, decay=1.9,
                brightness=0.40),
        _silence(0.15),
    ]), peak=0.62)


def alarm_pattern() -> np.ndarray:
    """Alternating high-low, six times. Deliberately insistent, for the things
    that genuinely cannot wait. Do not use it for bus announcements."""
    notes = ["E6", "C6"] * 3
    return _normalize(np.concatenate([
        _melody(notes, gap=0.185, ring=0.9, decay=7.0, brightness=0.9),
        _silence(0.14),
    ]), peak=0.85)


def end_tone() -> np.ndarray:
    """Optional short descending tone marking the end of an announcement."""
    return _normalize(np.concatenate([
        _envelope(_tone(659.3, 0.16, (1.0, 0.2, 0.0))),
        _envelope(_tone(523.3, 0.26, (1.0, 0.2, 0.0)), release=0.12),
        _silence(0.06),
    ]), peak=0.5)


CHIMES = {
    # Short ones
    "two_tone_bell": two_tone_bell,
    "attention": attention,
    "soft_alert": soft_alert,
    "urgent": urgent,
    # Longer, melodic ones
    "westminster": westminster,
    "arrival": arrival,
    "fanfare": fanfare,
    "xylophone": xylophone,
    "school_bell": school_bell,
    "double_chime": double_chime,
    "sunrise": sunrise,
    "alarm_pattern": alarm_pattern,
    # Not a choice -- an output setting
    "end_tone": end_tone,
}


def generate(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, builder in CHIMES.items():
        path = target_dir / f"{name}.wav"
        write_wav(path, builder(), RATE)
        print(f"wrote {path} ({path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "chimes"
    generate(destination)
