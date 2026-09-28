"""Transport-level guards shared by the platform and legacy web shells."""

from urllib.parse import urlsplit

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def origin_allowed(
    origin: str | None, host: str | None, allowed: tuple[str, ...]
) -> bool:
    """Light WebSocket Origin check.

    Non-browser clients (no ``Origin`` header) are allowed, so the Admin console
    and other local tooling keep working. Browsers must match ``ALLOWED_ORIGINS``
    when it is configured; otherwise the request host or a loopback origin is
    required.
    """
    if not origin:
        return True
    if allowed:
        return origin in allowed
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    if host and parsed.netloc == host:
        return True
    return parsed.hostname in _LOOPBACK_HOSTS


def is_loopback_host(host: str | None) -> bool:
    """True when a client address is local.

    Used for the loopback-only Admin endpoint: the check is about the socket peer
    address, never a client-supplied header.
    """
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    return host.startswith("127.") or host in ("::1", "0:0:0:0:0:0:0:1")
