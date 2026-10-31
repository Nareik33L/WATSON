"""Detect chip, RAM, and OS for the M0-A bake-off report."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HardwareInfo:
    chip: str
    ram_gb: float
    os_name: str
    os_version: str
    arch: str
    python_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_hardware() -> HardwareInfo:
    system = platform.system()
    if system == "Darwin":
        return _detect_macos()
    if system == "Linux":
        return _detect_linux()
    return HardwareInfo(
        chip=platform.processor() or platform.machine() or "unknown",
        ram_gb=_ram_gb_fallback(),
        os_name=system or "unknown",
        os_version=platform.version(),
        arch=platform.machine(),
        python_version=platform.python_version(),
    )


def format_hardware(info: HardwareInfo) -> str:
    return (
        f"{info.os_name} {info.os_version} / {info.chip} / {info.ram_gb:g} GB RAM "
        f"/ {info.arch} / Python {info.python_version}"
    )


def _detect_macos() -> HardwareInfo:
    chip = _sysctl("machdep.cpu.brand_string") or _sysctl("hw.model") or "Apple Silicon"
    mem_raw = _sysctl("hw.memsize")
    if mem_raw and mem_raw.isdigit():
        ram_gb = round(int(mem_raw) / (1024**3), 1)
    else:
        ram_gb = _ram_gb_fallback()
    version = _capture(["sw_vers", "-productVersion"]) or platform.mac_ver()[0] or "unknown"
    return HardwareInfo(
        chip=chip,
        ram_gb=ram_gb,
        os_name="macOS",
        os_version=version,
        arch=platform.machine(),
        python_version=platform.python_version(),
    )


def _detect_linux() -> HardwareInfo:
    chip = _linux_cpu_model() or platform.processor() or platform.machine() or "unknown"
    os_name, os_version = _linux_os_release()
    return HardwareInfo(
        chip=chip,
        ram_gb=_linux_ram_gb(),
        os_name=os_name,
        os_version=os_version,
        arch=platform.machine(),
        python_version=platform.python_version(),
    )


def _linux_cpu_model() -> str | None:
    path = Path("/proc/cpuinfo")
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        lowered = line.lower()
        if lowered.startswith("model name") or lowered.startswith("hardware"):
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
    return None


def _linux_ram_gb() -> float:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return _ram_gb_fallback()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            return round(kb / (1024**2), 1)
    return _ram_gb_fallback()


def _linux_os_release() -> tuple[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return "Linux", platform.release()
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    return data.get("NAME", "Linux"), data.get("VERSION_ID", platform.release())


def _ram_gb_fallback() -> float:
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        phys = os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        return 0.0
    if page and phys:
        return round(page * phys / (1024**3), 1)
    return 0.0


def _sysctl(name: str) -> str | None:
    return _capture(["sysctl", "-n", name])


def _capture(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None
