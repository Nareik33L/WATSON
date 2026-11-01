"""§11.2 metric thresholds in one config (plan §11.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")

HARD_ZERO = frozenset({"unsupported_achievements"})
HARD_HUNDRED = frozenset({"claims_with_evidence", "citations_resolvable"})


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


def load_thresholds(path: Path | None = None) -> dict[str, Threshold]:
    raw = json.loads((path or THRESHOLDS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("thresholds config must be a JSON object")
    out: dict[str, Threshold] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"threshold {name!r} must be an object")
        out[str(name)] = Threshold(
            minimum=_opt_float(spec, "min"),
            maximum=_opt_float(spec, "max"),
            maximum_exclusive=_opt_float(spec, "max_exclusive"),
        )
    return out


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
