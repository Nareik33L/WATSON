"""Database, migrations, repositories, and retrieval (FTS + vectors)."""

from watson.memory.db import DatabaseError, connect, open_database
from watson.memory.keys import (
    ENV_KEY,
    EnvKeyProvider,
    InMemoryKeyProvider,
    KeychainKeyProvider,
    KeyProvider,
    KeyProviderError,
    get_key_provider,
    set_key_provider,
)

__all__ = [
    "ENV_KEY",
    "DatabaseError",
    "EnvKeyProvider",
    "InMemoryKeyProvider",
    "KeychainKeyProvider",
    "KeyProvider",
    "KeyProviderError",
    "connect",
    "get_key_provider",
    "open_database",
    "set_key_provider",
]
