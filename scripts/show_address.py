"""Print the address staff should use.

    python scripts\\show_address.py

Run this any time you need the address again -- it does not need the announcer
to be running.

Add --public to also look up the school's internet address. You almost
certainly do not want that one; see the note it prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.netinfo import all_urls, hostname, primary_address, staff_url  # noqa: E402


def public_address(timeout: float = 5.0):
    """The school's address as the internet sees it. Needs internet access."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("ip")
    except Exception:
        return None


def main() -> int:
    config = load_config()
    port = config.port

    print()
    print("=" * 64)
    print(" THE ADDRESS STAFF SHOULD USE")
    print()
    if primary_address() is None:
        print("   This computer does not appear to be on a network.")
        print("   Plug in the network cable and run this again.")
        print("=" * 64)
        return 1

    print(f"        {staff_url(port)}")
    print()
    extras = [url for url in all_urls(port) if url != staff_url(port)]
    if extras:
        print("   Also reachable at: " + ", ".join(extras))
    print(f"   On this computer:  http://localhost:{port}")
    print(f"   By name:           http://{hostname()}:{port}")
    print("=" * 64)

    if "--public" in sys.argv:
        print()
        address = public_address()
        if address is None:
            print(" Could not look up the internet address (no internet access?).")
        else:
            print(f" The school's internet address is: {address}")
        print()
        print(" This is NOT the address to give staff, and the announcer should")
        print(" not be reachable at it. Anything on the internet that can reach")
        print(" the announcer can try to talk to the whole school. Give staff")
        print(" the local address above and leave the firewall closed.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
