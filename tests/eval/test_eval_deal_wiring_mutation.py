"""Named-mutation score for watson eval deal-score wiring.

No-ops and collection errors count as SURVIVED.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TARGET_MUTANTS = 15
KILL_FLOOR = 15
TEST_PATHS = (
    "tests/unit/test_eval_deal_gate.py",
    "tests/unit/test_cli.py::test_eval_cli_record_without_strict_exits_1_on_deal_miss",
    "tests/unit/test_eval_abort.py::test_run_eval_min_spec_fails_deal_accuracy_below_gate",
    "tests/unit/test_eval_abort.py::test_no_gold_run_fails_closed_empty_deal_blocks",
    "tests/eval/test_eval_deal_scores.py::test_gold_for_eval_run_pins_mode_sources",
    "tests/eval/test_eval_deal_scores.py::test_pipeline_output_reaches_scorer_as_predicted",
)
_COLLECTED = re.compile(r"(\d+) tests? collected")
_RAN = re.compile(r"(\d+) (passed|failed)")

CLI = "src/watson/cli.py"
DEALS = "src/watson/intelligence/eval/deals.py"
RUN = "src/watson/intelligence/eval/run.py"
THRESHOLDS = "src/watson/intelligence/eval/thresholds.py"

MUTANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "gold-ignore-mode",
        DEALS,
        "    if mode == \"record\":\n",
        "    if False:\n",
    ),
    (
        "gold-always-record",
        DEALS,
        "    if mode == \"record\":\n",
        "    if True:\n",
    ),
    (
        "drop-last-pipeline-gold",
        DEALS,
        "    pipeline_names = [name for name in V1_PIPELINE_DAYS if name in wanted]\n",
        "    pipeline_names = [name for name in V1_PIPELINE_DAYS[:-1] if name in wanted]\n",
    ),
    (
        "drop-last-deal-gold",
        DEALS,
        "        deal_names = [name for name in V1_DEAL_DAYS if name in wanted]\n",
        "        deal_names = [name for name in V1_DEAL_DAYS[:-1] if name in wanted]\n",
    ),
    (
        "cli-record-not-strict",
        CLI,
        "    strict = bool(args.strict) or mode == \"record\"\n",
        "    strict = bool(args.strict)\n",
    ),
    (
        "drop-predicted",
        RUN,
        "            predicted=predicted_day_from_collect(fixture.name, predicted),\n",
        "            predicted=None,\n",
    ),
    (
        "drop-frames",
        DEALS,
        (
            "            frame_ids=tuple(str(frame_id) "
            "for frame_id in (row.get(\"frame_ids\") or ())),\n"
        ),
        "            frame_ids=(),\n",
    ),
    (
        "drop-evidence",
        DEALS,
        (
            "            evidence = [\n"
            "                PredictedEvidence(\n"
            "                    frame_id=(str(item[\"frame_id\"]) "
            "if item.get(\"frame_id\") else None),\n"
            "                    segment_id=(str(item[\"segment_id\"]) "
            "if item.get(\"segment_id\") else None),\n"
            "                )\n"
            "                for item in (info.get(\"evidence\") or [])\n"
            "                if isinstance(item, dict)\n"
            "            ]\n"
        ),
        "            evidence = []\n",
    ),
    (
        "passed-return-true",
        RUN,
        "        return self.pipeline_passed\n",
        "        return True\n",
    ),
    (
        "empty-blocks-pass",
        DEALS,
        (
            "    grouping = grouping_report("
            "GroupingScore(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0))\n"
            "    zeros = {slot: 0 for slot in SLOTS}\n"
            "    summary = summary_report("
            "SummaryScore(0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0, zeros, zeros))\n"
        ),
        (
            "    grouping = grouping_report("
            "GroupingScore(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0, 0, 0, 0, 0, 0, 0))\n"
            "    zeros = {slot: 0 for slot in SLOTS}\n"
            "    summary = summary_report("
            "SummaryScore(1.0, 1.0, 1.0, 1.0, 0, 1.0, 0, 0, zeros, zeros))\n"
        ),
    ),
    (
        "privacy-ignored",
        THRESHOLDS,
        "    if privacy_fail:\n        return False\n",
        "    if False and privacy_fail:\n        return False\n",
    ),
    (
        "missing-blocks-pass",
        THRESHOLDS,
        "    if not grouping or not summary:\n        return False\n",
        "    if not grouping or not summary:\n        return True\n",
    ),
    (
        "malformed-blocks-pass",
        THRESHOLDS,
        "    except (KeyError, TypeError, ValueError):\n        return False\n",
        "    except (KeyError, TypeError, ValueError):\n        return True\n",
    ),
    (
        "run-eval-drop-mode",
        RUN,
        "            [day.name for day in predicted_days], fixtures_root, mode=mode\n",
        "            [day.name for day in predicted_days], fixtures_root\n",
    ),
    (
        "skip-deal-score",
        RUN,
        "        if gold.days:\n",
        "        if False and gold.days:\n",
    ),
)


def _apply(src_root: Path, relative: str, old: str, new: str) -> bool:
    path = src_root / relative.removeprefix("src/")
    original = path.read_text(encoding="utf-8")
    if old not in original:
        return False
    updated = original.replace(old, new, 1)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _run_pytest(src_root: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("WATSON_MUTATION", None)
    env["PYTHONPATH"] = os.pathsep.join((str(src_root), str(REPO)))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "-o",
            f"pythonpath={src_root} {REPO}",
            *TEST_PATHS,
            *(extra or []),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _collected_count(proc: subprocess.CompletedProcess[str]) -> int:
    blob = f"{proc.stdout}\n{proc.stderr}"
    match = _COLLECTED.search(blob)
    if match:
        return int(match.group(1))
    return 0


def _classify(src_root: Path, *, changed: bool) -> str:
    run = _run_pytest(src_root)
    blob = f"{run.stdout}\n{run.stderr}"
    ran = bool(_RAN.search(blob))
    if (not changed) or "ERROR collecting" in blob or run.returncode in {2, 3, 4, 5} or not ran:
        return "SURVIVED"
    if run.returncode == 1:
        return "KILLED"
    return "SURVIVED"


@pytest.mark.timeout(300)
def test_eval_deal_wiring_kills_named_mutants(tmp_path: Path) -> None:
    assert len(MUTANTS) == TARGET_MUTANTS
    names = [row[0] for row in MUTANTS]
    assert len(names) == len(set(names))
    src_root = tmp_path / "src"
    shutil.copytree(REPO / "src" / "watson", src_root / "watson")
    originals = {
        relative: (src_root / relative.removeprefix("src/")).read_text(encoding="utf-8")
        for _, relative, _, _ in MUTANTS
    }

    collect = _run_pytest(src_root, ["--collect-only"])
    n_collect = _collected_count(collect)
    if collect.returncode != 0 or n_collect == 0:
        pytest.fail(
            "mutation harness could not collect tests "
            f"(exit {collect.returncode}, collected {n_collect}):\n"
            f"{collect.stdout}\n{collect.stderr}"
        )

    noop_run = _run_pytest(src_root)
    noop_blob = f"{noop_run.stdout}\n{noop_run.stderr}"
    if noop_run.returncode != 0 or not _RAN.search(noop_blob):
        pytest.fail(
            "unmutated copy must pass pytest "
            f"(exit {noop_run.returncode}):\n{noop_run.stdout}\n{noop_run.stderr}"
        )

    killed = 0
    survived: list[str] = []
    for name, relative, old, new in MUTANTS:
        path = src_root / relative.removeprefix("src/")
        path.write_text(originals[relative], encoding="utf-8")
        changed = _apply(src_root, relative, old, new)
        if _classify(src_root, changed=changed) == "KILLED":
            killed += 1
        else:
            survived.append(name if changed else f"{name} (noop)")
        path.write_text(originals[relative], encoding="utf-8")

    print(f"eval deal wiring mutants killed {killed}/{TARGET_MUTANTS}; survived={survived}")
    assert killed >= KILL_FLOOR, (
        f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}. survived: {', '.join(survived)}"
    )
