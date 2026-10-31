from __future__ import annotations

from datetime import UTC, datetime

from PIL import Image

from watson_fixturegen.cadence import frame_id_for
from watson_fixturegen.phash import average_hash_hex
from watson_fixturegen.secrets import find_secret_hits
from watson_fixturegen.sidecar import dump_sidecar, load_sidecar, make_sidecar, validate_sidecar


def _sidecar(**overrides: object) -> dict[str, object]:
    payload = make_sidecar(
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
    payload.update(overrides)  # type: ignore[typeddict-item]
    return dict(payload)


def test_sidecar_roundtrip_is_canonical() -> None:
    text = dump_sidecar(_sidecar())
    again = dump_sidecar(load_sidecar(text))
    assert text == again
    assert '"capture_mode": "fixture"' in text
    assert '"captured_at": "2026-10-31T09:00:00Z"' in text
    assert text.startswith("{\n  \"frame_id\"")


def test_schema_allows_null_display_id_and_selftest() -> None:
    payload = _sidecar(display_id=None, capture_mode="selftest", window_title="")
    validate_sidecar(payload)


def test_average_hash_is_deterministic() -> None:
    image = Image.new("RGB", (64, 64), (240, 240, 244))
    image.paste((30, 80, 140), (8, 8, 40, 40))
    first = average_hash_hex(image)
    second = average_hash_hex(image.copy())
    assert first == second
    assert len(first) == 16
    int(first, 16)


def test_secret_and_brand_scanner() -> None:
    clean = "Priya Natarajan wrote from priya@acme.example about the 14 Nov demo."
    assert find_secret_hits(clean) == []
    assert find_secret_hits("token sk-abcdefghijklmnopqrstuvwxyz123456")
    assert find_secret_hits("open gmail to send this")
    assert find_secret_hits("4111 1111 1111 1111")
