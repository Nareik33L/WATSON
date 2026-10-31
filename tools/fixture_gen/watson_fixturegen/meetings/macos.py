"""macOS `say` / `afconvert` wrappers. Dialogue rendering is Darwin-only."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from watson_fixturegen.meetings.audio import read_pcm16_wav
from watson_fixturegen.meetings.script import SAMPLE_RATE

AAC_BITRATE = 32000

LINUX_DIALOGUE_ERROR = (
    "meeting fixture rendering uses macOS `say` and `afconvert` and cannot run "
    f"on {sys.platform}. Render dialogue scripts on a Mac: "
    "uv run watson-fixturegen meetings render <script> --out <dir>"
)


class MacOSRequiredError(RuntimeError):
    """Raised when a dialogue render is requested off macOS."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or LINUX_DIALOGUE_ERROR)


def is_macos() -> bool:
    return sys.platform == "darwin"


def require_macos(*, feature: str = "dialogue TTS") -> None:
    if not is_macos():
        raise MacOSRequiredError(
            f"{feature} uses macOS `say` and `afconvert` and cannot run on {sys.platform}. "
            "Render dialogue scripts on a Mac: "
            "uv run watson-fixturegen meetings render <script> --out <dir>"
        )


def list_say_voices() -> list[str]:
    require_macos(feature="`say` voice listing")
    say = shutil.which("say")
    if say is None:
        raise MacOSRequiredError("macOS `say` is not on PATH")
    proc = subprocess.run(
        [say, "-v", "?"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`say -v ?` failed: {proc.stderr.strip() or proc.returncode}")
    voices: list[str] = []
    for line in proc.stdout.splitlines():
        name = line.strip().split()[0] if line.strip() else ""
        if name:
            voices.append(name)
    return voices


def resolve_voice(requested: str, available: list[str] | None = None) -> str:
    names = available if available is not None else list_say_voices()
    if requested in names:
        return requested
    lowered = {name.lower(): name for name in names}
    if requested.lower() in lowered:
        return lowered[requested.lower()]
    fallbacks = ("Samantha", "Alex", "Fred", "Victoria", "Daniel", "Karen")
    for name in fallbacks:
        if name in names:
            return name
    if names:
        return names[0]
    raise RuntimeError("no macOS `say` voices are installed")


def say_to_pcm(text: str, voice: str, dest_wav: Path, sample_rate: int = SAMPLE_RATE) -> bytes:
    require_macos(feature="macOS `say`")
    say = shutil.which("say")
    if say is None:
        raise MacOSRequiredError("macOS `say` is not on PATH")
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        say,
        "-v",
        voice,
        "-o",
        str(dest_wav),
        f"--data-format=LEI16@{sample_rate}",
        text,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not dest_wav.is_file():
        raise RuntimeError(
            f"`say` failed for voice {voice!r} (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip() or 'no output'}"
        )
    pcm, rate = read_pcm16_wav(dest_wav)
    if rate != sample_rate:
        raise RuntimeError(f"`say` wrote {rate} Hz, expected {sample_rate}")
    return pcm


def afconvert_m4a(wav_path: Path, m4a_path: Path, bitrate: int = AAC_BITRATE) -> Path:
    require_macos(feature="`afconvert` AAC encode")
    afconvert = shutil.which("afconvert")
    if afconvert is None:
        raise MacOSRequiredError("macOS `afconvert` is not on PATH")
    m4a_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        afconvert,
        "-f",
        "m4af",
        "-d",
        "aac",
        "-b",
        str(bitrate),
        str(wav_path),
        str(m4a_path),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not m4a_path.is_file():
        raise RuntimeError(
            f"`afconvert` failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip() or 'no output'}"
        )
    return m4a_path
