"""Helper sidecar schema: fields, validation, and canonical JSON."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, TypedDict

# Contract: helper/README.md "Sidecar schema" and plan §3.1.
SIDECAR_FIELD_ORDER: Final[tuple[str, ...]] = (
    "frame_id",
    "captured_at",
    "display_id",
    "bundle_id",
    "app_name",
    "window_title",
    "window_id",
    "is_private_browsing",
    "url",
    "ocr",
    "phash",
    "width",
    "height",
    "capture_mode",
)

CAPTURE_MODES: Final[frozenset[str]] = frozenset({"frontmost", "full_display", "fixture"})
PHASH_HEX_LEN: Final[int] = 16


class OcrLine(TypedDict):
    text: str
    bbox: list[int]
    confidence: float


class OcrBlock(TypedDict):
    lines: list[OcrLine]


class Sidecar(TypedDict):
    frame_id: str
    captured_at: str
    display_id: int
    bundle_id: str
    app_name: str
    window_title: str
    window_id: int
    is_private_browsing: bool
    url: str | None
    ocr: OcrBlock
    phash: str
    width: int
    height: int
    capture_mode: str


def format_captured_at(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    return moment.isoformat(timespec="seconds")


def make_sidecar(
    *,
    frame_id: str,
    captured_at: datetime,
    display_id: int,
    bundle_id: str,
    app_name: str,
    window_title: str,
    window_id: int,
    is_private_browsing: bool,
    url: str | None,
    ocr_lines: list[OcrLine],
    phash: str,
    width: int,
    height: int,
    capture_mode: str = "fixture",
) -> Sidecar:
    payload: Sidecar = {
        "frame_id": frame_id,
        "captured_at": format_captured_at(captured_at),
        "display_id": display_id,
        "bundle_id": bundle_id,
        "app_name": app_name,
        "window_title": window_title,
        "window_id": window_id,
        "is_private_browsing": is_private_browsing,
        "url": url,
        "ocr": {"lines": ocr_lines},
        "phash": phash,
        "width": width,
        "height": height,
        "capture_mode": capture_mode,
    }
    validate_sidecar(payload)
    return payload


def dump_sidecar(sidecar: Mapping[str, Any]) -> str:
    ordered = {key: sidecar[key] for key in SIDECAR_FIELD_ORDER}
    return json.dumps(ordered, indent=2, ensure_ascii=True) + "\n"


def load_sidecar(text: str) -> dict[str, Any]:
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("sidecar must be a JSON object")
    validate_sidecar(loaded)
    return loaded


def validate_sidecar(payload: Mapping[str, Any]) -> None:
    missing = [key for key in SIDECAR_FIELD_ORDER if key not in payload]
    if missing:
        raise ValueError(f"sidecar missing fields: {', '.join(missing)}")
    extra = [key for key in payload if key not in SIDECAR_FIELD_ORDER]
    if extra:
        raise ValueError(f"sidecar has unknown fields: {', '.join(extra)}")

    _require_str(payload, "frame_id")
    _require_str(payload, "captured_at")
    datetime.fromisoformat(str(payload["captured_at"]))
    _require_int(payload, "display_id")
    _require_str(payload, "bundle_id")
    _require_str(payload, "app_name")
    _require_str(payload, "window_title")
    _require_int(payload, "window_id")
    if not isinstance(payload["is_private_browsing"], bool):
        raise ValueError("is_private_browsing must be a boolean")
    url = payload["url"]
    if url is not None and not isinstance(url, str):
        raise ValueError("url must be a string or null")
    _require_int(payload, "width")
    _require_int(payload, "height")
    if int(payload["width"]) <= 0 or int(payload["height"]) <= 0:
        raise ValueError("width and height must be positive")

    capture_mode = payload["capture_mode"]
    if capture_mode not in CAPTURE_MODES:
        raise ValueError(
            f"capture_mode must be one of {sorted(CAPTURE_MODES)}, got {capture_mode!r}"
        )

    phash = payload["phash"]
    if not isinstance(phash, str) or len(phash) != PHASH_HEX_LEN:
        raise ValueError(f"phash must be {PHASH_HEX_LEN} hex characters")
    int(phash, 16)

    ocr = payload["ocr"]
    if not isinstance(ocr, dict) or "lines" not in ocr or len(ocr) != 1:
        raise ValueError("ocr must be an object with only a lines array")
    lines = ocr["lines"]
    if not isinstance(lines, list):
        raise ValueError("ocr.lines must be an array")
    for index, line in enumerate(lines):
        _validate_ocr_line(line, index)


def _validate_ocr_line(line: object, index: int) -> None:
    prefix = f"ocr.lines[{index}]"
    if not isinstance(line, dict):
        raise ValueError(f"{prefix} must be an object")
    expected = {"text", "bbox", "confidence"}
    if set(line) != expected:
        raise ValueError(f"{prefix} fields must be {sorted(expected)}")
    if not isinstance(line["text"], str) or not line["text"].strip():
        raise ValueError(f"{prefix}.text must be a non-empty string")
    bbox = line["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"{prefix}.bbox must be [x, y, width, height]")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in bbox):
        raise ValueError(f"{prefix}.bbox values must be integers")
    _x, _y, width, height = bbox
    if width < 0 or height < 0:
        raise ValueError(f"{prefix}.bbox width/height must be >= 0")
    confidence = line["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError(f"{prefix}.confidence must be a number")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(f"{prefix}.confidence must be between 0 and 1")


def _require_str(payload: Mapping[str, Any], key: str) -> None:
    if not isinstance(payload[key], str) or not payload[key]:
        raise ValueError(f"{key} must be a non-empty string")


def _require_int(payload: Mapping[str, Any], key: str) -> None:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
