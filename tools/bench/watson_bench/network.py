"""Refuse non-loopback sockets during a benchmark run.

The guard is deliberately literal: it never calls DNS to decide whether a
host is local. Only loopback IPs/names, and only the configured Ollama
port, are allowed.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

OLLAMA_DEFAULT_PORT = 11434
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

_guard_depth = 0


class NetworkGuardError(RuntimeError):
    """Raised when the bench run tries to leave the loopback interface."""


@dataclass(frozen=True)
class OllamaEndpoint:
    base_url: str
    host: str
    port: int
    scheme: str


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


def parse_ollama_host(value: str) -> OllamaEndpoint:
    """Parse Ollama's own host forms into a canonical HTTP base URL.

    Accepts ``host``, ``host:port``, ``http(s)://host``, ``http(s)://host:port``.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("value is empty")
    scheme = "http"
    rest = raw
    if rest.startswith("https://"):
        scheme = "https"
        rest = rest[len("https://") :]
    elif rest.startswith("http://"):
        rest = rest[len("http://") :]
    elif "://" in rest.split("/", 1)[0]:
        raise ValueError(f"unsupported URL scheme in {value!r}")
    rest = rest.split("/", 1)[0]
    if not rest:
        raise ValueError(f"missing host in {value!r}")
    host, port = _split_host_port(rest)
    if port is None:
        port = OLLAMA_DEFAULT_PORT
    url_host = f"[{host}]" if ":" in host else host
    return OllamaEndpoint(
        base_url=f"{scheme}://{url_host}:{port}",
        host=host,
        port=port,
        scheme=scheme,
    )


def ollama_endpoint_or_reason(value: str) -> tuple[OllamaEndpoint | None, str | None]:
    try:
        endpoint = parse_ollama_host(value)
    except ValueError as exc:
        return None, f"Invalid OLLAMA_HOST {value!r} ({exc})."
    if not is_loopback_host(endpoint.host):
        return None, (
            f"OLLAMA_HOST {value!r} is not a loopback address. "
            "Only a local Ollama server is allowed during a run."
        )
    return endpoint, None


def configured_ollama_raw() -> str:
    return os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_URL


def allowed_ollama_port() -> int:
    endpoint, error = ollama_endpoint_or_reason(configured_ollama_raw())
    if error or endpoint is None:
        return OLLAMA_DEFAULT_PORT
    return endpoint.port


def assert_loopback_url(url: str) -> str:
    endpoint, error = ollama_endpoint_or_reason(url)
    if error or endpoint is None:
        raise NetworkGuardError(error or f"invalid Ollama host {url!r}")
    return endpoint.base_url


def guard_is_active() -> bool:
    return _guard_depth > 0


@contextmanager
def localhost_only(*, allowed_port: int | None = None) -> Iterator[None]:
    """Patch outbound socket/DNS so only loopback→Ollama-port succeeds."""
    global _guard_depth
    port = OLLAMA_DEFAULT_PORT if allowed_port is None else allowed_port
    originals = {
        "create_connection": socket.create_connection,
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "sendto": socket.socket.sendto,
        "sendmsg": socket.socket.sendmsg,
        "getaddrinfo": socket.getaddrinfo,
        "gethostbyname": socket.gethostbyname,
        "gethostbyname_ex": socket.gethostbyname_ex,
    }

    def guarded_create(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        _reject_destination(address, allowed_port=port, kind="connect")
        return originals["create_connection"](address, *args, **kwargs)

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        _reject_destination(address, allowed_port=port, kind="connect")
        return originals["connect"](self, address)

    def guarded_connect_ex(self, address):  # type: ignore[no-untyped-def]
        _reject_destination(address, allowed_port=port, kind="connect_ex")
        return originals["connect_ex"](self, address)

    def guarded_sendto(self, data, *args):  # type: ignore[no-untyped-def]
        if args:
            _reject_destination(args[-1], allowed_port=port, kind="sendto")
        return originals["sendto"](self, data, *args)

    def guarded_sendmsg(self, buffers, ancdata=(), flags=0, address=None):  # type: ignore[no-untyped-def]
        if address:
            _reject_destination(address, allowed_port=port, kind="sendmsg")
        return originals["sendmsg"](self, buffers, ancdata, flags, address)

    def guarded_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        _reject_lookup(host)
        return originals["getaddrinfo"](host, *args, **kwargs)

    def guarded_gethostbyname(host):  # type: ignore[no-untyped-def]
        _reject_lookup(host)
        return originals["gethostbyname"](host)

    def guarded_gethostbyname_ex(host):  # type: ignore[no-untyped-def]
        _reject_lookup(host)
        return originals["gethostbyname_ex"](host)

    socket.create_connection = guarded_create  # type: ignore[assignment]
    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = guarded_sendto  # type: ignore[method-assign]
    socket.socket.sendmsg = guarded_sendmsg  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = guarded_gethostbyname  # type: ignore[assignment]
    socket.gethostbyname_ex = guarded_gethostbyname_ex  # type: ignore[assignment]
    _guard_depth += 1
    try:
        yield
    finally:
        socket.create_connection = originals["create_connection"]
        socket.socket.connect = originals["connect"]
        socket.socket.connect_ex = originals["connect_ex"]
        socket.socket.sendto = originals["sendto"]
        socket.socket.sendmsg = originals["sendmsg"]
        socket.getaddrinfo = originals["getaddrinfo"]
        socket.gethostbyname = originals["gethostbyname"]
        socket.gethostbyname_ex = originals["gethostbyname_ex"]
        _guard_depth -= 1


def _split_host_port(rest: str) -> tuple[str, int | None]:
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            raise ValueError(f"unclosed IPv6 bracket in {rest!r}")
        host = rest[1:end]
        after = rest[end + 1 :]
        if not after:
            return host, None
        if after.startswith(":"):
            return host, _parse_port(after[1:])
        raise ValueError(f"invalid IPv6 hostport {rest!r}")
    if rest.count(":") > 1:
        # Bare IPv6 without a port (e.g. ::1).
        return rest.strip("[]"), None
    if ":" in rest:
        host, port_s = rest.rsplit(":", 1)
        if not host:
            raise ValueError(f"missing host in {rest!r}")
        return host, _parse_port(port_s)
    return rest, None


def _parse_port(text: str) -> int:
    if not text.isdigit():
        raise ValueError(f"invalid port {text!r}")
    port = int(text)
    if port < 1 or port > 65535:
        raise ValueError(f"invalid port {port}")
    return port


def _reject_lookup(host: object) -> None:
    if host in (None, "", b""):
        return
    host_s = _as_text(host)
    if is_loopback_host(host_s):
        return
    raise NetworkGuardError(
        f"Benchmark run blocked a DNS/lookup for {host_s!r}. "
        "Models are downloaded only by `watson-bench fetch`."
    )


def _reject_destination(address: object, *, allowed_port: int, kind: str) -> None:
    host, port = _address_parts(address)
    if host is None:
        raise NetworkGuardError(f"Benchmark run blocked {kind} without a host.")
    if host.startswith("/") or host.startswith("\x00"):
        return
    if not is_loopback_host(host):
        raise NetworkGuardError(
            f"Benchmark run blocked a non-loopback {kind} to {host!r}. "
            "Models are downloaded only by `watson-bench fetch`."
        )
    if port is not None and port != allowed_port:
        raise NetworkGuardError(
            f"Benchmark run blocked loopback {kind} to port {port} "
            f"(only the Ollama port {allowed_port} is allowed)."
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
