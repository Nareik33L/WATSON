"""The SQLCipher key must never be written to disk or logs."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from watson.memory.db import connect
from watson.memory.keys import (
    ENV_KEY,
    EnvKeyProvider,
    InMemoryKeyProvider,
    KeychainKeyProvider,
    KeyProviderError,
    get_key_provider,
    set_key_provider,
)
from watson.privacy.logging import configure_logging

TEST_KEY = bytes.fromhex("11" * 32)


def test_in_memory_repr_and_logs_hide_the_key() -> None:
    provider = InMemoryKeyProvider(TEST_KEY)
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    log = logging.getLogger("watson.keys")
    log.debug("provider %r key_hex_must_not_appear", provider)
    output = buf.getvalue()
    assert TEST_KEY.hex() not in output
    assert "redacted" in repr(provider)
    assert TEST_KEY.hex() not in repr(provider)


def test_connect_does_not_persist_the_key(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "watson.db"
    provider = InMemoryKeyProvider(TEST_KEY)
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, stream=buf)
    conn = connect(path, provider.get_key(), load_vec=False)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    output = buf.getvalue()
    assert TEST_KEY.hex() not in output
    assert TEST_KEY.hex().upper() not in output

    for file in tmp_path.rglob("*"):
        if not file.is_file():
            continue
        data = file.read_bytes()
        assert TEST_KEY not in data
        assert TEST_KEY.hex().encode() not in data
        assert TEST_KEY.hex().upper().encode() not in data


def test_keychain_provider_roundtrip_via_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store: dict[tuple[str, str], str] = {}

    def get_password(service: str, account: str) -> str | None:
        return store.get((service, account))

    def set_password(service: str, account: str, password: str) -> None:
        store[(service, account)] = password

    monkeypatch.setattr("keyring.get_password", get_password)
    monkeypatch.setattr("keyring.set_password", set_password)

    provider = KeychainKeyProvider(service="ai.watson.test", account="db")
    first = provider.get_key()
    second = provider.get_key()
    assert first == second
    assert len(first) == 32
    assert first.hex() == store[("ai.watson.test", "db")]
    assert "key=" not in repr(provider)


def test_get_key_provider_requires_override_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_key_provider(None)
    monkeypatch.setattr("watson.memory.keys.sys.platform", "linux")
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(KeyProviderError, match="key provider"):
        get_key_provider()
    set_key_provider(InMemoryKeyProvider(TEST_KEY))
    assert get_key_provider().get_key() == TEST_KEY
    set_key_provider(None)


def test_env_key_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    set_key_provider(None)
    monkeypatch.setattr("watson.memory.keys.sys.platform", "linux")
    monkeypatch.setenv(ENV_KEY, TEST_KEY.hex())
    provider = get_key_provider()
    assert isinstance(provider, EnvKeyProvider)
    assert provider.get_key() == TEST_KEY
    assert TEST_KEY.hex() not in repr(provider)
    monkeypatch.setenv(ENV_KEY, "not-hex")
    with pytest.raises(KeyProviderError, match="hex"):
        get_key_provider()
    set_key_provider(None)
