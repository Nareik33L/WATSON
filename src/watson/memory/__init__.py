"""Database, migrations, repositories, and retrieval (FTS + vectors)."""

from watson.memory.db import DatabaseError, connect, open_database
from watson.memory.keys import (
    InMemoryKeyProvider,
    KeychainKeyProvider,
    KeyProvider,
    KeyProviderError,
    get_key_provider,
    set_key_provider,
)

__all__ = [
    "DatabaseError",
    "InMemoryKeyProvider",
    "KeychainKeyProvider",
    "KeyProvider",
    "KeyProviderError",
    "connect",
    "get_key_provider",
    "open_database",
    "set_key_provider",
]
