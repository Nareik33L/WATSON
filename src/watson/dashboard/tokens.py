"""Per-install dashboard token. Keychain on darwin; env or 0600 file off-darwin."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

KEYCHAIN_SERVICE = "ai.watson.dashboard"
KEYCHAIN_ACCOUNT = "install-token"
TOKEN_BYTES = 32
ENV_TOKEN = "WATSON_DASHBOARD_TOKEN"
TOKEN_FILENAME = "auth_token"


class TokenError(RuntimeError):
    """Dashboard install token could not be obtained or was malformed."""


@runtime_checkable
class TokenProvider(Protocol):
    def get_token(self) -> str:
        """Return the per-install token. Must not log the value."""
        ...


class InMemoryTokenProvider:
    """Test/dev provider. The token stays in process memory only."""

    def __init__(self, token: str) -> None:
        if not token:
            raise TokenError("dashboard token must not be empty")
        self._token = token

    def get_token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return "InMemoryTokenProvider(token=<redacted>)"


class EnvTokenProvider(InMemoryTokenProvider):
    """Linux/CI provider. Reads WATSON_DASHBOARD_TOKEN from the environment."""

    def __repr__(self) -> str:
        return "EnvTokenProvider(token=<redacted>)"


class FileTokenProvider:
    """Off-darwin file provider. Creates a 0600 token file if it is missing."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get_token(self) -> str:
        if self._path.is_file():
            mode = self._path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise TokenError(f"{self._path} must be mode 0600")
            token = self._path.read_text(encoding="utf-8").strip()
            if not token:
                raise TokenError(f"{self._path} is empty")
            return token
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(mode=0o600, exist_ok=False)
        self._path.chmod(0o600)
        self._path.write_text(token + "\n", encoding="utf-8")
        self._path.chmod(0o600)
        return token

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
        return stored

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
