"""Named-mutation score for deal grouping and per-deal summaries.

Target ≥ 15 of 18 mutants. No-ops and collection errors count as SURVIVED.
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
TARGET_MUTANTS = 18
KILL_FLOOR = 15
TEST_PATHS = (
    "tests/unit/test_deal_grouping.py",
    "tests/unit/test_daily_summary.py",
)
_COLLECTED = re.compile(r"(\d+) tests? collected")
_RAN = re.compile(r"(\d+) (passed|failed)")

CONSOLIDATION = "src/watson/pipeline/consolidation.py"
SCORE = "src/watson/intelligence/eval/score.py"

MUTANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "none-kept-grouping",
        CONSOLIDATION,
        (
            "        if _is_none_deal_title(cluster.title):\n"
            "            continue\n"
            "        chosen = [\n"
            "            item\n"
            "            for item in _pick_indexes(cluster.observation_indexes, obs_ids)\n"
            "            if item not in assigned\n"
            "        ]\n"
        ),
        (
            "        if False and _is_none_deal_title(cluster.title):\n"
            "            continue\n"
            "        chosen = [\n"
            "            item\n"
            "            for item in _pick_indexes(cluster.observation_indexes, obs_ids)\n"
            "            if item not in assigned\n"
            "        ]\n"
        ),
    ),
    (
        "cite-claims-all",
        CONSOLIDATION,
        "        cite_claims = grouped\n        verified = verify_narrative(\n",
        "        cite_claims = claims\n        verified = verify_narrative(\n",
    ),
    (
        "grouped-all-claims",
        CONSOLIDATION,
        (
            "        grouped = [row for row in claims"
            ' if str(row.get("observation_id") or "") in chosen_set]\n'
        ),
        "        grouped = list(claims)\n",
    ),
    (
        "summarize-without-deals",
        CONSOLIDATION,
        "        if deal_groups and cite_claims\n",
        "        if claims\n",
    ),
    (
        "slot-drafts-raw",
        CONSOLIDATION,
        '            slot_drafts.append((key, slot, list(verified.claim_ids), "model"))\n',
        '            slot_drafts.append((key, slot, list(para.claim_ids), "model"))\n',
    ),
    (
        "unmatched-kept",
        CONSOLIDATION,
        "        if len(owners) != 1:\n            continue\n",
        "        if False and len(owners) != 1:\n            continue\n",
    ),
    (
        "slots-no-deal-id",
        CONSOLIDATION,
        ('                "deal_id": deal_id,\n                "source": source,\n'),
        '                "source": source,\n',
    ),
    (
        "slots-raw-tokens",
        CONSOLIDATION,
        "        if any(not cid or cid.isdigit() for cid in claim_ids):\n            continue\n",
        (
            "        if False and any(not cid or cid.isdigit() for cid in claim_ids):\n"
            "            continue\n"
        ),
    ),
    (
        "score-slots-unfiltered",
        SCORE,
        (
            "            if any(not cid or cid.isdigit() for cid in resolved):\n"
            "                continue\n"
        ),
        (
            "            if False and any(not cid or cid.isdigit()"
            " for cid in resolved):\n                continue\n"
        ),
    ),
    (
        "score-slots-no-deal",
        SCORE,
        (
            '            if not deal_id or slot_name not in {"what_happened",'
            ' "who_involved", "next_step"}:\n'
            "                continue\n"
        ),
        (
            '            if False and (not deal_id or slot_name not in {"what_happened",'
            ' "who_involved", "next_step"}):\n'
            "                continue\n"
        ),
    ),
    (
        "heading-model-text",
        CONSOLIDATION,
        '        parts.append(f"## {deal[\'title\']}\\n\\n" + "\\n\\n".join(paras))\n',
        '        parts.append("## Model heading\\n\\n" + "\\n\\n".join(paras))\n',
    ),
    (
        "leftover-appended",
        CONSOLIDATION,
        "        if len(owners) != 1:\n            continue\n        key = owners[0]\n",
        (
            "        if len(owners) != 1:\n"
            "            key = next(iter(buckets), None)\n"
            "            if key is None:\n"
            "                continue\n"
            "        else:\n"
            "            key = owners[0]\n"
        ),
    ),
    (
        "none-kept-insert",
        CONSOLIDATION,
        (
            "        if _is_none_deal_title(cluster.title):\n"
            "            continue\n"
            "        chosen = [\n"
            "            item\n"
            "            for item in _pick_indexes(cluster.observation_indexes, obs_ids)\n"
            "            if item not in assigned_obs\n"
            "        ]\n"
        ),
        (
            "        if False and _is_none_deal_title(cluster.title):\n"
            "            continue\n"
            "        chosen = [\n"
            "            item\n"
            "            for item in _pick_indexes(cluster.observation_indexes, obs_ids)\n"
            "            if item not in assigned_obs\n"
            "        ]\n"
        ),
    ),
    (
        "fallback-all-groups",
        CONSOLIDATION,
        "        fallback = _cited_fallback(cite_claims)\n",
        (
            "        fallback = _cited_fallback("
            "[row for _key, _title, rows in deal_groups for row in rows])\n"
        ),
    ),
    (
        "title-as-key",
        CONSOLIDATION,
        (
            "def _deal_group_key(index: int, title: str) -> int | str:\n"
            '    """Identity for a deal cluster. Title is not unique across look-alike deals."""\n'
            "    return index\n"
        ),
        (
            "def _deal_group_key(index: int, title: str) -> int | str:\n"
            '    """Identity for a deal cluster. Title is not unique across look-alike deals."""\n'
            "    return title\n"
        ),
    ),
    (
        "groups-len-key",
        CONSOLIDATION,
        (
            "            groups.append((_deal_group_key(index, cluster.title),"
            " cluster.title, grouped))\n"
        ),
        (
            "            groups.append((_deal_group_key(len(groups), cluster.title),"
            " cluster.title, grouped))\n"
        ),
    ),
    (
        "drop-append-markers",
        CONSOLIDATION,
        (
            '            expanded["text"] = append_missing_citations('
            'str(expanded["text"]), mapped, grouped_ids)\n'
        ),
        "            pass\n",
    ),
    (
        "inject-claims-text",
        CONSOLIDATION,
        (
            '            expanded["text"] = append_missing_citations('
            'str(expanded["text"]), mapped, grouped_ids)\n'
        ),
        (
            '            expanded["text"] = " ".join(\n'
            "                f\"{str(row.get('text') or '').rstrip('.')} "
            "[c:{row['id']}].\" for row in grouped\n"
            "            )\n"
        ),
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
def test_deal_grouping_kills_at_least_15_of_18(tmp_path: Path) -> None:
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

    print(f"deal grouping mutants killed {killed}/{TARGET_MUTANTS}; survived={survived}")
    assert killed >= KILL_FLOOR, (
        f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}. survived: {', '.join(survived)}"
    )
