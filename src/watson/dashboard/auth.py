"""Session cookie, CSRF, launch codes, and Host/Origin checks (D11 / §6)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from starlette.requests import Request

SESSION_COOKIE: Final = "watson_session"
CSRF_HEADER: Final = "x-csrf-token"
CSRF_FORM_FIELD: Final = "csrf_token"
ALLOWED_HOST_NAME: Final = "127.0.0.1"
MAC_LEN: Final = 32
LAUNCH_CODE_TTL_SECONDS: Final = 60
LAUNCH_CODE_BYTES: Final = 32
SESSION_TTL_SECONDS: Final = 12 * 60 * 60


class AuthError(ValueError):
    """Session or CSRF material was missing or forged."""


@dataclass(frozen=True)
class Session:
    sid: str
    csrf: str


class LaunchCodes:
    """Single-use, short-lived codes exchanged for a session cookie."""

    def __init__(
        self,
        *,
        ttl_seconds: float = LAUNCH_CODE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._codes: dict[str, float] = {}

    def mint(self) -> str:
        self._purge()
        code = secrets.token_urlsafe(LAUNCH_CODE_BYTES)
        self._codes[code] = self._clock() + self._ttl_seconds
        return code

    def consume(self, offered: str) -> bool:
        self._purge()
        if not offered:
            return False
        offered_b = offered.encode("utf-8")
        match: str | None = None
        for stored in self._codes:
            stored_b = stored.encode("utf-8")
            if len(stored_b) != len(offered_b):
                continue
            if hmac.compare_digest(stored_b, offered_b):
                match = stored
                break
        if match is None:
            return False
        del self._codes[match]
        return True

    def _purge(self) -> None:
        now = self._clock()
        expired = [code for code, expires_at in self._codes.items() if expires_at <= now]
        for code in expired:
            del self._codes[code]


def tokens_equal(left: str, right: str) -> bool:
    left_b = left.encode("utf-8")
    right_b = right.encode("utf-8")
    if len(left_b) != len(right_b):
        return False
    return hmac.compare_digest(left_b, right_b)


def new_session() -> Session:
    return Session(sid=secrets.token_urlsafe(24), csrf=secrets.token_urlsafe(24))


def sign_session(
    session: Session,
    secret: str,
    *,
    epoch: int,
    issued_at: int,
) -> str:
    payload = f"{session.sid}:{session.csrf}:{issued_at}:{epoch}".encode()
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + mac).decode("ascii")


def read_session(
    raw: str,
    secret: str,
    *,
    epoch: int,
    now: int,
    ttl: int,
) -> Session | None:
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, binascii.Error):
        return None
    if len(decoded) < MAC_LEN:
        return None
    payload, mac = decoded[:-MAC_LEN], decoded[-MAC_LEN:]
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parts = text.split(":")
    if len(parts) != 4:
        return None
    sid, csrf, issued_s, epoch_s = parts
    if not sid or not csrf:
        return None
    try:
        issued_at = int(issued_s)
        cookie_epoch = int(epoch_s)
    except ValueError:
        return None
    if cookie_epoch != epoch:
        return None
    if issued_at > now + 30:
        return None
    if now - issued_at > ttl:
        return None
    return Session(sid=sid, csrf=csrf)


def session_from_request(request: Request, secret: str) -> Session | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw is None or raw == "":
        return None
    app = request.app
    return read_session(
        raw,
        secret,
        epoch=int(app.state.session_epoch),
        now=int(app.state.clock()),
        ttl=int(app.state.session_ttl),
    )


def provided_csrf(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    return None


def csrf_matches(request: Request, session: Session) -> bool:
    offered = provided_csrf(request)
    if offered is None:
        return False
    return tokens_equal(offered, session.csrf)


def host_allowed(host_header: str | None, *, expected_port: int | None = None) -> bool:
    """Reject DNS-rebinding Host values. Only 127.0.0.1[:port] is accepted."""
    if host_header is None or host_header == "":
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        return False
    name, sep, port = host.partition(":")
    if name != ALLOWED_HOST_NAME:
        return False
    if expected_port is not None:
        if not sep or not port.isdigit() or int(port) != expected_port:
            return False
        return True
    if sep and not port.isdigit():
        return False
    return True


def origin_allowed(origin: str | None, *, expected_port: int | None = None) -> bool:
    """True when Origin is absent (same-document GET) or is http://127.0.0.1[:port]."""
    if origin is None or origin == "":
        return True
    parsed = urlparse(origin)
    if parsed.scheme != "http":
        return False
    if parsed.hostname != ALLOWED_HOST_NAME:
        return False
    if parsed.path not in {"", "/"}:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.params or parsed.fragment:
        return False
    if expected_port is not None:
        return parsed.port == expected_port
    return True


def cookie_kwargs(*, max_age: int = SESSION_TTL_SECONDS) -> dict[str, object]:
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": False,
        "path": "/",
        "max_age": max_age,
    }
