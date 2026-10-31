from __future__ import annotations

import socket

import pytest

from watson_bench.cli import main
from watson_bench.network import (
    NetworkGuardError,
    assert_loopback_url,
    guard_is_active,
    is_loopback_host,
    localhost_only,
)
from watson_bench.paths import samples_dir


def test_loopback_url_accepted() -> None:
    assert assert_loopback_url("http://127.0.0.1:11434").startswith("http://127.0.0.1")
    assert assert_loopback_url("127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_remote_url_rejected() -> None:
    with pytest.raises(NetworkGuardError):
        assert_loopback_url("http://example.com:11434")


def test_is_loopback_host_never_looks_up(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("DNS lookup must not happen")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "gethostbyname", boom)
    monkeypatch.setattr(socket, "gethostbyname_ex", boom)
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")
    assert not is_loopback_host("example.com")
    assert not is_loopback_host("not-a-real-host.invalid")


def test_guard_blocks_remote_connect() -> None:
    with localhost_only(), pytest.raises(NetworkGuardError):
        socket.create_connection(("1.1.1.1", 80), timeout=0.2)


def test_guard_blocks_connect_ex() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        with localhost_only(), pytest.raises(NetworkGuardError, match="connect_ex"):
            sock.connect_ex(("1.1.1.1", 80))
    finally:
        sock.close()


def test_guard_blocks_udp_sendto() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with localhost_only(), pytest.raises(NetworkGuardError, match="sendto"):
            sock.sendto(b"leak", ("1.1.1.1", 53))
    finally:
        sock.close()


def test_guard_blocks_sendmsg() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with localhost_only(), pytest.raises(NetworkGuardError, match="sendmsg"):
            sock.sendmsg([b"leak"], [], 0, ("1.1.1.1", 53))
    finally:
        sock.close()


def test_guard_blocks_getaddrinfo_and_gethostbyname() -> None:
    with localhost_only():
        with pytest.raises(NetworkGuardError, match="DNS"):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(NetworkGuardError, match="DNS"):
            socket.gethostbyname("example.com")
        with pytest.raises(NetworkGuardError, match="DNS"):
            socket.gethostbyname_ex("example.com")


def test_guard_allows_loopback_ollama_port_only() -> None:
    with localhost_only(allowed_port=11434):
        socket.getaddrinfo("127.0.0.1", 11434)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("127.0.0.1", 11434))
        except OSError:
            pass
        finally:
            sock.close()
        with pytest.raises(NetworkGuardError, match="port"):
            socket.socket().connect(("127.0.0.1", 80))


def test_watson_bench_run_enables_the_guard(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from watson_bench import run as run_mod

    seen: dict[str, bool] = {"inside": False, "active": False}
    original = run_mod._run_one

    def wrapped(*args: object, **kwargs: object):
        seen["active"] = guard_is_active()
        with pytest.raises(NetworkGuardError):
            socket.getaddrinfo("example.com", 443)
        leak = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(NetworkGuardError):
                leak.connect_ex(("1.1.1.1", 80))
        finally:
            leak.close()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(NetworkGuardError):
                udp.sendto(b"x", ("1.1.1.1", 53))
        finally:
            udp.close()
        seen["inside"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(run_mod, "_run_one", wrapped)
    code = main(
        [
            "run",
            "--profile",
            "8gb",
            "--runtime",
            "fake",
            "--role",
            "embeddings",
            "--frames",
            str(samples_dir()),
            "--out",
            str(tmp_path / "guard-run"),
        ]
    )
    assert code == 0
    assert seen["inside"]
    assert seen["active"]
    assert not guard_is_active()
