import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse

import httpx

MAX_BYTES = 5 * 1024 * 1024

_BLOCKED = {"localhost", "metadata.google.internal"}


def media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_public_ip(raw: str) -> bool:
    ip = ipaddress.ip_address(raw)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def public_url(url: str, resolve: Callable = socket.getaddrinfo) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED or host.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = resolve(host, port)
        except OSError:
            return False
        addresses = [info[4][0] for info in infos]
        return bool(addresses) and all(map(is_public_ip, addresses))
    return is_public_ip(host)


def fetch_image(
    url: str,
    *,
    get: Callable | None = None,
    resolve: Callable = socket.getaddrinfo,
    limit: int = MAX_BYTES,
) -> dict | None:
    if not public_url(url, resolve=resolve):
        return None
    getter = get or _get
    try:
        response = getter(url, timeout=10.0, follow_redirects=False)
    except Exception:
        return None
    final = str(getattr(response, "url", url))
    if final != url and not public_url(final, resolve=resolve):
        return None
    if getattr(response, "status_code", 0) != 200:
        return None
    data = getattr(response, "content", b"")
    if not isinstance(data, (bytes, bytearray)) or len(data) > limit:
        return None
    kind = media_type(bytes(data))
    if kind is None:
        return None
    return {"data": bytes(data), "media_type": kind}


def _get(url: str, **kwargs):
    return httpx.get(url, **kwargs)
