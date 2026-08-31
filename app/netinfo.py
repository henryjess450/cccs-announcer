"""Work out which address staff should type into their browsers.

This is the question everybody actually asks when the announcer is installed:
"what do I tell people to open?"

The answer is the machine's address ON THE SCHOOL NETWORK -- something like
192.168.1.42. It is NOT the school's public/internet address. The announcer is
meant to be reachable from staff computers in the building and from nowhere
else; a PA system that can be reached from the internet is a PA system that can
be hijacked from the internet.

Everything here works with no internet connection, because the PA machine may
well have none.
"""

from __future__ import annotations

import socket
from typing import List, Optional

# Addresses that are never useful to hand to a staff member.
_USELESS_PREFIXES = ("127.", "0.", "169.254.")


def _is_usable(address: str) -> bool:
    return bool(address) and not address.startswith(_USELESS_PREFIXES)


def _is_private(address: str) -> bool:
    """True for the ranges a school LAN actually uses."""
    if address.startswith(("10.", "192.168.")):
        return True
    if address.startswith("172."):
        try:
            second = int(address.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def _route_address() -> Optional[str]:
    """The address of whichever interface would carry outbound traffic.

    Opening a UDP socket and "connecting" it sends no packets -- it just asks
    the operating system to pick a route -- so this works offline and is
    instant. It is the most reliable way to get the address that other machines
    on the network can actually reach.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.settimeout(0.2)
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def _hostname_addresses() -> List[str]:
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        return list(addresses)
    except OSError:
        return []


def lan_addresses() -> List[str]:
    """Every address staff could reach this machine on, best guess first."""
    found: List[str] = []

    primary = _route_address()
    if primary and _is_usable(primary):
        found.append(primary)

    for address in _hostname_addresses():
        if _is_usable(address) and address not in found:
            found.append(address)

    # Prefer ordinary private LAN addresses; anything else goes after, but
    # otherwise keep discovery order (the routed address first).
    order = {address: index for index, address in enumerate(found)}
    found.sort(key=lambda a: (not _is_private(a), order[a]))
    return found


def primary_address() -> Optional[str]:
    addresses = lan_addresses()
    return addresses[0] if addresses else None


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "this-computer"


def staff_url(port: int) -> str:
    """The one line to write on a sticky note."""
    address = primary_address()
    return f"http://{address}:{port}" if address else f"http://localhost:{port}"


def all_urls(port: int) -> List[str]:
    return [f"http://{address}:{port}" for address in lan_addresses()]
