from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from watson_fixturegen.paths import scenarios_dir
from watson_fixturegen.phash import hamming_distance
from watson_fixturegen.render import render_scenario

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_acme_q4"
SCENARIO = scenarios_dir() / "day_acme_q4.yaml"
BBOX_TOLERANCE_PX = 2
PHASH_HAMMING_MAX = 4
TEXT_FIELDS = (
    "frame_id",
    "captured_at",
    "display_id",
    "bundle_id",
    "app_name",
    "window_title",
    "window_id",
    "is_private_browsing",
    "url",
    "width",
    "height",
    "capture_mode",
)


def _assert_sidecar_close(generated: dict[str, Any], committed: dict[str, Any]) -> None:
    for key in TEXT_FIELDS:
        assert generated[key] == committed[key], f"{key} mismatch"
    assert len(generated["ocr"]["lines"]) == len(committed["ocr"]["lines"])
    for left, right in zip(generated["ocr"]["lines"], committed["ocr"]["lines"], strict=True):
        assert left["text"] == right["text"]
        assert left["confidence"] == right["confidence"]
        assert len(left["bbox"]) == 4
        for a, b in zip(left["bbox"], right["bbox"], strict=True):
            assert abs(a - b) <= BBOX_TOLERANCE_PX, f"bbox {left['bbox']} vs {right['bbox']}"
    assert hamming_distance(generated["phash"], committed["phash"]) <= PHASH_HAMMING_MAX


@pytest.mark.fixture_regen
def test_regen_sidecars_and_truth_match_committed(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    out = tmp_path / "day_acme_q4"
    render_scenario(SCENARIO, out)
    committed_truth = (FIXTURE / "truth.yaml").read_text(encoding="utf-8")
    generated_truth = (out / "truth.yaml").read_text(encoding="utf-8")
    assert generated_truth == committed_truth
    assert yaml.safe_load(generated_truth) == yaml.safe_load(committed_truth)
    generated_sample = (out / "sample50.txt").read_text(encoding="utf-8")
    committed_sample = (FIXTURE / "sample50.txt").read_text(encoding="utf-8")
    assert generated_sample == committed_sample
    generated = sorted((out / "frames").glob("*/*.json"))
    committed = sorted((FIXTURE / "frames").glob("*/*.json"))
    assert [path.name for path in generated] == [path.name for path in committed]
    for left, right in zip(generated, committed, strict=True):
        _assert_sidecar_close(
            json.loads(left.read_text(encoding="utf-8")),
            json.loads(right.read_text(encoding="utf-8")),
        )
