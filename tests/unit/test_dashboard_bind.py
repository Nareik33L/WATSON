"""Dashboard bind policy: 127.0.0.1 only (plan §6 / §11.3)."""

from __future__ import annotations

import socket

import pytest

from watson.dashboard.bind import (
    ALLOWED_BIND_HOST,
    BindError,
    bind_available_port,
    pick_available_port,
    validate_bind_host,
)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "::1", "localhost", "192.168.1.10", "10.0.0.5", ""],
)
def test_non_loopback_and_alias_hosts_are_refused(host: str) -> None:
    with pytest.raises(BindError, match="127.0.0.1"):
        validate_bind_host(host)


def test_loopback_literal_is_accepted() -> None:
    assert validate_bind_host("127.0.0.1") == ALLOWED_BIND_HOST
    assert validate_bind_host(" 127.0.0.1 ") == ALLOWED_BIND_HOST


def test_pick_port_falls_back_when_preferred_is_busy() -> None:
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    busy = int(occupied.getsockname()[1])
    try:
        chosen = pick_available_port("127.0.0.1", busy)
        assert chosen != busy
        assert 1 <= chosen <= 65535
    finally:
        occupied.close()


def test_pick_port_refuses_non_loopback_before_bind() -> None:
    with pytest.raises(BindError, match="127.0.0.1"):
        pick_available_port("0.0.0.0", 8000)


def test_bind_available_port_holds_the_port() -> None:
    held = bind_available_port("127.0.0.1", 18000)
    try:
        port = int(held.getsockname()[1])
        challenger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                challenger.bind(("127.0.0.1", port))
        finally:
            challenger.close()
        chosen = pick_available_port("127.0.0.1", port)
        assert chosen != port
    finally:
        held.close()
