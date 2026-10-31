from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from watson_fixturegen.cadence import frame_id_for
from watson_fixturegen.sidecar import make_sidecar, validate_sidecar

REPO = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO / "schemas" / "sidecar.schema.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _valid() -> dict[str, object]:
    return dict(
        make_sidecar(
            frame_id=frame_id_for("day_acme_q4", 42, 1),
            captured_at=datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
            display_id=1,
            bundle_id="com.lumen.mail",
            app_name="Lumen Mail",
            window_title="Inbox",
            window_id=1,
            is_private_browsing=False,
            url=None,
            ocr_lines=[{"text": "Hello Priya", "bbox": [8, 12, 88, 28], "confidence": 1.0}],
            phash="0123456789abcdef",
            width=1280,
            height=800,
            capture_mode="fixture",
        )
    )


def test_schema_file_is_draft_2020_12() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["url"]["type"] == "null"
    assert schema["properties"]["capture_mode"]["enum"] == ["frontmost", "selftest", "fixture"]
    assert schema["properties"]["frame_id"]["format"] == "uuid"
    assert schema["properties"]["phash"]["pattern"] == "^[0-9a-f]{16}$"


def test_valid_sidecar_matches_json_schema() -> None:
    payload = _valid()
    validate_sidecar(payload)
    _validator().validate(payload)


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"frame_id": "f0001"}, "frame_id"),
        ({"url": "https://intranet.lumen.example/onboarding"}, "url"),
        ({"capture_mode": "synthetic"}, "capture_mode"),
    ],
)
def test_schema_rejects_known_invalid_values(patch: dict[str, object], needle: str) -> None:
    payload = _valid()
    payload.update(patch)
    with pytest.raises(ValidationError) as exc:
        _validator().validate(payload)
    assert needle in exc.value.absolute_path
    with pytest.raises(ValueError):
        validate_sidecar(payload)
