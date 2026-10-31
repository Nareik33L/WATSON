"""Per-install dashboard token. Keychain on darwin; env or 0600 file off-darwin."""

from __future__ import annotations

import errno
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

KEYCHAIN_SERVICE = "ai.watson.dashboard"
KEYCHAIN_ACCOUNT = "install-token"
TOKEN_BYTES = 32
MIN_TOKEN_LENGTH = 16
ENV_TOKEN = "WATSON_DASHBOARD_TOKEN"
TOKEN_FILENAME = "auth_token"


class TokenError(RuntimeError):
    """Dashboard install token could not be obtained or was malformed."""


@runtime_checkable
class TokenProvider(Protocol):
    def get_token(self) -> str:
        """Return the per-install token. Must not log the value."""
        ...


def _require_token(token: str, *, source: str) -> str:
    if len(token) < MIN_TOKEN_LENGTH:
        raise TokenError(f"{source} dashboard token must be at least {MIN_TOKEN_LENGTH} characters")
    return token


class InMemoryTokenProvider:
    """Test/dev provider. The token stays in process memory only."""

    def __init__(self, token: str) -> None:
        self._token = _require_token(token, source="in-memory")

    def get_token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "InMemoryTokenProvider(token=<redacted>)"


class EnvTokenProvider(InMemoryTokenProvider):
    """Linux/CI provider. Reads WATSON_DASHBOARD_TOKEN from the environment."""

    def __repr__(self) -> str:
        return "EnvTokenProvider(token=<redacted>)"


def _open_token_file(path: Path, flags: int, mode: int = 0o600) -> int:
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags, mode)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EPERM}:
            raise TokenError(f"{path} must not be a symlink") from exc
        raise


def _read_existing_token_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = _open_token_file(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TokenError(f"{path} must be a regular file")
        if info.st_uid != os.getuid():
            raise TokenError(f"{path} must be owned by the current user")
        if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise TokenError(f"{path} must be mode 0600")
        raw = os.read(fd, 4096)
    finally:
        os.close(fd)
    return _require_token(raw.decode("utf-8").strip(), source=str(path))


def _write_new_token_file(path: Path) -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = _open_token_file(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (token + "\n").encode())
    finally:
        os.close(fd)
    return token


class FileTokenProvider:
    """Off-darwin file provider. Creates a 0600 token file if it is missing."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get_token(self) -> str:
        try:
            return _read_existing_token_file(self._path)
        except FileNotFoundError:
            return _write_new_token_file(self._path)

    def __repr__(self) -> str:
        return f"FileTokenProvider(path={self._path!r})"


class KeychainTokenProvider:
    """macOS Keychain storage via ``keyring`` (plan D11 / §6)."""

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
    ) -> None:
        self._service = service
        self._account = account

    def get_token(self) -> str:
        import keyring

        stored = keyring.get_password(self._service, self._account)
        if stored is None or stored == "":
            token = secrets.token_urlsafe(TOKEN_BYTES)
            keyring.set_password(self._service, self._account, token)
            return token
        return _require_token(stored, source="keychain")

    def __repr__(self) -> str:
        return f"KeychainTokenProvider(service={self._service!r}, account={self._account!r})"


_override: TokenProvider | None = None


def set_token_provider(provider: TokenProvider | None) -> None:
    """Install a process-wide provider (tests). ``None`` restores the default."""
    global _override
    _override = provider


def get_token_provider(session_dir: Path | None = None) -> TokenProvider:
    if _override is not None:
        return _override
    if sys.platform == "darwin":
        return KeychainTokenProvider()
    env = os.environ.get(ENV_TOKEN)
    if env is not None and env != "":
        return EnvTokenProvider(env)
    if session_dir is None:
        raise TokenError(
            "No dashboard token provider. On macOS the token lives in Keychain; "
            f"on Linux set {ENV_TOKEN} or provide a session directory for a 0600 file."
        )
    return FileTokenProvider(session_dir / TOKEN_FILENAME)
