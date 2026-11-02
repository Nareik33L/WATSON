"""§11.2 metric thresholds in one config (plan §11.2)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")

HARD_ZERO = frozenset({"unsupported_achievements"})
HARD_HUNDRED = frozenset({"claims_with_evidence", "citations_resolvable"})
PROFILE_KEY = "profiles"
# Auto-select min-spec below 16 GiB so the 8 GB dev Mac is included; 16 GB+ is primary.
MIN_SPEC_RAM_LIMIT_BYTES = 16 * 1024**3
KNOWN_PROFILES = frozenset({"primary", "min-spec"})


@dataclass(frozen=True)
class Threshold:
    minimum: float | None = None
    maximum: float | None = None
    maximum_exclusive: float | None = None

    def allows(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        if self.maximum_exclusive is not None and value >= self.maximum_exclusive:
            return False
        return True

    def describe(self) -> str:
        if self.minimum is not None and self.maximum is None and self.maximum_exclusive is None:
            return f">= {self.minimum:g}"
        if self.maximum is not None:
            return f"<= {self.maximum:g}"
        if self.maximum_exclusive is not None:
            return f"< {self.maximum_exclusive:g}"
        return "any"


def load_duplicate_jaccard_min(path: Path | None = None) -> float:
    """Minimum token-set Jaccard for a paraphrased adjacent duplicate."""
    raw = json.loads((path or THRESHOLDS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("thresholds config must be a JSON object")
    spec = raw.get("duplicate_observation_rate")
    if not isinstance(spec, dict) or "jaccard_min" not in spec:
        raise ValueError("duplicate_observation_rate.jaccard_min is required")
    raw_value = spec["jaccard_min"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError(
            f"duplicate_observation_rate.jaccard_min must be in (0, 1], got {raw_value!r}"
        )
    value = float(raw_value)
    if not 0 < value <= 1:
        raise ValueError(f"duplicate_observation_rate.jaccard_min must be in (0, 1], got {value:g}")
    return value


def load_thresholds(path: Path | None = None) -> dict[str, Threshold]:
    raw = json.loads((path or THRESHOLDS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("thresholds config must be a JSON object")
    out: dict[str, Threshold] = {}
    for name, spec in raw.items():
        if name == PROFILE_KEY:
            continue
        if not isinstance(spec, dict):
            raise ValueError(f"threshold {name!r} must be an object")
        out[str(name)] = Threshold(
            minimum=_opt_float(spec, "min"),
            maximum=_opt_float(spec, "max"),
            maximum_exclusive=_opt_float(spec, "max_exclusive"),
        )
    return out


def load_profile_thresholds(path: Path | None = None) -> dict[str, dict[str, Threshold]]:
    raw = json.loads((path or THRESHOLDS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("thresholds config must be a JSON object")
    specs = raw.get(PROFILE_KEY)
    if not isinstance(specs, dict) or not specs:
        raise ValueError("thresholds config must include a profiles object")
    out: dict[str, dict[str, Threshold]] = {}
    for profile, rules in specs.items():
        name = str(profile)
        if name not in KNOWN_PROFILES:
            raise ValueError(f"unknown eval profile {name!r}")
        if not isinstance(rules, dict):
            raise ValueError(f"profile {name!r} must be an object")
        parsed: dict[str, Threshold] = {}
        for metric, spec in rules.items():
            if not isinstance(spec, dict):
                raise ValueError(f"profile {name!r} metric {metric!r} must be an object")
            parsed[str(metric)] = Threshold(
                minimum=_opt_float(spec, "min"),
                maximum=_opt_float(spec, "max"),
                maximum_exclusive=_opt_float(spec, "max_exclusive"),
            )
        out[name] = parsed
    missing = KNOWN_PROFILES - set(out)
    if missing:
        raise ValueError(f"thresholds profiles missing {sorted(missing)}")
    return out


def physical_ram_bytes() -> int:
    """Installed RAM in bytes. Linux MemTotal, Darwin hw.memsize, else sysconf."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("MemTotal:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    break
                return int(parts[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        text = proc.stdout.strip()
        if text.isdigit():
            return int(text)
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        phys = os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        return 0
    if page and phys:
        return int(page) * int(phys)
    return 0


def select_eval_profile(name: str | None = None, *, ram_bytes: int | None = None) -> str:
    """``min-spec`` when RAM < 16 GiB (covers the 8 GB Mac); ``primary`` otherwise."""
    if name in KNOWN_PROFILES:
        return name
    installed = physical_ram_bytes() if ram_bytes is None else ram_bytes
    if installed < MIN_SPEC_RAM_LIMIT_BYTES:
        return "min-spec"
    return "primary"


def gate_profile(
    *,
    understand_p95_s: float | None,
    worker_plus_model_peak_bytes: int | None,
    profile: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Compare understand p95 and worker+model peak to the active §11.4 profile."""
    if profile not in KNOWN_PROFILES:
        raise ValueError(f"unknown eval profile {profile!r}")
    rules = load_profile_thresholds(path)[profile]
    measured: dict[str, float | None] = {
        "understand_p95_s": None if understand_p95_s is None else float(understand_p95_s),
        "worker_plus_model_peak_bytes": (
            None if worker_plus_model_peak_bytes is None else float(worker_plus_model_peak_bytes)
        ),
    }
    failures: list[str] = []
    for name, rule in rules.items():
        value = measured.get(name)
        if value is None or not rule.allows(value):
            failures.append(name)
    return {"passed": not failures, "failures": failures, "profile": profile}


def gate_metrics(
    metrics: dict[str, float], thresholds: dict[str, Threshold] | None = None
) -> dict[str, Any]:
    """Compare named metrics to §11.2 thresholds. Keys are never swapped.

    A missing metric or an empty aggregate is a failure (undefined → fail).
    """
    rules = thresholds if thresholds is not None else load_thresholds()
    failures: list[str] = []
    hard_failures: list[str] = []
    if not metrics:
        names = list(rules)
        hard = [name for name in names if name in HARD_ZERO or name in HARD_HUNDRED]
        return {
            "passed": False,
            "failures": ["empty_aggregate", *names],
            "hard_failures": hard,
        }
    for name, rule in rules.items():
        if name not in metrics:
            failures.append(name)
            if name in HARD_ZERO or name in HARD_HUNDRED:
                hard_failures.append(name)
            continue
        value = float(metrics[name])
        if rule.allows(value):
            continue
        failures.append(name)
        if name in HARD_ZERO or name in HARD_HUNDRED:
            hard_failures.append(name)
    return {
        "passed": not failures,
        "failures": failures,
        "hard_failures": hard_failures,
    }


def _opt_float(spec: dict[str, Any], key: str) -> float | None:
    if key not in spec:
        return None
    return float(spec[key])
