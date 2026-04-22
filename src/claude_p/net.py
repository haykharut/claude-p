"""
Helpers for detecting the host's reachable URLs.

Used by the Settings page to stop users from having to guess their own
IP / hostname when setting up mounts or remote access.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class HostInfo:
    hostname: str          # e.g. "hayks-macbook-pro-4"
    mdns_name: str | None  # e.g. "hayks-macbook-pro-4.local" when available
    lan_ip: str            # best-effort outgoing interface IP
    port: int


def _detect_lan_ip() -> str:
    """Best-effort reverse: open a UDP socket to a public address and read
    our own sockname. Doesn't actually send anything. Returns 127.0.0.1
    if offline or on a host with no routable interface.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except OSError:
            pass


def detect_host(port: int) -> HostInfo:
    hostname = socket.gethostname().strip() or "localhost"
    # macOS and most linux distros append `.local` via mDNS. We don't
    # probe — if the hostname already ends in .local we use it as-is;
    # otherwise we suggest the `.local` form as a candidate for Apple
    # devices on the LAN and leave it to the reader to verify.
    mdns = hostname if hostname.endswith(".local") else f"{hostname}.local"
    return HostInfo(
        hostname=hostname,
        mdns_name=mdns,
        lan_ip=_detect_lan_ip(),
        port=port,
    )


def dashboard_urls(info: HostInfo) -> list[tuple[str, str]]:
    """Return [(label, url)] pairs ordered loopback → lan → mdns."""
    out: list[tuple[str, str]] = [
        ("on this machine", f"http://localhost:{info.port}"),
    ]
    if info.lan_ip and info.lan_ip != "127.0.0.1":
        out.append(("on your LAN", f"http://{info.lan_ip}:{info.port}"))
    if info.mdns_name and info.mdns_name != "localhost.local":
        out.append(("from Apple devices", f"http://{info.mdns_name}:{info.port}"))
    return out


def webdav_urls(info: HostInfo) -> list[tuple[str, str]]:
    return [(label, f"{url}/fs") for label, url in dashboard_urls(info)]
