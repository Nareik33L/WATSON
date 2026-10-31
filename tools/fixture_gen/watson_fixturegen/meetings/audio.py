"""16 kHz mono WAV helpers (stdlib only)."""

from __future__ import annotations

import math
import random
import struct
import wave
from array import array
from pathlib import Path

from watson_fixturegen.meetings.script import SAMPLE_RATE, NoiseSpec

SAMPLE_WIDTH = 2
INT16_MAX = 32767


def write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


def read_pcm16_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1:
            raise ValueError(f"{path}: expected mono WAV, got {handle.getnchannels()} channels")
        if handle.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path}: expected 16-bit WAV")
        rate = handle.getframerate()
        pcm = handle.readframes(handle.getnframes())
    return pcm, rate


def duration_s(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    if sample_rate <= 0:
        return 0.0
    return (len(pcm) // SAMPLE_WIDTH) / float(sample_rate)


def silence_pcm(duration_s: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    n_samples = max(0, int(round(duration_s * sample_rate)))
    return b"\x00\x00" * n_samples


def tone_pcm(
    duration_s: float,
    frequency_hz: float = 440.0,
    amplitude: float = 0.28,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    n_samples = max(0, int(round(duration_s * sample_rate)))
    peak = int(amplitude * INT16_MAX)
    samples = array("h")
    two_pi = 2.0 * math.pi
    for index in range(n_samples):
        value = int(peak * math.sin(two_pi * frequency_hz * index / sample_rate))
        samples.append(_clip(value))
    return samples.tobytes()


def concat_pcm(chunks: list[bytes]) -> bytes:
    return b"".join(chunks)


def mix_white_noise(pcm: bytes, spec: NoiseSpec) -> bytes:
    samples = array("h")
    samples.frombytes(pcm)
    rng = random.Random(spec.seed)
    scale = max(0, int(spec.amplitude * INT16_MAX))
    if scale == 0:
        return pcm
    mixed = array("h")
    for sample in samples:
        mixed.append(_clip(int(sample) + rng.randint(-scale, scale)))
    return mixed.tobytes()


def peak_abs(pcm: bytes) -> int:
    if not pcm:
        return 0
    samples = array("h")
    samples.frombytes(pcm)
    return max(abs(int(sample)) for sample in samples)


def pack_int16(values: list[int]) -> bytes:
    return struct.pack("<" + "h" * len(values), *(_clip(v) for v in values))


def _clip(value: int) -> int:
    return max(-INT16_MAX - 1, min(INT16_MAX, value))
