from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.render import render_scenario

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_acme_q4"
SCENARIO = scenarios_dir() / "day_acme_q4.yaml"


@pytest.mark.fixture_regen
def test_regen_sidecars_and_truth_match_committed(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    out = tmp_path / "day_acme_q4"
    render_scenario(SCENARIO, out)
    committed_truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    generated_truth = yaml.safe_load((out / "truth.yaml").read_text(encoding="utf-8"))
    assert generated_truth == committed_truth
    generated = sorted((out / "frames").glob("*/*.json"))
    committed = sorted((FIXTURE / "frames").glob("*/*.json"))
    assert [path.name for path in generated] == [path.name for path in committed]
    for left, right in zip(generated, committed, strict=True):
        assert json.loads(left.read_text(encoding="utf-8")) == json.loads(
            right.read_text(encoding="utf-8")
        )
