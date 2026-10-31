"""Load and validate capture sidecars against ``schemas/sidecar.schema.json``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMA_FILENAME = "sidecar.schema.json"


class SidecarError(ValueError):
    """Sidecar JSON is missing, malformed, or fails the helper schema."""


def sidecar_schema_path() -> Path:
    """Resolve the committed schema, then the packaged copy."""
    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "schemas" / SCHEMA_FILENAME,  # repo checkout
        here.parents[1] / "data" / SCHEMA_FILENAME,  # wheel force-include
        Path.cwd() / "schemas" / SCHEMA_FILENAME,
    )
    for path in candidates:
        if path.is_file():
            return path
    raise SidecarError(
        "sidecar schema not found (expected schemas/sidecar.schema.json)"
    )


@lru_cache(maxsize=1)
def sidecar_validator() -> Draft202012Validator:
    schema = json.loads(sidecar_schema_path().read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def validate_sidecar_payload(payload: dict[str, Any]) -> None:
    """Raise SidecarError naming the field path, never the instance value."""
    try:
        sidecar_validator().validate(payload)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise SidecarError(f"invalid sidecar field {path}") from exc


def load_sidecar(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SidecarError(f"{path.name}: not valid JSON") from exc
    if not isinstance(raw, dict):
        raise SidecarError(f"{path.name}: sidecar must be a JSON object")
    validate_sidecar_payload(raw)
    return raw
