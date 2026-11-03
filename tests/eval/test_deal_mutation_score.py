"""Operator-mutation score for the deal scorer. Target ≥ 80% of 55 mutants."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEALS = REPO / "src" / "watson" / "intelligence" / "eval" / "deals.py"
TARGET_MUTANTS = 55
KILL_FLOOR = 44  # 80% of 55
TEST_PATHS = (
    "tests/eval/test_deal_scorer_e2e.py",
    "tests/unit/test_deal_metrics.py",
)

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
        "asserts_relation",
        "is_bare_term_list",
        "required_terms_present",
        "slot_modal_ok",
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


def _apply_mutant(index: int) -> str:
    tree = ast.parse(DEALS.read_text(encoding="utf-8"), filename=str(DEALS))
    mutator = _Mutator(target=index)

    class _Scoped(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            if node.name in _SCORED_FUNCS:
                return mutator.visit(node)
            return node

    mutated = _Scoped().visit(tree)
    ast.fix_missing_locations(mutated)
    return ast.unparse(mutated)


def _mutant_killed(index: int, src_root: Path) -> bool:
    deals = src_root / "watson" / "intelligence" / "eval" / "deals.py"
    deals.write_text(_apply_mutant(index), encoding="utf-8")
    env = os.environ.copy()
    env.pop("WATSON_MUTATION", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=no",
            "-o",
            f"pythonpath={src_root}",
            *TEST_PATHS,
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode != 0


def test_deal_scorer_kills_at_least_80_percent_of_55(tmp_path: Path) -> None:
    sites = _count_sites()
    assert sites >= TARGET_MUTANTS, f"expected ≥ {TARGET_MUTANTS} operator sites, found {sites}"
    src_root = tmp_path / "src"
    shutil.copytree(REPO / "src" / "watson", src_root / "watson")
    killed = 0
    for index in range(1, TARGET_MUTANTS + 1):
        if _mutant_killed(index, src_root):
            killed += 1
    assert killed >= KILL_FLOOR, f"killed {killed}/{TARGET_MUTANTS}; need ≥ {KILL_FLOOR}"
    print(f"deal scorer mutants killed {killed}/{TARGET_MUTANTS}")
