"""Shared fixtures for WATSON unit and privacy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from watson.memory.keys import InMemoryKeyProvider, set_key_provider

TEST_KEY = bytes.fromhex("11" * 32)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WATSON_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def key_provider(monkeypatch: pytest.MonkeyPatch) -> InMemoryKeyProvider:
    provider = InMemoryKeyProvider(TEST_KEY)
    set_key_provider(provider)
    monkeypatch.setattr(
        "watson.memory.keys.get_key_provider",
        lambda: provider,
    )
    try:
        yield provider
    finally:
        set_key_provider(None)
