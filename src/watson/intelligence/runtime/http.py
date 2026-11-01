"""Loopback-only HTTP helpers for the Ollama adapter. No watson-bench import."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin, urlparse

from watson.intelligence.runtime.types import RuntimeCallError, RuntimeUnavailable
from watson.privacy.network import is_loopback_host

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_PORT = 11434


def canonical_ollama_url(value: str) -> str:
    """Return ``scheme://loopback:port``. Remote hosts never connect."""
    raw = value.strip()
    if not raw:
        raise RuntimeUnavailable("intelligence.ollama_host is empty.")
    scheme_end = raw.find("://")
    if scheme_end != -1:
        if raw[:scheme_end].lower() not in {"http", "https"}:
            raise RuntimeUnavailable(
                f"intelligence.ollama_host {value!r} uses an unsupported scheme."
            )
    else:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeUnavailable(
            f"intelligence.ollama_host {value!r} uses an unsupported scheme."
        )
    host = parsed.hostname or ""
    if not is_loopback_host(host):
        raise RuntimeUnavailable(
            f"intelligence.ollama_host {value!r} is not a loopback address. "
            "Only a local Ollama server is allowed."
        )
    port = parsed.port or DEFAULT_OLLAMA_PORT
    url_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{url_host}:{port}"


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST/GET JSON to ``base_url``. ``base_url`` must already be loopback."""
    canonical_ollama_url(base_url)
    root = base_url if base_url.endswith("/") else base_url + "/"
    url = urljoin(root, path.lstrip("/"))
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeCallError(f"Ollama HTTP {exc.code} on {path}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason if not isinstance(exc.reason, BaseException) else str(exc.reason)
        raise RuntimeUnavailable(
            f"Ollama is not reachable at {base_url} ({reason}). "
            "Start the local Ollama app or set intelligence.runtime = \"fake\"."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeCallError(f"Ollama timed out on {path}") from exc
    if not raw:
        return {}
    loaded = json.loads(raw.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeCallError(f"Ollama {path} returned a non-object")
    return loaded
