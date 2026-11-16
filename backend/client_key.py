"""Rate-limit key function.

In the Fly deployment the backend is private and every request comes from the
Streamlit frontend, so the transport peer is one address for all visitors. The
frontend forwards the visitor's address in X-Mirum-Client-IP. That header is
honoured only when MIRUM_TRUSTED_CLIENT_HEADER is on, which is safe only when
the backend is unreachable from the public internet (anyone who can call it
directly could otherwise forge the header and dodge or poison the limits).
"""
import ipaddress

import config

CLIENT_IP_HEADER = "X-Mirum-Client-IP"
_MAX_HEADER_LEN = 64  # longest textual IPv6 form is 45 chars


def peer_address(request) -> str:
    """Same result as slowapi.util.get_remote_address."""
    client = getattr(request, "client", None)
    if not client or not client.host:
        return "127.0.0.1"
    return client.host


def parse_client_ip(value) -> str | None:
    """Return the normalised address, or None if value is not exactly one IP."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > _MAX_HEADER_LEN or "%" in value:  # no IPv6 scope ids
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_key(request) -> str:
    """Limiter key: trusted forwarded visitor address, else the transport peer."""
    try:
        if config.TRUSTED_CLIENT_HEADER:
            parsed = parse_client_ip(request.headers.get(CLIENT_IP_HEADER))
            if parsed:
                return parsed
    except Exception:  # noqa: BLE001 - a key function must never raise
        pass
    try:
        return peer_address(request)
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
