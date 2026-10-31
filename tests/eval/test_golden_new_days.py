"""Schema, truth, and trap checks for the three M2b V1 day fixtures."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from watson.config.schema import DEFAULT_EXCLUDED_BUNDLE_IDS
from watson_fixturegen.secrets import scan_texts
from watson_fixturegen.sidecar import validate_sidecar

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "schemas" / "sidecar.schema.json"
MEETING = REPO / "tests" / "fixtures" / "day_meeting_heavy"
TRAPS = REPO / "tests" / "fixtures" / "day_sensitive_traps"
EMPTY = REPO / "tests" / "fixtures" / "day_near_empty"
VAULT_BUNDLE = "com.apple.Passwords"
PLANTED = (
    "4111111111111111",
    "4111 1111 1111 1111",
    "482913",
    "cedar-trap-pass-9f3",
    "sk-abcdefghijklmnopqrstuvwxyz123456",
)


def _sidecars(fixture: Path) -> list[Path]:
    return sorted((fixture / "frames").glob("*/*.json"))


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _assert_sidecars(fixture: Path, *, minimum: int, maximum: int) -> list[dict[str, object]]:
    paths = _sidecars(fixture)
    assert minimum <= len(paths) <= maximum
    validator = _validator()
    payloads: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_sidecar(payload)
        validator.validate(payload)
        assert payload["capture_mode"] == "fixture"
        assert uuid.UUID(payload["frame_id"]).version == 5
        assert path.with_suffix(".png").is_file()
        assert payload["frame_id"] not in seen
        seen.add(str(payload["frame_id"]))
        payloads.append(payload)
    return payloads


def test_meeting_heavy_sidecars_and_truth() -> None:
    payloads = _assert_sidecars(MEETING, minimum=60, maximum=140)
    assert any(item["bundle_id"] == "com.lumen.room" for item in payloads)
    truth = yaml.safe_load((MEETING / "truth.yaml").read_text(encoding="utf-8"))
    present = {item["frame_id"] for item in payloads}
    assert len(truth["expected_segments"]) >= 8
    for segment in truth["expected_segments"]:
        assert set(segment["frame_ids"]) <= present
    texts = [str(item["window_title"]) for item in payloads]
    for item in payloads:
        for line in item["ocr"]["lines"]:  # type: ignore[index]
            texts.append(str(line["text"]))
    assert scan_texts(texts) == []


def test_near_empty_has_few_frames_and_two_segments() -> None:
    payloads = _assert_sidecars(EMPTY, minimum=6, maximum=20)
    truth = yaml.safe_load((EMPTY / "truth.yaml").read_text(encoding="utf-8"))
    assert len(truth["expected_segments"]) == 2
    assert truth["meta"]["frame_count"] == len(payloads)
    texts = [str(item["window_title"]) for item in payloads]
    for item in payloads:
        for line in item["ocr"]["lines"]:  # type: ignore[index]
            texts.append(str(line["text"]))
    assert scan_texts(texts) == []


def test_sensitive_traps_plant_secrets_and_skip_excluded() -> None:
    payloads = _assert_sidecars(TRAPS, minimum=30, maximum=90)
    truth = yaml.safe_load((TRAPS / "truth.yaml").read_text(encoding="utf-8"))
    skipped_ids = {row["frame_id"] for row in truth["skipped"]}
    assert skipped_ids
    skipped = [item for item in payloads if item["frame_id"] in skipped_ids]
    assert skipped
    bundles = {str(item["bundle_id"]) for item in skipped}
    assert VAULT_BUNDLE in bundles
    assert VAULT_BUNDLE in DEFAULT_EXCLUDED_BUNDLE_IDS
    assert any("Sign in" in str(item["window_title"]) for item in skipped)
    assert all(segment["bundle_id"] != VAULT_BUNDLE for segment in truth["expected_segments"])
    work = [item for item in payloads if item["frame_id"] not in skipped_ids]
    blob = "\n".join(
        str(line["text"])
        for item in work
        for line in item["ocr"]["lines"]  # type: ignore[index]
    )
    assert PLANTED[0] in blob
    assert PLANTED[2] in blob
    assert PLANTED[3] in blob
    assert PLANTED[4] in blob
