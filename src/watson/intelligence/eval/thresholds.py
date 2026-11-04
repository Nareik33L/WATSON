"""§11.2 metric thresholds in one config (plan §11.2)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")

# Parked Revision 2 quality rows. Still measured; they do not fail --strict.
PARKED_QUALITY = frozenset(
    {
        "project_f1",
        "people_f1",
        "completed_accuracy",
        "decisions_recall",
        "decisions_precision",
        "commitments_recall",
        "commitments_precision",
        "unsupported_achievements",
        "duplicate_observation_rate",
        "noise_non_work_rate",
    }
)
# Measured zero for the parked unsupported-achievements row. Not a hard gate.
HARD_ZERO = frozenset({"unsupported_achievements"})
HARD_HUNDRED = frozenset({"claims_with_evidence", "citations_resolvable"})
DEAL_ACCURACY_MIN = 0.85
PROFILE_KEY = "profiles"
# Auto-select min-spec below 16 GiB so the 8 GB dev Mac is included; 16 GB+ is primary.
MIN_SPEC_RAM_LIMIT_BYTES = 16 * 1024**3
KNOWN_PROFILES = frozenset({"primary", "min-spec"})
# 8 GB engineering headroom. Reported in report.json; never a profile gate.
HEADROOM_UNDERSTAND_P95_S = 30.0
HEADROOM_PEAK_BYTES = int(3.3 * 1024**3)


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
    consolidate_s: float | None = None,
    completed_without_kill: float | None = None,
) -> dict[str, Any]:
    """Compare measured profile metrics to the §11.4 profile.

    Primary gates understand p95, worker+model peak, and consolidation.
    Min-spec records p95, peak, and ``consolidate_s`` but gates
    ``completed_without_kill`` (eval finished every fixture day with no
    crash, OOM kill, or connection loss).
    """
    if profile not in KNOWN_PROFILES:
        raise ValueError(f"unknown eval profile {profile!r}")
    rules = load_profile_thresholds(path)[profile]
    measured: dict[str, float | None] = {
        "understand_p95_s": None if understand_p95_s is None else float(understand_p95_s),
        "worker_plus_model_peak_bytes": (
            None if worker_plus_model_peak_bytes is None else float(worker_plus_model_peak_bytes)
        ),
        "consolidate_s": None if consolidate_s is None else float(consolidate_s),
        "completed_without_kill": (
            None if completed_without_kill is None else float(completed_without_kill)
        ),
    }
    failures: list[str] = []
    for name, rule in rules.items():
        value = measured.get(name)
        if value is None or not rule.allows(value):
            failures.append(name)
    return {"passed": not failures, "failures": failures, "profile": profile}


def headroom_status(
    *,
    understand_p95_s: float | None,
    worker_plus_model_peak_bytes: int | None,
) -> dict[str, Any]:
    """8 GB engineering headroom. Informational only — never a gate."""
    p95 = None if understand_p95_s is None else float(understand_p95_s)
    peak = None if worker_plus_model_peak_bytes is None else int(worker_plus_model_peak_bytes)
    return {
        "gating": False,
        "understand_p95_s": {
            "max_exclusive": HEADROOM_UNDERSTAND_P95_S,
            "value": p95,
            "within": p95 is not None and p95 < HEADROOM_UNDERSTAND_P95_S,
        },
        "worker_plus_model_peak_bytes": {
            "max_exclusive": HEADROOM_PEAK_BYTES,
            "value": peak,
            "within": peak is not None and peak < HEADROOM_PEAK_BYTES,
        },
    }


def deal_quality_passed(
    grouping: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    privacy_fail: bool,
) -> bool:
    """§11.2 deal gate: both accuracies ≥ 0.85 and no privacy leak."""
    if privacy_fail:
        return False
    if not grouping or not summary:
        return False
    try:
        grouping_acc = float(grouping["accuracy"])
        summary_acc = float(summary["accuracy"])
    except (KeyError, TypeError, ValueError):
        return False
    return grouping_acc >= DEAL_ACCURACY_MIN and summary_acc >= DEAL_ACCURACY_MIN


def passing_deal_blocks() -> tuple[dict[str, Any], dict[str, Any]]:
    """Structural-ok deal blocks for tests that are not scoring deals."""
    grouping = {
        "accuracy": 1.0,
        "assignment_accuracy": 1.0,
        "deal_segment_recall": 1.0,
        "pairwise_precision": 1.0,
        "pairwise_recall": 1.0,
        "pairwise_f1": 1.0,
        "correct": 0,
        "segments": 0,
        "deal_segments": 0,
        "deal_recalled": 0,
        "pairwise_tp": 0,
        "pairwise_fp": 0,
        "pairwise_fn": 0,
        "days": {},
    }
    summary = {
        "accuracy": 1.0,
        "claim_precision": 1.0,
        "what_happened_recall": 1.0,
        "next_step_recall": 1.0,
        "who_involved_recall": 1.0,
        "unsupported": 0,
        "correct": 0,
        "claims": 0,
        "slot_tp": {},
        "slot_gold": {},
        "days": {},
    }
    return grouping, summary


def gate_metrics(
    metrics: dict[str, float], thresholds: dict[str, Threshold] | None = None
) -> dict[str, Any]:
    """Compare named metrics to §11.2 thresholds. Keys are never swapped.

    Parked quality rows are measured and listed under ``report_only_failures``.
    Only structural hard 100% metrics fail ``passed``. An empty aggregate is a
    failure (undefined → fail).
    """
    rules = thresholds if thresholds is not None else load_thresholds()
    failures: list[str] = []
    hard_failures: list[str] = []
    report_only_failures: list[str] = []
    if not metrics:
        names = list(rules)
        hard = [name for name in names if name in HARD_HUNDRED]
        return {
            "passed": False,
            "failures": ["empty_aggregate", *hard],
            "hard_failures": hard,
            "report_only_failures": [name for name in names if name in PARKED_QUALITY],
        }
    for name, rule in rules.items():
        parked = name in PARKED_QUALITY
        if name not in metrics:
            if parked:
                report_only_failures.append(name)
            else:
                failures.append(name)
                if name in HARD_HUNDRED:
                    hard_failures.append(name)
            continue
        value = float(metrics[name])
        if rule.allows(value):
            continue
        if parked:
            report_only_failures.append(name)
            continue
        failures.append(name)
        if name in HARD_HUNDRED:
            hard_failures.append(name)
    return {
        "passed": not failures,
        "failures": failures,
        "hard_failures": hard_failures,
        "report_only_failures": report_only_failures,
    }


def _opt_float(spec: dict[str, Any], key: str) -> float | None:
    if key not in spec:
        return None
    return float(spec[key])
