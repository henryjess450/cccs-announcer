"""Print every playback device this machine can see.

Run this on the PA machine to find the exact text to put in PA_AUDIO_DEVICE:

    python scripts\\list_audio_devices.py

Copy any distinctive part of the device name -- matching is case-insensitive
and only needs to be a substring.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import sounddevice as sd
    except Exception as exc:
        print("Could not load the audio library:", exc)
        print("Install it with:  pip install sounddevice")
        return 1

    try:
        devices = sd.query_devices()
        default_output = sd.default.device[1]
    except Exception as exc:
        print("Could not read the audio devices:", exc)
        return 1

    print("Playback devices on this computer")
    print("=" * 70)
    found = False
    for index, device in enumerate(devices):
        if device.get("max_output_channels", 0) <= 0:
            continue
        found = True
        marker = "  <-- system default" if index == default_output else ""
        print(f"[{index}] {device['name']}")
        print(f"     channels: {device['max_output_channels']}   "
              f"default rate: {int(device.get('default_samplerate') or 0)} Hz{marker}")
    if not found:
        print("No playback devices found. Check that speakers or the audio")
        print("interface are plugged in and enabled in Windows Sound settings.")
        return 1

    print()
    print("Put part of the name of the device wired to the PA into .env, e.g.")
    print("    PA_AUDIO_DEVICE=Realtek")
    return 0


if __name__ == "__main__":
    sys.exit(main())
