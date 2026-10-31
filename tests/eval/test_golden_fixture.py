from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from watson_fixturegen.secrets import scan_texts
from watson_fixturegen.sidecar import validate_sidecar

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_acme_q4"
SCHEMA_PATH = REPO / "schemas" / "sidecar.schema.json"


def _sidecars() -> list[Path]:
    return sorted((FIXTURE / "frames").glob("*/*.json"))


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_committed_sidecars_match_helper_schema() -> None:
    paths = _sidecars()
    assert 120 <= len(paths) <= 200
    validator = _schema_validator()
    seen: set[str] = set()
    stamps: list[datetime] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_sidecar(payload)
        validator.validate(payload)
        assert payload["capture_mode"] == "fixture"
        assert payload["frame_id"] == path.stem
        assert payload["url"] is None
        assert uuid.UUID(payload["frame_id"]).version == 5
        assert payload["captured_at"].endswith("Z")
        assert payload["display_id"] is None or isinstance(payload["display_id"], int)
        for line in payload["ocr"]["lines"]:
            x_min, y_min, x_max, y_max = line["bbox"]
            assert x_max >= x_min and y_max >= y_min
        assert path.with_suffix(".png").is_file()
        assert payload["frame_id"] not in seen
        seen.add(payload["frame_id"])
        stamps.append(datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00")))
    assert len(set(stamps)) == len(stamps)


def test_truth_evidence_and_segments_exist() -> None:
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    present = {path.stem for path in _sidecars()}
    assert len(truth["decisions"]) >= 2
    assert len(truth["commitments"]) >= 2
    assert truth["completed"]
    for decision in truth["decisions"]:
        assert decision["evidence_frames"]
        assert set(decision["evidence_frames"]) <= present
        for frame_id in decision["evidence_frames"]:
            uuid.UUID(frame_id)
    for segment in truth["expected_segments"]:
        ids = segment["frame_ids"]
        assert ids
        assert segment["start_frame"] == ids[0]
        assert segment["end_frame"] == ids[-1]
        assert set(ids) <= present
        start_index, end_index = segment["fixture_index"]
        assert isinstance(start_index, int) and isinstance(end_index, int)
        assert start_index <= end_index


def test_sample50_ids_exist() -> None:
    lines = [
        line.strip()
        for line in (FIXTURE / "sample50.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 50
    present = {path.stem for path in _sidecars()}
    assert set(lines) <= present
    for frame_id in lines:
        uuid.UUID(frame_id)


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
