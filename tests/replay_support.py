"""Shared helpers for replay unit and privacy tests."""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watson.memory.db import connect
from watson.memory.migrate import upgrade_head

REPO = Path(__file__).resolve().parent.parent
DAY_ACME_Q4 = REPO / "tests" / "fixtures" / "day_acme_q4"
TEST_KEY = bytes.fromhex("11" * 32)


def open_test_db(data_dir: Path):
    path = data_dir / "watson.db"
    upgrade_head(path, TEST_KEY)
    path.chmod(0o600)
    return connect(path, TEST_KEY, load_vec=False)


def write_png(path: Path, width: int = 2, height: int = 2) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + (b"\x00\x00\x00" * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return png


def frame_id_for(name: str, seed: int, index: int) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"watson.fixture:{name}:{seed}")
    return str(uuid.uuid5(namespace, str(index)))


def make_sidecar(
    *,
    frame_id: str,
    captured_at: str,
    ocr_lines: list[dict[str, Any]],
    width: int = 2,
    height: int = 2,
    window_title: str = "Inbox",
    bundle_id: str = "com.lumen.mail",
    app_name: str = "Lumen Mail",
    is_private_browsing: bool = False,
) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "captured_at": captured_at,
        "display_id": 1,
        "bundle_id": bundle_id,
        "app_name": app_name,
        "window_title": window_title,
        "window_id": 1,
        "is_private_browsing": is_private_browsing,
        "url": None,
        "ocr": {"lines": ocr_lines},
        "phash": "0123456789abcdef",
        "width": width,
        "height": height,
        "capture_mode": "fixture",
    }


def write_mini_fixture(
    root: Path,
    *,
    name: str = "day_mini",
    date: str = "2026-10-31",
    frames: int = 2,
    planted: str = "PLANTED_MINI_OCR_UNIQUE_DO_NOT_LOG",
) -> Path:
    """Write a tiny valid fixture (PNG + sidecar + manifest) under ``root``."""
    fixture = root / name
    day_dir = fixture / "frames" / date
    day_dir.mkdir(parents=True)
    entries: dict[str, dict[str, object]] = {}
    start = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    for index in range(1, frames + 1):
        frame_id = frame_id_for(name, 1, index)
        png_path = day_dir / f"{frame_id}.png"
        write_png(png_path, 2, 2)
        captured = start.replace(minute=index)
        sidecar = make_sidecar(
            frame_id=frame_id,
            captured_at=captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ocr_lines=[
                {
                    "text": f"{planted} line {index}",
                    "bbox": [0, 0, 2, 2],
                    "confidence": 1.0,
                }
            ],
        )
        (day_dir / f"{frame_id}.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        rel = f"frames/{date}/{frame_id}.png"
        entries[frame_id] = {
            "png": rel,
            "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
            "width": 2,
            "height": 2,
        }
    manifest = {"name": name, "date": date, "seed": 1, "frames": entries}
    (fixture / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return fixture


def distinctive_ocr_texts(fixture_dir: Path, *, min_len: int = 24) -> list[str]:
    texts: list[str] = []
    for path in fixture_dir.glob("frames/*/*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for line in payload["ocr"]["lines"]:
            text = str(line["text"])
            if len(text) >= min_len:
                texts.append(text)
    return texts
