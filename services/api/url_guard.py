"""Reject local/private HTTP destinations for operator-supplied URLs.

Shared by the Mabang size-chart import and the Excel image downloader so both
paths enforce the same rule.
"""

from __future__ import annotations

import ipaddress
import socket

_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}


def is_private_or_local_host(hostname: str) -> bool:
    """Reject local/private destinations while allowing arbitrary public CDNs.

    DNS lookup failures are left to the caller so an otherwise valid public
    hostname can still be handled by its resolver/proxy.
    """
    host = str(hostname or "").rstrip(".").lower()
    if not host or host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return bool(
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        )
    try:
        resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for item in resolved:
        try:
            candidate = ipaddress.ip_address(item[4][0])
        except ValueError:
            continue
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_reserved
            or candidate.is_unspecified
        ):
            return True
    return False
