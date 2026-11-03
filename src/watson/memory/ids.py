"""UUIDv7 identifiers (plan §4). Python 3.12 has no stdlib uuid7."""

from __future__ import annotations

import os
import time
import uuid
from contextvars import ContextVar, Token

_ISSUED_IDS: ContextVar[list[str] | None] = ContextVar("watson_issued_ids", default=None)
_ISSUED_STAMPS: ContextVar[list[str] | None] = ContextVar("watson_issued_stamps", default=None)


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: millisecond timestamp + random bits."""
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (timestamp_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def new_id() -> str:
    value = str(uuid7())
    issued = _ISSUED_IDS.get()
    if issued is not None:
        issued.append(value)
    return value


def note_stamp(stamp: str) -> None:
    """Record a run-issued ISO timestamp so eval hashing can replace it."""
    issued = _ISSUED_STAMPS.get()
    if issued is not None and stamp not in issued:
        issued.append(stamp)


def bind_issued(
    ids: list[str] | None = None, stamps: list[str] | None = None
) -> tuple[Token[list[str] | None], Token[list[str] | None]]:
    """Start collecting IDs/stamps this run issues. Pass lists to seed the registry."""
    id_token = _ISSUED_IDS.set(ids if ids is not None else [])
    stamp_token = _ISSUED_STAMPS.set(stamps if stamps is not None else [])
    return id_token, stamp_token


def reset_issued(
    tokens: tuple[Token[list[str] | None], Token[list[str] | None]],
) -> None:
    _ISSUED_STAMPS.reset(tokens[1])
    _ISSUED_IDS.reset(tokens[0])


def issued_ids() -> tuple[str, ...]:
    current = _ISSUED_IDS.get()
    return tuple(current) if current is not None else ()


def issued_stamps() -> tuple[str, ...]:
    current = _ISSUED_STAMPS.get()
    return tuple(current) if current is not None else ()
