from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from watson_fixturegen.secrets import scan_texts
from watson_fixturegen.sidecar import validate_sidecar

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_acme_q4"


def _sidecars() -> list[Path]:
    return sorted((FIXTURE / "frames").glob("*/*.json"))


def test_committed_sidecars_match_helper_schema() -> None:
    paths = _sidecars()
    assert 120 <= len(paths) <= 200
    previous_id = ""
    previous_at: datetime | None = None
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_sidecar(payload)
        assert payload["capture_mode"] == "fixture"
        assert payload["frame_id"] == path.stem
        assert path.with_suffix(".png").is_file()
        assert payload["frame_id"] > previous_id
        captured = datetime.fromisoformat(payload["captured_at"])
        if previous_at is not None:
            assert captured > previous_at
        previous_id = payload["frame_id"]
        previous_at = captured


def test_truth_evidence_and_segments_exist() -> None:
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    present = {path.stem for path in _sidecars()}
    assert len(truth["decisions"]) >= 2
    assert len(truth["commitments"]) >= 2
    assert truth["completed"]
    for decision in truth["decisions"]:
        assert decision["evidence_frames"]
        assert set(decision["evidence_frames"]) <= present
    for segment in truth["expected_segments"]:
        ids = segment["frame_ids"]
        assert ids
        assert segment["start_frame"] == ids[0]
        assert segment["end_frame"] == ids[-1]
        assert set(ids) <= present


def test_sample50_ids_exist() -> None:
    lines = [
        line.strip()
        for line in (FIXTURE / "sample50.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 50
    present = {path.stem for path in _sidecars()}
    assert set(lines) <= present


def test_fixture_has_no_real_looking_secrets() -> None:
    texts: list[str] = []
    texts.append((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    for path in _sidecars():
        payload = json.loads(path.read_text(encoding="utf-8"))
        texts.append(payload["window_title"])
        texts.append(payload.get("url") or "")
        for line in payload["ocr"]["lines"]:
            texts.append(line["text"])
    hits = scan_texts(texts)
    assert hits == []
