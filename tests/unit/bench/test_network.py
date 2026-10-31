from __future__ import annotations

import socket

import pytest

from watson_bench.network import NetworkGuardError, assert_loopback_url, localhost_only


def test_loopback_url_accepted() -> None:
    assert assert_loopback_url("http://127.0.0.1:11434").startswith("http://127.0.0.1")


def test_remote_url_rejected() -> None:
    with pytest.raises(NetworkGuardError):
        assert_loopback_url("http://example.com:11434")


def test_guard_blocks_remote_connect() -> None:
    with localhost_only(), pytest.raises(NetworkGuardError):
        socket.create_connection(("example.com", 80), timeout=0.2)


def test_guard_allows_loopback_attempt() -> None:
    with localhost_only():
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=0.05)
        except (OSError, ConnectionError, TimeoutError):
            # Nothing is listening on port 1; the guard must not be why it failed.
            return
