"""Loopback-only bind policy for the dashboard (plan §6 / §11.3)."""

from __future__ import annotations

import socket

ALLOWED_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_FALLBACK_SPAN = 20


class BindError(ValueError):
    """Refused a non-loopback or otherwise disallowed bind address."""


def validate_bind_host(host: str) -> str:
    """Accept only ``127.0.0.1``. Refuse ``0.0.0.0``, LAN IPs, ``localhost``, ``::``."""
    cleaned = host.strip().strip("[]")
    if cleaned != ALLOWED_BIND_HOST:
        raise BindError(f"dashboard bind host must be {ALLOWED_BIND_HOST}, not {host!r}")
    return cleaned


def bind_available_port(host: str, preferred: int = DEFAULT_PORT) -> socket.socket:
    """Bind ``preferred`` (or the next free port) and return the held socket."""
    bound_host = validate_bind_host(host)
    if preferred < 1 or preferred > 65535:
        raise BindError(f"dashboard port must be 1–65535, not {preferred}")
    last = min(preferred + PORT_FALLBACK_SPAN, 65535)
    for port in range(preferred, last + 1):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((bound_host, port))
        except OSError:
            probe.close()
            continue
        return probe
    raise BindError(f"no free port on {bound_host} in {preferred}–{last}")


def pick_available_port(host: str, preferred: int = DEFAULT_PORT) -> int:
    """Return ``preferred`` if free on ``host``, otherwise the next free port."""
    sock = bind_available_port(host, preferred)
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()
