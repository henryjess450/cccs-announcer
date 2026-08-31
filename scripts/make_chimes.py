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


def attention() -> np.ndarray:
    """Two rising tones. The default 'listen up' chime."""
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
    """Gentle, low-urgency. For routine notices that should not startle a classroom."""
    body = _tone(523.3, 0.9, (1.0, 0.15, 0.0)) + 0.5 * _tone(659.3, 0.9, (1.0, 0.1, 0.0))
    return _normalize(np.concatenate([
        _envelope(body, attack=0.12, release=0.30),
        _silence(0.08),
    ]), peak=0.55)


def urgent() -> np.ndarray:
    """Three fast beeps. Reserved for priority announcements."""
    beep = _envelope(_tone(988.0, 0.13, (1.0, 0.3, 0.15)), attack=0.005, release=0.025)
    return _normalize(np.concatenate([
        beep, _silence(0.07), beep, _silence(0.07), beep, _silence(0.12),
    ]), peak=0.8)


def end_tone() -> np.ndarray:
    """Optional short descending tone marking the end of an announcement."""
    return _normalize(np.concatenate([
        _envelope(_tone(659.3, 0.16, (1.0, 0.2, 0.0))),
        _envelope(_tone(523.3, 0.26, (1.0, 0.2, 0.0)), release=0.12),
        _silence(0.06),
    ]), peak=0.5)


CHIMES = {
    "attention": attention,
    "two_tone_bell": two_tone_bell,
    "soft_alert": soft_alert,
    "urgent": urgent,
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
