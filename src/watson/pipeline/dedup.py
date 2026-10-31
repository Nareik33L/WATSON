"""Perceptual hashes of spool PNGs for near-duplicate consecutive frames.

pHash (DCT) and dHash (gradient) are 64-bit hex strings. They identify
near-identical images; they do not contain enough information to reconstruct
pixels (64 bits vs millions of image bits). Raw PNG bytes never leave this
module except as those hashes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

HASH_HEX_LEN = 16
DEFAULT_DEDUP_THRESHOLD = 4
PHASH_SIZE = 8
PHASH_FREQ = 4  # 32×32 sample, then 8×8 low-frequency DCT block
DHASH_WIDTH = 9
DHASH_HEIGHT = 8


@dataclass(frozen=True)
class FrameHashes:
    """Content-free 64-bit perceptual hashes of one PNG."""

    phash: str
    dhash: str

    def __post_init__(self) -> None:
        for name, value in (("phash", self.phash), ("dhash", self.dhash)):
            if len(value) != HASH_HEX_LEN:
                raise ValueError(f"{name} must be {HASH_HEX_LEN} hex characters")
            int(value, 16)


@dataclass(frozen=True)
class DedupDecision:
    """Whether ``current`` is a near-duplicate of ``previous``."""

    duplicate: bool
    distance: int
    threshold: int
    previous_frame_id: str
    frame_id: str


class DedupError(ValueError):
    """PNG could not be hashed."""


def hash_png(path: Path) -> FrameHashes:
    """Compute pHash and dHash from a spool PNG. Does not keep pixel buffers."""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            phash = perceptual_hash_hex(rgb)
            dhash = difference_hash_hex(rgb)
            rgb.close()
    except (OSError, ValueError) as exc:
        raise DedupError(f"{path.name}: cannot hash PNG") from exc
    return FrameHashes(phash=phash, dhash=dhash)


def perceptual_hash_hex(image: Image.Image) -> str:
    """64-bit DCT pHash (8×8 low frequencies of a 32×32 grayscale sample)."""
    sample = PHASH_SIZE * PHASH_FREQ
    gray = image.convert("L").resize((sample, sample), Image.Resampling.LANCZOS)
    pixels = list(gray.tobytes())
    grid = [
        [float(pixels[row * sample + col]) for col in range(sample)] for row in range(sample)
    ]
    dct_rows = [_dct_ii(row) for row in grid]
    dct = [_dct_ii([dct_rows[row][col] for row in range(sample)]) for col in range(sample)]
    # dct[col] is now a column-wise transform; rebuild the 8×8 low-frequency block.
    block: list[float] = []
    for row in range(PHASH_SIZE):
        for col in range(PHASH_SIZE):
            block.append(dct[col][row])
    median = _median(block)
    bits = 0
    for index, value in enumerate(block):
        if value >= median:
            bits |= 1 << (63 - index)
    return f"{bits:016x}"


def difference_hash_hex(image: Image.Image) -> str:
    """64-bit dHash: 9×8 grayscale, bit set when a pixel is darker than its right neighbour."""
    gray = image.convert("L").resize((DHASH_WIDTH, DHASH_HEIGHT), Image.Resampling.LANCZOS)
    pixels = list(gray.tobytes())
    bits = 0
    for row in range(DHASH_HEIGHT):
        for col in range(DHASH_HEIGHT):
            left = pixels[row * DHASH_WIDTH + col]
            right = pixels[row * DHASH_WIDTH + col + 1]
            if left > right:
                bits |= 1 << (63 - (row * DHASH_HEIGHT + col))
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    """Bit count of XOR of two 16-char hex hashes."""
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def decide_duplicate(
    current: FrameHashes,
    previous: FrameHashes,
    *,
    frame_id: str,
    previous_frame_id: str,
    threshold: int = DEFAULT_DEDUP_THRESHOLD,
) -> DedupDecision:
    """Near-duplicate when *either* pHash or dHash is within ``threshold``."""
    if threshold < 0:
        raise ValueError("dedup threshold must be >= 0")
    distance = min(
        hamming_distance(current.phash, previous.phash),
        hamming_distance(current.dhash, previous.dhash),
    )
    return DedupDecision(
        duplicate=distance <= threshold,
        distance=distance,
        threshold=threshold,
        previous_frame_id=previous_frame_id,
        frame_id=frame_id,
    )


def _dct_ii(vector: list[float]) -> list[float]:
    """Unnormalised DCT-II. Scale is irrelevant because we threshold on the median."""
    n = len(vector)
    factor = math.pi / n
    out = [0.0] * n
    for k in range(n):
        total = 0.0
        for index, value in enumerate(vector):
            total += value * math.cos(factor * (index + 0.5) * k)
        out[k] = total
    return out


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
