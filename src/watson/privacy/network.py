"""Refuse non-loopback sockets unless WATSON_ALLOW_NETWORK=1 (plan §6).

Shared with ``tools/bench`` so the benchmark harness does not reimplement the
same patches. Bench wraps :func:`localhost_only` with an Ollama-port restriction.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

ALLOW_NETWORK_ENV = "WATSON_ALLOW_NETWORK"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

_guard_depth = 0


class NetworkGuardError(RuntimeError):
    """Raised when a process tries to leave the loopback interface."""


def is_loopback_host(host: str) -> bool:
    """True only for a literal loopback name or IP. Never performs a lookup."""
    cleaned = host.strip().strip("[]").lower()
    if not cleaned:
        return False
    if cleaned in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        return False


def guard_is_active() -> bool:
    return _guard_depth > 0


@contextmanager
def localhost_only(
    *,
    allowed_port: int | None = None,
    bypass_env: str | None = None,
    error_prefix: str = "WATSON",
    lookup_hint: str = "Outbound network is disabled.",
    connect_hint: str = "Outbound network is disabled.",
    port_hint: str | None = None,
) -> Iterator[None]:
    """Patch outbound socket/DNS so only loopback destinations succeed.

    ``allowed_port`` restricts loopback TCP/UDP to that port (bench / Ollama).
    ``None`` allows any loopback port (runtime worker / dashboard).
    """
    global _guard_depth
    if bypass_env and os.environ.get(bypass_env) == "1":
        yield
        return

    originals: dict[str, Any] = {
        "create_connection": socket.create_connection,
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "sendmsg": socket.socket.sendmsg,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
    }

    def guarded_create(address: Any, *args: Any, **kwargs: Any) -> Any:
        _reject_destination(
            address,
            allowed_port=allowed_port,
            kind="connect",
            error_prefix=error_prefix,
            connect_hint=connect_hint,
            port_hint=port_hint,
        )
        return originals["create_connection"](address, *args, **kwargs)

    def guarded_connect(self: socket.socket, address: Any) -> None:
        _reject_destination(
            address,
            allowed_port=allowed_port,
            kind="connect",
            error_prefix=error_prefix,
            connect_hint=connect_hint,
            port_hint=port_hint,
        )
        originals["connect"](self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _reject_destination(
            address,
            allowed_port=allowed_port,
            kind="connect_ex",
            error_prefix=error_prefix,
            connect_hint=connect_hint,
            port_hint=port_hint,
        )
        return int(originals["connect_ex"](self, address))

    def guarded_sendto(self: socket.socket, data: Any, *args: Any) -> int:
        if args:
            _reject_destination(
                args[-1],
                allowed_port=allowed_port,
                kind="sendto",
                error_prefix=error_prefix,
                connect_hint=connect_hint,
                port_hint=port_hint,
            )
        return int(originals["sendto"](self, data, *args))

    def guarded_sendmsg(
        self: socket.socket,
        buffers: Any,
        ancdata: Any = (),
        flags: int = 0,
        address: Any = None,
    ) -> int:
        if address:
            _reject_destination(
                address,
                allowed_port=allowed_port,
                kind="sendmsg",
                error_prefix=error_prefix,
                connect_hint=connect_hint,
                port_hint=port_hint,
            )
        return int(originals["sendmsg"](self, buffers, ancdata, flags, address))

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _reject_lookup(host, error_prefix=error_prefix, lookup_hint=lookup_hint)
        return originals["getaddrinfo"](host, *args, **kwargs)

    def guarded_gethostbyname(host: Any) -> Any:
        _reject_lookup(host, error_prefix=error_prefix, lookup_hint=lookup_hint)
        return originals["gethostbyname"](host)

    def guarded_gethostbyname_ex(host: Any) -> Any:
        _reject_lookup(host, error_prefix=error_prefix, lookup_hint=lookup_hint)
        return originals["gethostbyname_ex"](host)

    _install_socket_patches(
        {
            "create_connection": guarded_create,
            "connect": guarded_connect,
            "connect_ex": guarded_connect_ex,
            "sendto": guarded_sendto,
            "sendmsg": guarded_sendmsg,
            "getaddrinfo": guarded_getaddrinfo,
            "gethostbyname": guarded_gethostbyname,
            "gethostbyname_ex": guarded_gethostbyname_ex,
        }
    )
    _guard_depth += 1
    try:
        yield
    finally:
        _install_socket_patches(originals)
        _guard_depth -= 1


def _install_socket_patches(fns: dict[str, Any]) -> None:
    """setattr avoids mypy method-assign errors on socket.socket.*."""
    setattr(socket, "create_connection", fns["create_connection"])
    setattr(socket.socket, "connect", fns["connect"])
    setattr(socket.socket, "connect_ex", fns["connect_ex"])
    setattr(socket.socket, "sendto", fns["sendto"])
    setattr(socket.socket, "sendmsg", fns["sendmsg"])
    setattr(socket, "getaddrinfo", fns["getaddrinfo"])
    setattr(socket, "gethostbyname", fns["gethostbyname"])
    setattr(socket, "gethostbyname_ex", fns["gethostbyname_ex"])


@contextmanager
def runtime_network_guard() -> Iterator[None]:
    """Process guard used by the WATSON CLI. Honours WATSON_ALLOW_NETWORK=1."""
    with localhost_only(bypass_env=ALLOW_NETWORK_ENV):
        yield


def _reject_lookup(host: object, *, error_prefix: str, lookup_hint: str) -> None:
    if host in (None, "", b""):
        return
    host_s = _as_text(host)
    if is_loopback_host(host_s):
        return
    raise NetworkGuardError(
        f"{error_prefix} blocked a DNS/lookup for {host_s!r}. {lookup_hint}"
    )


def _reject_destination(
    address: object,
    *,
    allowed_port: int | None,
    kind: str,
    error_prefix: str,
    connect_hint: str,
    port_hint: str | None,
) -> None:
    host, port = _address_parts(address)
    if host is None:
        raise NetworkGuardError(f"{error_prefix} blocked {kind} without a host.")
    if host.startswith("/") or host.startswith("\x00"):
        return
    if not is_loopback_host(host):
        raise NetworkGuardError(
            f"{error_prefix} blocked a non-loopback {kind} to {host!r}. {connect_hint}"
        )
    if allowed_port is not None and port is not None and port != allowed_port:
        extra = port_hint or f"(only port {allowed_port} is allowed)."
        raise NetworkGuardError(
            f"{error_prefix} blocked loopback {kind} to port {port} {extra}"
        )


def _address_parts(address: object) -> tuple[str | None, int | None]:
    if address is None:
        return None, None
    if isinstance(address, (str, bytes)):
        return _as_text(address), None
    if isinstance(address, tuple) and address:
        host = address[0]
        host_s = None if host in (None, "") else _as_text(host)
        port_i: int | None = None
        if len(address) > 1 and address[1] is not None:
            try:
                port_i = int(address[1])
            except (TypeError, ValueError):
                port_i = None
        return host_s, port_i
    return _as_text(address), None


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
