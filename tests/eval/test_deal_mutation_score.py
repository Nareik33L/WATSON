"""Operator-mutation score for the deal scorer. Target ≥ 80% of 55 mutants."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEALS = REPO / "src" / "watson" / "intelligence" / "eval" / "deals.py"
DEAL_GOLD = REPO / "tests" / "fixtures" / "deal_gold"
TARGET_MUTANTS = 55
KILL_FLOOR = 44  # 80% of 55
TEST_PATHS = (
    "tests/eval/test_deal_scorer_e2e.py",
    "tests/unit/test_deal_metrics.py",
    "tests/eval/test_fixture_validation.py",
)
_COLLECTED = re.compile(r"(\d+) tests? collected")
_RAN = re.compile(r"(\d+) (passed|failed)")

_BINOPS: dict[type[ast.AST], type[ast.AST]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
}
_SCORED_FUNCS = frozenset(
    {
        "score_deals",
        "score_grouping",
        "score_summary",
        "align_segments",
        "match_clusters",
        "contradicts",
        "required_terms_present",
        "slot_modal_ok",
        "is_multi_fact_claim",
        "matching_slot_facts",
        "fact_terms_satisfied",
        "_claim_covers",
        "_unsupported",
        "_assignment_correct",
        "_pair_claims",
        "privacy_leak",
        "extract_dates",
        "extract_amounts",
        "person_entities_match",
        "resolve_person_id",
        "honest_has_roster_name_and_deadline",
    }
)


class _Mutator(ast.NodeTransformer):
    def __init__(self, target: int) -> None:
        self.target = target
        self.seen = 0
        self.applied = False

    def _take(self) -> bool:
        self.seen += 1
        if self.seen == self.target and not self.applied:
            self.applied = True
            return True
        return False

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if node.ops and type(node.ops[0]) in _BINOPS and self._take():
            node.ops[0] = _BINOPS[type(node.ops[0])]()
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        if self._take():
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._take():
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if node.value is True and self._take():
            return ast.copy_location(ast.Constant(value=False), node)
        if node.value is False and self._take():
            return ast.copy_location(ast.Constant(value=True), node)
        if node.value == 0 and self._take():
            return ast.copy_location(ast.Constant(value=1), node)
        if node.value == 1 and self._take():
            return ast.copy_location(ast.Constant(value=0), node)
        return node


def _count_sites() -> int:
    tree = ast.parse(DEALS.read_text(encoding="utf-8"), filename=str(DEALS))
    counter = _Mutator(target=10**9)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _SCORED_FUNCS:
            counter.visit(node)
    return counter.seen


def _apply_mutant(index: int) -> tuple[str, bool]:
    original = DEALS.read_text(encoding="utf-8")
    tree = ast.parse(original, filename=str(DEALS))
    mutator = _Mutator(target=index)

    class _Scoped(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            if node.name in _SCORED_FUNCS:
                return mutator.visit(node)
            return node

    mutated = _Scoped().visit(tree)
    ast.fix_missing_locations(mutated)
    source = ast.unparse(mutated)
    return source, mutator.applied and source != ast.unparse(ast.parse(original))


def _sample_indices(site_count: int, n: int) -> list[int]:
    if site_count < n:
        raise AssertionError(f"expected ≥ {n} operator sites, found {site_count}")
    indices: list[int] = []
    for i in range(n):
        idx = 1 + (i * site_count) // n
        if indices and idx <= indices[-1]:
            idx = indices[-1] + 1
        if idx > site_count:
            idx = site_count
        indices.append(idx)
    return indices


def _run_pytest(src_root: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("WATSON_MUTATION", None)
    env["WATSON_DEAL_GOLD"] = str(DEAL_GOLD)
    env["PYTHONPATH"] = str(src_root)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "-o",
            f"pythonpath={src_root}",
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


def _classify(src_root: Path, *, original: str | None = None) -> str:
    """Return KILLED or SURVIVED. Collection errors and no-ops survive.

    Always runs pytest. A no-op (mutant source equals the original) is SURVIVED
    even if the suite is red; a collection error is SURVIVED, not killed.
    """
    deals = src_root / "watson" / "intelligence" / "eval" / "deals.py"
    is_noop = original is not None and deals.read_text(encoding="utf-8") == original
    run = _run_pytest(src_root)
    blob = f"{run.stdout}\n{run.stderr}"
    ran = bool(_RAN.search(blob))
    if is_noop or "ERROR collecting" in blob or run.returncode in {2, 3, 4, 5} or not ran:
        return "SURVIVED"
    if run.returncode == 1:
        return "KILLED"
    return "SURVIVED"


@pytest.mark.timeout(300)
def test_deal_scorer_kills_at_least_80_percent_of_55(tmp_path: Path) -> None:
    assert DEAL_GOLD.is_dir(), f"deal gold fixtures missing at {DEAL_GOLD}"
    sites = _count_sites()
    indices = _sample_indices(sites, TARGET_MUTANTS)
    src_root = tmp_path / "src"
    shutil.copytree(REPO / "src" / "watson", src_root / "watson")
    original = (src_root / "watson" / "intelligence" / "eval" / "deals.py").read_text(
        encoding="utf-8"
    )

    collect = _run_pytest(src_root, ["--collect-only"])
    n_collect = _collected_count(collect)
    if collect.returncode != 0 or n_collect == 0:
        pytest.fail(
            "mutation harness could not collect tests against the fixture-reachable "
            f"copy (exit {collect.returncode}, collected {n_collect}):\n"
            f"{collect.stdout}\n{collect.stderr}"
        )

    noop_run = _run_pytest(src_root)
    noop_blob = f"{noop_run.stdout}\n{noop_run.stderr}"
    if noop_run.returncode != 0 or not _RAN.search(noop_blob):
        pytest.fail(
            "unmutated copy must pass pytest against the fixture-reachable copy "
            f"(exit {noop_run.returncode}):\n{noop_run.stdout}\n{noop_run.stderr}"
        )

    killed = 0
    for index in indices:
        mutant, changed = _apply_mutant(index)
        deals = src_root / "watson" / "intelligence" / "eval" / "deals.py"
        deals.write_text(mutant if changed else original, encoding="utf-8")
        if _classify(src_root, original=original) == "KILLED":
            killed += 1
        deals.write_text(original, encoding="utf-8")

    assert killed >= KILL_FLOOR, f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}"
    print(
        f"deal scorer mutants killed {killed}/{TARGET_MUTANTS} (sampled {len(indices)} of {sites})"
    )
