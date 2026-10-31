"""Deterministic 64-bit average hash matching the helper sidecar schema."""

from __future__ import annotations

from PIL import Image

HASH_SIZE = 8


def average_hash_hex(image: Image.Image, *, size: int = HASH_SIZE) -> str:
    """Return a 16-char lowercase hex average hash (8×8 mean-threshold).

    Same algorithm as `PerceptualHash.hash` in the Swift helper: grayscale,
    shrink to 8×8, bit *i* is set when that pixel is >= the mean. Resize uses
    nearest-neighbour so the same RGB buffer always yields the same digest.
    """
    gray = image.convert("L").resize((size, size), Image.Resampling.NEAREST)
    values = list(gray.tobytes())
    mean = sum(values) // len(values)
    bits = 0
    for index, pixel in enumerate(values):
        if pixel >= mean:
            bits |= 1 << (63 - index)
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    """Bit count of XOR of two 16-char hex average hashes."""
    return bin(int(left, 16) ^ int(right, 16)).count("1")
