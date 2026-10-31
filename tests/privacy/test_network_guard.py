"""Runtime network guard (plan §6) — loopback only unless WATSON_ALLOW_NETWORK=1."""

from __future__ import annotations

import socket

import pytest

from watson.privacy.network import (
    ALLOW_NETWORK_ENV,
    NetworkGuardError,
    localhost_only,
    runtime_network_guard,
)


def test_guard_blocks_connect_connect_ex_create_connection_getaddrinfo_sendto() -> None:
    with localhost_only():
        with pytest.raises(NetworkGuardError, match="connect"):
            socket.create_connection(("1.1.1.1", 80), timeout=0.2)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            with pytest.raises(NetworkGuardError, match="connect"):
                sock.connect(("1.1.1.1", 80))
            with pytest.raises(NetworkGuardError, match="connect_ex"):
                sock.connect_ex(("1.1.1.1", 80))
        finally:
            sock.close()

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(NetworkGuardError, match="sendto"):
                udp.sendto(b"leak", ("1.1.1.1", 53))
        finally:
            udp.close()

        with pytest.raises(NetworkGuardError, match="DNS"):
            socket.getaddrinfo("example.com", 443)


def test_guard_allows_loopback_any_port() -> None:
    with localhost_only():
        socket.getaddrinfo("127.0.0.1", 9)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", 9))
        except OSError:
            pass
        finally:
            sock.close()


def test_allow_network_env_bypasses_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_NETWORK_ENV, "1")
    with runtime_network_guard():
        try:
            socket.getaddrinfo("example.com", 443)
        except NetworkGuardError:
            pytest.fail("WATSON_ALLOW_NETWORK=1 must disable the runtime guard")
        except OSError:
            pass


def test_runtime_guard_blocks_non_loopback_connect_attempt() -> None:
    """§6 / §11.3: worker/CLI refuse a non-loopback TCP connect."""
    with runtime_network_guard():
        with pytest.raises(NetworkGuardError, match="non-loopback"):
            socket.create_connection(("1.1.1.1", 443), timeout=0.2)
