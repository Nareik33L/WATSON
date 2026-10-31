"""Refuse non-loopback sockets during a benchmark run."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlparse

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NetworkGuardError(RuntimeError):
    """Raised when the bench run tries to leave the loopback interface."""


def is_loopback_host(host: str) -> bool:
    cleaned = host.strip("[]")
    if cleaned in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(cleaned, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            if not ipaddress.ip_address(addr).is_loopback:
                return False
        except ValueError:
            return False
    return True


def assert_loopback_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or not is_loopback_host(host):
        raise NetworkGuardError(
            f"Only a local Ollama server is allowed during a run (got {url!r})."
        )
    return url


@contextmanager
def localhost_only() -> Iterator[None]:
    """Patch socket connect paths so only loopback destinations succeed."""

    original_create = socket.create_connection
    original_connect = socket.socket.connect

    def guarded_create(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        _reject(str(host))
        return original_create(address, *args, **kwargs)

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        _reject(str(host))
        return original_connect(self, address)

    socket.create_connection = guarded_create  # type: ignore[assignment]
    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.create_connection = original_create
        socket.socket.connect = original_connect


def _reject(host: str) -> None:
    if is_loopback_host(host):
        return
    # Unix-domain sockets pass a filesystem path / sockaddr; allow those.
    if host.startswith("/") or host.startswith("\x00"):
        return
    raise NetworkGuardError(
        f"Benchmark run blocked a non-loopback connection to {host!r}. "
        "Models are downloaded only by `watson-bench fetch`."
    )
