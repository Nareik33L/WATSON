from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS
from watson_fixturegen.secrets import scan_texts
from watson_fixturegen.sidecar import validate_sidecar

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "day_fragmented_multi"
SCHEMA_PATH = REPO / "schemas" / "sidecar.schema.json"
VAULT_BUNDLE = "com.apple.Passwords"


def _sidecars() -> list[Path]:
    return sorted((FIXTURE / "frames").glob("*/*.json"))


def test_fragmented_sidecars_match_helper_schema() -> None:
    paths = _sidecars()
    assert 80 <= len(paths) <= 250
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    seen: set[str] = set()
    excluded = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_sidecar(payload)
        validator.validate(payload)
        assert payload["capture_mode"] == "fixture"
        assert uuid.UUID(payload["frame_id"]).version == 5
        assert payload["captured_at"].endswith("Z")
        assert path.with_suffix(".png").is_file()
        assert payload["frame_id"] not in seen
        seen.add(payload["frame_id"])
        datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
        if payload["bundle_id"] == VAULT_BUNDLE:
            excluded += 1
            assert payload["is_private_browsing"] is True
            assert payload["app_name"] == "Passwords"
            assert VAULT_BUNDLE in DEFAULT_EXCLUDED_BUNDLE_IDS
        else:
            assert payload["is_private_browsing"] is False
            assert payload["bundle_id"] != VAULT_BUNDLE
    assert excluded >= 1


def test_fragmented_truth_records_skip_and_three_projects() -> None:
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    present = {path.stem for path in _sidecars()}
    assert len(truth["projects"]) >= 3
    assert truth["skipped"]
    assert {row["bundle_id"] for row in truth["skipped"]} == {VAULT_BUNDLE}
    skipped_ids = {row["frame_id"] for row in truth["skipped"]}
    assert skipped_ids
    assert skipped_ids <= present
    for decision in truth["decisions"]:
        assert decision["evidence_frames"]
        assert set(decision["evidence_frames"]) <= present
        assert set(decision["evidence_frames"]).isdisjoint(skipped_ids)
    assert truth["meta"]["frame_count"] == len(present)
    for segment in truth["expected_segments"]:
        assert segment["bundle_id"] != VAULT_BUNDLE
        assert set(segment["frame_ids"]).isdisjoint(skipped_ids)


def test_fragmented_sample50_and_no_secrets() -> None:
    lines = [
        line.strip()
        for line in (FIXTURE / "sample50.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    present = {path.stem for path in _sidecars()}
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text(encoding="utf-8"))
    skipped_ids = {row["frame_id"] for row in truth["skipped"]}
    assert 1 <= len(lines) <= 50
    assert set(lines) <= present
    assert set(lines).isdisjoint(skipped_ids)
    texts = [
        *truth["projects"],
        *truth["people"],
        *truth["completed"],
        *truth["not_completed"],
        *truth["noise"],
        *(item["text"] for item in truth["decisions"]),
        *(item["text"] for item in truth["commitments"]),
    ]
    for path in _sidecars():
        payload = json.loads(path.read_text(encoding="utf-8"))
        texts.append(payload["window_title"])
        for line in payload["ocr"]["lines"]:
            texts.append(line["text"])
    assert scan_texts(texts) == []
