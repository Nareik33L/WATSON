"""Session cookie, CSRF, and Host/Origin checks (D11 / §6)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from starlette.requests import Request

SESSION_COOKIE: Final = "watson_session"
CSRF_HEADER: Final = "x-csrf-token"
CSRF_FORM_FIELD: Final = "csrf_token"
ALLOWED_HOST_NAME: Final = "127.0.0.1"


class AuthError(ValueError):
    """Session or CSRF material was missing or forged."""


@dataclass(frozen=True)
class Session:
    sid: str
    csrf: str


def tokens_equal(left: str, right: str) -> bool:
    left_b = left.encode("utf-8")
    right_b = right.encode("utf-8")
    if len(left_b) != len(right_b):
        return False
    return hmac.compare_digest(left_b, right_b)


def new_session() -> Session:
    return Session(sid=secrets.token_urlsafe(24), csrf=secrets.token_urlsafe(24))


def sign_session(session: Session, secret: str) -> str:
    payload = f"{session.sid}:{session.csrf}".encode()
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + mac).decode("ascii")


def read_session(raw: str, secret: str) -> Session | None:
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, binascii.Error):
        return None
    try:
        payload, mac = decoded.rsplit(b".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    sid, sep, csrf = text.partition(":")
    if not sep or not sid or not csrf:
        return None
    return Session(sid=sid, csrf=csrf)


def session_from_request(request: Request, secret: str) -> Session | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw is None or raw == "":
        return None
    return read_session(raw, secret)


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
    if sep:
        if not port.isdigit():
            return False
        if expected_port is not None and int(port) != expected_port:
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
    if expected_port is not None and parsed.port is not None and parsed.port != expected_port:
        return False
    return True


def cookie_kwargs() -> dict[str, object]:
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": False,
        "path": "/",
    }
