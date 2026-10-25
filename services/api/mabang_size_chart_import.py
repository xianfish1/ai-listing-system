from __future__ import annotations

import base64
import ipaddress
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from services.api.size_chart_uploads import MAX_UPLOAD_BYTES, save_size_chart


_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _is_private_or_local_host(hostname: str) -> bool:
    """Reject local/private destinations while allowing arbitrary public CDNs.

    DNS lookup failures are left to ``urlopen`` so an otherwise valid public
    hostname can still be handled by the caller's resolver/proxy.
    """
    host = hostname.rstrip(".").lower()
    if not host or host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
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
    return any(
        ipaddress.ip_address(item[4][0]).is_private
        or ipaddress.ip_address(item[4][0]).is_loopback
        or ipaddress.ip_address(item[4][0]).is_link_local
        or ipaddress.ip_address(item[4][0]).is_reserved
        or ipaddress.ip_address(item[4][0]).is_unspecified
        for item in resolved
    )


def import_mabang_size_chart(url: str, storage_dir: Path) -> dict[str, Any]:
    parsed = urlparse(str(url or "").strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or _is_private_or_local_host(parsed.hostname)
    ):
        raise ValueError("马帮尺码图来源无效")

    request = Request(parsed.geturl(), headers={"User-Agent": "ai-listing-system/1.0"})
    with urlopen(request, timeout=20) as response:
        content_type = str(response.headers.get_content_type() or "").lower()
        content = response.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("马帮尺码图超过 5 MiB")

    saved = save_size_chart(
        {
            "file_name": Path(parsed.path).name or "mabang-size-chart",
            "content_type": content_type,
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        storage_dir=storage_dir,
    )
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[content_type]
    return {**saved, "file_path": str(storage_dir / "uploads" / "size-charts" / f"{saved['id']}.{extension}")}
