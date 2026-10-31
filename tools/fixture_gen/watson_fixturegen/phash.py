"""Deterministic 64-bit difference hash (dHash) of a screenshot."""

from __future__ import annotations

from PIL import Image

HASH_SIZE = 8


def dhash_hex(image: Image.Image, *, size: int = HASH_SIZE) -> str:
    """Return a 16-char lowercase hex dHash.

    Resize uses nearest-neighbour so the same RGB buffer always yields the
    same digest, independent of LANCZOS implementation details.
    """
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.NEAREST)
    values = list(gray.tobytes())
    bits = 0
    bit = 1 << (size * size - 1)
    width = size + 1
    for y in range(size):
        row = y * width
        for x in range(size):
            if values[row + x] > values[row + x + 1]:
                bits |= bit
            bit >>= 1
    return f"{bits:016x}"
