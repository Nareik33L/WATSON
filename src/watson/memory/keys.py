"""Database key providers. The key is never written to disk or logs."""

from __future__ import annotations

import secrets
import sys
from typing import Protocol, runtime_checkable

KEYCHAIN_SERVICE = "ai.watson.db"
KEYCHAIN_ACCOUNT = "sqlcipher-key"
KEY_BYTES = 32


class KeyProviderError(RuntimeError):
    """Key material could not be obtained or was malformed."""


@runtime_checkable
class KeyProvider(Protocol):
    def get_key(self) -> bytes:
        """Return a 32-byte SQLCipher raw key. Must not log the value."""
        ...


class InMemoryKeyProvider:
    """Test/dev provider. The key stays in process memory only."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise KeyProviderError(f"database key must be {KEY_BYTES} bytes")
        self._key = key

    def get_key(self) -> bytes:
        return self._key

    def __repr__(self) -> str:
        return f"InMemoryKeyProvider(key=<redacted {KEY_BYTES} bytes>)"


class KeychainKeyProvider:
    """macOS Keychain storage via ``keyring`` (plan D7 / §6)."""

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        account: str = KEYCHAIN_ACCOUNT,
    ) -> None:
        self._service = service
        self._account = account

    def get_key(self) -> bytes:
        import keyring

        stored = keyring.get_password(self._service, self._account)
        if stored is None:
            key = secrets.token_bytes(KEY_BYTES)
            # Keychain / Secret Service — not a file we control. Hex is not logged.
            keyring.set_password(self._service, self._account, key.hex())
            return key
        try:
            key = bytes.fromhex(stored)
        except ValueError as exc:
            raise KeyProviderError("Keychain entry is not a hex key") from exc
        if len(key) != KEY_BYTES:
            raise KeyProviderError(f"Keychain entry is {len(key)} bytes; expected {KEY_BYTES}")
        return key

    def __repr__(self) -> str:
        return f"KeychainKeyProvider(service={self._service!r}, account={self._account!r})"


_override: KeyProvider | None = None


def set_key_provider(provider: KeyProvider | None) -> None:
    """Install a process-wide provider (tests). ``None`` restores the default."""
    global _override
    _override = provider


def get_key_provider() -> KeyProvider:
    if _override is not None:
        return _override
    if sys.platform == "darwin":
        return KeychainKeyProvider()
    raise KeyProviderError(
        "No database key provider. On macOS the key lives in Keychain; "
        "tests should install an InMemoryKeyProvider."
    )
