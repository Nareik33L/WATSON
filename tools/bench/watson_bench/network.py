"""Refuse non-loopback sockets during a benchmark run.

The guard is deliberately literal: it never calls DNS to decide whether a
host is local. Only loopback IPs/names, and only the configured Ollama
port, are allowed.

Socket patching lives in ``watson.privacy.network`` so the runtime guard
and this harness stay in lockstep.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from watson.privacy.network import (  # noqa: F401 — re-exported for tests
    LOOPBACK_HOSTS,
    NetworkGuardError,
    guard_is_active,
    is_loopback_host,
)
from watson.privacy.network import localhost_only as _localhost_only

OLLAMA_DEFAULT_PORT = 11434
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class OllamaEndpoint:
    base_url: str
    host: str
    port: int
    scheme: str


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


@contextmanager
def localhost_only(*, allowed_port: int | None = None) -> Iterator[None]:
    """Patch outbound socket/DNS so only loopback→Ollama-port succeeds."""
    port = OLLAMA_DEFAULT_PORT if allowed_port is None else allowed_port
    with _localhost_only(
        allowed_port=port,
        error_prefix="Benchmark run",
        lookup_hint="Models are downloaded only by `watson-bench fetch`.",
        connect_hint="Models are downloaded only by `watson-bench fetch`.",
        port_hint=f"(only the Ollama port {port} is allowed).",
    ):
        yield


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
